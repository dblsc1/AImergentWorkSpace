package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

type sinkReq struct {
	method, path, query, auth, ctype, body string
}

// fakeSink：记下每个请求；respond 返回 (状态码, 响应体)，nil / 0 = 200 + {"ok":true,...}。
type fakeSink struct {
	*httptest.Server
	mu      sync.Mutex
	reqs    []sinkReq
	respond func(r sinkReq) (int, string)
}

func newFakeSink(t *testing.T, respond func(sinkReq) (int, string)) *fakeSink {
	f := &fakeSink{respond: respond}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		b, _ := io.ReadAll(q.Body)
		r := sinkReq{q.Method, q.URL.Path, q.URL.RawQuery, q.Header.Get("Authorization"), q.Header.Get("Content-Type"), string(b)}
		f.mu.Lock()
		f.reqs = append(f.reqs, r)
		f.mu.Unlock()
		if f.respond != nil {
			if code, body := f.respond(r); code != 0 {
				w.WriteHeader(code)
				io.WriteString(w, body)
				return
			}
		}
		switch {
		case strings.HasSuffix(r.path, "/lock") && r.method == "POST":
			io.WriteString(w, `{"ok":true,"lockId":"lk-1","petId":"main","expiresAt":1}`)
		case strings.HasSuffix(r.path, "/config"):
			io.WriteString(w, `{"main":{"pets":[{"id":"first"},{"id":"second"}]}}`)
		case r.method == "DELETE":
			w.WriteHeader(204)
		default:
			io.WriteString(w, `{"ok":true}`)
		}
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeSink) seen() []sinkReq {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]sinkReq(nil), f.reqs...)
}

func (f *fakeSink) steps() []string {
	var out []string
	for _, r := range f.seen() {
		out = append(out, r.method+" "+strings.TrimPrefix(r.path, "/dsh-pet-7340"))
	}
	return out
}

func petStr(s string) *string { return &s }

func dshCfg(url string) PetConfig {
	return PetConfig{Enabled: true, Renderer: "dsh-pet", URL: url, Token: petStr("T"), PetID: petStr("x"),
		Moods: map[string]*string{"working": petStr("working"), "waiting": petStr("waiting"), "happy": petStr("success"), "worried": petStr("error")}}
}

func mkPetEv(typ, mood, text string, data map[string]any) PetEvent {
	return PetEvent{V: 1, Type: typ, At: "2026-11-07T10:12:30+08:00", Text: text, Mood: mood, Data: data}
}

func TestPetURLRules(t *testing.T) { // AD-D01 / D02
	for _, u := range []string{"http://127.0.0.1:3080", "http://localhost:3080", "http://[::1]:3080", "http://127.0.0.2:1", "https://x.example"} {
		if err := petURLOK(u); err != nil {
			t.Errorf("%s 该放行：%v", u, err)
		}
	}
	for _, u := range []string{"http://example.com", "http://localhost.evil.com", "http://10.0.0.5", "ftp://127.0.0.1", "127.0.0.1:80", ""} {
		if petURLOK(u) == nil {
			t.Errorf("%s 不该放行", u)
		}
	}
}

func TestPetBadURLSendsNothing(t *testing.T) { // AD-D02
	f := newFakeSink(t, nil)
	pc := PetConfig{Enabled: true, URL: "http://example.com/pet-event"}
	petSend(f.Client(), pc, mkPetEv("alert", "worried", "x", nil))
	if len(f.seen()) != 0 {
		t.Fatal("不合规还发了")
	}
}

func TestPetNoRedirectFollowed(t *testing.T) { // AD-D03
	target := newFakeSink(t, nil)
	for _, pc := range []PetConfig{
		{Enabled: true, Renderer: "http"},
		dshCfg(""),
	} {
		hop := newFakeSink(t, func(sinkReq) (int, string) { return 0, "" })
		hop.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, target.URL+"/elsewhere", http.StatusFound)
		})
		pc.URL = hop.URL
		petSend(hop.Client(), pc, mkPetEv("agentDone", "happy", "x", map[string]any{"agent": "a"}))
	}
	if n := len(target.seen()); n != 0 {
		t.Fatalf("跳转目标收到 %d 个请求", n)
	}
}

func TestPetDshNeverSeesTokens(t *testing.T) { // AD-D04：设备令牌与 http 的 token 都不进 dsh-pet
	f := newFakeSink(t, nil)
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("agentWaiting", "waiting", "garden 在等你回话", map[string]any{"agent": "garden"}))
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("focus", "idle", "休息一下", map[string]any{"afk": true}))
	rs := f.seen()
	if len(rs) < 5 {
		t.Fatalf("请求太少：%v", f.steps())
	}
	for _, r := range rs {
		if r.auth != "" || strings.Contains(r.body+r.query+r.path, "DEV") || strings.Contains(r.body+r.query, "Bearer") {
			t.Fatalf("带了凭据：%+v", r)
		}
	}
}

