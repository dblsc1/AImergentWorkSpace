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
	"sort"
	"strings"
	"sync"
	"testing"
	"time"
)

// ── 测试用假服务 ─────────────────────────────────────────────────────────

// fakeAWWeb：窗口 / 离开 / 浏览器扩展三类桶，按「有重叠」筛。
func fakeAWWeb(t *testing.T, window, afk, web []awEvent) *httptest.Server {
	host, _ := os.Hostname()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/0/buckets/" {
			json.NewEncoder(w).Encode(map[string]awBucket{
				"win": {ID: "win", Type: "currentwindow", Hostname: host},
				"afk": {ID: "afk", Type: "afkstatus", Hostname: host},
				"web": {ID: "web", Type: "web.tab.current", Hostname: host},
			})
			return
		}
		start, _ := time.Parse(time.RFC3339Nano, r.URL.Query().Get("start"))
		end, _ := time.Parse(time.RFC3339Nano, r.URL.Query().Get("end"))
		src := map[string][]awEvent{"/api/0/buckets/win/events": window, "/api/0/buckets/afk/events": afk, "/api/0/buckets/web/events": web}[r.URL.Path]
		out := []awEvent{}
		for _, e := range src {
			if !e.end().Before(start) && !e.Timestamp.After(end) {
				out = append(out, e)
			}
		}
		json.NewEncoder(w).Encode(out)
	}))
}

type req struct {
	path string
	body map[string]any
}

// liveCockpit：记下每个请求；respond 返回 (状态码, 响应体)，nil = 200 + 缺省响应。
type liveCockpit struct {
	*httptest.Server
	mu       sync.Mutex
	reqs     []req
	settings string // GET detector/settings 的响应体；空 = 404
	respond  func(path string) (int, string)
	block    chan struct{} // 非 nil：suggestions 请求卡在这里
	blocked  chan struct{}
	runs     int
}

func newLiveCockpit(t *testing.T) *liveCockpit {
	c := &liveCockpit{}
	c.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		if strings.HasSuffix(p, "/detector/settings") {
			c.mu.Lock()
			s := c.settings
			c.mu.Unlock()
			if s == "" {
				w.WriteHeader(404)
				return
			}
			io.WriteString(w, s)
			return
		}
		b, _ := io.ReadAll(r.Body)
		var m map[string]any
		_ = json.Unmarshal(b, &m)
		c.mu.Lock()
		c.reqs = append(c.reqs, req{p, m})
		block, blocked := c.block, c.blocked
		respond := c.respond
		c.mu.Unlock()
		if strings.HasSuffix(p, "/activity/suggestions") && block != nil {
			select {
			case blocked <- struct{}{}:
			default:
			}
			<-block
		}
		if respond != nil {
			if code, body := respond(p); code != 0 {
				w.WriteHeader(code)
				io.WriteString(w, body)
				return
			}
		}
		switch {
		case strings.HasSuffix(p, "/agents/start"):
			c.mu.Lock()
			c.runs++
			id := fmt.Sprintf("run_%d", c.runs)
			c.mu.Unlock()
			w.WriteHeader(201)
			fmt.Fprintf(w, `{"runId":%q}`, id)
		case strings.HasSuffix(p, "/phase"):
			io.WriteString(w, `{"applied":true}`)
		case strings.HasSuffix(p, "/stop"):
			io.WriteString(w, `{"duplicate":false}`)
		default:
			io.WriteString(w, `{"ok":true}`)
		}
	}))
	return c
}

func (c *liveCockpit) take() []req {
	c.mu.Lock()
	defer c.mu.Unlock()
	r := c.reqs
	c.reqs = nil
	return r
}

func (c *liveCockpit) posts(suffix string) []req {
	c.mu.Lock()
	defer c.mu.Unlock()
	var out []req
	for _, r := range c.reqs {
		if strings.HasSuffix(r.path, suffix) {
			out = append(out, r)
		}
	}
	return out
}

func liveConfig(aw, ck string) Config {
	c, _ := defaultConfig(os.TempDir())
	c.Enabled, c.Presence = true, true
	c.CockpitURL = ck
	c.ActivityWatchURL = aw + "/api/0"
	c.DeviceToken, c.DeviceID, c.RulesFile = "test-device-token", "dev_test", ""
	c.MergeGapMinutes, c.MinSegmentMinutes = 0, 1
	return c
}

