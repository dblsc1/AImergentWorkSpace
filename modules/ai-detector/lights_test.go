package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const deadPid = 0x7ffffff0

func lwrite(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func byKey(as []lightAgent) map[string]lightAgent {
	m := map[string]lightAgent{}
	for _, a := range as {
		m[a.Key] = a
	}
	return m
}

func TestLightsConfigDefaultsAndValidation(t *testing.T) {
	dir := t.TempDir()
	d, err := defaultConfig(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !on(d.AgentLights) || strings.Join(d.AgentLightsReport, ",") != "hermes" {
		t.Fatalf("defaults %v %v", d.AgentLights, d.AgentLightsReport)
	}
	// 老配置文件没有这两个键：开，上报 hermes。
	var old Config
	if err := json.Unmarshal([]byte(`{"enabled":true}`), &old); err != nil {
		t.Fatal(err)
	}
	if rep, err := old.lightsReport(); err != nil || !on(old.AgentLights) || strings.Join(rep, ",") != "hermes" {
		t.Fatalf("old config: %v %v", rep, err)
	}
	if rep, err := (Config{AgentLightsReport: []string{}}).lightsReport(); err != nil || len(rep) != 0 {
		t.Fatalf("empty list means none: %v %v", rep, err)
	}
	if rep, err := (Config{AgentLightsReport: []string{"claude", "codex", "hermes"}}).lightsReport(); err != nil || len(rep) != 3 {
		t.Fatalf("all: %v %v", rep, err)
	}
	if _, err := (Config{AgentLightsReport: []string{"claude", "gemini"}}).lightsReport(); err == nil || !strings.Contains(err.Error(), "gemini") {
		t.Fatalf("unknown value must error: %v", err)
	}
	// 关着：状态文件桥不跳过任何类。
	f := false
	if got := lightsIgnore(Config{AgentLights: &f}); got != nil {
		t.Fatalf("lights off must not skip: %v", got)
	}
	if got := lightsIgnore(Config{AgentLightsReport: []string{"claude", "hermes"}}); strings.Join(got, ",") != "claude:,hermes:" {
		t.Fatalf("ignore %v", got)
	}
}

func TestLightsRemoteSettingsDoNotWipe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"settings":{"privacy":{"titles":"drop"},"idle":{},"presence":true}}`)
	}))
	defer srv.Close()
	f := false
	cfg := Config{DeviceID: "dev_t", AgentLights: &f, AgentLightsReport: []string{"claude"}}
	if err := applyRemoteSettings(&cfg, srv.Client(), srv.URL); err != nil {
		t.Fatal(err)
	}
	if cfg.AgentLights == nil || *cfg.AgentLights || len(cfg.AgentLightsReport) != 1 || cfg.Privacy.Titles != "drop" {
		t.Fatalf("remote settings touched lights: %+v", cfg)
	}
}

func TestLightsClaudeStates(t *testing.T) {
	home := t.TempDir()
	s := filepath.Join(home, ".claude", "sessions")
	me := os.Getpid()
	lwrite(t, filepath.Join(s, "a.json"), fmt.Sprintf(`{"pid":%d,"status":"busy","cwd":"/x/garden"}`, me))
	lwrite(t, filepath.Join(s, "b.json"), fmt.Sprintf(`{"pid":%d,"status":"idle","waitingFor":"tool permission","cwd":"/x/garden"}`, me))
	lwrite(t, filepath.Join(s, "c.json"), fmt.Sprintf(`{"pid":%d,"status":"waiting"}`, me))
	lwrite(t, filepath.Join(s, "d.json"), fmt.Sprintf(`{"pid":%d,"status":"completed"}`, me))
	lwrite(t, filepath.Join(s, "e.json"), fmt.Sprintf(`{"pid":%d,"status":"weird"}`, me))
	lwrite(t, filepath.Join(s, "f.json"), fmt.Sprintf(`{"pid":%d,"status":"busy"}`, deadPid))
	lwrite(t, filepath.Join(s, "g.json"), `{"status":"running"}`)
	lwrite(t, filepath.Join(s, "bad.json"), `{`)
	lwrite(t, filepath.Join(s, "arr.json"), `[]`)
	m := byKey(discoverClaude(home))
	want := map[string]string{
		"claude:a": "working", "claude:b": "waiting_permission", "claude:c": "complete", "claude:d": "complete",
		"claude:e": "error", "claude:f": "error", "claude:g": "working",
	}
	if len(m) != len(want) {
		t.Fatalf("got %v", m)
	}
	for k, st := range want {
		if m[k].State != st {
			t.Errorf("%s: %s want %s", k, m[k].State, st)
		}
	}
	if m["claude:a"].Label != "Claude garden" || m["claude:c"].Label != "Claude "+fmt.Sprint(me) || m["claude:g"].Label != "Claude g" || m["claude:f"].Detail != "process missing" {
		t.Fatalf("labels %+v", m)
	}
	if got := discoverClaude(t.TempDir()); len(got) != 0 {
		t.Fatalf("no dir: %v", got)
	}
}

// codexFixture 写一份会话记录：lines 为事件（type, payload.type），mtime 设成 now-age。
func codexFixture(t *testing.T, home string, now time.Time, age time.Duration, name string, cwd string, lines ...[2]string) string {
	t.Helper()
	d := now.Add(-age)
	p := filepath.Join(home, ".codex", "sessions", d.Format("2006"), d.Format("01"), d.Format("02"), name)
	body := ""
	if cwd != "" {
		body = fmt.Sprintf(`{"type":"session_meta","payload":{"type":"x","cwd":%q,"instructions":"SECRET"}}`, cwd) + "\n"
	}
	for _, l := range lines {
		body += fmt.Sprintf(`{"type":%q,"payload":{"type":%q,"message":"SECRET"}}`, l[0], l[1]) + "\n"
	}
	lwrite(t, p, body)
	if err := os.Chtimes(p, d, d); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLightsCodexStates(t *testing.T) {
	now := time.Now()
	const name = "rollout-2026-10-27T10-00-00-0192abcd-1234.jsonl"
	ev := func(ty string) [2]string { return [2]string{"event_msg", ty} }
	cases := []struct {
		name   string
		age    time.Duration
		lines  [][2]string
		want   string
		absent bool
	}{
		{"started", time.Minute, [][2]string{ev("task_started")}, "working", false},
		{"started then complete", time.Minute, [][2]string{ev("task_started"), ev("task_complete")}, "complete", false},
		{"complete then started", time.Minute, [][2]string{ev("task_complete"), ev("task_started")}, "working", false},
		{"approval pending", time.Minute, [][2]string{ev("task_started"), ev("exec_approval_request")}, "waiting_permission", false},
		{"patch approval", time.Minute, [][2]string{ev("apply_patch_approval_request")}, "waiting_permission", false},
		{"user input", time.Minute, [][2]string{ev("task_started"), ev("request_user_input")}, "waiting_permission", false},
		{"approval then later event", time.Minute, [][2]string{ev("task_started"), ev("exec_approval_request"), ev("exec_command_end")}, "working", false},
		{"recent mtime only", 5 * time.Second, [][2]string{ev("agent_message")}, "working", false},
		{"turn aborted", time.Minute, [][2]string{ev("task_started"), ev("turn_aborted")}, "complete", false},
		{"started but log stale", 4 * time.Minute, [][2]string{ev("task_started")}, "complete", false},
		{"idle", time.Minute, [][2]string{ev("agent_message")}, "complete", false},
		{"older than 12h", 13 * time.Hour, [][2]string{ev("task_started")}, "", true},
	}
	for _, c := range cases {
		home := t.TempDir()
		codexFixture(t, home, now, c.age, name, "/x/garden", c.lines...)
		got := discoverCodex(home, now)
		if c.absent {
			if len(got) != 0 {
				t.Errorf("%s: want none, got %v", c.name, got)
			}
			continue
		}
		if len(got) != 1 || got[0].State != c.want || got[0].Key != "codex:0192abcd-1234" || got[0].Label != "Codex garden" {
			t.Errorf("%s: %+v want %s", c.name, got, c.want)
		}
		if b, _ := json.Marshal(got); strings.Contains(string(b), "SECRET") {
			t.Errorf("%s: content leaked", c.name)
		}
	}
	// 没有 session_meta：标签用会话号前 8 位。
	home := t.TempDir()
	codexFixture(t, home, now, time.Minute, name, "", ev("task_started"))
	if got := discoverCodex(home, now); len(got) != 1 || got[0].Label != "Codex 0192abcd" {
		t.Errorf("no meta: %+v", got)
	}
	// 取最近改过的那份；只尾部大文件也能读（半行被丢）。
	home = t.TempDir()
	codexFixture(t, home, now, time.Hour, "rollout-2026-10-27T08-00-00-old.jsonl", "/o/older", ev("task_started"))
	p := codexFixture(t, home, now, time.Minute, name, "/x/garden")
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0o600)
	f.WriteString(strings.Repeat(`{"type":"response_item","payload":{"type":"x"}}`+"\n", 3000))
	f.WriteString(`{"type":"event_msg","payload":{"type":"task_started"}}` + "\n")
	f.Close()
	os.Chtimes(p, now.Add(-time.Minute), now.Add(-time.Minute))
	if got := discoverCodex(home, now); len(got) != 1 || got[0].Key != "codex:0192abcd-1234" || got[0].State != "working" {
		t.Errorf("newest/tail: %+v", got)
	}
	if got := discoverCodex(t.TempDir(), now); len(got) != 0 {
		t.Fatalf("no sessions: %v", got)
	}
}

func TestLightsHermesProfilesAndDeadPid(t *testing.T) {
	home := t.TempDir()
	h := filepath.Join(home, ".hermes")
	me := os.Getpid()
	lwrite(t, filepath.Join(h, "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","active_agents":2}`, me))
	lwrite(t, filepath.Join(h, "profiles", "idle", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","platforms":{"tg":{"state":"connected"}}}`, me))
	lwrite(t, filepath.Join(h, "profiles", "dead", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","active_agents":1}`, deadPid))
	lwrite(t, filepath.Join(h, "profiles", "badplat", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"state":"running","platforms":{"tg":{"state":"fatal"}}}`, me))
	lwrite(t, filepath.Join(h, "profiles", "partial", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","platforms":{"tg":{"state":"fatal"},"dc":{"state":"connected"}}}`, me))
	lwrite(t, filepath.Join(h, "profiles", "stopped", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"stopped"}`, me))
	lwrite(t, filepath.Join(h, "profiles", "nopid", "gateway_state.json"), `{}`)
	lwrite(t, filepath.Join(h, "profiles", "junk", "gateway_state.json"), `nope`)
	m := byKey(discoverHermes(home))
	want := map[string]string{
		"hermes:default": "working", "hermes:idle": "complete", "hermes:badplat": "error",
		"hermes:partial": "complete", "hermes:stopped": "error", "hermes:nopid": "complete",
	}
	if len(m) != len(want) {
		t.Fatalf("got %v", m)
	}
	for k, st := range want {
		if m[k].State != st {
			t.Errorf("%s: %s want %s", k, m[k].State, st)
		}
	}
	if _, ok := m["hermes:dead"]; ok {
		t.Fatal("dead pid profile must be skipped")
	}
	if m["hermes:default"].Label != "Hermes" || m["hermes:idle"].Label != "Hermes idle" {
		t.Fatalf("labels %+v", m)
	}
}

func TestLightsDisplayFileAndReportSplit(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	me := os.Getpid()
	lwrite(t, filepath.Join(home, ".claude", "sessions", "s1.json"), fmt.Sprintf(`{"pid":%d,"status":"busy","cwd":"/x/garden"}`, me))
	lwrite(t, filepath.Join(home, ".hermes", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","active_agents":1}`, me))
	lwrite(t, filepath.Join(home, ".hermes", "profiles", "dead", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running"}`, deadPid))

	ck := newLiveCockpit(t)
	defer ck.Close()
	cfg := liveConfig("http://127.0.0.1:1", ck.URL)
	state := filepath.Join(t.TempDir(), "state.json")
	b := &bridge{light: true}
	now := time.Now()
	lightsTick(cfg, b, http.DefaultClient, home, now, state)

	// 显示文件：全部显示（含只显示不上报的 claude），死网关不在。
	var doc struct {
		UpdatedAt int64        `json:"updated_at"`
		Agents    []lightAgent `json:"agents"`
	}
	raw, err := os.ReadFile(filepath.Join(home, ".agent-traffic-light", "agents.json"))
	if err != nil || json.Unmarshal(raw, &doc) != nil {
		t.Fatalf("display file: %v %s", err, raw)
	}
	m := byKey(doc.Agents)
	if doc.UpdatedAt != now.Unix() || len(m) != 2 || m["claude:s1"].State != "working" || m["hermes:default"].State != "working" || m["hermes:default"].Label != "Hermes" {
		t.Fatalf("display %s", raw)
	}
	if !strings.Contains(string(raw), `"detail"`) {
		t.Fatalf("detail key missing: %s", raw)
	}
	if ents, _ := filepath.Glob(filepath.Join(home, ".agent-traffic-light", "*.tmp")); len(ents) != 0 {
		t.Fatalf("temp file left: %v", ents)
	}
	// 上报：只有 hermes。
	r := ck.take()
	if len(r) != 1 || !strings.HasSuffix(r[0].path, "/agents/start") || r[0].body["agent"] != "hermes" || r[0].body["phase"] != "working" ||
		r[0].body["clientKey"] != clientKey(cfg.DeviceID, "hermes:default") || r[0].body["label"] != "Hermes" {
		t.Fatalf("reports %v", r)
	}
	if b2, _ := json.Marshal(r); strings.Contains(string(b2), "garden") || strings.Contains(string(b2), "active") {
		t.Fatalf("leak: %s", b2)
	}
	// 相位变了才再发；网关消失 → stop。
	lwrite(t, filepath.Join(home, ".hermes", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running"}`, me))
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(4*time.Second), state)
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/phase") || r[0].body["phase"] != "idle" {
		t.Fatalf("phase %v", r)
	}
	os.Remove(filepath.Join(home, ".hermes", "gateway_state.json"))
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(8*time.Second), state)
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/stop") || r[0].body["outcome"] != "done" {
		t.Fatalf("stop %v", r)
	}
	var st State
	loadJSON(state, &st)
	if len(st.Agents) != 0 {
		t.Fatalf("lights must not touch bridge mappings: %+v", st.Agents)
	}

	// claude 也列进上报：开运行。
	cfg.AgentLightsReport = []string{"claude"}
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(12*time.Second), state)
	if r := ck.take(); len(r) != 1 || r[0].body["agent"] != "claude" || r[0].body["label"] != "Claude garden" {
		t.Fatalf("claude report %v", r)
	}
	// 暂停：显示文件照写，不发请求。
	cfg.Paused = true
	os.Remove(filepath.Join(home, ".agent-traffic-light", "agents.json"))
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(16*time.Second), state)
	if _, err := os.Stat(filepath.Join(home, ".agent-traffic-light", "agents.json")); err != nil {
		t.Fatal("paused: display file must still be written")
	}
	cfg.Paused = false
	// 红绿灯关：不读不写显示文件，已开的运行收掉。
	os.Remove(filepath.Join(home, ".agent-traffic-light", "agents.json"))
	f := false
	cfg.AgentLights = &f
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(20*time.Second), state)
	if _, err := os.Stat(filepath.Join(home, ".agent-traffic-light", "agents.json")); err == nil {
		t.Fatal("wrote display file while off")
	}
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/stop") {
		t.Fatalf("turning lights off must stop open runs: %v", r)
	}
	// 未知的上报类别：什么都不做。
	cfg.AgentLights, cfg.AgentLightsReport = nil, []string{"nope"}
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(24*time.Second), state)
	if _, err := os.Stat(filepath.Join(home, ".agent-traffic-light", "agents.json")); err == nil {
		t.Fatal("wrote display file with invalid config")
	}
}