func TestPetHTTPSink(t *testing.T) { // AD-D05 / D14
	f := newFakeSink(t, nil)
	e := mkPetEv("alert", "worried", "记录断了：ActivityWatch 没在跑", map[string]any{"kind": "aw"})
	petSend(f.Client(), PetConfig{Enabled: true, URL: f.URL + "/pet-event", Token: petStr("T")}, e)
	petSend(f.Client(), PetConfig{Enabled: true, URL: f.URL + "/pet-event"}, e)
	petSend(f.Client(), PetConfig{Enabled: true, URL: f.URL + "/pet-event", Token: petStr("")}, e)
	rs := f.seen()
	if len(rs) != 3 {
		t.Fatal(rs)
	}
	if rs[0].method != "POST" || rs[0].path != "/pet-event" || rs[0].ctype != "application/json" || rs[0].auth != "Bearer T" {
		t.Fatalf("%+v", rs[0])
	}
	var got PetEvent
	if err := json.Unmarshal([]byte(rs[0].body), &got); err != nil || got.Type != "alert" || got.Text != e.Text || got.Data["kind"] != "aw" || got.V != 1 {
		t.Fatalf("体不是原样 PetEvent：%s", rs[0].body)
	}
	if rs[1].auth != "" || rs[2].auth != "" {
		t.Fatal("token 为空时不该带 Authorization")
	}
	if strings.Contains(rs[0].body, "T\"") && strings.Contains(rs[0].body, "token") {
		t.Fatal("令牌不该进体")
	}
}

func TestPetHTTPStatuses(t *testing.T) { // AD-D15
	for code, ok := range map[int]bool{200: true, 204: true, 500: false} {
		f := newFakeSink(t, func(sinkReq) (int, string) { return code, "" })
		pc := PetConfig{Enabled: true, URL: f.URL}
		err := httpSend(withPolicy(f.Client(), petSinkWait, petNoRedirect), pc, mkPetEv("alert", "worried", "x", nil))
		if (err == nil) != ok {
			t.Fatalf("%d -> %v", code, err)
		}
		if n := len(f.seen()); n != 1 {
			t.Fatalf("%d：请求了 %d 次，不该重试", code, n)
		}
	}
}

func TestPetDshFocusOnlyWorkStatus(t *testing.T) { // AD-D06
	f := newFakeSink(t, nil)
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("focus", "working", "在写提示词（garden）", map[string]any{}))
	rs := f.seen()
	if len(rs) != 1 || rs[0].method != "POST" || rs[0].path != "/dsh-pet-7340/work-status" {
		t.Fatalf("%v", f.steps())
	}
	var b map[string]any
	_ = json.Unmarshal([]byte(rs[0].body), &b)
	if b["state"] != "working" || b["task"] != "在写提示词（garden）" || len(b) != 2 {
		t.Fatalf("%s", rs[0].body)
	}
}

func TestPetDshWorkStatusTaskClipped(t *testing.T) { // 上游 task 至多 40 个码点
	f := newFakeSink(t, nil)
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("focus", "working", "在"+strings.Repeat("长", 55), map[string]any{}))
	var b map[string]any
	_ = json.Unmarshal([]byte(f.seen()[0].body), &b)
	if r := []rune(b["task"].(string)); len(r) != 40 || r[39] != '…' {
		t.Fatalf("task=%q", b["task"])
	}
}

func TestPetDshBubbleOrder(t *testing.T) { // AD-D07 / D08
	want := []string{"POST /work-status", "POST /lock", "POST /broadcast", "POST /lock/release"}
	for _, e := range []PetEvent{
		mkPetEv("agentWaiting", "waiting", "garden 在等你回话", nil),
		mkPetEv("agentDone", "happy", "garden 干完了", nil),
		mkPetEv("alert", "worried", "记录断了：ActivityWatch 没在跑", nil),
		mkPetEv("alertCleared", "happy", "记录恢复了", nil),
	} {
		f := newFakeSink(t, nil)
		petSend(f.Client(), dshCfg(f.URL), e)
		if got := f.steps(); strings.Join(got, ",") != strings.Join(want, ",") {
			t.Fatalf("%s：%v", e.Type, got)
		}
		rs := f.seen()
		var bc map[string]any
		_ = json.Unmarshal([]byte(rs[2].body), &bc)
		if bc["text"] != e.Text || !strings.Contains(rs[2].query, "lock=lk-1") || !strings.Contains(rs[2].query, "pet=x") || !strings.Contains(rs[2].query, "surface=any") {
			t.Fatalf("broadcast：%+v", rs[2])
		}
		if !strings.Contains(rs[3].body, `"lockId":"lk-1"`) || !strings.Contains(rs[3].query, "pet=x") {
			t.Fatalf("release：%+v", rs[3])
		}
	}
}