// 运行时拼出来的假密钥（源码里不留字面量，gitleaks 在 CI 里扫）。
func fakeGitHubToken() string { return "gh" + "p_" + strings.Repeat("aB3", 12) }

// ── 在场心跳 ─────────────────────────────────────────────────────────────

// 心跳的 app / title 与同一条窗口事件上传出去的段完全一样（含强制脱敏、隐私选项、代号）。
func TestPresenceRedactionParityWithUpload(t *testing.T) {
	secret := fakeGitHubToken()
	now := time.Now().Truncate(time.Second)
	cases := []struct {
		name, app, title string
		privacy          Privacy
		web              []awEvent
	}{
		{"secret+path+email", "code", "export T=" + secret + " /home/alice/garden/plot.gd bob@corp.com", Privacy{}, nil},
		{"paths half", "vim", "vim /home/alice/work/garden/plot.gd", Privacy{Paths: "half"}, nil},
		{"app-only", "WeChat.exe", "张三：明天把合同发我", Privacy{}, nil},
		{"titles drop", "code", "plot.gd — garden — Code", Privacy{Titles: "drop"}, nil},
		{"pseudonymize", "code", "plot.gd — garden — Code " + secret, Privacy{Titles: "pseudonymize"}, nil},
		{"browser no tab", "firefox", "Private stuff - Mozilla Firefox", Privacy{}, nil},
		{"browser tab", "firefox", "Fix #1 - Mozilla Firefox", Privacy{},
			[]awEvent{{Timestamp: now.Add(-5 * time.Minute), Duration: 290, Data: map[string]any{"url": "https://github.com/a/b/pull/1?token=" + secret, "title": "Fix #1"}}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ev := awEvent{Timestamp: now.Add(-5 * time.Minute), Duration: 290, Data: map[string]any{"app": tc.app, "title": tc.title}}
			aw := fakeAWWeb(t, []awEvent{ev}, nil, tc.web)
			defer aw.Close()
			ck := newLiveCockpit(t)
			defer ck.Close()
			cfg := liveConfig(aw.URL, ck.URL)
			cfg.Privacy = tc.privacy
			cfg.dir = t.TempDir() // 代号对照表：两条路共用一份
			remotePresence.Store(nil)

			if _, err := tick(cfg, &State{Active: true, Cursor: now.Add(-10 * time.Minute)}, now, http.DefaultClient); err != nil {
				t.Fatal(err)
			}
			if sent, err := presenceBeat(cfg, http.DefaultClient, now); err != nil || !sent {
				t.Fatalf("beat sent=%v err=%v", sent, err)
			}
			up := ck.posts("/activity/suggestions")
			pr := ck.posts("/activity/presence")
			if len(up) != 1 || len(pr) != 1 {
				t.Fatalf("upload=%d presence=%d", len(up), len(pr))
			}
			seg := up[0].body["segments"].([]any)[0].(map[string]any)
			p := pr[0].body
			if p["app"] != seg["app"] || p["title"] != seg["title"] || p["afk"] != false || p["deviceId"] != "dev_test" || len(p) != 4 {
				t.Fatalf("presence %v\nupload app=%v title=%v", p, seg["app"], seg["title"])
			}
			if b, _ := json.Marshal(p); strings.Contains(string(b), secret) || strings.Contains(string(b), "alice") || strings.Contains(string(b), "bob@") {
				t.Fatalf("leaked: %s", b)
			}
		})
	}
}