func TestLightsReportListShrinkStopsRuns(t *testing.T) {
	home := t.TempDir()
	lwrite(t, filepath.Join(home, ".claude", "sessions", "s1.json"), fmt.Sprintf(`{"pid":%d,"status":"busy"}`, os.Getpid()))
	ck := newLiveCockpit(t)
	defer ck.Close()
	cfg := liveConfig("http://127.0.0.1:1", ck.URL)
	cfg.AgentLightsReport = []string{"claude"}
	state := filepath.Join(t.TempDir(), "state.json")
	b := &bridge{light: true}
	now := time.Now()
	lightsTick(cfg, b, http.DefaultClient, home, now, state)
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/start") {
		t.Fatalf("start %v", r)
	}
	// 3 秒内不再上报。
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(time.Second), state)
	cfg.AgentLightsReport = []string{} // 明确不上报任何类
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(4*time.Second), state)
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/stop") {
		t.Fatalf("shrink must stop: %v", r)
	}
	var st State
	loadJSON(state, &st)
	if len(st.LightAgents) != 0 {
		t.Fatalf("mapping left %v", st.LightAgents)
	}
}

func TestLightsBackoffAfterNetworkFailure(t *testing.T) {
	home := t.TempDir()
	lwrite(t, filepath.Join(home, ".hermes", "gateway_state.json"), fmt.Sprintf(`{"pid":%d,"gateway_state":"running","active_agents":1}`, os.Getpid()))
	ck := newLiveCockpit(t)
	defer ck.Close()
	ck.respond = func(string) (int, string) { return 503, "" }
	cfg := liveConfig("http://127.0.0.1:1", ck.URL)
	state := filepath.Join(t.TempDir(), "state.json")
	b := &bridge{light: true}
	now := time.Now()
	lightsTick(cfg, b, http.DefaultClient, home, now, state)
	ck.take()
	ck.respond = nil
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(5*time.Second), state)
	if r := ck.take(); len(r) != 0 {
		t.Fatalf("inside backoff: %v", r)
	}
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(16*time.Second), state)
	if r := ck.take(); len(r) != 1 {
		t.Fatalf("after backoff: %v", r)
	}
}

