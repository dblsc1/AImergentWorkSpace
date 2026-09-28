package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"
)

// fakeAW 模拟 ActivityWatch：按「与区间有重叠」筛，**故意不裁剪**，验证本地会裁。
// 桶里还混着另一台机器（ActivityWatch 多设备同步）的窗口桶，验证不会被挑中。
func fakeAW(t *testing.T, window, afk []awEvent) *httptest.Server {
	host, _ := os.Hostname()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/0/buckets/" {
			json.NewEncoder(w).Encode(map[string]awBucket{
				"aw-watcher-window_a-other": {ID: "aw-watcher-window_a-other", Type: "currentwindow", Hostname: "a-other"},
				"aw-watcher-window_h":       {ID: "aw-watcher-window_h", Type: "currentwindow", Hostname: host},
				"aw-watcher-afk_h":          {ID: "aw-watcher-afk_h", Type: "afkstatus", Hostname: host},
			})
			return
		}
		if strings.Contains(r.URL.Path, "other") {
			t.Errorf("read another host's bucket: %s", r.URL.Path)
		}
		start, _ := time.Parse(time.RFC3339Nano, r.URL.Query().Get("start"))
		end, _ := time.Parse(time.RFC3339Nano, r.URL.Query().Get("end"))
		src := window
		if strings.Contains(r.URL.Path, "afk") {
			src = afk
		}
		out := []awEvent{}
		for _, e := range src {
			if !e.end().Before(start) && !e.Timestamp.After(end) {
				out = append(out, e)
			}
		}
		json.NewEncoder(w).Encode(out)
	}))
}

type cockpit struct {
	*httptest.Server
	mu     sync.Mutex
	status int
	auth   []string
	bodies [][]byte
}

func fakeCockpit(t *testing.T) *cockpit {
	c := &cockpit{status: 200}
	c.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c.mu.Lock()
		defer c.mu.Unlock()
		c.auth = append(c.auth, r.Header.Get("Authorization"))
		switch r.URL.Path {
		case "/Cockpit/api/core/activity/suggestions":
			b, _ := io.ReadAll(r.Body)
			c.bodies = append(c.bodies, b)
			w.WriteHeader(c.status)
		case "/Cockpit/api/core/views/tree":
			io.WriteString(w, `{"zones":[{"id":"z1","name":"学习"}],"projects":[{"id":"p1","zoneId":"z1","name":"garden",
				"tasks":[{"id":"t_a1","name":"写提示词","done":false},{"id":"t_old","name":"旧","done":true}]}]}`)
		default:
			w.WriteHeader(404)
		}
	}))
	return c
}

func win(min, dur float64, app, title string) awEvent {
	return awEvent{Timestamp: at(min), Duration: dur * 60, Data: map[string]any{"app": app, "title": title}}
}

func testConfig(aw, ck string) Config {
	c, _ := defaultConfig(os.TempDir())
	c.Enabled = true
	c.CockpitURL = ck + "/Cockpit/"
	c.ActivityWatchURL = aw + "/api/0"
	c.DeviceToken = "test-device-token"
	c.DeviceID = "dev_test"
	c.RulesFile = ""
	return c
}

// 已处于活跃状态、游标在 t0 的状态。
func activeState() *State { return &State{Cursor: at(0), Active: true} }

