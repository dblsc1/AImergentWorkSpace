package main

// v0.5：红绿灯（ai-detector.agent-lights.v1）。本机直接看 Claude Code / Codex / Hermes 的状态，
// 写显示文件，并把 agentLightsReport 里的几类交给状态文件桥同一套上报。

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
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

const codexTail = 64 << 10

// codexSession 找最近两天的目录里改动最晚的 rollout-*.jsonl（只按修改时间，不读内容）。
func codexSession(home string, now time.Time) (string, time.Time) {
	var best string
	var bestT time.Time
	for _, d := range []time.Time{now, now.AddDate(0, 0, -1)} {
		fs, _ := filepath.Glob(filepath.Join(home, ".codex", "sessions", d.Format("2006"), d.Format("01"), d.Format("02"), "rollout-*.jsonl"))
		for _, f := range fs {
			if fi, err := os.Stat(f); err == nil && fi.ModTime().After(bestT) {
				best, bestT = f, fi.ModTime()
			}
		}
	}
	return best, bestT
}

// discoverCodex 读最近一份会话记录的尾部，只看事件的 type / payload.type，不读内容。
func discoverCodex(home string, now time.Time) []lightAgent {
	path, mt := codexSession(home, now)
	if path == "" || now.Sub(mt) > lightsCodexRecent {
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	type ev struct {
		Type    string `json:"type"`
		Payload struct {
			Type string `json:"type"`
			Cwd  string `json:"cwd"`
		} `json:"payload"`
	}
	// 第一行 session_meta 带 cwd：只取 cwd，最多读 1 MB。
	var cwd string
	head := make([]byte, 1<<20)
	n, _ := f.Read(head)
	if line, _, _ := strings.Cut(string(head[:n]), "\n"); line != "" {
		var e ev
		if json.Unmarshal([]byte(line), &e) == nil && e.Type == "session_meta" {
			cwd = e.Payload.Cwd
		}
	}
	fi, err := f.Stat()
	if err != nil {
		return nil
	}
	off := max(0, fi.Size()-codexTail)
	buf := make([]byte, fi.Size()-off)
	m, _ := f.ReadAt(buf, off)
	lines := strings.Split(string(buf[:m]), "\n")
	if off > 0 && len(lines) > 0 {
		lines = lines[1:] // 开头可能是半行
	}
	started, completed, approval, lastMsg := -1, -1, -1, -1
	for i, l := range lines {
		var e ev
		if json.Unmarshal([]byte(l), &e) != nil {
			continue
		}
		pt := e.Payload.Type
		if strings.HasSuffix(pt, "_approval_request") || pt == "request_user_input" {
			approval = i
		}
		if e.Type != "event_msg" {
			continue
		}
		lastMsg = i
		switch pt {
		case "task_started":
			started = i
		case "task_complete":
			completed = i
		}
	}
	secs := int(max(0, now.Sub(mt)) / time.Second)
	state, detail := "complete", fmt.Sprintf("updated %ds ago", secs)
	switch {
	case approval >= 0 && approval >= lastMsg:
		state, detail = "waiting_permission", "approval request"
	case started > completed:
		state, detail = "working", "task started"
	case now.Sub(mt) <= lightsWorkingRecent:
		state, detail = "working", fmt.Sprintf("updated %ds ago", secs)
	}
	stem := strings.TrimSuffix(filepath.Base(path), ".jsonl")
	id := strings.TrimPrefix(stem, "rollout-")
	if len(id) > 20 {
		id = id[20:] // 去掉 YYYY-MM-DDTHH-MM-SS-
	}
	label := "Codex"
	if c := shortCwd(cwd); c != "" {
		label += " " + c
	} else {
		label += " " + truncRunes(id, 8)
	}
	return []lightAgent{{"codex:" + id, label, state, detail}}
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
