package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"
)

// petRig：假 HoneyComb（views/current）+ 注入时钟 + 记事件的假发送。
type petRig struct {
	t      *testing.T
	srv    *httptest.Server
	mu     sync.Mutex
	body   string
	status int
	auth   []string
	gets   int
	clock  time.Time
	b      *petBridge
	got    []PetEvent
	awDown bool
	upFail int
	cfg    Config
}

func newPetRig(t *testing.T, pc *PetConfig) *petRig {
	r := &petRig{t: t, status: 200, body: `{"focus":null,"agents":[]}`,
		clock: time.Date(2026, 11, 7, 10, 0, 0, 0, time.FixedZone("CST", 8*3600))}
	r.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		r.mu.Lock()
		defer r.mu.Unlock()
		if q.URL.Path != "/api/core/views/current" {
			w.WriteHeader(404)
			return
		}
		r.gets++
		r.auth = append(r.auth, q.Header.Get("Authorization")+"|"+q.Header.Get("X-Admin-Token"))
		w.WriteHeader(r.status)
		fmt.Fprint(w, r.body)
	}))
	t.Cleanup(r.srv.Close)
	r.b = &petBridge{now: func() time.Time { return r.clock }, hc: r.srv.Client(),
		health: func() (bool, int) { return r.awDown, r.upFail },
		send:   func(_ PetConfig, e PetEvent) { r.got = append(r.got, e) }}
	r.cfg = Config{CockpitURL: r.srv.URL, DeviceToken: "DEV", Pet: pc}
	return r
}

func onPet() *PetConfig { return &PetConfig{Enabled: true} }

// round：设好这一轮的 views/current，跑一轮，再推 10 秒。
func (r *petRig) round(body string) []PetEvent {
	r.mu.Lock()
	r.body = body
	r.mu.Unlock()
	n := len(r.got)
	r.b.tick(r.cfg)
	r.clock = r.clock.Add(petPoll)
	return r.got[n:]
}

func focusJSON(task, proj, disp string) string {
	q := func(s string) string {
		if s == "" {
			return "null"
		}
		b, _ := json.Marshal(s)
		return string(b)
	}
	return fmt.Sprintf(`{"focus":{"state":"present","app":"x","title":"SECRET-TITLE https://secret.example/a?token=abc /home/u/secret","taskName":%s,"projectName":%s,"displayName":%s},"agents":[]}`, q(task), q(proj), q(disp))
}

func agentsJSON(phase string, ids ...string) string {
	var a []string
	for _, id := range ids {
		a = append(a, fmt.Sprintf(`{"runId":"r-%s","agent":%q,"label":null,"phase":%q}`, id, id, phase))
	}
	return `{"focus":null,"agents":[` + strings.Join(a, ",") + `]}`
}

func TestPetOffMakesNoRequests(t *testing.T) { // AD-E01 / E02
	for _, pc := range []*PetConfig{nil, {Enabled: false}} {
		r := newPetRig(t, pc)
		r.round(focusJSON("a", "b", ""))
		r.round(focusJSON("c", "d", ""))
		if r.gets != 0 || len(r.got) != 0 {
			t.Fatalf("关着也有动静：gets=%d events=%v", r.gets, r.got)
		}
	}
}

func TestPetPollsWithDeviceToken(t *testing.T) { // AD-E03
	r := newPetRig(t, onPet())
	r.round(`{"focus":null,"agents":[]}`)
	r.round(`{"focus":null,"agents":[]}`)
	if r.gets != 2 || r.auth[0] != "Bearer DEV|" {
		t.Fatalf("gets=%d auth=%v", r.gets, r.auth)
	}
}