func TestUploadPayloadShapeAndBearer(t *testing.T) {
	aw := fakeAW(t, []awEvent{
		win(0, 30, "code", "main.go — garden — Code"),
		win(30, 2, "WeChat.exe", "张三: 13812345678 bob@x.com"),
		win(32, 20, "code", "plot.gd — garden — Code"),
	}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	st := activeState()
	msg, err := tick(testConfig(aw.URL, ck.URL), st, at(60), http.DefaultClient)
	if err != nil {
		t.Fatal(err)
	}
	if len(ck.bodies) != 1 || ck.auth[0] != "Bearer test-device-token" {
		t.Fatalf("msg=%s bodies=%d auth=%v", msg, len(ck.bodies), ck.auth)
	}
	var raw map[string]any
	json.Unmarshal(ck.bodies[0], &raw)
	if raw["deviceId"] != "dev_test" || len(raw) != 2 {
		t.Fatalf("top-level: %v", raw)
	}
	segs := raw["segments"].([]any)
	if len(segs) != 1 {
		t.Fatalf("segments: %s", ck.bodies[0])
	}
	s := segs[0].(map[string]any)
	for _, k := range []string{"startAt", "endAt", "durationSeconds", "app", "title", "suggestion"} {
		if _, ok := s[k]; !ok {
			t.Fatalf("missing %s: %v", k, s)
		}
	}
	if len(s) != 6 {
		t.Fatalf("extra fields: %v", s)
	}
	start, err := time.Parse(time.RFC3339, s["startAt"].(string))
	if err != nil || !start.Equal(at(0)) || !strings.ContainsAny(s["startAt"].(string)[19:], "+-") {
		t.Fatalf("startAt %v %v", s["startAt"], err)
	}
	if s["durationSeconds"].(float64) != 52*60 || s["app"] != "code" {
		t.Fatalf("%v", s)
	}
	body := string(ck.bodies[0])
	if strings.Contains(body, "bob@x.com") || strings.Contains(body, "13812345678") || strings.Contains(body, "张三") {
		t.Fatalf("raw data leaked: %s", body)
	}
	sg := s["suggestion"].(map[string]any)
	if sg["taskId"] != nil || sg["confidence"].(float64) != 0 || sg["classifier"] != "rules" || len(sg) != 4 {
		t.Fatalf("suggestion %v", sg)
	}
	if !st.Cursor.Equal(at(55)) {
		t.Fatalf("cursor %v", st.Cursor)
	}
}

func TestCursorOnlyAdvancesOn2xxAndResendIsIdentical(t *testing.T) {
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	st := activeState()

	ck.status = 503
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err == nil {
		t.Fatal("want error on 503")
	}
	if !st.Cursor.Equal(at(0)) {
		t.Fatalf("cursor moved on failure: %v", st.Cursor)
	}
	ck.status = 401
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err == nil || !strings.Contains(err.Error(), "令牌") {
		t.Fatalf("401 err=%v", err)
	}
	ck.status = 201
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if !st.Cursor.Equal(at(55)) {
		t.Fatalf("cursor %v", st.Cursor)
	}
	// 三次发的是同一份内容：服务端按 startAt 防重，重发安全。
	if string(ck.bodies[0]) != string(ck.bodies[2]) {
		t.Fatalf("resend differs:\n%s\n%s", ck.bodies[0], ck.bodies[2])
	}
}

func TestOfflineRetriesNextTick(t *testing.T) {
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	cfg := testConfig(aw.URL, "http://127.0.0.1:1") // 没人监听
	st := activeState()
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err == nil || !st.Cursor.Equal(at(0)) {
		t.Fatalf("err=%v cursor=%v", err, st.Cursor)
	}
}

// noNet 在任何请求发出时让测试失败。
type noNet struct{ t *testing.T }

func (n noNet) RoundTrip(r *http.Request) (*http.Response, error) {
	n.t.Fatalf("unexpected HTTP call: %s %s", r.Method, r.URL)
	return nil, nil
}

func TestDisabledPausedFirstRunMakeZeroCalls(t *testing.T) {
	hc := &http.Client{Transport: noNet{t}}
	cfg := testConfig("http://aw.invalid", "http://ck.invalid")

	cfg.Enabled = false
	st := activeState()
	if msg, err := tick(cfg, st, at(60), hc); err != nil || !strings.Contains(msg, "不上传") || st.Active {
		t.Fatalf("disabled: %s %v", msg, err)
	}
	cfg.Enabled, cfg.Paused = true, true
	st = activeState()
	if msg, err := tick(cfg, st, at(60), hc); err != nil || !strings.Contains(msg, "暂停") || st.Active {
		t.Fatalf("paused: %s %v", msg, err)
	}
	// 恢复后第一轮：游标跳到现在，暂停期间（0–60）不补传，也不发请求。
	cfg.Paused = false
	if msg, err := tick(cfg, st, at(60), hc); err != nil || !st.Cursor.Equal(at(60)) || !st.Active {
		t.Fatalf("resume: %s %v %v", msg, err, st.Cursor)
	}
	// init 写出的默认配置就是关着的。
	if c, _ := defaultConfig(t.TempDir()); c.Enabled {
		t.Fatal("default must be disabled")
	}
}

func TestBacklogIsBounded(t *testing.T) {
	aw := fakeAW(t, nil, nil)
	defer aw.Close()
	cfg := testConfig(aw.URL, "http://ck.invalid")
	cfg.MaxBacklogHours = 1
	st := activeState()
	tick(cfg, st, at(600), http.DefaultClient)
	if st.Cursor.Before(at(540)) {
		t.Fatalf("cursor %v", st.Cursor)
	}
}

func TestRulesThenServiceFallback(t *testing.T) {
	dir := t.TempDir()
	rules := filepath.Join(dir, "rules.json")
	os.WriteFile(rules, []byte(`{"rules":[{"app":"^code$","title":"garden","taskId":"t_a1"}]}`), 0o600)

	var got struct {
		Segments []map[string]any `json:"segments"`
		Tasks    []taskRef        `json:"tasks"`
	}
	var svcAuth string
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		svcAuth = r.Header.Get("Authorization")
		json.NewDecoder(r.Body).Decode(&got)
		io.WriteString(w, `{"suggestions":[
			{"segmentId":"seg_1","taskId":"t_a1","confidence":1.7,"reason":"标题含 garden"},
			{"segmentId":"seg_0","taskId":"t_a1","confidence":0.1,"reason":"不许覆盖规则"},
			{"segmentId":"seg_2","taskId":"t_ghost","confidence":0.9,"reason":"瞎编的任务"}]}`)
	}))
	defer svc.Close()

	aw := fakeAW(t, []awEvent{
		win(0, 20, "code", "a.go — garden — Code"),
		win(20, 20, "krita", "garden notes"),
		win(40, 10, "blender", "scene"),
	}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.RulesFile = rules
	cfg.ClassifierURL = svc.URL
	cfg.ClassifierToken = "svc-only"
	cfg.MergeGapMinutes = 1
	if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	// 服务只收到规则没认出来的两段，任务树拼成路径、已完成任务不给。
	// 分类服务只拿到它自己的 classifierToken，HoneyComb 的设备令牌绝不给它。
	if len(got.Segments) != 2 || len(got.Tasks) != 1 || got.Tasks[0].Path != "学习 / garden / 写提示词" || svcAuth != "Bearer svc-only" {
		t.Fatalf("service got %+v auth=%q", got, svcAuth)
	}
	var up uploadBody
	json.Unmarshal(ck.bodies[0], &up)
	s := up.Segments
	if len(s) != 3 {
		t.Fatalf("%s", ck.bodies[0])
	}
	if s[0].Suggestion.Classifier != "rules" || *s[0].Suggestion.TaskID != "t_a1" || s[0].Suggestion.Confidence != 0.9 {
		t.Errorf("rules: %+v", s[0].Suggestion)
	}
	if s[1].Suggestion.Classifier != "service" || *s[1].Suggestion.TaskID != "t_a1" || s[1].Suggestion.Confidence != 1 {
		t.Errorf("service: %+v", s[1].Suggestion)
	}
	if s[2].Suggestion.TaskID != nil || s[2].Suggestion.Confidence != 0 {
		t.Errorf("unknown task must become null: %+v", s[2].Suggestion)
	}
}

