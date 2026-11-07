package main

// v0.6：桌宠桥接（desktop.pet.v1「一」「二」）。每 10 秒读一次 views/current 与本机健康状态，
// 有变化才产生 PetEvent，交给 petsink.go 发出去。发失败只写日志，不影响其他功能。

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const (
	petPoll          = 10 * time.Second
	petFocusGap      = 60 * time.Second
	petServerFails   = 3
	petUploadFails   = 3
	petTextMax       = 60
	petDefaultURL    = "http://127.0.0.1:47340/pet-event"
	petDefaultRender = "http"
)

// PetConfig：本机配置的 pet 节。整节不存在 = 关。
type PetConfig struct {
	Enabled  bool               `json:"enabled"`
	Renderer string             `json:"renderer,omitempty"` // "http"（缺省）| "dsh-pet"
	URL      string             `json:"url,omitempty"`
	Token    *string            `json:"token,omitempty"` // 只给 http 用
	PetID    *string            `json:"petId,omitempty"` // 只给 dsh-pet 用
	Moods    map[string]*string `json:"moods,omitempty"` // 只给 dsh-pet 用：mood → 工作状态名；缺 / null = 跳过 work-status
}

func (p PetConfig) renderer() string {
	if p.Renderer == "" {
		return petDefaultRender
	}
	return p.Renderer
}

func (p PetConfig) target() string {
	if p.URL == "" {
		if p.renderer() == "dsh-pet" {
			return "http://127.0.0.1:3080"
		}
		return petDefaultURL
	}
	return p.URL
}

func (p PetConfig) check() error {
	if r := p.renderer(); r != "http" && r != "dsh-pet" {
		return fmt.Errorf("pet.renderer 只能是 http / dsh-pet，现在是 %q", p.Renderer)
	}
	return nil
}

// PetEvent：契约「一」。
type PetEvent struct {
	V    int            `json:"v"`
	Type string         `json:"type"`
	At   string         `json:"at"`
	Text string         `json:"text"`
	Mood string         `json:"mood"`
	Data map[string]any `json:"data"`
}

// petClip：超过 max 个字符截到 max-1 个加 …。
func petClip(s string, max int) string {
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max-1]) + "…"
}

type petCurrent struct {
	Focus *struct {
		State       string  `json:"state"`
		ProjectName *string `json:"projectName"`
		TaskName    *string `json:"taskName"`
		DisplayName *string `json:"displayName"`
	} `json:"focus"`
	Agents []struct {
		RunID string  `json:"runId"`
		Agent string  `json:"agent"`
		Label *string `json:"label"`
		Phase *string `json:"phase"`
	} `json:"agents"`
}

type petRun struct{ name, phase string }

type petBridge struct {
	now    func() time.Time
	hc     *http.Client
	health func() (awDown bool, uploadFails int)
	send   func(PetConfig, PetEvent)

	started   bool
	focusSeen string // 上一轮看到的焦点键（不论发没发）
	focusAt   time.Time
	runs      map[string]petRun
	fails     int
	alerts    map[string]bool
	q         quietLog
}

func newPetBridge(p paths, hc *http.Client) *petBridge {
	return &petBridge{now: time.Now, hc: hc,
		health: func() (bool, int) {
			var st State
			_ = loadJSON(p.state, &st)
			return st.Health.AwState == "unreachable", st.UploadFails
		},
		send: func(c PetConfig, e PetEvent) { petSend(hc, c, e) }}
}