func TestLightsConfigRoundTripKeepsEmptyReport(t *testing.T) {
	p := filepath.Join(t.TempDir(), "c.json")
	if err := saveJSON(p, Config{AgentLightsReport: []string{}}); err != nil {
		t.Fatal(err)
	}
	var c Config
	if err := loadJSON(p, &c); err != nil {
		t.Fatal(err)
	}
	if rep, err := c.lightsReport(); err != nil || c.AgentLightsReport == nil || len(rep) != 0 {
		t.Fatalf("[] lost: %v %v", c.AgentLightsReport, err)
	}
}

func TestLightsBridgeSkipsReportedKinds(t *testing.T) {
	e := newBridgeEnv(t) // 红绿灯缺省开、上报 hermes
	r := e.poll(`{"key":"hermes:default","state":"working"}`, `{"key":"claude:1","state":"working"}`)
	if len(r) != 1 || r[0].body["agent"] != "claude" {
		t.Fatalf("bridge must skip hermes: %v", r)
	}
	e2 := newBridgeEnv(t)
	f := false
	e2.cfg.AgentLights = &f
	if r := e2.poll(`{"key":"hermes:default","state":"working"}`); len(r) != 1 {
		t.Fatalf("lights off: bridge keeps hermes: %v", r)
	}
	e3 := newBridgeEnv(t)
	e3.cfg.AgentLightsReport = []string{"claude", "codex"}
	if r := e3.poll(`{"key":"hermes:default","state":"working"}`, `{"key":"claude:1","state":"working"}`, `{"key":"codex:1","state":"working"}`); len(r) != 1 || r[0].body["agent"] != "hermes" {
		t.Fatalf("custom report: %v", r)
	}
}