func TestPresenceOffAfkAndRemoteToggle(t *testing.T) {
	now := time.Now()
	ev := awEvent{Timestamp: now.Add(-time.Minute), Duration: 55, Data: map[string]any{"app": "code", "title": "plot.gd"}}
	aw := fakeAWWeb(t, []awEvent{ev}, []awEvent{{Timestamp: now.Add(-30 * time.Second), Duration: 25, Data: map[string]any{"status": "afk"}}}, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	defer remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)

	// 本机关、网页没设：零请求（连设置都不拉）。暂停同样。
	remotePresence.Store(nil)
	off := cfg
	off.Presence = false
	paused := cfg
	paused.Paused = true
	for _, c := range []Config{off, paused} {
		if sent, err := presenceBeat(c, &http.Client{Transport: noNet{t}}, now); sent || err != nil {
			t.Fatalf("sent=%v err=%v", sent, err)
		}
	}

	// 离开：app / title 都是 ""。
	if sent, err := presenceBeat(cfg, http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("sent=%v err=%v", sent, err)
	}
	if p := ck.take()[0].body; p["afk"] != true || p["app"] != "" || p["title"] != "" {
		t.Fatalf("afk body %v", p)
	}

	// 网页上开（本机关）：发，且用网页的隐私选项（titles=drop）。
	aw2 := fakeAWWeb(t, []awEvent{ev}, nil, nil)
	defer aw2.Close()
	off.ActivityWatchURL = aw2.URL + "/api/0"
	ck.settings = `{"settings":{"privacy":{"titles":"drop"},"idle":{},"presence":true}}`
	on := true
	remotePresence.Store(&on)
	if sent, err := presenceBeat(off, http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("remote on: sent=%v err=%v", sent, err)
	}
	if p := ck.take()[0].body; p["app"] != "code" || p["title"] != "" {
		t.Fatalf("remote privacy not applied: %v", p)
	}
	// 网页上关（本机开）：拉到设置后不发。
	ck.settings = `{"settings":{"privacy":{},"idle":{},"presence":false}}`
	if sent, _ := presenceBeat(cfg, http.DefaultClient, now); sent || len(ck.posts("/presence")) != 0 {
		t.Fatal("remote off still sent")
	}
	if p := remotePresence.Load(); p == nil || *p {
		t.Fatalf("remotePresence %v", p)
	}
	// 网页上 presence 改回 null：同步一轮拉设置时刷新，之后回到本机配置。
	ck.settings = `{"settings":{"privacy":{},"idle":{},"presence":null}}`
	if err := applyRemoteSettings(&Config{DeviceID: "dev_test"}, http.DefaultClient, ck.URL); err != nil {
		t.Fatal(err)
	}
	if sent, err := presenceBeat(cfg, http.DefaultClient, now); !sent || err != nil || remotePresence.Load() != nil {
		t.Fatalf("null: sent=%v err=%v", sent, err)
	}
}