func strOr(p *string) string {
	if p == nil {
		return ""
	}
	return strings.TrimSpace(*p)
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (b *petBridge) emit(pc PetConfig, typ, text, mood string, data map[string]any) {
	b.send(pc, PetEvent{V: 1, Type: typ, At: b.now().Format(time.RFC3339), Text: petClip(text, petTextMax), Mood: mood, Data: data})
}

func (b *petBridge) fetch(cfg Config) (petCurrent, error) {
	var cur petCurrent
	req, err := http.NewRequest("GET", strings.TrimRight(cfg.CockpitURL, "/")+"/api/core/views/current", nil)
	if err != nil {
		return cur, err
	}
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	resp, err := withPolicy(b.hc, 8*time.Second, sameHostOnly).Do(req)
	if err != nil {
		return cur, err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return cur, fmt.Errorf("views/current 返回 %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return cur, err
	}
	return cur, json.Unmarshal(body, &cur)
}

// tick：一轮。
func (b *petBridge) tick(cfg Config) {
	pc := cfg.Pet
	if pc == nil || !pc.Enabled {
		b.started = false
		return
	}
	if err := pc.check(); err != nil {
		b.q.printf("桌宠桥接没启用：%v", err)
		return
	}
	if cfg.CockpitURL == "" || cfg.DeviceToken == "" {
		return
	}
	cur, err := b.fetch(cfg)
	if err != nil {
		b.fails++
		b.q.printf("桌宠桥接读 views/current 失败（连续第 %d 轮）：%v", b.fails, err)
	} else {
		b.fails = 0
		b.q.last = ""
		b.focus(*pc, cur)
		b.agents(*pc, cur)
		b.started = true // 基线要等读 views/current 成功一次才算建好
	}
	b.alert(*pc)
}

func (b *petBridge) focus(pc PetConfig, cur petCurrent) {
	var key, text, mood string
	data := map[string]any{}
	switch f := cur.Focus; {
	case f == nil:
	case f.State == "afk":
		key, text, mood, data = "afk", "休息一下", "idle", map[string]any{"afk": true}
	default:
		task, proj, disp := strOr(f.TaskName), strOr(f.ProjectName), strOr(f.DisplayName)
		switch {
		case task != "" && proj != "":
			text = "在" + task + "（" + proj + "）"
		case task != "":
			text = "在" + task
		case proj != "":
			text = "在 " + proj
		case disp != "":
			text = "在看 " + disp
		}
		if text != "" {
			key, mood = task+"\x00"+proj+"\x00"+disp, "working"
			data = map[string]any{"taskName": nilIfEmpty(task), "projectName": nilIfEmpty(proj), "displayName": nilIfEmpty(disp)}
		}
	}
	changed := key != b.focusSeen
	b.focusSeen = key
	if !changed || key == "" || !b.started {
		return
	}
	now := b.now()
	if !b.focusAt.IsZero() && now.Sub(b.focusAt) < petFocusGap {
		return // 被压掉的丢弃，不补发
	}
	b.focusAt = now
	b.emit(pc, "focus", text, mood, data)
}

func (b *petBridge) agents(pc PetConfig, cur petCurrent) {
	now := map[string]petRun{}
	for _, a := range cur.Agents {
		name := strOr(a.Label)
		if name == "" {
			name = a.Agent
		}
		now[a.RunID] = petRun{name, strOr(a.Phase)}
	}
	if b.started {
		for id, r := range now {
			reason := map[string]string{"waiting_input": "input", "waiting_permission": "permission"}[r.phase]
			if reason == "" || b.runs[id].phase == r.phase {
				continue
			}
			ask := map[string]string{"input": "在等你回话", "permission": "在等你批权限"}[reason]
			b.emit(pc, "agentWaiting", r.name+" "+ask, "waiting", map[string]any{"agent": r.name, "reason": reason})
		}
		for id, r := range b.runs {
			if _, ok := now[id]; !ok { // 取舍：views/current 已滤掉失联的运行，失联的也会被当成干完了
				b.emit(pc, "agentDone", r.name+" 干完了", "happy", map[string]any{"agent": r.name})
			}
		}
	}
	b.runs = now
}

var petAlertText = map[string]string{
	"aw":     "记录断了：ActivityWatch 没在跑",
	"upload": "记录断了：上传连续失败",
	"server": "记录断了：连不上服务端",
}

func (b *petBridge) alert(pc PetConfig) {
	awDown, upFails := b.health()
	now := map[string]bool{"aw": awDown, "upload": upFails >= petUploadFails, "server": b.fails >= petServerFails}
	if b.alerts == nil {
		b.alerts = map[string]bool{}
	}
	for _, kind := range []string{"aw", "upload", "server"} {
		switch was := b.alerts[kind]; {
		case now[kind] && !was:
			b.emit(pc, "alert", petAlertText[kind], "worried", map[string]any{"kind": kind})
		case !now[kind] && was:
			b.emit(pc, "alertCleared", "记录恢复了", "happy", map[string]any{"kind": kind})
		}
		b.alerts[kind] = now[kind]
	}
}

func petLoop(p paths, hc *http.Client) {
	b := newPetBridge(p, hc)
	every(func() time.Duration {
		if cfg, err := readConfig(p); err == nil {
			b.tick(cfg)
		}
		return petPoll
	})
}
