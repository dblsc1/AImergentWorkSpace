package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

// healthCockpit：记下每次 PUT /detector/health 的请求体；reply 是响应体，status 非 0 时按它回。
type healthCockpit struct {
	*httptest.Server
	mu     sync.Mutex
	bodies []map[string]any
	reply  string
	status int
}

func newHealthCockpit() *healthCockpit {
	c := &healthCockpit{reply: `{"restartNonce":null}`}
	c.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		var m map[string]any
		_ = json.Unmarshal(b, &m)
		c.mu.Lock()
		defer c.mu.Unlock()
		if r.Method != http.MethodPut || r.URL.Path != "/api/core/detector/health" || r.Header.Get("Authorization") != "Bearer test-device-token" || r.URL.Query().Get("deviceId") != "dev_test" {
			w.WriteHeader(400)
			return
		}
		c.bodies = append(c.bodies, m)
		if c.status != 0 {
			w.WriteHeader(c.status)
			return
		}
		io.WriteString(w, c.reply)
	}))
	return c
}

func (c *healthCockpit) n() int { c.mu.Lock(); defer c.mu.Unlock(); return len(c.bodies) }

func (c *healthCockpit) last() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.bodies[len(c.bodies)-1]
}

func (c *healthCockpit) set(reply string, status int) {
	c.mu.Lock()
	c.reply, c.status = reply, status
	c.mu.Unlock()
}

type spawnLog struct {
	calls [][]string
	err   error
}

func (s *spawnLog) spawn(argv []string) error {
	s.calls = append(s.calls, append([]string(nil), argv...))
	return s.err
}

func testReporter(t *testing.T, reach bool, sp *spawnLog) (*healthReporter, paths) {
	p := pathsIn(t.TempDir())
	h := newHealthReporter(p, http.DefaultClient)
	h.spawn = sp.spawn
	h.reachable = func(Config) bool { return reach }
	h.resolve = func() []string { return []string{"/usr/bin/aw-qt"} }
	return h, p
}

func TestAwStateMapping(t *testing.T) {
	cfg := Config{Enabled: true}
	yes, no := func() bool { return true }, func() bool { return false }
	down := fmt.Errorf("%w（x）：%w", errAWDown, errors.New("refused"))
	for _, c := range []struct {
		name  string
		cfg   Config
		err   error
		sent  bool
		reach func() bool
		want  string
	}{
		{"ok", cfg, nil, true, no, "ok"},
		{"down", cfg, down, false, yes, "unreachable"},
		{"no window", cfg, errNoWindow, false, yes, "no_window"},
		{"other error: unknown", cfg, errors.New("拉不到网页上的检测设置"), false, yes, ""},
		{"presence off, reachable", cfg, nil, false, yes, "ok"},
		{"presence off, not reachable", cfg, nil, false, no, "unreachable"},
		{"paused", Config{Enabled: true, Paused: true}, down, false, no, "paused"},
		{"disabled", Config{}, down, false, no, "disabled"},
	} {
		if got := awStateOf(c.cfg, c.err, c.sent, c.reach); got != c.want {
			t.Errorf("%s: got %q want %q", c.name, got, c.want)
		}
	}
}

func TestHealthThrottle(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	h, _ := testReporter(t, true, &spawnLog{})
	t0 := time.Now()
	h.report(cfg, "ok", t0)
	h.report(cfg, "ok", t0.Add(5*time.Second))
	h.report(cfg, "ok", t0.Add(59*time.Second))
	if ck.n() != 1 {
		t.Fatalf("unchanged state within 60s: %d PUTs", ck.n())
	}
	h.report(cfg, "unreachable", t0.Add(10*time.Second)) // 变了立刻报
	h.report(cfg, "unreachable", t0.Add(71*time.Second))
	if ck.n() != 3 {
		t.Fatalf("change + 60s: %d PUTs", ck.n())
	}
	h.report(cfg, "", t0.Add(200*time.Second)) // 说不准：不报
	if ck.n() != 3 || ck.last()["awState"] != "unreachable" {
		t.Fatal("unknown state must not be reported")
	}
}

