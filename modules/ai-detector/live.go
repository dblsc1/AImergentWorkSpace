package main

// v0.3：在场心跳（ai-detector.presence.v1）与状态文件桥（ai-detector.agent-status-bridge.v1）。
// 两者都只在 run 常驻模式里、各自一个 goroutine 跑，不拖慢 5 分钟一轮的同步；契约见 module_docs/contract.md。

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

// ── 离开本机的标题：上传、在场心跳、代理 label 共用 ─────────────────────────

var psMu sync.Mutex // 同步一轮与心跳 / 桥可能同时给新标题分配代号：对照表的读—改—存串起来

// sendTitles 把「隐私选项处理后」的标题变成真正离开本机的样子：titles=pseudonymize 时换代号
// （对照表先存盘），最后再过一遍强制脱敏（幂等）。
func sendTitles(cfg Config, titles []string) ([]string, error) {
	var ps *pseudonyms
	if cfg.Privacy.Titles == "pseudonymize" {
		psMu.Lock()
		defer psMu.Unlock()
		pp := ""
		if cfg.dir != "" {
			pp = filepath.Join(cfg.dir, "pseudonyms.json")
		}
		var err error
		if ps, err = loadPseudonyms(pp); err != nil {
			return nil, err
		}
	}
	out := make([]string, len(titles))
	for i, t := range titles {
		if ps != nil {
			t = ps.token(t)
		}
		out[i] = scrubSecrets(t)
	}
	if ps != nil {
		if err := ps.save(); err != nil {
			return nil, fmt.Errorf("标题代号对照表存不了，这一轮不上传：%w", err)
		}
	}
	return out, nil
}

// remotePresence：网页设置里的 presence（没设 / 设置为 null / 老 nexus-core = nil）。applyRemoteSettings 更新。
// 心跳先按它（没有就按本机配置）决定要不要发；要发之前再拉一次设置，保证用的是此刻的隐私选项。
var remotePresence atomic.Pointer[bool]

// quietLog：常驻循环几秒一次，同样的失败只记一次，恢复后再出错再记。
type quietLog struct{ last string }

func (q *quietLog) printf(format string, a ...any) {
	if s := fmt.Sprintf(format, a...); s != q.last {
		log.Print(s)
		q.last = s
	}
}

func active(cfg Config) bool {
	return cfg.Enabled && !cfg.Paused && cfg.CockpitURL != "" && cfg.DeviceToken != "" && cfg.DeviceID != ""
}

var loopsStop = make(chan struct{}) // 只给测试用：关掉它，run 的几个循环退出

// every 先跑一次 f，再睡 f 返回的间隔，循环。run 里同步、心跳、桥各一个，互不等待。
func every(f func() time.Duration) {
	for {
		select {
		case <-loopsStop:
			return
		case <-time.After(f()):
		}
	}
}

// ── 在场心跳 ─────────────────────────────────────────────────────────────

func presenceInterval(cfg Config) time.Duration {
	s := cfg.PresenceSeconds
	if s < 5 || s > 300 {
		s = 15
	}
	return time.Duration(s * float64(time.Second))
}

type presenceBody struct {
	DeviceID string `json:"deviceId"`
	App      string `json:"app"`
	Title    string `json:"title"`
	Afk      bool   `json:"afk"`
	// presence.v1 v1.1：网页设置 autoTrack 开着、规则命中当前窗口时才带。
	Guess *presenceGuess `json:"guess,omitempty"`
}

// presenceGuess：规则对当前窗口的猜测。TaskID / ProjectID 恰好一个。
type presenceGuess struct {
	TaskID     *string `json:"taskId,omitempty"`
	ProjectID  *string `json:"projectId,omitempty"`
	Confidence float64 `json:"confidence"`
	Classifier string  `json:"classifier"`
}