func TestPetFocusTexts(t *testing.T) { // AD-E04 – E07
	cases := []struct{ task, proj, disp, text string }{
		{"写提示词", "garden", "", "在写提示词（garden）"},
		{"", "garden", "", "在 garden"},
		{"", "", "github.com", "在看 github.com"},
		{"写提示词", "", "", "在写提示词"},
	}
	for _, c := range cases {
		r := newPetRig(t, onPet())
		r.round(`{"focus":null,"agents":[]}`)
		ev := r.round(focusJSON(c.task, c.proj, c.disp))
		if len(ev) != 1 || ev[0].Type != "focus" || ev[0].Text != c.text || ev[0].Mood != "working" {
			t.Fatalf("%+v -> %+v", c, ev)
		}
		if c.task == "写提示词" && c.proj == "garden" {
			d := ev[0].Data
			if d["taskName"] != "写提示词" || d["projectName"] != "garden" || d["displayName"] != nil {
				t.Fatalf("data=%v", d)
			}
		}
	}
	r := newPetRig(t, onPet())
	r.round(`{"focus":null,"agents":[]}`)
	if ev := r.round(focusJSON("", "", "")); len(ev) != 0 { // AD-E07
		t.Fatalf("三者都认不出还发了：%v", ev)
	}
}

func TestPetDisplayNameIsServerValue(t *testing.T) { // AD-E23
	r := newPetRig(t, onPet())
	r.round(`{"focus":null,"agents":[]}`)
	ev := r.round(focusJSON("t", "p", "figma.com · 首页改版"))
	if ev[0].Data["displayName"] != "figma.com · 首页改版" {
		t.Fatalf("%v", ev[0].Data)
	}
}

func TestPetAfkAndReturn(t *testing.T) { // AD-E08 / E09
	r := newPetRig(t, onPet())
	r.round(focusJSON("t", "p", ""))
	r.clock = r.clock.Add(time.Minute)
	ev := r.round(`{"focus":{"state":"afk"},"agents":[]}`)
	if len(ev) != 1 || ev[0].Data["afk"] != true || ev[0].Text != "休息一下" || ev[0].Mood != "idle" {
		t.Fatalf("%+v", ev)
	}
	r.clock = r.clock.Add(time.Minute)
	ev = r.round(focusJSON("t", "p", ""))
	if len(ev) != 1 || ev[0].Text != "在t（p）" {
		t.Fatalf("回到原目标应再发：%+v", ev)
	}
}

func TestPetFocusDebounce(t *testing.T) { // AD-E10
	for _, gap := range []time.Duration{59 * time.Second, 60 * time.Second} {
		r := newPetRig(t, onPet())
		r.round(`{"focus":null,"agents":[]}`)
		if ev := r.round(focusJSON("a", "p", "")); len(ev) != 1 {
			t.Fatal("第一条该发")
		}
		r.clock = r.clock.Add(gap - petPoll) // round 已推 10 秒
		ev := r.round(focusJSON("b", "p", ""))
		if want := gap >= petFocusGap; (len(ev) == 1) != want {
			t.Fatalf("间隔 %v：发了 %d 条", gap, len(ev))
		}
		if !(gap >= petFocusGap) { // 被压掉的不补发
			if ev := r.round(focusJSON("b", "p", "")); len(ev) != 0 {
				t.Fatalf("被压掉的 focus 补发了：%v", ev)
			}
		}
	}
}

func TestPetSameDataSentOnce(t *testing.T) { // AD-E11
	r := newPetRig(t, onPet())
	r.round(`{"focus":null,"agents":[]}`)
	r.round(focusJSON("a", "p", ""))
	for i := 0; i < 3; i++ {
		r.clock = r.clock.Add(time.Minute)
		if ev := r.round(focusJSON("a", "p", "")); len(ev) != 0 {
			t.Fatalf("重复发了：%v", ev)
		}
	}
}

func TestPetAgentWaiting(t *testing.T) { // AD-E12 / E13
	for phase, want := range map[string][2]string{
		"waiting_input":      {"input", "garden 在等你回话"},
		"waiting_permission": {"permission", "garden 在等你批权限"},
	} {
		r := newPetRig(t, onPet())
		r.round(agentsJSON("working", "garden"))
		ev := r.round(agentsJSON(phase, "garden"))
		if len(ev) != 1 || ev[0].Type != "agentWaiting" || ev[0].Data["reason"] != want[0] ||
			ev[0].Data["agent"] != "garden" || ev[0].Text != want[1] || ev[0].Mood != "waiting" {
			t.Fatalf("%s -> %+v", phase, ev)
		}
		if ev := r.round(agentsJSON(phase, "garden")); len(ev) != 0 {
			t.Fatalf("同一阶段重复发：%v", ev)
		}
	}
}