func TestServiceFailureDoesNotBlock(t *testing.T) {
	var svcAuth []string
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		svcAuth = append(svcAuth, r.Header.Get("Authorization"))
		w.WriteHeader(500)
	}))
	defer svc.Close()
	aw := fakeAW(t, []awEvent{win(0, 20, "blender", "scene")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.ClassifierURL = svc.URL + "/classify?q=leakmarker"
	st := activeState()
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var up uploadBody
	json.Unmarshal(ck.bodies[0], &up)
	sg := up.Segments[0].Suggestion
	if len(up.Segments) != 1 || sg.TaskID != nil || sg.Classifier != "service" || sg.Reason != "分类服务不可用" || !st.Cursor.Equal(at(55)) {
		t.Fatalf("%+v cursor=%v", up, st.Cursor)
	}
	// 错误细节（地址、查询串）只进本机日志，不进上传的 reason；分类服务拿不到设备令牌。
	if strings.Contains(string(ck.bodies[0]), "leakmarker") || strings.Contains(string(ck.bodies[0]), "127.0.0.1") || svcAuth[0] != "" {
		t.Fatalf("leak: %s auth=%v", ck.bodies[0], svcAuth)
	}
}

func TestBadRulesFileStopsUpload(t *testing.T) {
	p := filepath.Join(t.TempDir(), "rules.json")
	os.WriteFile(p, []byte(`{"rules":[{"app":"(","taskId":"t"}]}`), 0o600)
	if _, err := loadRules(p); err == nil {
		t.Fatal("bad regex must error")
	}
	if r, err := loadRules(filepath.Join(t.TempDir(), "missing.json")); err != nil || r != nil {
		t.Fatal("missing file = no rules")
	}
}

func TestPauseCLIMarksInactive(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("AI_DETECTOR_HOME", dir)
	if err := cli([]string{"init"}, io.Discard); err != nil {
		t.Fatal(err)
	}
	p := pathsIn(dir)
	saveJSON(p.state, State{Cursor: at(0), Active: true})
	if err := cli([]string{"pause"}, io.Discard); err != nil {
		t.Fatal(err)
	}
	var st State
	var cfg Config
	loadJSON(p.state, &st)
	loadJSON(p.config, &cfg)
	if st.Active || !cfg.Paused || cfg.Enabled {
		t.Fatalf("st=%+v cfg paused=%v enabled=%v", st, cfg.Paused, cfg.Enabled)
	}
	if fi, _ := os.Stat(p.config); fi.Mode().Perm() != 0o600 {
		t.Fatalf("config perm %v", fi.Mode().Perm())
	}
}

func TestAutostartFiles(t *testing.T) {
	for goos, want := range map[string]string{
		"windows": `"" run", 0, False`,
		"darwin":  "<string>run</string>",
		"linux":   `Exec="/opt/ai detector" run`,
	} {
		path, content, err := autostartFile(goos, "/opt/ai detector")
		if err != nil || !strings.Contains(content, want) || path == "" {
			t.Errorf("%s: %s %q %v", goos, path, content, err)
		}
	}
}

func TestClassifierURLMustBeHTTPSUnlessLoopback(t *testing.T) {
	for u, ok := range map[string]bool{
		"https://cls.example.com/x": true, "http://127.0.0.1:9000": true, "http://localhost/x": true,
		"http://[::1]:9/": true, "http://cls.example.com/x": false, "http://192.168.1.5/x": false,
		"ftp://x.com": false, "not a url": false,
	} {
		if err := checkClassifierURL(u); (err == nil) != ok {
			t.Errorf("%s: err=%v", u, err)
		}
	}
	// 配了不合规的地址：整轮不上传、不发请求，游标不动。
	cfg := testConfig("http://aw.invalid", "http://ck.invalid")
	cfg.ClassifierURL = "http://cls.example.com/x"
	st := activeState()
	if _, err := tick(cfg, st, at(60), &http.Client{Transport: noNet{t}}); err == nil || !st.Cursor.Equal(at(0)) {
		t.Fatalf("err=%v cursor=%v", err, st.Cursor)
	}
}

func TestClassifierRedirectIsNotFollowed(t *testing.T) {
	hit := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hit = true }))
	defer target.Close()
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer svc.Close()
	aw := fakeAW(t, []awEvent{win(0, 20, "blender", "scene")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.ClassifierURL = svc.URL
	if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var up uploadBody
	json.Unmarshal(ck.bodies[0], &up)
	if hit || up.Segments[0].Suggestion.Reason != "分类服务不可用" {
		t.Fatalf("hit=%v %+v", hit, up.Segments[0].Suggestion)
	}
}

func TestCockpitRedirectsAreRestricted(t *testing.T) {
	var evil []string
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		evil = append(evil, r.Header.Get("Authorization"))
	}))
	defer other.Close()
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	for name, code := range map[string]int{"cross-host": http.StatusTemporaryRedirect, "post-to-get": http.StatusFound} {
		var self *httptest.Server
		self = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet {
				return // 被改成 GET 的话这里回 200，不能被当成上传成功
			}
			dst := other.URL + r.URL.Path
			if name == "post-to-get" {
				dst = self.URL + "/elsewhere"
			}
			http.Redirect(w, r, dst, code)
		}))
		cfg := testConfig(aw.URL, self.URL)
		st := activeState()
		if _, err := tick(cfg, st, at(60), http.DefaultClient); err == nil || !st.Cursor.Equal(at(0)) {
			t.Errorf("%s: err=%v cursor=%v", name, err, st.Cursor)
		}
		self.Close()
	}
	if len(evil) != 0 {
		t.Fatalf("followed cross-host redirect: %v", evil)
	}
}

