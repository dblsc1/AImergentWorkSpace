package main

// v0.5：红绿灯（ai-detector.agent-lights.v1）。本机直接看 Claude Code / Codex / Hermes 的状态，
// 写显示文件，并把 agentLightsReport 里的几类交给状态文件桥同一套上报。

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite" // 纯 Go，无 CGO；只以 mode=ro 打开
)

const (
	lightsPoll          = time.Second
	lightsWorkingRecent = 15 * time.Second
	lightsCodexRecent   = 12 * time.Hour
)

var lightsKinds = []string{"claude", "codex", "hermes"}

// lightAgent 是显示文件里的一条（格式同状态文件桥）。
type lightAgent struct {
	Key    string `json:"key"`
	Label  string `json:"label"`
	State  string `json:"state"`
	Detail string `json:"detail"`
}

// lightsReport：agentLightsReport 的有效值；nil = 缺省 ["hermes"]；未知值报错。
func (c Config) lightsReport() ([]string, error) {
	if c.AgentLightsReport == nil {
		return []string{"hermes"}, nil
	}
	for _, k := range c.AgentLightsReport {
		ok := false
		for _, w := range lightsKinds {
			ok = ok || k == w
		}
		if !ok {
			return nil, fmt.Errorf("agentLightsReport 只能是 %s，现在有 %q", strings.Join(lightsKinds, " / "), k)
		}
	}
	return c.AgentLightsReport, nil
}

// lightsIgnore：红绿灯开着时，状态文件桥要跳过的 key 前缀。
func lightsIgnore(cfg Config) []string {
	rep, err := cfg.lightsReport()
	if err != nil || !on(cfg.AgentLights) {
		return nil
	}
	var out []string
	for _, k := range rep {
		out = append(out, k+":")
	}
	return out
}

func shortCwd(cwd string) string {
	if cwd == "" {
		return ""
	}
	if n := filepath.Base(cwd); n != "." && n != string(filepath.Separator) {
		return n
	}
	return cwd
}

func jsonInt(v any) int64 {
	switch x := v.(type) {
	case float64:
		return int64(x)
	case string:
		n, _ := strconv.ParseInt(strings.TrimSpace(x), 10, 64)
		return n
	}
	return 0
}

func jsonStr(v any) string {
	s, _ := v.(string)
	return s
}

func readObj(path string) map[string]any {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return nil
	}
	return m
}

func discoverClaude(home string) []lightAgent {
	files, _ := filepath.Glob(filepath.Join(home, ".claude", "sessions", "*.json"))
	sort.Strings(files)
	var out []lightAgent
	for _, f := range files {
		d := readObj(f)
		if d == nil {
			continue
		}
		stem := strings.TrimSuffix(filepath.Base(f), ".json")
		pid := jsonInt(d["pid"])
		raw, waiting := strings.ToLower(jsonStr(d["status"])), jsonStr(d["waitingFor"])
		state, detail := "error", waiting
		if detail == "" {
			detail = raw
		}
		switch {
		case pid > 0 && !pidAlive(int(pid)):
			detail = "process missing"
		case strings.Contains(strings.ToLower(waiting), "permission"):
			state = "waiting_permission"
		case raw == "busy" || raw == "running" || raw == "active":
			state = "working"
		case raw == "waiting" || raw == "idle" || raw == "done" || raw == "complete" || raw == "completed":
			state = "complete"
		}
		name := shortCwd(jsonStr(d["cwd"]))
		if name == "" {
			name = stem
			if pid > 0 {
				name = strconv.FormatInt(pid, 10)
			}
		}
		out = append(out, lightAgent{"claude:" + stem, "Claude " + name, state, detail})
	}
	return out
}

// sqliteRows 以只读方式打开库跑一条查询；任何失败都当「没有」。
func sqliteRows(path, query string, args []any, scan func(*sql.Rows) error) {
	if _, err := os.Stat(path); err != nil {
		return
	}
	p := filepath.ToSlash(path)
	if !strings.HasPrefix(p, "/") {
		p = "/" + p // Windows 盘符路径
	}
	db, err := sql.Open("sqlite", "file:"+(&url.URL{Path: p}).EscapedPath()+"?mode=ro&_pragma=busy_timeout(200)")
	if err != nil {
		return
	}
	defer db.Close()
	rows, err := db.Query(query, args...)
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		if scan(rows) != nil {
			return
		}
	}
}

var (
	codexApproval   = []string{`"sandbox_permissions":"require_escalated"`, `'sandbox_permissions': 'require_escalated'`, "require_escalated", "request_user_input"}
	codexCompletion = []string{"function_call_output", "response.function_call_arguments.done", `"type":"response.completed"`, "response.completed", "tool_result"}
)

func containsAny(s string, subs []string) bool {
	for _, x := range subs {
		if strings.Contains(s, x) {
			return true
		}
	}
	return false
}