// presenceBeat 发一次心跳。返回 (是否发了, 错误)。失败就丢：不重试、不排队。
func presenceBeat(cfg Config, hc *http.Client, now time.Time) (bool, error) {
	want := cfg.Presence
	if p := remotePresence.Load(); p != nil {
		want = *p
	}
	if !active(cfg) || !want {
		return false, nil // 关 / 暂停 / 没开心跳：零请求
	}
	base := strings.TrimRight(cfg.CockpitURL, "/")
	cockpit := withPolicy(hc, hc.Timeout, sameHostOnly)
	if err := applyRemoteSettings(&cfg, cockpit, base); err != nil {
		return false, err
	}
	if !cfg.Presence {
		return false, nil // 网页上刚关掉
	}
	if err := cfg.Privacy.check(); err != nil {
		return false, err
	}
	from := now.Add(-time.Minute)
	d, err := awClient{base: cfg.ActivityWatchURL, http: hc}.fetch(from, now, cfg.WindowBucket, cfg.AfkBucket)
	if err != nil {
		return false, err
	}
	body := presenceBody{DeviceID: cfg.DeviceID}
	var lastAfk *awEvent
	for i := range d.afk {
		if lastAfk == nil || d.afk[i].Timestamp.After(lastAfk.Timestamp) {
			lastAfk = &d.afk[i]
		}
	}
	body.Afk = lastAfk != nil && lastAfk.str("status") == "afk"
	if !body.Afk { // 离开时 app、title 都发 ""
		var win *awEvent
		for i := range d.window {
			if win == nil || d.window[i].end().After(win.end()) {
				win = &d.window[i]
			}
		}
		if win == nil {
			return false, errors.New("ActivityWatch 最近一分钟没有窗口记录")
		}
		// 最新这条就是当前窗口，延到现在：标题刚变时记录器先写一条 0 秒的事件，
		// 不延的话裁剪后是空的，心跳就误报「没有窗口记录」（标题每秒变的终端几乎每拍都中）。
		// 开始时刻不早于 now（同一秒、时钟差）时多给一秒，否则区间仍是空的。
		cur, to := *win, now
		if !cur.Timestamp.Before(now) {
			to = cur.Timestamp.Add(time.Second)
		}
		if cur.end().Before(to) {
			cur.Duration = to.Sub(cur.Timestamp).Seconds()
		}
		// 脱敏走和上传**同一条路**：buildFragments（强制脱敏 → 隐私选项 → app-only / 浏览器对标签页）
		// 再 sendTitles（代号 → 强制脱敏）。只喂最新这一条窗口事件、不扣离开（离开已经单独看过）。
		frags := buildFragments(awData{window: []awEvent{cur}, web: d.web}, from, to, newRedactor(cfg))
		if len(frags) == 0 {
			return false, errors.New("ActivityWatch 最近一分钟没有窗口记录")
		}
		f := frags[len(frags)-1]
		t, err := sendTitles(cfg, []string{f.Title})
		if err != nil {
			return false, err
		}
		body.App, body.Title = scrubSecrets(f.App), t[0]
		if cfg.autoTrack {
			// 与上传同一个 matchRules、同样匹配「隐私选项处理后、换代号前」的标题；在本机算，只发目标 id 与把握。
			if sg, ok := matchRules(rulesForBeat(cfg, cockpit, base, now), segment{App: f.App, Title: f.Title}); ok {
				body.Guess = &presenceGuess{sg.TaskID, sg.ProjectID, sg.Confidence, sg.Classifier}
			}
		}
	}
	b, _ := json.Marshal(body)
	if _, _, err := postJSON(cockpit, cfg.DeviceToken, base+"/api/core/activity/presence", b); err != nil {
		return false, err
	}
	return true, nil
}

func presenceLoop(p paths, hc *http.Client) {
	var q quietLog
	every(func() time.Duration {
		cfg, err := readConfig(p)
		if err != nil {
			return 15 * time.Second
		}
		if sent, err := presenceBeat(cfg, hc, time.Now()); err != nil {
			q.printf("在场心跳没发出去（丢掉，不补发）：%v", err) // 错误里不含标题
		} else if sent {
			q.last = ""
		}
		return presenceInterval(cfg)
	})
}

// ── 状态文件桥 ───────────────────────────────────────────────────────────

const (
	bridgePoll    = 3 * time.Second
	bridgeStale   = 30 * time.Second
	bridgeMaxKeep = 20 // 每个 key 的待发队列上限
)

// agentKinds：key 前缀在这里才原样当 agent / tool 名上报，否则报 "agent"（只增）。
var agentKinds = map[string]bool{"claude": true, "codex": true, "hermes": true, "gemini": true, "opencode": true, "aider": true, "cursor": true}

var statePhase = map[string]string{
	"working": "working", "waiting_input": "waiting_input", "waiting_permission": "waiting_permission",
	"complete": "idle", "idle": "idle", "error": "error",
}

// agentRun：一个 key 对应的运行，存在状态文件里，重启接着用。
type agentRun struct {
	RunID   string     `json:"runId"`
	Phase   string     `json:"phase"`             // 最近观测到的相位（发出去的或排进队列的）
	Pending []phaseObs `json:"pending,omitempty"` // 网络失败没发出去的，下一轮原样重发
}