func TestHealthNonceHandledOnceAcrossProcessRestart(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	sp := &spawnLog{}
	h, p := testReporter(t, false, sp)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt", "--x"}
	ck.set(`{"restartNonce":"abc123"}`, 0)
	t0 := time.Now()
	h.report(cfg, "unreachable", t0)
	if len(sp.calls) != 1 || !reflect.DeepEqual(sp.calls[0], cfg.AwStartCommand) {
		t.Fatalf("spawn: %v", sp.calls)
	}
	// 下一次 PUT 带结果，服务端于是不再给 nonce
	ck.set(`{"restartNonce":null}`, 0)
	h.report(cfg, "unreachable", t0.Add(5*time.Second))
	if got := ck.last()["restart"]; !reflect.DeepEqual(got, map[string]any{"nonce": "abc123", "result": "started"}) {
		t.Fatalf("restart result: %v", got)
	}
	n := ck.n()
	h.report(cfg, "unreachable", t0.Add(10*time.Second))
	h.report(cfg, "unreachable", t0.Add(70*time.Second))
	if ck.n() != n+1 {
		t.Fatal("expected only the throttled periodic PUT")
	}
	if _, ok := ck.last()["restart"]; ok {
		t.Fatal("result reported twice")
	}
	// 进程重启（新 reporter、同一个状态文件）：服务端还给同一个 nonce，不重放
	sp2 := &spawnLog{}
	h2 := newHealthReporter(p, http.DefaultClient)
	h2.spawn, h2.reachable = sp2.spawn, func(Config) bool { return false }
	ck.set(`{"restartNonce":"abc123"}`, 0)
	h2.report(cfg, "unreachable", t0.Add(time.Hour))
	if len(sp2.calls) != 0 {
		t.Fatalf("replayed: %v", sp2.calls)
	}
	// 状态文件里有最近一次的 awState（status 命令用）
	var st State
	_ = loadJSON(p.state, &st)
	if st.Health.AwState != "unreachable" || st.Health.RestartNonce != "abc123" {
		t.Fatalf("state: %+v", st.Health)
	}
}

func TestRestartResults(t *testing.T) {
	for _, c := range []struct {
		name  string
		reach bool
		cmd   []string
		def   []string
		err   error
		want  string
		calls int
	}{
		{"already running", true, nil, nil, nil, "already_running", 0},
		{"started via default", false, nil, []string{"aw-qt"}, nil, "started", 1},
		{"unsupported", false, nil, nil, nil, "unsupported", 0},
		{"spawn error", false, []string{"/nope"}, nil, errors.New("not found"), "failed", 1},
	} {
		ck := newHealthCockpit()
		sp := &spawnLog{err: c.err}
		h, _ := testReporter(t, c.reach, sp)
		h.resolve = func() []string { return c.def }
		cfg := liveConfig("", ck.URL)
		cfg.AwStartCommand = c.cmd
		ck.set(`{"restartNonce":"n1"}`, 0)
		h.report(cfg, "ok", time.Now())
		h.report(cfg, "ok", time.Now())
		got, _ := ck.last()["restart"].(map[string]any)
		if got["result"] != c.want || len(sp.calls) != c.calls {
			t.Errorf("%s: result=%v calls=%d", c.name, got, len(sp.calls))
		}
		ck.Close()
	}
}

func TestRestartAtMostOncePer30s(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	sp := &spawnLog{}
	h, _ := testReporter(t, false, sp)
	cfg := liveConfig("", ck.URL)
	t0 := time.Now()
	ck.set(`{"restartNonce":"n1"}`, 0)
	h.report(cfg, "unreachable", t0)
	ck.set(`{"restartNonce":"n2"}`, 0)
	h.report(cfg, "unreachable", t0.Add(5*time.Second)) // 带着 n1 的结果；响应里是新 nonce n2
	if len(sp.calls) != 1 {
		t.Fatalf("second nonce within 30s spawned: %v", sp.calls)
	}
	h.report(cfg, "unreachable", t0.Add(10*time.Second))
	if got := ck.last()["restart"].(map[string]any); got["nonce"] != "n2" || got["result"] != "failed" {
		t.Fatalf("throttled result: %v", got)
	}
	ck.set(`{"restartNonce":"n3"}`, 0)
	h.report(cfg, "no_window", t0.Add(31*time.Second))
	if len(sp.calls) != 2 {
		t.Fatalf("after 30s should spawn: %v", sp.calls)
	}
}