func TestUploadInBatchesCursorFollowsAckedBatches(t *testing.T) {
	// 450 段、每段 10 分钟间隔 6 分钟（> G=5，不会并）：3 批（200/200/50）。
	var evs []awEvent
	for i := 0; i < 450; i++ {
		evs = append(evs, win(float64(i*16), 10, "code", "x"))
	}
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.MaxBacklogHours = 0
	now := at(450*16 + 60)

	// 第二批失败：游标停在第 201 段（下标 200）的开始。
	calls := 0
	ck.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		b, _ := io.ReadAll(r.Body)
		ck.bodies = append(ck.bodies, b)
		if calls == 2 {
			w.WriteHeader(502)
		}
	})
	st := activeState()
	if _, err := tick(cfg, st, now, http.DefaultClient); err == nil || !strings.Contains(err.Error(), "200/450") {
		t.Fatalf("err=%v", err)
	}
	if !st.Cursor.Equal(at(200*16)) || st.FailedParams == "" {
		t.Fatalf("cursor=%v failed=%q", st.Cursor, st.FailedParams)
	}
	var b1 uploadBody
	json.Unmarshal(ck.bodies[0], &b1)
	if len(b1.Segments) != 200 {
		t.Fatalf("batch size %d", len(b1.Segments))
	}
	// 下一轮从游标重算：重发的第一批和失败那批逐字节相同（startAt 稳定，防重成立）。
	if _, err := tick(cfg, st, now, http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if string(ck.bodies[1]) != string(ck.bodies[2]) || len(ck.bodies) != 4 || st.FailedParams != "" {
		t.Fatalf("resend differs or wrong count: %d", len(ck.bodies))
	}
}