// 标题刚变：最新事件 0 秒（记录器还没续上），心跳照发最新标题，不报「没有窗口记录」。
func TestPresenceZeroDurationLatest(t *testing.T) {
	now := time.Now()
	aw := fakeAWWeb(t, []awEvent{
		{Timestamp: now.Add(-40 * time.Second), Duration: 30, Data: map[string]any{"app": "ptyxis", "title": "old"}},
		{Timestamp: now.Add(-2 * time.Second), Duration: 0, Data: map[string]any{"app": "ptyxis", "title": "new"}},
	}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	if sent, err := presenceBeat(liveConfig(aw.URL, ck.URL), http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("sent=%v err=%v", sent, err)
	}
	if p := ck.posts("/presence")[0].body; p["title"] != "new" {
		t.Fatalf("presence %v", p)
	}
}

// 失败就丢：返回错误，没有任何排队；拉不到设置时不发（不能退回可能更宽松的本机隐私选项）。
func TestPresenceFailureDropped(t *testing.T) {
	now := time.Now()
	aw := fakeAWWeb(t, []awEvent{{Timestamp: now.Add(-time.Minute), Duration: 55, Data: map[string]any{"app": "code", "title": "x"}}}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)
	ck.respond = func(string) (int, string) { return 503, "" }
	if sent, err := presenceBeat(cfg, http.DefaultClient, now); sent || err == nil {
		t.Fatalf("sent=%v err=%v", sent, err)
	}
	ck.respond = nil
	ck.take()
	if sent, _ := presenceBeat(cfg, http.DefaultClient, now); !sent || len(ck.take()) != 1 {
		t.Fatal("next beat should send exactly one (nothing queued)")
	}
	ck.settings = "not json"
	if sent, err := presenceBeat(cfg, http.DefaultClient, now); sent || err == nil || len(ck.take()) != 0 {
		t.Fatalf("bad settings: sent=%v err=%v", sent, err)
	}
}

// 同步一轮卡住（上传很慢）时，心跳和状态文件桥照常跑。
func TestLiveLoopsIndependentOfSync(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()
	aw := fakeAWWeb(t, []awEvent{{Timestamp: now.Add(-20 * time.Minute), Duration: 20 * 60, Data: map[string]any{"app": "code", "title": "x"}}}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	ck.block, ck.blocked = make(chan struct{}), make(chan struct{}, 1)
	remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)
	cfg.PresenceSeconds = 5
	cfg.AgentStatusFile = filepath.Join(dir, "agents.json")
	p := pathsIn(dir)
	if err := saveJSON(p.config, cfg); err != nil {
		t.Fatal(err)
	}
	saveJSON(p.state, State{Active: true, Cursor: now.Add(-30 * time.Minute)})

	done := make(chan struct{})
	go func() { runLoop(p, http.DefaultClient); close(done) }()
	defer func() {
		close(loopsStop)
		close(ck.block)
		<-done
		log.SetOutput(os.Stderr)
	}()
	select {
	case <-ck.blocked:
	case <-time.After(10 * time.Second):
		t.Fatal("sync never uploaded")
	}
	ck.take()
	writeStatus(t, cfg.AgentStatusFile, time.Now(), `{"key":"codex:1","state":"working"}`)
	deadline := time.Now().Add(9 * time.Second)
	for time.Now().Before(deadline) && (len(ck.posts("/activity/presence")) == 0 || len(ck.posts("/agents/start")) == 0) {
		time.Sleep(100 * time.Millisecond)
	}
	if len(ck.posts("/activity/presence")) == 0 || len(ck.posts("/agents/start")) == 0 {
		t.Fatalf("while sync blocked: %v", ck.take())
	}
}

// ── 状态文件桥 ───────────────────────────────────────────────────────────

func writeStatus(t *testing.T, path string, updated time.Time, agents ...string) {
	t.Helper()
	s := fmt.Sprintf(`{"updated_at":%d.5,"agents":[%s]}`, updated.Unix(), strings.Join(agents, ","))
	if err := os.WriteFile(path, []byte(s), 0o600); err != nil {
		t.Fatal(err)
	}
}

type bridgeEnv struct {
	t     *testing.T
	ck    *liveCockpit
	cfg   Config
	state string
	b     *bridge
}

func newBridgeEnv(t *testing.T) *bridgeEnv {
	dir := t.TempDir()
	ck := newLiveCockpit(t)
	t.Cleanup(ck.Close)
	cfg := liveConfig("http://127.0.0.1:1", ck.URL)
	cfg.AgentStatusFile = filepath.Join(dir, "agents.json")
	return &bridgeEnv{t, ck, cfg, filepath.Join(dir, "state.json"), &bridge{}}
}

func (e *bridgeEnv) poll(agents ...string) []req {
	now := time.Now()
	writeStatus(e.t, e.cfg.AgentStatusFile, now, agents...)
	e.b.poll(e.cfg, http.DefaultClient, now, e.state)
	return e.ck.take()
}

func TestBridgeStaleAndMalformedReportNothing(t *testing.T) {
	e := newBridgeEnv(t)
	e.poll(`{"key":"codex:1","state":"working"}`)
	now := time.Now()
	for name, content := range map[string]string{
		"stale":           fmt.Sprintf(`{"updated_at":%d,"agents":[]}`, now.Add(-31*time.Second).Unix()),
		"array":           `[]`,
		"updated string":  `{"updated_at":"now","agents":[]}`,
		"agents object":   fmt.Sprintf(`{"updated_at":%d,"agents":{}}`, now.Unix()),
		"missing agents":  fmt.Sprintf(`{"updated_at":%d}`, now.Unix()),
		"not json":        `{`,
		"missing updated": `{"agents":[]}`,
	} {
		os.WriteFile(e.cfg.AgentStatusFile, []byte(content), 0o600)
		e.b.poll(e.cfg, http.DefaultClient, now, e.state)
		if r := e.ck.take(); len(r) != 0 {
			t.Fatalf("%s: reported %v", name, r)
		}
	}
	os.Remove(e.cfg.AgentStatusFile)
	e.b.poll(e.cfg, http.DefaultClient, now, e.state)
	if len(e.ck.take()) != 0 || e.b.agents["codex:1"] == nil {
		t.Fatal("missing file must not stop runs")
	}
	// 关 / 暂停：不读、不发。
	off := e.cfg
	off.Paused = true
	writeStatus(t, e.cfg.AgentStatusFile, now)
	e.b.poll(off, &http.Client{Transport: noNet{t}}, now, e.state)
}

func TestBridgeStartMappingAndRedaction(t *testing.T) {
	e := newBridgeEnv(t)
	secret := fakeGitHubToken()
	e.cfg.AgentStatusIgnore = []string{"claude:"}
	r := e.poll(
		`{"key":"codex:7f3a","label":"garden `+secret+`","state":"complete","detail":"SECRET-DETAIL"}`,
		`{"key":"secret_project:1","label":"ab","state":"waiting_permission"}`,
		`{"key":"claude:abc","state":"working"}`,
		`{"key":"hermes:1","state":"thinking"}`,
		`{"state":"working"}`,
		`{"key":"`+strings.Repeat("k", 129)+`","state":"working"}`,
	)
	if len(r) != 2 {
		t.Fatalf("want 2 starts, got %v", r)
	}
	a, b := r[0].body, r[1].body
	if a["agent"] != "codex" || a["tool"] != "codex" || a["phase"] != "idle" || a["clientKey"] != clientKey("dev_test", "codex:7f3a") || len(a["clientKey"].(string)) != 32 {
		t.Fatalf("start a %v", a)
	}
	if a["label"] != "garden [已隐藏:密钥]" || a["match"] != a["label"] {
		t.Fatalf("label %v", a)
	}
	if b["agent"] != "agent" || b["phase"] != "waiting_permission" || b["label"] != "ab" || b["match"] != nil {
		t.Fatalf("start b %v", b)
	}
	all, _ := json.Marshal(r)
	for _, bad := range []string{secret, "SECRET-DETAIL", "7f3a", "secret_project", "detail"} {
		if strings.Contains(string(all), bad) {
			t.Fatalf("leaked %q: %s", bad, all)
		}
	}
	// titles=drop：label 不发。
	e2 := newBridgeEnv(t)
	e2.cfg.Privacy.Titles = "drop"
	if r := e2.poll(`{"key":"codex:1","label":"garden","state":"working"}`); r[0].body["label"] != nil {
		t.Fatalf("drop: %v", r[0].body)
	}
}

func TestBridgePhasesReplyAndStop(t *testing.T) {
	e := newBridgeEnv(t)
	e.poll(`{"key":"codex:1","state":"working"}`, `{"key":"aider:2","state":"idle"}`)
	// 同相位：不发。
	if r := e.poll(`{"key":"codex:1","state":"working"}`, `{"key":"aider:2","state":"complete"}`); len(r) != 0 {
		t.Fatalf("no change but sent %v", r)
	}
	steps := []struct {
		codex, aider string
		want         []string // path 尾 + reply
	}{
		{"waiting_input", "working", []string{"run_1/phase waiting_input false", "run_2/phase working false"}}, // idle → working 不连线
		{"working", "error", []string{"run_1/phase working true", "run_2/phase error false"}},                  // 等人 → 干活 连线
		{"waiting_permission", "working", []string{"run_1/phase waiting_permission false", "run_2/phase working false"}},
		{"working", "error", []string{"run_1/phase working true", "run_2/phase error false"}},
	}
	for i, s := range steps {
		r := e.poll(`{"key":"codex:1","state":"`+s.codex+`","detail":"x"}`, `{"key":"aider:2","state":"`+s.aider+`"}`)
		var got []string
		for _, q := range r {
			if _, ok := q.body["detail"]; ok {
				t.Fatalf("detail sent: %v", q.body)
			}
			if _, err := time.Parse(time.RFC3339Nano, q.body["at"].(string)); err != nil {
				t.Fatalf("at %v", q.body["at"])
			}
			got = append(got, fmt.Sprintf("%s %v %v", q.path[strings.LastIndex(q.path[:strings.LastIndex(q.path, "/")], "/")+1:], q.body["phase"], q.body["reply"] == true))
		}
		sort.Strings(got) // 按 key 排序发，这里只比集合
		if strings.Join(got, "|") != strings.Join(s.want, "|") {
			t.Fatalf("step %d: %v want %v", i, got, s.want)
		}
	}
	// 消失：aider 最后是 error → failed；codex → done。
	r := e.poll()
	if len(r) != 2 || r[0].body["outcome"] != "failed" || !strings.HasSuffix(r[0].path, "run_2/stop") || r[1].body["outcome"] != "done" {
		t.Fatalf("stops %v", r)
	}
	if len(e.b.agents) != 0 {
		t.Fatalf("mappings left %v", e.b.agents)
	}
}

func TestBridgeRetryKeepsAtAndOrder(t *testing.T) {
	e := newBridgeEnv(t)
	e.poll(`{"key":"codex:1","state":"waiting_input"}`, `{"key":"codex:2","state":"working"}`)
	e.ck.respond = func(string) (int, string) { return 503, "" }
	r := e.poll(`{"key":"codex:1","state":"working"}`, `{"key":"codex:2","state":"idle"}`)
	if len(r) != 1 {
		t.Fatalf("after first failure nothing else should be sent this poll: %v", r)
	}
	first := r[0].body
	time.Sleep(10 * time.Millisecond)
	e.poll(`{"key":"codex:1","state":"idle"}`, `{"key":"codex:2","state":"idle"}`) // 仍失败：只试一次
	e.ck.respond = nil
	r = e.poll(`{"key":"codex:1","state":"idle"}`, `{"key":"codex:2","state":"idle"}`)
	if len(r) != 3 {
		t.Fatalf("resend: %v", r)
	}
	if r[0].body["at"] != first["at"] || r[0].body["phase"] != "working" || r[0].body["reply"] != true {
		t.Fatalf("resend changed: %v vs %v", r[0].body, first)
	}
	if r[1].body["phase"] != "idle" || r[1].body["at"] == first["at"] || !strings.HasSuffix(r[2].path, "run_2/phase") {
		t.Fatalf("queue order: %v", r)
	}
	if len(e.b.agents["codex:1"].Pending) != 0 {
		t.Fatal("pending not drained")
	}
}

func TestBridgeClosedDropsMappingAndRestarts(t *testing.T) {
	e := newBridgeEnv(t)
	e.poll(`{"key":"codex:1","state":"working"}`)
	e.ck.respond = func(p string) (int, string) {
		if strings.HasSuffix(p, "/phase") {
			return 200, `{"applied":false,"reason":"closed"}`
		}
		return 0, ""
	}
	if r := e.poll(`{"key":"codex:1","state":"idle"}`); len(r) != 1 || e.b.agents["codex:1"] != nil {
		t.Fatalf("closed: %v %v", r, e.b.agents)
	}
	e.ck.respond = nil
	r := e.poll(`{"key":"codex:1","state":"idle"}`)
	if len(r) != 1 || !strings.HasSuffix(r[0].path, "/agents/start") || e.b.agents["codex:1"].RunID != "run_2" {
		t.Fatalf("restart: %v", r)
	}
	// 404 同样忘掉映射。
	e.ck.respond = func(p string) (int, string) { return 404, "" }
	e.poll(`{"key":"codex:1","state":"working"}`)
	if e.b.agents["codex:1"] != nil {
		t.Fatal("404 kept mapping")
	}
}

func TestBridgeStatePersistsAcrossRestart(t *testing.T) {
	e := newBridgeEnv(t)
	e.poll(`{"key":"codex:1","state":"working"}`)
	e.ck.respond = func(string) (int, string) { return 503, "" }
	e.poll(`{"key":"codex:1","state":"waiting_input"}`)
	e.ck.respond = nil
	// 同步一轮写游标不会盖掉桥的映射。
	if err := updateState(e.state, func(s *State) { ag := s.Agents; *s = State{Cursor: time.Now()}; s.Agents = ag }); err != nil {
		t.Fatal(err)
	}
	e.b = &bridge{} // 重启
	r := e.poll(`{"key":"codex:1","state":"waiting_input"}`)
	if len(r) != 1 || !strings.HasSuffix(r[0].path, "run_1/phase") || r[0].body["phase"] != "waiting_input" {
		t.Fatalf("after restart: %v", r)
	}
	var st State
	loadJSON(e.state, &st)
	if st.Cursor.IsZero() || st.Agents["codex:1"].RunID != "run_1" || len(st.Agents["codex:1"].Pending) != 0 {
		t.Fatalf("state %+v", st)
	}
}
