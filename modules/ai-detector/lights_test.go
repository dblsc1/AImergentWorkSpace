package main

import (
	"database/sql"
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

func execAll(t *testing.T, path string, stmts ...string) {
	t.Helper()
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, s := range stmts {
		if _, err := db.Exec(s); err != nil {
			t.Fatalf("%s: %v", s, err)
		}
	}
}

func codexFixture(t *testing.T, home string, updated time.Time, bodies ...string) { // bodies 从旧到新
	t.Helper()
	d := filepath.Join(home, ".codex")
	os.MkdirAll(d, 0o700)
	execAll(t, filepath.Join(d, "state_5.sqlite"),
		`create table threads (id text, title text, cwd text, updated_at integer, updated_at_ms integer)`,
		fmt.Sprintf(`insert into threads values ('old-thread','old','/o/older',%d,%d)`, updated.Unix()-100, (updated.Unix()-100)*1000),
		fmt.Sprintf(`insert into threads values ('thread-1234567890','title line\nsecond','/x/garden',%d,%d)`, updated.Unix(), updated.Unix()*1000))
	st := []string{`create table logs (id integer primary key autoincrement, ts integer, target text, thread_id text, feedback_log_body text)`,
		`insert into logs (thread_id, feedback_log_body) values ('other','response.in_progress')`}
	for _, b := range bodies {
		st = append(st, fmt.Sprintf(`insert into logs (thread_id, feedback_log_body) values ('thread-1234567890','%s')`, b))
	}
	execAll(t, filepath.Join(d, "logs_2.sqlite"), st...)
}

func TestLightsCodexStates(t *testing.T) {
	now := time.Now()
	cases := []struct {
		name    string
		age     time.Duration
		bodies  []string
		want    string
		wantNil bool
	}{
		{"recent update, no logs", 5 * time.Second, nil, "working", false},
		{"older update, no logs", 60 * time.Second, nil, "complete", false},
		{"in_progress within 30s", 25 * time.Second, []string{"x", "RESPONSE.IN_PROGRESS"}, "working", false},
		{"in_progress too old", 40 * time.Second, []string{"response.in_progress"}, "complete", false},
		{"approval pending", 60 * time.Second, []string{"call", `{"sandbox_permissions":"require_escalated"}`}, "waiting_permission", false},
		{"approval then completion", 60 * time.Second, []string{`require_escalated`, "function_call_output"}, "complete", false},
		{"completion before approval still waits", 60 * time.Second, []string{"tool_result", "request_user_input", "noise"}, "waiting_permission", false},
		{"older than 12h", 13 * time.Hour, nil, "", true},
	}
	for _, c := range cases {
		home := t.TempDir()
		codexFixture(t, home, now.Add(-c.age), c.bodies...)
		got := discoverCodex(home, now)
		if c.wantNil {
			if len(got) != 0 {
				t.Errorf("%s: want none, got %v", c.name, got)
			}
			continue
		}
		if len(got) != 1 || got[0].State != c.want || got[0].Key != "codex:thread-1234567890" || got[0].Label != "Codex garden" {
			t.Errorf("%s: %+v want %s", c.name, got, c.want)
		}
	}
	if got := discoverCodex(t.TempDir(), now); len(got) != 0 {
		t.Fatalf("no db: %v", got)
	}
	// 库是只读打开的：不会在没有库的地方建出文件。
	home := t.TempDir()
	discoverCodex(home, now)
	if _, err := os.Stat(filepath.Join(home, ".codex")); err == nil {
		t.Fatal("created .codex")
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
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(time.Second), state)
	if r := ck.take(); len(r) != 1 || !strings.HasSuffix(r[0].path, "/phase") || r[0].body["phase"] != "idle" {
		t.Fatalf("phase %v", r)
	}
	os.Remove(filepath.Join(home, ".hermes", "gateway_state.json"))
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(2*time.Second), state)
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
	lightsTick(cfg, b, http.DefaultClient, home, now.Add(3*time.Second), state)
	if r := ck.take(); len(r) != 1 || r[0].body["agent"] != "claude" || r[0].body["label"] != "Claude garden" {
		t.Fatalf("claude report %v", r)
	}
	// 暂停：显示文件照写，不发请求。
	cfg.Paused = true
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(4*time.Second), state)
	// 红绿灯关：不读不写。
	os.Remove(filepath.Join(home, ".agent-traffic-light", "agents.json"))
	f := false
	cfg.AgentLights = &f
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(5*time.Second), state)
	if _, err := os.Stat(filepath.Join(home, ".agent-traffic-light", "agents.json")); err == nil {
		t.Fatal("wrote display file while off")
	}
	// 未知的上报类别：什么都不做。
	cfg.AgentLights, cfg.AgentLightsReport = nil, []string{"nope"}
	lightsTick(cfg, b, &http.Client{Transport: noNet{t}}, home, now.Add(6*time.Second), state)
	if _, err := os.Stat(filepath.Join(home, ".agent-traffic-light", "agents.json")); err == nil {
		t.Fatal("wrote display file with invalid config")
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