type phaseObs struct {
	Phase string `json:"phase"`
	At    string `json:"at"`
	Reply bool   `json:"reply,omitempty"`
}

type fileAgent struct{ key, label, phase string }

// readStatusFile 读新鲜的状态文件；过期、读不了、格式不对都返回 false（这一轮什么都不报）。
func readStatusFile(path string, now time.Time, ignore []string) ([]fileAgent, bool) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, false
	}
	var doc struct {
		UpdatedAt *float64           `json:"updated_at"`
		Agents    *[]json.RawMessage `json:"agents"`
	}
	if json.Unmarshal(b, &doc) != nil || doc.UpdatedAt == nil || doc.Agents == nil {
		return nil, false
	}
	if now.Sub(time.Unix(0, int64(*doc.UpdatedAt*1e9))) > bridgeStale {
		return nil, false
	}
	var out []fileAgent
	seen := map[string]bool{}
next:
	for _, raw := range *doc.Agents {
		var a struct{ Key, Label, State string }
		if json.Unmarshal(raw, &a) != nil || a.Key == "" || utf8.RuneCountInString(a.Key) > 128 || seen[a.Key] {
			continue
		}
		ph, ok := statePhase[a.State]
		if !ok {
			continue
		}
		for _, p := range ignore {
			if p != "" && strings.HasPrefix(a.Key, p) {
				continue next
			}
		}
		seen[a.Key] = true
		out = append(out, fileAgent{a.Key, a.Label, ph})
	}
	return out, true
}

func agentKind(key string) string {
	k, _, _ := strings.Cut(key, ":")
	if agentKinds[k] {
		return k
	}
	return "agent"
}

func clientKey(deviceID, key string) string {
	h := sha256.Sum256([]byte(deviceID + key))
	return hex.EncodeToString(h[:])[:32]
}

func truncRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

type sendResult int

const (
	sentOK     sendResult = iota
	sentClosed            // 404 / applied:false closed：运行已不在，忘掉映射
	sentDrop              // 请求本身不合格（4xx）：丢这一条，重发也没用
	sentRetry             // 网络、5xx、令牌问题：下一轮原样重发
)

type bridge struct {
	agents map[string]*agentRun
	loaded bool
	dirty  bool // 上次写状态文件失败：下一轮就算没变化也再写
	q      quietLog
}