func TestChangedParamsAfterFailureAreLogged(t *testing.T) {
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	st := activeState()
	st.FailedParams = "G=10分钟,M=3分钟"
	var buf strings.Builder
	log.SetOutput(&buf)
	defer log.SetOutput(os.Stderr)
	tick(cfg, st, at(60), http.DefaultClient)
	if !strings.Contains(buf.String(), "G=10分钟,M=3分钟") || st.FailedParams != "" {
		t.Fatalf("log=%q failed=%q", buf.String(), st.FailedParams)
	}
}

func TestOtherHostBucketIsNeverGuessed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/0/buckets/" {
			json.NewEncoder(w).Encode(map[string]awBucket{
				"aw-watcher-window_laptop": {Type: "currentwindow", Hostname: "laptop-not-me"},
				"aw-watcher-afk_laptop":    {Type: "afkstatus", Hostname: "laptop-not-me"},
			})
			return
		}
		json.NewEncoder(w).Encode([]awEvent{})
	}))
	defer srv.Close()
	c := awClient{base: srv.URL + "/api/0", http: http.DefaultClient}
	if _, err := c.fetch(at(0), at(10), "", ""); err == nil || !strings.Contains(err.Error(), "windowBucket") {
		t.Fatalf("err=%v", err)
	}
	// 主机名改过：配置里写明桶 id 就用它。
	if _, err := c.fetch(at(0), at(10), "aw-watcher-window_laptop", "aw-watcher-afk_laptop"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.fetch(at(0), at(10), "nope", "aw-watcher-afk_laptop"); err == nil {
		t.Fatal("unknown explicit bucket must error")
	}
}

func TestLock(t *testing.T) {
	p := filepath.Join(t.TempDir(), "x.lock")
	rel, err := acquireLock(p)
	if err != nil {
		t.Fatal(err)
	}
	// 另一个活着的进程拿着：拒绝。
	os.WriteFile(p, []byte(fmt.Sprint(os.Getppid())), 0o600) // 父进程（go test）活着
	if _, err := acquireLock(p); err == nil {
		t.Fatal("lock held by a live process must be refused")
	}
	os.WriteFile(p, []byte("999999999"), 0o600) // 不存在的进程：旧锁，接管
	rel2, err := acquireLock(p)
	if err != nil {
		t.Fatal(err)
	}
	rel2()
	rel()
	if _, err := os.Stat(p); !os.IsNotExist(err) {
		t.Fatal("release must remove the lock file")
	}
}

func TestLockDoesNotStealStartingOrOwnLock(t *testing.T) {
	p := filepath.Join(t.TempDir(), "x.lock")
	// 刚建好、还没写 pid 的锁：不能当旧锁删。
	os.WriteFile(p, nil, 0o600)
	if _, err := acquireLock(p); err == nil || !strings.Contains(err.Error(), "正在启动") {
		t.Fatalf("err=%v", err)
	}
	if _, err := os.Stat(p); err != nil {
		t.Fatal("young empty lock must not be removed")
	}
	// 空文件放久了：残骸，接管。
	old := time.Now().Add(-time.Minute)
	os.Chtimes(p, old, old)
	rel, err := acquireLock(p)
	if err != nil {
		t.Fatal(err)
	}
	// pid 是自己的锁：不删。
	if _, err := acquireLock(p); err == nil {
		t.Fatal("own-pid lock must be refused")
	}
	rel()
}

