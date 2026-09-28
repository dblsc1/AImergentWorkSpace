package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeAW 模拟 ActivityWatch：按「与区间有重叠」筛，**故意不裁剪**，验证本地会裁。
func fakeAW(t *testing.T, window, afk []awEvent) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/0/buckets/" {
			json.NewEncoder(w).Encode(map[string]awBucket{
				"aw-watcher-window_h": {ID: "aw-watcher-window_h", Type: "currentwindow", Hostname: "h"},
				"aw-watcher-afk_h":    {ID: "aw-watcher-afk_h", Type: "afkstatus", Hostname: "h"},
			})
			return
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
	c := defaultConfig(os.TempDir())
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
	if defaultConfig(t.TempDir()).Enabled {
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
		win(20, 20, "firefox", "garden notes"),
		win(40, 10, "blender", "scene"),
	}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.RulesFile = rules
	cfg.ClassifierURL = svc.URL
	cfg.MergeGapMinutes = 1
	if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	// 服务只收到规则没认出来的两段，任务树拼成路径、已完成任务不给。
	if len(got.Segments) != 2 || len(got.Tasks) != 1 || got.Tasks[0].Path != "学习 / garden / 写提示词" || svcAuth != "Bearer test-device-token" {
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
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
	defer svc.Close()
	aw := fakeAW(t, []awEvent{win(0, 20, "blender", "scene")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.ClassifierURL = svc.URL
	st := activeState()
	if _, err := tick(cfg, st, at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var up uploadBody
	json.Unmarshal(ck.bodies[0], &up)
	sg := up.Segments[0].Suggestion
	if len(up.Segments) != 1 || sg.TaskID != nil || sg.Classifier != "service" || !strings.Contains(sg.Reason, "不可用") || !st.Cursor.Equal(at(55)) {
		t.Fatalf("%+v cursor=%v", up, st.Cursor)
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