// poll 读一次状态文件并上报变化；映射有变就写回状态文件。
func (b *bridge) poll(cfg Config, hc *http.Client, now time.Time, statePath string) {
	if !active(cfg) || cfg.AgentStatusFile == "" {
		return // 关 / 暂停：不读文件、零请求
	}
	if !b.loaded {
		var st State
		_ = loadJSON(statePath, &st)
		b.agents, b.loaded = st.Agents, true
	}
	if b.agents == nil {
		b.agents = map[string]*agentRun{}
	}
	file, fresh := readStatusFile(cfg.AgentStatusFile, now, cfg.AgentStatusIgnore)
	if !fresh {
		return // 过期：不编转入、不 stop，在跑的运行留给服务端的遗忘超时
	}
	before, _ := json.Marshal(b.agents)
	base := strings.TrimRight(cfg.CockpitURL, "/")
	c := withPolicy(hc, hc.Timeout, sameHostOnly)
	down := false // 这一轮碰到网络失败后不再发（本轮不重试）
	send := func(path string, body any) (sendResult, []byte) {
		if down {
			return sentRetry, nil
		}
		bs, _ := json.Marshal(body)
		resp, code, err := postJSON(c, cfg.DeviceToken, base+path, bs)
		var r struct {
			Applied *bool  `json:"applied"`
			Reason  string `json:"reason"`
		}
		_ = json.Unmarshal(resp, &r)
		switch {
		case code == http.StatusNotFound:
			return sentClosed, resp
		case err == nil && r.Applied != nil && !*r.Applied && r.Reason == "closed":
			return sentClosed, resp
		case err == nil:
			return sentOK, resp
		case code >= 400 && code < 500 && code != 401 && code != 403 && code != 408 && code != 429:
			b.q.printf("状态文件桥：服务端拒收（%v），丢掉这一条", err)
			return sentDrop, resp
		}
		down = true
		b.q.printf("状态文件桥：上报失败（%v），下一轮重发", err)
		return sentRetry, resp
	}
	cur := map[string]fileAgent{}
	for _, a := range file {
		cur[a.key] = a
	}
	at := now.Format("2006-01-02T15:04:05.000-07:00")
	forgotten := map[string]bool{}
	keys := make([]string, 0, len(b.agents))
	for k := range b.agents {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		m := b.agents[k]
		phasePath := "/api/core/agents/" + m.RunID + "/phase"
		closed := false
		for len(m.Pending) > 0 && !down {
			res, _ := send(phasePath, m.Pending[0])
			if res == sentRetry {
				break
			}
			if res == sentClosed {
				closed = true
				break
			}
			m.Pending = m.Pending[1:]
		}
		if closed {
			delete(b.agents, k)
			forgotten[k] = true
			continue
		}
		a, present := cur[k]
		if !present {
			if len(m.Pending) > 0 {
				continue // 先把没发出去的相位补上，再 stop
			}
			outcome := "done"
			if m.Phase == "error" {
				outcome = "failed"
			}
			if res, _ := send("/api/core/agents/"+m.RunID+"/stop", map[string]string{"outcome": outcome}); res != sentRetry {
				delete(b.agents, k)
			}
			continue
		}
		if a.phase == m.Phase {
			continue
		}
		// 只有「等人」解除转回干活才算人回话；idle / error → working 分不清是人还是自动重试，不连线。
		obs := phaseObs{Phase: a.phase, At: at, Reply: a.phase == "working" && strings.HasPrefix(m.Phase, "waiting_")}
		m.Phase = a.phase
		if len(m.Pending) == 0 {
			switch res, _ := send(phasePath, obs); res {
			case sentOK, sentDrop:
				continue
			case sentClosed:
				delete(b.agents, k)
				forgotten[k] = true
				continue
			}
		}
		m.Pending = append(m.Pending, obs)
		if len(m.Pending) > bridgeMaxKeep {
			m.Pending = m.Pending[len(m.Pending)-bridgeMaxKeep:]
		}
	}

	// 第一次看到的 key：开运行。label 的脱敏要用此刻的网页设置，只在真有要开的时候拉一次。
	var rcfg *Config
	tried := false
	for _, a := range file {
		if b.agents[a.key] != nil || forgotten[a.key] || down {
			continue
		}
		if rcfg == nil {
			if tried {
				break
			}
			tried = true
			c2 := cfg
			if err := applyRemoteSettings(&c2, c, base); err != nil {
				b.q.printf("状态文件桥：%v（这一轮不开新运行）", err)
				break
			}
			if err := c2.Privacy.check(); err != nil {
				b.q.printf("状态文件桥：%v（这一轮不开新运行）", err)
				break
			}
			rcfg = &c2
		}
		kind := agentKind(a.key)
		body := map[string]string{"agent": kind, "tool": kind, "phase": a.phase, "clientKey": clientKey(cfg.DeviceID, a.key)}
		if a.label != "" {
			// label 按上传的标题脱敏规则：强制脱敏 → 隐私选项（titles=drop 就没了）→ 代号 → 强制脱敏。
			t, _ := newRedactor(*rcfg).window(kind, a.label, nil)
			sent, err := sendTitles(*rcfg, []string{t})
			if err != nil {
				b.q.printf("状态文件桥：%v（这一轮不开新运行）", err)
				break
			}
			if l := truncRunes(sent[0], 64); l != "" {
				body["label"] = l
				if utf8.RuneCountInString(l) >= 3 {
					body["match"] = l
				}
			}
		}
		res, resp := send("/api/core/agents/start", body)
		if res != sentOK {
			continue
		}
		var r struct {
			RunID string `json:"runId"`
		}
		if json.Unmarshal(resp, &r) != nil || r.RunID == "" {
			continue
		}
		b.agents[a.key] = &agentRun{RunID: r.RunID, Phase: a.phase}
	}
	if !down {
		b.q.last = ""
	}
	if after, _ := json.Marshal(b.agents); b.dirty || string(before) != string(after) {
		snap := map[string]*agentRun{}
		_ = json.Unmarshal(after, &snap)
		err := updateState(statePath, func(s *State) { s.Agents = snap })
		if b.dirty = err != nil; b.dirty {
			b.q.printf("状态文件桥：状态文件写不了：%v", err)
		}
	}
}

func bridgeLoop(p paths, hc *http.Client) {
	b := &bridge{}
	every(func() time.Duration {
		if cfg, err := readConfig(p); err == nil {
			b.poll(cfg, hc, time.Now(), p.state)
		}
		return bridgePoll
	})
}