func TestPetBaselineSilent(t *testing.T) { // AD-E14：启动第一轮只建基线
	r := newPetRig(t, onPet())
	if ev := r.round(agentsJSON("waiting_input", "garden")); len(ev) != 0 {
		t.Fatalf("基线轮发了：%v", ev)
	}
	if ev := r.round(agentsJSON("waiting_input", "garden")); len(ev) != 0 {
		t.Fatalf("基线里已在等人的不该补发：%v", ev)
	}
}

func TestPetAgentDone(t *testing.T) { // AD-E15
	r := newPetRig(t, onPet())
	r.round(agentsJSON("working", "garden"))
	ev := r.round(`{"focus":null,"agents":[]}`)
	if len(ev) != 1 || ev[0].Type != "agentDone" || ev[0].Text != "garden 干完了" || ev[0].Mood != "happy" {
		t.Fatalf("%+v", ev)
	}
}

func TestPetAgentLabelPreferred(t *testing.T) {
	r := newPetRig(t, onPet())
	r.round(`{"focus":null,"agents":[]}`)
	ev := r.round(`{"agents":[{"runId":"1","agent":"claude","label":"garden","phase":"waiting_input"}]}`)
	if len(ev) != 1 || ev[0].Text != "garden 在等你回话" {
		t.Fatalf("%+v", ev)
	}
}

func TestPetAwAlert(t *testing.T) { // AD-E16 / E19 / E20
	r := newPetRig(t, onPet())
	if ev := r.round(`{"agents":[]}`); len(ev) != 0 { // E20：没出过 alert 不发 cleared
		t.Fatalf("%v", ev)
	}
	r.awDown = true
	ev := r.round(`{"agents":[]}`)
	if len(ev) != 1 || ev[0].Type != "alert" || ev[0].Data["kind"] != "aw" ||
		!strings.Contains(ev[0].Text, "ActivityWatch 没在跑") || ev[0].Mood != "worried" {
		t.Fatalf("%+v", ev)
	}
	if ev := r.round(`{"agents":[]}`); len(ev) != 0 {
		t.Fatalf("alert 重复发：%v", ev)
	}
	r.awDown = false
	ev = r.round(`{"agents":[]}`)
	if len(ev) != 1 || ev[0].Type != "alertCleared" || ev[0].Data["kind"] != "aw" || ev[0].Text != "记录恢复了" || ev[0].Mood != "happy" {
		t.Fatalf("%+v", ev)
	}
}

func TestPetUploadAlertNeedsThreeRounds(t *testing.T) { // AD-E17
	r := newPetRig(t, onPet())
	r.upFail = 2
	if ev := r.round(`{"agents":[]}`); len(ev) != 0 {
		t.Fatalf("2 轮就发了：%v", ev)
	}
	r.upFail = 3
	ev := r.round(`{"agents":[]}`)
	if len(ev) != 1 || ev[0].Type != "alert" || ev[0].Data["kind"] != "upload" {
		t.Fatalf("%+v", ev)
	}
}

func TestPetServerAlert(t *testing.T) { // AD-E18：连续 3 轮失败
	r := newPetRig(t, onPet())
	r.round(`{"agents":[]}`)
	r.status = 500
	for i := 1; i <= 3; i++ {
		ev := r.round("")
		if (len(ev) == 1) != (i == 3) {
			t.Fatalf("第 %d 轮失败：%v", i, ev)
		}
		if i == 3 && (ev[0].Type != "alert" || ev[0].Data["kind"] != "server") {
			t.Fatalf("%+v", ev)
		}
	}
	r.status = 200
	ev := r.round(`{"agents":[]}`)
	if len(ev) != 1 || ev[0].Type != "alertCleared" || ev[0].Data["kind"] != "server" {
		t.Fatalf("%+v", ev)
	}
}