// 安全底线：服务端发来的任何东西都不会成为命令，也不会拼进命令。
func TestServerDataNeverReachesArgv(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	sp := &spawnLog{}
	h, _ := testReporter(t, false, sp)
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	evil := `"command":["/bin/sh","-c","touch /tmp/pwned"],"awStartCommand":["/bin/evil"],"argv":["x"],"cmd":"rm -rf /"`
	ck.set(`{`+evil+`,"restartNonce":"n-1_ok"}`, 0)
	h.report(cfg, "unreachable", time.Now())
	if !reflect.DeepEqual(sp.calls, [][]string{{"/opt/aw/aw-qt"}}) {
		t.Fatalf("argv: %v", sp.calls)
	}
	// 缺省命令同理：来自 resolve（本机），不含 nonce
	sp.calls = nil
	cfg.AwStartCommand = nil
	h.lastRun = time.Time{}
	ck.set(`{`+evil+`,"restartNonce":"n-2_ok"}`, 0)
	h.report(cfg, "unreachable", time.Now())
	for _, a := range sp.calls[0] {
		if strings.Contains(a, "n-2") || strings.Contains(a, "evil") || strings.Contains(a, "pwned") {
			t.Fatalf("server data in argv: %v", sp.calls)
		}
	}
	// 形状不对的 nonce 整个不理：不拉起，也不原样回报
	sp.calls = nil
	ck.set(`{"restartNonce":null}`, 0)
	h.report(cfg, "ok", time.Now().Add(30*time.Minute)) // 先把 n-2 的结果送出去
	for _, bad := range []string{"a b", "$(id)", "x;y", strings.Repeat("a", 65)} {
		b, _ := json.Marshal(map[string]string{"restartNonce": bad})
		ck.set(string(b), 0)
		h.lastRun = time.Time{}
		h.report(cfg, "unreachable", time.Now().Add(time.Hour+time.Duration(len(bad))*time.Minute))
		if _, ok := ck.last()["restart"]; ok || len(sp.calls) != 0 {
			t.Fatalf("bad nonce %q accepted", bad)
		}
	}
}

// awStartCommand 不能从网页设置进来：settings 文档里带它，照样是未知键，也不会改 Config。
func TestAwStartCommandNotFromWebSettings(t *testing.T) {
	ck := newLiveCockpit(t)
	defer ck.Close()
	ck.settings = `{"settings":{"awStartCommand":["/bin/evil"],"privacy":{"awStartCommand":["/bin/evil"]},"idle":{}}}`
	cfg := Config{CockpitURL: ck.URL, DeviceToken: "t", DeviceID: "d"}
	if err := applyRemoteSettings(&cfg, http.DefaultClient, ck.URL); err != nil {
		t.Fatal(err)
	}
	if cfg.AwStartCommand != nil {
		t.Fatalf("web settings set awStartCommand: %v", cfg.AwStartCommand)
	}
	if k := unknownKeys([]byte(`{"privacy":{"awStartCommand":["x"]}}`)); !reflect.DeepEqual(k, []string{"privacy.awStartCommand"}) {
		t.Fatalf("unknown keys: %v", k)
	}
}

func TestHealth404StopsAndFailureIsQuiet(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	h, _ := testReporter(t, true, &spawnLog{})
	ck.set("", 404)
	t0 := time.Now()
	h.report(cfg, "ok", t0)
	h.report(cfg, "unreachable", t0.Add(time.Hour))
	if ck.n() != 1 || !h.dead {
		t.Fatalf("404 should stop: n=%d dead=%v", ck.n(), h.dead)
	}
	// 失败不 panic、不置 dead、下一次变化照试
	h2, _ := testReporter(t, true, &spawnLog{})
	ck.set("", 503)
	h2.report(cfg, "ok", t0)
	h2.report(cfg, "unreachable", t0.Add(time.Second))
	if h2.dead || !h2.failed || ck.n() != 3 {
		t.Fatalf("503: dead=%v failed=%v n=%d", h2.dead, h2.failed, ck.n())
	}
}