func TestDeadClassifierCalledOncePerRound(t *testing.T) {
	calls := 0
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(500)
	}))
	defer svc.Close()
	var evs []awEvent
	for i := 0; i < 450; i++ {
		evs = append(evs, win(float64(i*16), 10, "code", "x"))
	}
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.MaxBacklogHours = 0
	cfg.ClassifierURL = svc.URL
	if _, err := tick(cfg, activeState(), at(450*16+60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if calls != 1 || len(ck.bodies) != 3 {
		t.Fatalf("classifier calls=%d uploads=%d", calls, len(ck.bodies))
	}
	var last uploadBody
	json.Unmarshal(ck.bodies[2], &last)
	if last.Segments[0].Suggestion.Reason != "分类服务不可用" {
		t.Fatalf("reason=%q", last.Segments[0].Suggestion.Reason)
	}
}

func TestOnceRefusesWhileRunHoldsLock(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("AI_DETECTOR_HOME", dir)
	cli([]string{"init"}, io.Discard)
	os.WriteFile(pathsIn(dir).lock, []byte(fmt.Sprint(os.Getppid())), 0o600)
	if err := cli([]string{"once"}, io.Discard); err == nil || !strings.Contains(err.Error(), "正在同步") {
		t.Fatalf("err=%v", err)
	}
	// 常驻进程在跑时 pause 只改配置，不碰状态文件。
	saveJSON(pathsIn(dir).state, State{Cursor: at(0), Active: true})
	if err := cli([]string{"pause"}, io.Discard); err != nil {
		t.Fatal(err)
	}
	var st State
	loadJSON(pathsIn(dir).state, &st)
	if !st.Active {
		t.Fatal("state must be left to the running process")
	}
}

func TestSaveJSONConcurrentWritersLeaveValidFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "s.json")
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func(i int) { defer wg.Done(); saveJSON(p, State{LastOutcome: strings.Repeat("x", i*1000)}) }(i)
	}
	wg.Wait()
	var st State
	if err := loadJSON(p, &st); err != nil {
		t.Fatal(err)
	}
	if m, _ := filepath.Glob(filepath.Join(dir, "*.tmp")); len(m) != 0 {
		t.Fatalf("temp files left: %v", m)
	}
}

func TestAutostartRefusesDangerousPaths(t *testing.T) {
	for goos, exe := range map[string]string{
		"linux":   "/opt/$(rm -rf ~)/ai",
		"darwin":  "/Applications/a\nb/ai",
		"windows": `C:\x"y\ai.exe`,
	} {
		if _, _, err := autostartFile(goos, exe); err == nil {
			t.Errorf("%s: %q must be refused", goos, exe)
		}
	}
	if _, c, err := autostartFile("darwin", "/Apps/R&D <x>/ai"); err != nil || !strings.Contains(c, "R&amp;D &lt;x&gt;") {
		t.Errorf("plist escape: %v %s", err, c)
	}
	if _, _, err := autostartFile("windows", `C:\Program Files\HoneyComb\ai-detector.exe`); err != nil {
		t.Errorf("normal windows path refused: %v", err)
	}
}

func TestCleanReason(t *testing.T) {
	if got := cleanReason("a\x00b\nc\u202e"); got != "abc\u202e" && got != "abc" {
		t.Fatalf("%q", got)
	}
	long := strings.Repeat("字", 100) // 300 字节
	got := cleanReason(long)
	if len(got) > 200 || !utf8.ValidString(got) {
		t.Fatalf("len=%d valid=%v", len(got), utf8.ValidString(got))
	}
}

func TestOverlappingWindowEventsCountOnce(t *testing.T) {
	// 记录器重启：同一段时间出现两条重叠事件。Active 只算一遍；从段中间任何重算点
	// 开始读，段的起点都不漂。
	d := awData{window: []awEvent{
		{Timestamp: at(0), Duration: 20 * 60, Data: map[string]any{"app": "code", "title": "x"}},
		{Timestamp: at(10), Duration: 20 * 60, Data: map[string]any{"app": "code", "title": "x"}},
		{Timestamp: at(12), Duration: 60, Data: map[string]any{"app": "code", "title": "x"}}, // 完全包含
	}}
	s := merge(buildFragments(d, at(0), at(60), newRedactor(Config{})), G)
	if len(s) != 1 || s[0].Active != 30*time.Minute {
		t.Fatalf("%+v", s)
	}
}