func TestPetServerFailuresMustBeConsecutive(t *testing.T) {
	r := newPetRig(t, onPet())
	r.round(`{"agents":[]}`)
	for i := 0; i < 2; i++ {
		r.status = 500
		r.round("")
		r.round("")
		r.status = 200
		r.round(`{"agents":[]}`)
	}
	if len(r.got) != 0 {
		t.Fatalf("%v", r.got)
	}
}

func TestPetEnvelopeAndClip(t *testing.T) { // AD-E21
	r := newPetRig(t, onPet())
	r.round(`{"agents":[]}`)
	long := strings.Repeat("长", 80)
	ev := r.round(focusJSON(long, "p", ""))
	if len(ev) != 1 {
		t.Fatal(ev)
	}
	e := ev[0]
	if e.V != 1 || utf8.RuneCountInString(e.Text) != 60 || !strings.HasSuffix(e.Text, "…") {
		t.Fatalf("%+v len=%d", e, utf8.RuneCountInString(e.Text))
	}
	if _, err := time.Parse(time.RFC3339, e.At); err != nil || !strings.HasSuffix(e.At, "+08:00") {
		t.Fatalf("at=%q", e.At)
	}
	if petClip(strings.Repeat("a", 60), 60) != strings.Repeat("a", 60) {
		t.Fatal("正好 60 个不该截")
	}
}

func TestPetNeverLeaksRawTitle(t *testing.T) { // AD-E22
	r := newPetRig(t, onPet())
	r.round(`{"agents":[]}`)
	r.round(focusJSON("t", "p", "github.com"))
	r.round(`{"focus":{"state":"afk","title":"SECRET-TITLE"},"agents":[]}`)
	b, _ := json.Marshal(r.got)
	for _, s := range []string{"SECRET-TITLE", "secret.example", "token=abc", "/home/u", "DEV"} {
		if strings.Contains(string(b), s) {
			t.Fatalf("事件里有 %q：%s", s, b)
		}
	}
	if len(r.got) == 0 {
		t.Fatal("没产生事件，检查无意义")
	}
}

func TestPetRendererEnum(t *testing.T) { // AD-D16 / D17
	if err := (PetConfig{Renderer: "x"}).check(); err == nil {
		t.Fatal("renderer 写错该报错")
	}
	r := newPetRig(t, &PetConfig{Enabled: true, Renderer: "x"})
	r.round(focusJSON("a", "b", ""))
	if r.gets != 0 {
		t.Fatal("配置不合规还在轮询")
	}
	for _, p := range []PetConfig{{}, {Renderer: "http"}, {Renderer: "dsh-pet"}} {
		if err := p.check(); err != nil {
			t.Fatal(err)
		}
	}
	if (PetConfig{}).target() != "http://127.0.0.1:47340/pet-event" {
		t.Fatal("缺省 url 不对")
	}
}

func TestPetReenableRebaselines(t *testing.T) {
	r := newPetRig(t, onPet())
	r.round(agentsJSON("working", "garden"))
	r.cfg.Pet = nil
	r.round(`{"agents":[]}`)
	r.cfg.Pet = onPet()
	if ev := r.round(`{"agents":[]}`); len(ev) != 0 {
		t.Fatalf("重新打开的第一轮应只建基线：%v", ev)
	}
}

func TestPetConfigParses(t *testing.T) {
	var c Config
	if err := json.Unmarshal([]byte(`{"pet":{"enabled":true,"renderer":"dsh-pet","url":"http://127.0.0.1:3080","token":null,"petId":null,"moods":{"working":"working","idle":null}}}`), &c); err != nil {
		t.Fatal(err)
	}
	if c.Pet == nil || !c.Pet.Enabled || c.Pet.Moods["working"] == nil || c.Pet.Moods["idle"] != nil {
		t.Fatalf("%+v", c.Pet)
	}
	var none Config
	_ = json.Unmarshal([]byte(`{}`), &none)
	if none.Pet != nil {
		t.Fatal("缺省应为 nil = 关")
	}
}