func discoverCodex(home string, now time.Time) []lightAgent {
	var id, title, cwd string
	var updated int64
	found := false
	sqliteRows(filepath.Join(home, ".codex", "state_5.sqlite"),
		`select id, title, cwd, updated_at from threads order by coalesce(updated_at_ms, updated_at * 1000) desc limit 1`, nil,
		func(r *sql.Rows) error {
			var t, c sql.NullString
			var u sql.NullInt64
			found = true
			err := r.Scan(&id, &t, &c, &u)
			title, cwd, updated = t.String, c.String, u.Int64
			return err
		})
	if !found || id == "" {
		return nil
	}
	age := now.Sub(time.Unix(updated, 0))
	if age > lightsCodexRecent {
		return nil
	}
	if age < 0 {
		age = 0
	}
	var bodies []string // 最新在前
	sqliteRows(filepath.Join(home, ".codex", "logs_2.sqlite"),
		`select feedback_log_body from logs where thread_id = ? or thread_id is null order by id desc limit 80`, []any{id},
		func(r *sql.Rows) error {
			var b sql.NullString
			err := r.Scan(&b)
			bodies = append(bodies, strings.ToLower(b.String))
			return err
		})
	if len(bodies) > 60 {
		bodies = bodies[:60]
	}
	secs := int(age.Seconds())
	state, detail := "complete", fmt.Sprintf("updated %ds ago", secs)
	newest := -1
	for i, b := range bodies {
		if containsAny(b, codexApproval) {
			newest = i
			break
		}
	}
	recent := strings.Join(bodies[:min(len(bodies), 40)], "\n")
	switch {
	case newest >= 0 && !containsAny(strings.Join(bodies[:newest], "\n"), codexCompletion):
		state, detail = "waiting_permission", fmt.Sprintf("approval/input marker, %ds ago", secs)
	case (strings.Contains(recent, "response.in_progress") || strings.Contains(recent, `"status":"in_progress"`)) && age <= 2*lightsWorkingRecent:
		state, detail = "working", fmt.Sprintf("active logs, %ds ago", secs)
	case age <= lightsWorkingRecent:
		state, detail = "working", fmt.Sprintf("updated %ds ago", secs)
	}
	name := shortCwd(cwd)
	if name == "" {
		name = truncRunes(strings.SplitN(title, "\n", 2)[0], 22)
	}
	if name == "" {
		name = truncRunes(id, 8)
	}
	return []lightAgent{{"codex:" + id, "Codex " + name, state, detail}}
}

func discoverHermes(home string) []lightAgent {
	type prof struct{ name, path string }
	profs := []prof{{"default", filepath.Join(home, ".hermes", "gateway_state.json")}}
	more, _ := filepath.Glob(filepath.Join(home, ".hermes", "profiles", "*", "gateway_state.json"))
	sort.Strings(more)
	for _, p := range more {
		profs = append(profs, prof{filepath.Base(filepath.Dir(p)), p})
	}
	okState := func(s string) bool { s = strings.ToLower(s); return s == "connected" || s == "running" || s == "ready" }
	var out []lightAgent
	for _, p := range profs {
		d := readObj(p.path)
		if d == nil {
			continue
		}
		if pid := jsonInt(d["pid"]); pid > 0 && !pidAlive(int(pid)) {
			continue // 网关不在了：不显示、不上报
		}
		gw := jsonStr(d["gateway_state"])
		if gw == "" {
			gw = jsonStr(d["state"])
		}
		active := jsonInt(d["active_agents"])
		state, detail := "complete", "present"
		switch {
		case active > 0:
			state, detail = "working", fmt.Sprintf("%d active", active)
		case okState(gw):
			plats, _ := d["platforms"].(map[string]any)
			var bad []string
			for n, v := range plats {
				if m, ok := v.(map[string]any); ok && !okState(jsonStr(m["state"])) {
					bad = append(bad, n)
				}
			}
			sort.Strings(bad)
			detail = gw
			if len(bad) > 0 {
				detail = strings.Join(bad, ", ")
				if len(bad) == len(plats) {
					state = "error"
				}
			}
		case gw != "":
			state, detail = "error", gw
		}
		label := "Hermes"
		if p.name != "default" {
			label += " " + p.name
		}
		out = append(out, lightAgent{"hermes:" + p.name, label, state, detail})
	}
	return out
}

func discoverLights(home string, now time.Time) []lightAgent {
	out := []lightAgent{}
	out = append(out, discoverClaude(home)...)
	out = append(out, discoverCodex(home, now)...)
	return append(out, discoverHermes(home)...)
}

// lightsToReport：只留 agentLightsReport 里的类，转成桥用的形状（key 超长的跳过）。
func lightsToReport(all []lightAgent, report []string) []fileAgent {
	var out []fileAgent
	for _, a := range all {
		kind, _, _ := strings.Cut(a.Key, ":")
		for _, k := range report {
			if k == kind && len(a.Key) <= 128 {
				out = append(out, fileAgent{a.Key, a.Label, statePhase[a.State]})
			}
		}
	}
	return out
}

func lightsFilePath(home string) string {
	return filepath.Join(home, ".agent-traffic-light", "agents.json")
}

// lightsTick：一拍。关着什么都不碰；开着写显示文件，再把要上报的交给桥。
func lightsTick(cfg Config, b *bridge, hc *http.Client, home string, now time.Time, statePath string) {
	if !on(cfg.AgentLights) {
		return
	}
	report, err := cfg.lightsReport()
	if err != nil {
		b.q.printf("红绿灯：%v", err)
		return
	}
	all := discoverLights(home, now)
	if err := saveJSON(lightsFilePath(home), map[string]any{"updated_at": now.Unix(), "agents": all}); err != nil {
		b.q.printf("红绿灯：显示文件写不了：%v", err)
	}
	if active(cfg) && len(report) > 0 {
		b.report(cfg, hc, now, statePath, lightsToReport(all, report))
	}
}

func lightsLoop(p paths, hc *http.Client) {
	b := &bridge{light: true}
	every(func() time.Duration {
		if cfg, err := readConfig(p); err == nil {
			if home, err := os.UserHomeDir(); err == nil {
				lightsTick(cfg, b, hc, home, time.Now(), p.state)
			}
		}
		return lightsPoll
	})
}