// 健康报告失败（服务端不通）不影响在场心跳本身：presenceLoop 的两步互相独立，这里验心跳照发、报告 503。
func TestHealthFailureDoesNotBreakPresence(t *testing.T) {
	now := time.Now()
	aw := fakeAWWeb(t, []awEvent{{Timestamp: now.Add(-time.Minute), Duration: 55, Data: map[string]any{"app": "code", "title": "x"}}}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	defer ck.Close()
	remotePresence.Store(nil)
	cfg := liveConfig(aw.URL, ck.URL)
	ck.respond = func(p string) (int, string) {
		if strings.HasSuffix(p, "/detector/health") {
			return 503, ""
		}
		return 0, ""
	}
	hc := &healthReporter{hc: http.DefaultClient, state: filepath.Join(t.TempDir(), "s.json"), reachable: func(Config) bool { return true }}
	sent, err := presenceBeat(cfg, http.DefaultClient, now)
	hc.report(cfg, awStateOf(cfg, err, sent, func() bool { return true }), now)
	if !sent || err != nil || len(ck.posts("/activity/presence")) != 1 {
		t.Fatalf("presence broke: sent=%v err=%v", sent, err)
	}
	if !hc.failed {
		t.Fatal("health failure not noticed")
	}
}

func TestDefaultAwStart(t *testing.T) {
	lp := func(string) (string, error) { return "/usr/bin/aw-qt", nil }
	nolp := func(string) (string, error) { return "", errors.New("no") }
	if got := defaultAwStart("linux", os.Getenv, lp); !reflect.DeepEqual(got, []string{"/usr/bin/aw-qt"}) {
		t.Fatal(got)
	}
	if defaultAwStart("linux", os.Getenv, nolp) != nil {
		t.Fatal("linux without aw-qt should be nil")
	}
	if got := defaultAwStart("darwin", os.Getenv, nolp); !reflect.DeepEqual(got, []string{"open", "-a", "ActivityWatch"}) {
		t.Fatal(got)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "Programs", "ActivityWatch", "aw-qt.exe")
	if defaultAwStart("windows", func(string) string { return dir }, nolp) != nil {
		t.Fatal("windows without exe should be nil")
	}
	_ = os.MkdirAll(filepath.Dir(exe), 0o755)
	_ = os.WriteFile(exe, nil, 0o755)
	if got := defaultAwStart("windows", func(string) string { return dir }, nolp); !reflect.DeepEqual(got, []string{exe}) {
		t.Fatal(got)
	}
}

func TestAutoStart(t *testing.T) {
	cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
	t0 := time.Now()
	step := func(h *healthReporter, st string, at time.Duration) { h.auto(cfg, st, t0.Add(at)) }

	// 一次抖动不拉，连续两次才拉；中间恢复会清零
	sp := &spawnLog{}
	h, _ := testReporter(t, false, sp)
	step(h, "unreachable", 0)
	step(h, "ok", time.Second)
	step(h, "unreachable", 2*time.Second)
	if len(sp.calls) != 0 {
		t.Fatalf("blip spawned: %v", sp.calls)
	}
	step(h, "unreachable", 7*time.Second)
	if len(sp.calls) != 1 || h.autoState != "started" || !reflect.DeepEqual(sp.calls[0], cfg.AwStartCommand) {
		t.Fatalf("calls=%v state=%q", sp.calls, h.autoState)
	}

	// 与按钮共用 30 秒：刚自动拉过，按钮拉不了；反过来也一样
	if got := h.start(cfg.AwStartCommand, t0.Add(10*time.Second)); got != "failed" || len(sp.calls) != 1 {
		t.Fatalf("button within 30s: %q", got)
	}
	sp2 := &spawnLog{}
	h2, _ := testReporter(t, false, sp2)
	h2.start(cfg.AwStartCommand, t0)
	step(h2, "unreachable", time.Second)
	step(h2, "unreachable", 6*time.Second)
	if len(sp2.calls) != 1 {
		t.Fatalf("auto within 30s of button spawned: %v", sp2.calls)
	}

	// 连拉 3 次没回来：第 4 个可拉的时刻停手；ActivityWatch 回来后恢复
	sp3 := &spawnLog{}
	h3, _ := testReporter(t, false, sp3)
	for i := 0; i < 12; i++ {
		step(h3, "unreachable", time.Duration(i)*31*time.Second)
	}
	if len(sp3.calls) != 3 || !h3.gaveUp || h3.autoState != "gave_up" {
		t.Fatalf("calls=%d gaveUp=%v state=%q", len(sp3.calls), h3.gaveUp, h3.autoState)
	}
	step(h3, "ok", 20*time.Minute)
	step(h3, "unreachable", 21*time.Minute)
	step(h3, "unreachable", 22*time.Minute)
	if len(sp3.calls) != 4 || h3.gaveUp {
		t.Fatalf("should resume after reachable: calls=%d", len(sp3.calls))
	}

	// 关掉 / paused / disabled：从不拉
	off := false
	for _, c := range []struct {
		name string
		cfg  Config
		st   string
	}{
		{"off", Config{Enabled: true, AwAutoStart: &off, AwStartCommand: cfg.AwStartCommand}, "unreachable"},
		{"paused", Config{Enabled: true, Paused: true, AwStartCommand: cfg.AwStartCommand}, "paused"},
		{"disabled", Config{AwStartCommand: cfg.AwStartCommand}, "disabled"},
	} {
		sp := &spawnLog{}
		h, _ := testReporter(t, false, sp)
		for i := 0; i < 5; i++ {
			h.auto(c.cfg, c.st, t0.Add(time.Duration(i)*time.Minute))
		}
		if len(sp.calls) != 0 {
			t.Errorf("%s spawned", c.name)
		}
		if c.name == "off" && h.autoState != "off" {
			t.Errorf("off state %q", h.autoState)
		}
	}

	// 没有可运行的命令：不拉，报 unsupported
	sp4 := &spawnLog{}
	h4, _ := testReporter(t, false, sp4)
	h4.resolve = func() []string { return nil }
	for i := 0; i < 4; i++ {
		h4.auto(Config{Enabled: true}, "unreachable", t0.Add(time.Duration(i)*time.Minute))
	}
	if len(sp4.calls) != 0 || h4.autoState != "unsupported" {
		t.Fatalf("calls=%v state=%q", sp4.calls, h4.autoState)
	}
}

// 自动路径的状态随健康报告上去，且服务端的数据照样到不了 argv。
func TestAutoStartReportedAndServerDataNeverReachesArgv(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	sp := &spawnLog{}
	h, _ := testReporter(t, false, sp)
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	ck.set(`{"command":["/bin/evil"],"awStartCommand":["/bin/evil"],"autoStart":"x","restartNonce":null}`, 0)
	t0 := time.Now()
	for i := 0; i < 2; i++ {
		at := t0.Add(time.Duration(i) * 5 * time.Second)
		h.auto(cfg, "unreachable", at)
		h.report(cfg, "unreachable", at)
	}
	if !reflect.DeepEqual(sp.calls, [][]string{{"/opt/aw/aw-qt"}}) {
		t.Fatalf("argv: %v", sp.calls)
	}
	if ck.last()["autoStart"] != "started" || ck.last()["awState"] != "unreachable" {
		t.Fatalf("body: %v", ck.last())
	}
	// awAutoStart 也不能从网页设置进来
	cs := newLiveCockpit(t)
	defer cs.Close()
	cs.settings = `{"settings":{"awAutoStart":false,"privacy":{},"idle":{}}}`
	c2 := Config{CockpitURL: cs.URL, DeviceToken: "t", DeviceID: "d"}
	if err := applyRemoteSettings(&c2, http.DefaultClient, cs.URL); err != nil || c2.AwAutoStart != nil {
		t.Fatalf("web settings set awAutoStart: %v %v", err, c2.AwAutoStart)
	}
}