func TestPetDshAfkDeletes(t *testing.T) { // AD-D09
	f := newFakeSink(t, nil)
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("focus", "idle", "休息一下", map[string]any{"afk": true}))
	if got := f.steps(); len(got) != 1 || got[0] != "DELETE /work-status" {
		t.Fatalf("%v", got)
	}
}

func TestPetDshNullMoodSkipsWorkStatus(t *testing.T) { // AD-D10
	f := newFakeSink(t, nil)
	pc := dshCfg(f.URL)
	pc.Moods["working"] = nil
	petSend(f.Client(), pc, mkPetEv("focus", "working", "在 garden", map[string]any{}))
	if len(f.seen()) != 0 {
		t.Fatalf("%v", f.steps())
	}
	pc.Moods = nil
	petSend(f.Client(), pc, mkPetEv("agentDone", "happy", "garden 干完了", nil))
	if got := f.steps(); strings.Join(got, ",") != "POST /lock,POST /broadcast,POST /lock/release" {
		t.Fatalf("气泡要照发：%v", got)
	}
}

func TestPetDshPetID(t *testing.T) { // AD-D11
	f := newFakeSink(t, nil)
	pc := dshCfg(f.URL)
	petSend(f.Client(), pc, mkPetEv("agentDone", "happy", "x", nil))
	if q := f.seen()[2].query; !strings.Contains(q, "pet=x") {
		t.Fatalf("配了 x：%s", q)
	}
	pc.PetID = nil
	f2 := newFakeSink(t, nil)
	pc.URL = f2.URL
	petSend(f2.Client(), pc, mkPetEv("agentDone", "happy", "x", nil))
	for _, r := range f2.seen() {
		if strings.Contains(r.path, "/lock") || strings.Contains(r.path, "/broadcast") {
			if !strings.Contains(r.query, "pet=first") {
				t.Fatalf("null 该用第一只：%+v", r)
			}
		}
	}
}

func TestPetDshBroadcastFailureReleasesLock(t *testing.T) { // AD-D12
	f := newFakeSink(t, func(r sinkReq) (int, string) {
		if strings.HasSuffix(r.path, "/broadcast") {
			return 500, ""
		}
		return 0, ""
	})
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("alert", "worried", "x", nil))
	got := f.steps()
	if len(got) != 4 || got[3] != "POST /lock/release" {
		t.Fatalf("%v", got)
	}
}

func TestPetDshBusinessFailureStops(t *testing.T) {
	f := newFakeSink(t, func(r sinkReq) (int, string) {
		if strings.HasSuffix(r.path, "/lock") {
			return 200, `{"ok":false,"reason":"locked"}`
		}
		return 0, ""
	})
	petSend(f.Client(), dshCfg(f.URL), mkPetEv("alert", "worried", "x", nil))
	if got := f.steps(); strings.Join(got, ",") != "POST /work-status,POST /lock" {
		t.Fatalf("拿不到锁不该继续：%v", got)
	}
}

func TestPetSinkConnectionRefusedOnlyLogs(t *testing.T) { // AD-D13
	f := newFakeSink(t, nil)
	pc := dshCfg(f.URL)
	f.Close()
	petSend(&http.Client{}, pc, mkPetEv("alert", "worried", "x", nil)) // 不 panic、不阻塞
	pc.Renderer = "http"
	petSend(&http.Client{}, pc, mkPetEv("alert", "worried", "x", nil))
}

func TestPetSinkFailureDoesNotRetryAndBridgeContinues(t *testing.T) { // AD-E24
	art := newFakeSink(t, func(sinkReq) (int, string) { return 500, "" })
	r := newPetRig(t, &PetConfig{Enabled: true, URL: art.URL})
	r.b.send = func(c PetConfig, e PetEvent) { petSend(art.Client(), c, e) }
	r.round(`{"agents":[]}`)
	r.awDown = true
	r.round(`{"agents":[]}`)
	r.round(`{"agents":[]}`)
	if n := len(art.seen()); n != 1 {
		t.Fatalf("500 后请求了 %d 次（只该 1 次，不重试）", n)
	}
	if r.gets != 3 {
		t.Fatalf("轮询被拖停了：%d", r.gets)
	}
}

func TestPetSinkBodyHasNoRawTitle(t *testing.T) { // AD-E22：发往美术的请求体
	art := newFakeSink(t, nil)
	r := newPetRig(t, &PetConfig{Enabled: true, URL: art.URL, Token: petStr("T")})
	r.b.send = func(c PetConfig, e PetEvent) { petSend(art.Client(), c, e) }
	r.round(`{"agents":[]}`)
	r.round(focusJSON("t", "p", "github.com"))
	r.awDown = true
	r.round(`{"agents":[]}`)
	if len(art.seen()) < 2 {
		t.Fatal("没发出东西，检查无意义")
	}
	for _, q := range art.seen() {
		for _, s := range []string{"SECRET-TITLE", "secret.example", "token=abc", "DEV"} {
			if strings.Contains(q.body, s) {
				t.Fatalf("体里有 %q：%s", s, q.body)
			}
		}
	}
}
