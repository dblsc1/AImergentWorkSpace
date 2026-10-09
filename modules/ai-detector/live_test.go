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
			if p["app"] != seg["app"] || p["title"] != seg["title"] || p["afk"] != false || p["deviceId"] != "dev_test" || len(p) != 6 {
				t.Fatalf("presence %v\nupload app=%v title=%v", p, seg["app"], seg["title"])
			}
			// v1.2：段里的 app / title 是同一条脱敏路径出来的同一份（最后一段 = 当前窗口 = 顶层）
			spans := p["spans"].([]any)
			if last := spans[len(spans)-1].(map[string]any); len(spans) != 1 || last["app"] != seg["app"] || last["title"] != seg["title"] {
				t.Fatalf("spans %v", spans)
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

// presence.v1 v1.1：网页设置 autoTrack 开着时，心跳带的 guess 与同一个窗口上传出去的建议是同一个目标、同一个把握
// （同一个 matchRules，匹配换代号之前的标题）；关着、没设过、没命中、离开时没有这个键（请求体与 v1.0 相同）。
func TestPresenceGuessParityWithUpload(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	rules := filepath.Join(t.TempDir(), "rules.json")
	os.WriteFile(rules, []byte(`{"rules":[{"title":"garden","taskId":"t_a1"},{"app":"firefox","projectId":"p_2","confidence":0.7}]}`), 0o600)
	settings := func(extra string) string {
		return `{"settings":{"privacy":{` + extra + `},"idle":{},"presence":true,"autoTrack":true}}`
	}
	cases := []struct {
		name, app, title, settings string
		key, id                    string // 期望 guess 的目标键与 id；key 为空 = 不带 guess
		conf                       float64
	}{
		{"task rule", "code", "plot.gd — garden — Code", settings(""), "taskId", "t_a1", 0.9},
		{"project rule", "firefox", "Docs - Mozilla Firefox", settings(""), "projectId", "p_2", 0.7},
		{"matches the title before pseudonymization", "code", "plot.gd — garden — Code", settings(`"titles":"pseudonymize"`), "taskId", "t_a1", 0.9},
		{"no rule", "vim", "notes", settings(""), "", "", 0},
		{"switch off", "code", "plot.gd — garden — Code", `{"settings":{"privacy":{},"idle":{},"presence":true,"autoTrack":false}}`, "", "", 0},
		{"old settings doc without the key", "code", "plot.gd — garden — Code", `{"settings":{"privacy":{},"idle":{}}}`, "", "", 0},
		{"never set on the web", "code", "plot.gd — garden — Code", "", "", "", 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ev := awEvent{Timestamp: now.Add(-5 * time.Minute), Duration: 290, Data: map[string]any{"app": tc.app, "title": tc.title}}
			aw := fakeAWWeb(t, []awEvent{ev}, nil, nil)
			defer aw.Close()
			ck := newLiveCockpit(t)
			defer ck.Close()
			defer remotePresence.Store(nil)
			ck.settings = tc.settings
			cfg := liveConfig(aw.URL, ck.URL)
			cfg.RulesFile, cfg.dir = rules, t.TempDir()

			if _, err := tick(cfg, &State{Active: true, Cursor: now.Add(-10 * time.Minute)}, now, http.DefaultClient); err != nil {
				t.Fatal(err)
			}
			if sent, err := presenceBeat(cfg, http.DefaultClient, now); err != nil || !sent {
				t.Fatalf("beat sent=%v err=%v", sent, err)
			}
			sug := ck.posts("/activity/suggestions")[0].body["segments"].([]any)[0].(map[string]any)["suggestion"].(map[string]any)
			p := ck.posts("/activity/presence")[0].body
			g, has := p["guess"].(map[string]any)
			if tc.key == "" {
				if _, onSpan := p["spans"].([]any)[0].(map[string]any)["guess"]; has || onSpan || len(p) != 6 {
					t.Fatalf("unexpected guess: %v", p)
				}
				return
			}
			if !has || len(g) != 3 || g[tc.key] != tc.id || g["confidence"] != tc.conf || g["classifier"] != "rules" {
				t.Fatalf("guess %v", p["guess"])
			}
			if sug[tc.key] != g[tc.key] || sug["confidence"] != g["confidence"] || sug["classifier"] != g["classifier"] {
				t.Fatalf("guess %v != upload suggestion %v", g, sug)
			}
			if sg, _ := p["spans"].([]any)[0].(map[string]any)["guess"].(map[string]any); sg[tc.key] != tc.id || sg["confidence"] != tc.conf {
				t.Fatalf("span guess %v != %v", sg, g)
			}
			if tc.key == "projectId" && sug["taskId"] != nil {
				t.Fatalf("project-only rule uploaded a task: %v", sug)
			}
			if tc.settings != settings("") && p["title"] == tc.title {
				t.Fatalf("title not pseudonymized: %v", p)
			}
		})
	}

	// 离开：不带 guess（app / title 都是空的，没有窗口可猜）。
	ev := awEvent{Timestamp: now.Add(-time.Minute), Duration: 55, Data: map[string]any{"app": "code", "title": "garden"}}
	aw := fakeAWWeb(t, []awEvent{ev}, []awEvent{{Timestamp: now.Add(-30 * time.Second), Duration: 25, Data: map[string]any{"status": "afk"}}}, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	defer remotePresence.Store(nil)
	ck.settings = settings("")
	cfg := liveConfig(aw.URL, ck.URL)
	cfg.RulesFile = rules
	if sent, err := presenceBeat(cfg, http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("afk beat sent=%v err=%v", sent, err)
	}
	if p := ck.posts("/activity/presence")[0].body; p["afk"] != true || p["guess"] != nil || p["app"] != "" {
		t.Fatalf("afk body %v", p)
	}
}

// 心跳用的规则每 60 秒才重拉一次；拉不到 / 本机规则写坏 = 这次没有规则（不带 guess），心跳照发。
func TestRulesForBeatCachesAndSurvivesBrokenRules(t *testing.T) {
	var hits int
	body := `{"version":1,"rules":[{"id":"a","app":"code","title":null,"taskId":"t_1","confidence":0.9,"enabled":true}]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		io.WriteString(w, body)
	}))
	defer srv.Close()
	now := time.Now()
	cfg := Config{DeviceToken: "tok"}
	for i, at := range []time.Duration{0, 30 * time.Second, 59 * time.Second, 60 * time.Second} {
		if rs := rulesForBeat(cfg, srv.Client(), srv.URL, now.Add(at)); len(rs) != 1 {
			t.Fatalf("#%d rules=%v", i, rs)
		}
	}
	if hits != 2 {
		t.Fatalf("fetched %d times, want 2", hits)
	}
	body = `{"version":0,"rules":[]}` // 服务端没存过 → 本机 rules.json，而它写坏了
	cfg.RulesFile = filepath.Join(t.TempDir(), "rules.json")
	os.WriteFile(cfg.RulesFile, []byte(`{broken`), 0o600)
	if rs := rulesForBeat(cfg, srv.Client(), srv.URL, now); rs != nil {
		t.Fatalf("broken rules must mean no rules, got %v", rs)
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
	// 边界：0 秒事件正好开始在 now。
	aw2 := fakeAWWeb(t, []awEvent{{Timestamp: now, Duration: 0, Data: map[string]any{"app": "ptyxis", "title": "edge"}}}, nil, nil)
	defer aw2.Close()
	ck.take()
	if sent, err := presenceBeat(liveConfig(aw2.URL, ck.URL), http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("edge: sent=%v err=%v", sent, err)
	}
	if p := ck.posts("/presence")[0].body; p["title"] != "edge" {
		t.Fatalf("edge presence %v", p)
	}
}

// ── presence.v1 v1.2：每拍带上一拍以来各窗口的停留 ─────────────────────────

func winEv(at time.Time, sec float64, app, title string) awEvent {
	return awEvent{Timestamp: at, Duration: sec, Data: map[string]any{"app": app, "title": title}}
}

type gotSpan struct {
	app, title string
	from       time.Time
	seconds    float64
}

func bodySpans(t *testing.T, body map[string]any) []gotSpan {
	t.Helper()
	raw, _ := body["spans"].([]any)
	out := make([]gotSpan, len(raw))
	for i, x := range raw {
		m := x.(map[string]any)
		from, err := time.Parse(time.RFC3339Nano, m["from"].(string))
		if err != nil {
			t.Fatal(err)
		}
		out[i] = gotSpan{m["app"].(string), m["title"].(string), from, m["seconds"].(float64)}
	}
	return out
}

func TestPresenceDefaultIsFiveSeconds(t *testing.T) {
	c, _ := defaultConfig(t.TempDir())
	for _, s := range []float64{c.PresenceSeconds, 0, 4, 301} {
		c.PresenceSeconds = s
		if got := presenceInterval(c); got != 5*time.Second {
			t.Fatalf("presenceSeconds=%v → %v", s, got)
		}
	}
	c.PresenceSeconds = 30
	if presenceInterval(c) != 30*time.Second {
		t.Fatal("30 秒以内照配置")
	}
	// 一拍只往回带 presenceLookback：间隔比它的一半长，丢一拍（或只是晚了一点）那段停留就没了。
	// 31–300 仍是合法配置，实际按 presenceMaxInterval 发。
	for _, s := range []float64{31, 60, 300} {
		c.PresenceSeconds = s
		if got := presenceInterval(c); got != presenceMaxInterval || 2*got > presenceLookback {
			t.Fatalf("presenceSeconds=%v → %v", s, got)
		}
	}
}

// 每 3 秒切一次窗口（记录器 1 秒一条）：每个窗口一段、按时间排、互不重叠；离开的那几秒不算；
// 0 秒的闪现不成段，被它隔开的同一个窗口并成一段。
func TestPresenceSpansSerialUnderFastSwitching(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	t0 := now.Add(-12 * time.Second)
	var win []awEvent
	for i := 0; i < 12; i++ { // 0–3 A，3–6 B，6–9 A，9–12 B；每秒一条
		title := []string{"A", "B"}[i/3%2]
		win = append(win, winEv(t0.Add(time.Duration(i)*time.Second), 1, "ptyxis", title))
	}
	win = append(win, winEv(t0.Add(10*time.Second), 0, "ptyxis", "blip")) // B 中间闪一下
	afk := []awEvent{{Timestamp: t0.Add(4 * time.Second), Duration: 1, Data: map[string]any{"status": "afk"}},
		{Timestamp: t0.Add(5 * time.Second), Duration: 7, Data: map[string]any{"status": "not-afk"}}}
	aw := fakeAWWeb(t, win, afk, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)
	cfg.SegmentByTitleApps = []string{"ptyxis"}
	b := &beater{covered: t0}
	if sent, err := b.beat(cfg, http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("sent=%v err=%v", sent, err)
	}
	body := ck.posts("/presence")[0].body
	got := bodySpans(t, body)
	want := []struct {
		title    string
		at, secs float64
	}{{"A", 0, 3}, {"B", 3, 1}, {"B", 5, 1}, {"A", 6, 3}, {"B", 9, 3}}
	if len(got) != len(want) {
		t.Fatalf("spans %v", got)
	}
	for i, w := range want {
		g := got[i]
		if g.app != "ptyxis" || g.title != w.title || !g.from.Equal(t0.Add(time.Duration(w.at*float64(time.Second)))) || g.seconds != w.secs {
			t.Fatalf("span %d = %+v, want %+v", i, g, w)
		}
		if i > 0 && g.from.Before(got[i-1].from.Add(time.Duration(got[i-1].seconds*float64(time.Second)))) {
			t.Fatalf("span %d overlaps the previous one: %v", i, got)
		}
	}
	if body["title"] != "B" || body["truncated"] != nil || body["sentAt"] != now.UTC().Format(time.RFC3339Nano) {
		t.Fatalf("top level %v", body)
	}
	if !b.covered.Equal(now) {
		t.Fatalf("covered %v", b.covered)
	}
}

// 一拍至多 12 段：超了留当前窗口 + 其余里最长的，仍按时间排，并标 truncated。
func TestPresenceSpansCap(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	t0 := now.Add(-40 * time.Second)
	var win []awEvent
	for i := 0; i < 20; i++ { // 20 个窗口各 2 秒；偶数号的前一秒让给一个 1 秒的短窗口 → 偶数号只剩 1 秒
		at := t0.Add(time.Duration(2*i) * time.Second)
		if i%2 == 0 {
			win = append(win, winEv(at, 1, "code", fmt.Sprintf("short-%d", i)), winEv(at.Add(time.Second), 1, "code", fmt.Sprintf("w%d", i)))
		} else {
			win = append(win, winEv(at, 2, "code", fmt.Sprintf("w%d", i)))
		}
	}
	aw := fakeAWWeb(t, win, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	if sent, err := presenceBeat(liveConfig(aw.URL, ck.URL), http.DefaultClient, now); !sent || err != nil {
		t.Fatalf("sent=%v err=%v", sent, err)
	}
	body := ck.posts("/presence")[0].body
	got := bodySpans(t, body)
	if len(got) != presenceMaxSpans || body["truncated"] != true {
		t.Fatalf("%d spans truncated=%v", len(got), body["truncated"])
	}
	if last := got[len(got)-1]; last.title != "w19" || body["title"] != "w19" {
		t.Fatalf("current window must survive: %+v", last)
	}
	long := 0
	for i, g := range got {
		if g.seconds == 2 {
			long++
		}
		if i > 0 && !g.from.After(got[i-1].from) {
			t.Fatalf("not in time order: %v", got)
		}
	}
	if long != 10 { // 10 个 2 秒的全留下
		t.Fatalf("kept %d of the 10 longest: %v", long, got)
	}
}

// 一拍没发出去：不排队、不重发；下一拍从上一次成功的地方接着带（所以时间线上没有洞），最多往回 presenceLookback。
func TestPresenceLookbackAfterFailedBeat(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	t0 := now.Add(-10 * time.Minute)
	aw := fakeAWWeb(t, []awEvent{winEv(t0, 3600, "code", "x")}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)
	b := &beater{}
	beat := func(at time.Time) (bool, []gotSpan) {
		ck.take()
		sent, _ := b.beat(cfg, http.DefaultClient, at)
		if !sent {
			return false, nil
		}
		return true, bodySpans(t, ck.posts("/presence")[0].body)
	}
	if ok, sp := beat(now); !ok || len(sp) != 1 || sp[0].seconds != 60 { // 第一拍：没有上一拍，带最近一分钟
		t.Fatalf("first beat %v", sp)
	}
	ck.respond = func(string) (int, string) { return 503, "" }
	if ok, _ := beat(now.Add(5 * time.Second)); ok {
		t.Fatal("failed beat reported as sent")
	}
	ck.respond = nil
	if ok, sp := beat(now.Add(10 * time.Second)); !ok || len(sp) != 1 || !sp[0].from.Equal(now) || sp[0].seconds != 10 {
		t.Fatalf("beat after a failure should cover from the last success: %v", sp)
	}
	if ok, sp := beat(now.Add(15 * time.Second)); !ok || !sp[0].from.Equal(now.Add(10*time.Second)) || sp[0].seconds != 5 {
		t.Fatalf("steady beat %v", sp)
	}
	if ok, sp := beat(now.Add(5 * time.Minute)); !ok || sp[0].seconds != 60 { // 断了很久：只带 presenceLookback
		t.Fatalf("bounded lookback %v", sp)
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
