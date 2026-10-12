package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
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
	onPut  func() // 请求到了、回复之前调（模拟「发送期间别处有了新结果」）
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
		if c.onPut != nil {
			c.onPut()
		}
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
	mu    sync.Mutex
	calls [][]string
	err   error
	wait  <-chan int // 子进程退出码；nil = 一直不退出
	onRun func()     // 拉起时的副作用（比如让假的 ActivityWatch 起来）
}

func (s *spawnLog) spawn(argv []string, _ string) (<-chan int, error) {
	s.mu.Lock()
	s.calls = append(s.calls, append([]string(nil), argv...))
	s.mu.Unlock()
	if s.onRun != nil {
		s.onRun()
	}
	return s.wait, s.err
}

func (s *spawnLog) n() int { s.mu.Lock(); defer s.mu.Unlock(); return len(s.calls) }

func (s *spawnLog) all() [][]string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([][]string(nil), s.calls...)
}

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) now() time.Time      { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *fakeClock) add(d time.Duration) { c.mu.Lock(); c.t = c.t.Add(d); c.mu.Unlock() }

type env struct {
	h     *healthReporter
	p     paths
	clk   *fakeClock
	reach *atomic.Bool
	sp    *spawnLog
}

// 假时钟、假拉起、假的「ActivityWatch 连不连得上」；goos 缺省 darwin，免得测试机有没有 DISPLAY 影响结果。
func testEnv(t *testing.T, reach bool, sp *spawnLog) *env {
	p := pathsIn(t.TempDir())
	h := newHealthReporter(p, http.DefaultClient)
	e := &env{h: h, p: p, clk: &fakeClock{t: time.Now()}, reach: &atomic.Bool{}, sp: sp}
	e.reach.Store(reach)
	h.clock, h.goos = e.clk.now, "darwin"
	h.spawn = sp.spawn
	h.reachable = func(Config) bool { return e.reach.Load() }
	h.resolve = func() []string { return []string{"/usr/bin/aw-qt"} }
	h.probeWait, h.probeEvery = 40*time.Millisecond, 5*time.Millisecond
	return e
}

func (e *env) result() *restartResult { e.h.mu.Lock(); defer e.h.mu.Unlock(); return e.h.result }

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	for i := 0; i < 400; i++ {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timeout: %s", what)
}

func (e *env) waitResult(t *testing.T) restartResult {
	waitFor(t, "restart result", func() bool { return e.result() != nil })
	return *e.result()
}

func (e *env) lastStart(t *testing.T) *startRecord {
	var st State
	_ = loadJSON(e.p.state, &st)
	return st.Health.LastStart
}

func (e *env) settle() { time.Sleep(150 * time.Millisecond) } // 让在途的探测走完（probeWait 40ms）

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
	e := testEnv(t, true, &spawnLog{})
	h := e.h
	h.report(cfg, "ok")
	e.clk.add(5 * time.Second)
	h.report(cfg, "ok")
	e.clk.add(9 * time.Second)
	h.report(cfg, "ok")
	if ck.n() != 1 {
		t.Fatalf("unchanged state within 15s: %d PUTs", ck.n())
	}
	e.clk.add(time.Second)
	h.report(cfg, "ok")          // 15 秒到了
	h.report(cfg, "unreachable") // 变了立刻报
	if ck.n() != 3 {
		t.Fatalf("15s + change: %d PUTs", ck.n())
	}
	e.clk.add(time.Minute)
	h.report(cfg, "") // 说不准：不报
	if ck.n() != 3 || ck.last()["awState"] != "unreachable" {
		t.Fatal("unknown state must not be reported")
	}
}

func TestNonceHandledOnceAcrossProcessRestart(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt", "--x"}
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	sp.onRun = func() { e.reach.Store(true) } // 拉起后接口起来
	ck.set(`{"restartNonce":"abc123"}`, 0)
	e.h.report(cfg, "unreachable")
	if r := e.waitResult(t); r.result != "started" || r.nonce != "abc123" {
		t.Fatalf("result: %+v", r)
	}
	if !reflect.DeepEqual(sp.all(), [][]string{cfg.AwStartCommand}) {
		t.Fatalf("spawn: %v", sp.all())
	}
	ck.set(`{"restartNonce":null}`, 0)
	e.clk.add(time.Second)
	e.h.report(cfg, "ok") // 结果在下一次 PUT 里带上去
	if got := ck.last()["restart"]; !reflect.DeepEqual(got, map[string]any{"nonce": "abc123", "result": "started"}) {
		t.Fatalf("restart result: %v", got)
	}
	n := ck.n()
	e.h.report(cfg, "ok")
	if ck.n() != n {
		t.Fatal("result reported twice")
	}
	// 进程重启（新 reporter、同一个状态文件）：服务端还给同一个 nonce，不重放
	sp2 := &spawnLog{}
	e2 := testEnv(t, false, sp2)
	e2.h.state = e.p.state
	ck.set(`{"restartNonce":"abc123"}`, 0)
	e2.h.report(cfg, "unreachable")
	e2.settle()
	if sp2.n() != 0 || e2.result() != nil {
		t.Fatalf("replayed: %v %v", sp2.all(), e2.result())
	}
	var st State
	_ = loadJSON(e.p.state, &st)
	if st.Health.RestartNonce != "abc123" {
		t.Fatalf("state: %+v", st.Health)
	}
}

func TestRestartResultsAndReasons(t *testing.T) {
	cfg := Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}
	exit := func(c int) <-chan int { ch := make(chan int, 1); ch <- c; return ch }
	for _, c := range []struct {
		name         string
		reach, upNow bool
		sp           *spawnLog
		goos         string
		def          []string
		cfgCmd       []string
		want, reason string
	}{
		{"already running", true, false, &spawnLog{}, "darwin", nil, cfg.AwStartCommand, "already_running", ""},
		{"started", false, true, &spawnLog{}, "darwin", nil, cfg.AwStartCommand, "started", ""},
		{"started via default", false, true, &spawnLog{}, "darwin", []string{"aw-qt"}, nil, "started", ""},
		{"unsupported", false, false, &spawnLog{}, "darwin", nil, nil, "unsupported", ""},
		{"not found", false, false, &spawnLog{err: fmt.Errorf("x: %w", exec.ErrNotFound)}, "darwin", nil, cfg.AwStartCommand, "failed", "not_found"},
		{"spawn error", false, false, &spawnLog{err: errors.New("permission denied")}, "darwin", nil, cfg.AwStartCommand, "failed", "spawn_error"},
		{"exited early", false, false, &spawnLog{wait: exit(3)}, "darwin", nil, cfg.AwStartCommand, "failed", "exited_early"},
		{"exit 0 but never up", false, false, &spawnLog{wait: exit(0)}, "darwin", nil, cfg.AwStartCommand, "failed", "not_up"},
		{"not up", false, false, &spawnLog{}, "darwin", nil, cfg.AwStartCommand, "failed", "not_up"},
		{"no display", false, false, &spawnLog{}, "linux", nil, cfg.AwStartCommand, "failed", "no_display"},
	} {
		e := testEnv(t, c.reach, c.sp)
		if c.upNow {
			c.sp.onRun = func() { e.reach.Store(true) }
		}
		e.h.goos, e.h.getenv = c.goos, func(string) string { return "" }
		e.h.resolve = func() []string { return c.def }
		cc := Config{AwStartCommand: c.cfgCmd}
		e.h.restart(cc, "n1")
		r := e.waitResult(t)
		if r.result != c.want || r.reason != c.reason || r.nonce != "n1" {
			t.Errorf("%s: %+v", c.name, r)
		}
		wantSpawn := 0
		if c.want == "started" || c.reason == "not_found" || c.reason == "spawn_error" || c.reason == "exited_early" || c.reason == "not_up" {
			wantSpawn = 1
		}
		if c.sp.n() != wantSpawn {
			t.Errorf("%s: spawn calls %d", c.name, c.sp.n())
		}
		if c.want == "failed" {
			e.settle()
			if ls := e.lastStart(t); ls == nil || ls.Outcome != "failed" || ls.Reason != c.reason || ls.Trigger != "button" {
				t.Errorf("%s: record %+v", c.name, ls)
			}
		}
	}
	// 退出码记进记录
	sp := &spawnLog{wait: exit(3)}
	e := testEnv(t, false, sp)
	e.h.restart(cfg, "n1")
	e.waitResult(t)
	e.settle()
	if ls := e.lastStart(t); ls.ExitCode == nil || *ls.ExitCode != 3 || !reflect.DeepEqual(ls.Argv, cfg.AwStartCommand) {
		t.Fatalf("record %+v", ls)
	}
	// 有 DISPLAY 就不是 no_display
	e = testEnv(t, false, &spawnLog{})
	e.h.goos, e.h.getenv = "linux", func(k string) string { return map[string]string{"WAYLAND_DISPLAY": "wayland-0"}[k] }
	e.h.restart(cfg, "n1")
	if r := e.waitResult(t); r.reason != "not_up" {
		t.Fatalf("with WAYLAND_DISPLAY: %+v", r)
	}
}

// 状态文件写不进去：nonce 记不下来就不拉，结果 failed / state_unwritable。
func TestRestartNotSpawnedWhenNonceNotPersisted(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	blocker := filepath.Join(t.TempDir(), "file")
	_ = os.WriteFile(blocker, nil, 0o600)
	e.h.state = filepath.Join(blocker, "state.json") // 父路径是个普通文件：建不出来
	e.h.restart(Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}, "n1")
	r := e.waitResult(t)
	if r.result != "failed" || r.reason != "state_unwritable" || sp.n() != 0 {
		t.Fatalf("%+v spawn=%d", r, sp.n())
	}
	e.h.restart(Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}, "n1") // 内存里也记着：不重复处理
	e.settle()
	if sp.n() != 0 {
		t.Fatal("spawned on replay")
	}
}

// 30 秒内到的 nonce 不回 failed：留着不处理，限频过了再处理。
func TestNonceInsideCooldownIsLeftUnhandled(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}
	e.h.restart(cfg, "n1")
	e.waitResult(t)
	e.clk.add(5 * time.Second)
	e.h.restart(cfg, "n2")
	e.settle()
	var st State
	_ = loadJSON(e.p.state, &st)
	if sp.n() != 1 || st.Health.RestartNonce != "n1" || e.result().nonce != "n1" {
		t.Fatalf("n2 should be untouched: spawn=%d state=%q result=%+v", sp.n(), st.Health.RestartNonce, e.result())
	}
	e.clk.add(30 * time.Second)
	e.h.restart(cfg, "n2")
	waitFor(t, "n2 handled", func() bool { r := e.result(); return r != nil && r.nonce == "n2" })
	if sp.n() != 2 {
		t.Fatalf("spawn after cooldown: %d", sp.n())
	}
}

// 冷却用动手那一刻的时钟：拿到 nonce 之后的网络探测很慢，限频照这一刻算，也照这一刻记。
func TestCooldownUsesClockAtSpawn(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}
	t0 := e.clk.now()
	e.h.lastRun = t0.Add(-10 * time.Second)
	e.h.reachable = func(Config) bool { e.clk.add(25 * time.Second); return false } // 慢请求：25 秒过去了
	e.h.restart(cfg, "n1")
	waitFor(t, "spawned", func() bool { return sp.n() == 1 })
	e.h.mu.Lock()
	got := e.h.lastRun
	e.h.mu.Unlock()
	if !got.Equal(t0.Add(25 * time.Second)) {
		t.Fatalf("lastRun %v, want clock at spawn %v", got.Sub(t0), 25*time.Second)
	}
}

// 按钮在暂停 / 没开同步时也能用；自动拉起这时从不动。
func TestButtonWorksWhenPausedOrDisabledAutoDoesNot(t *testing.T) {
	for _, c := range []Config{{Paused: true, Enabled: true}, {}} {
		c.AwStartCommand = []string{"/opt/aw/aw-qt"}
		sp := &spawnLog{}
		e := testEnv(t, false, sp)
		for i := 0; i < 5; i++ {
			e.clk.add(time.Minute)
			e.h.auto(c, awStateOf(c, errAWDown, false, func() bool { return false }))
		}
		e.settle()
		if sp.n() != 0 {
			t.Fatalf("auto spawned with %+v", c)
		}
		e.h.restart(c, "n1")
		if r := e.waitResult(t); r.result != "failed" || sp.n() != 1 { // 假 AW 一直没起来：failed/not_up，但确实拉了
			t.Fatalf("button %+v spawn=%d", r, sp.n())
		}
	}
}

// 安全底线：服务端发来的任何东西都不会成为命令，也不会拼进命令；本机的输出和错误串也不会发给服务端。
func TestServerDataNeverReachesArgvAndLocalTextNeverLeaves(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	sp := &spawnLog{err: errors.New("SECRET-ERR-/home/me/x")}
	e := testEnv(t, false, sp)
	dir := filepath.Dir(e.p.state)
	_ = os.WriteFile(filepath.Join(dir, "aw-start.log"), []byte("SECRET-LOG-LINE\n"), 0o600)
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	evil := `"command":["/bin/sh","-c","touch /tmp/pwned"],"awStartCommand":["/bin/evil"],"argv":["x"],"cmd":"rm -rf /"`
	ck.set(`{`+evil+`,"restartNonce":"n-1_ok"}`, 0)
	e.h.report(cfg, "unreachable")
	e.waitResult(t)
	e.clk.add(time.Minute)
	e.h.report(cfg, "unreachable") // 带着失败结果
	// 自动路径
	for i := 0; i < 2; i++ {
		e.h.auto(cfg, "unreachable")
	}
	e.settle()
	for _, a := range sp.all() {
		if !reflect.DeepEqual(a, []string{"/opt/aw/aw-qt"}) {
			t.Fatalf("argv: %v", a)
		}
	}
	// 形状不对的 nonce 整个不理
	n := sp.n()
	for _, bad := range []string{"a b", "$(id)", "x;y", strings.Repeat("a", 65)} {
		b, _ := json.Marshal(map[string]string{"restartNonce": bad})
		ck.set(string(b), 0)
		e.clk.add(time.Minute)
		e.h.report(cfg, "unreachable")
	}
	e.settle()
	if sp.n() != n {
		t.Fatal("bad nonce spawned")
	}
	ck.mu.Lock()
	defer ck.mu.Unlock()
	allowed := map[string]bool{"awState": true, "autoStart": true, "autoReason": true, "restart": true, "logUpload": true}
	for _, b := range ck.bodies {
		raw, _ := json.Marshal(b)
		if strings.Contains(string(raw), "SECRET") || strings.Contains(string(raw), "/opt/aw") {
			t.Fatalf("local text leaked: %s", raw)
		}
		for k := range b {
			if !allowed[k] {
				t.Fatalf("unexpected key %q", k)
			}
		}
	}
}

// awStartCommand / awAutoStart 不能从网页设置进来。
func TestAwKeysNotFromWebSettings(t *testing.T) {
	ck := newLiveCockpit(t)
	defer ck.Close()
	ck.settings = `{"settings":{"awStartCommand":["/bin/evil"],"awAutoStart":false,"privacy":{"awStartCommand":["/bin/evil"]},"idle":{}}}`
	cfg := Config{CockpitURL: ck.URL, DeviceToken: "t", DeviceID: "d"}
	if err := applyRemoteSettings(&cfg, http.DefaultClient, ck.URL); err != nil {
		t.Fatal(err)
	}
	if cfg.AwStartCommand != nil || cfg.AwAutoStart != nil {
		t.Fatalf("web settings set local-only keys: %v %v", cfg.AwStartCommand, cfg.AwAutoStart)
	}
	if k := unknownKeys([]byte(`{"privacy":{"awStartCommand":["x"]}}`)); !reflect.DeepEqual(k, []string{"privacy.awStartCommand"}) {
		t.Fatalf("unknown keys: %v", k)
	}
}

func TestHealth404StopsAndFailureIsQuiet(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	e := testEnv(t, true, &spawnLog{})
	ck.set("", 404)
	e.h.report(cfg, "ok")
	e.h.report(cfg, "unreachable")
	if ck.n() != 1 || !e.h.dead {
		t.Fatalf("404 should stop: n=%d dead=%v", ck.n(), e.h.dead)
	}
	e2 := testEnv(t, true, &spawnLog{})
	ck.set("", 503)
	e2.h.report(cfg, "ok")
	e2.h.report(cfg, "unreachable")
	if e2.h.dead || !e2.h.failed || ck.n() != 2 { // 失败后按 15 秒退避：状态变了也先不立刻重发
		t.Fatalf("503: dead=%v failed=%v n=%d", e2.h.dead, e2.h.failed, ck.n())
	}
	e2.clk.add(15 * time.Second)
	e2.h.report(cfg, "unreachable")
	if ck.n() != 3 {
		t.Fatalf("retry after backoff: n=%d", ck.n())
	}
}

// 健康服务器卡住：observe 立刻返回（心跳线程不等它），而且心跳照常按间隔发。
func TestHangingHealthServerDoesNotDelayPresence(t *testing.T) {
	now := time.Now()
	aw := fakeAWWeb(t, []awEvent{{Timestamp: now.Add(-time.Hour), Duration: 3600, Data: map[string]any{"app": "code", "title": "x"}}}, nil, nil)
	defer aw.Close()
	ck := newLiveCockpit(t)
	release := make(chan struct{})
	defer ck.Close()
	defer close(release)
	ck.respond = func(p string) (int, string) {
		if strings.HasSuffix(p, "/detector/health") {
			<-release
		}
		return 0, ""
	}
	remotePresence.Store(nil)
	dir, _ := os.MkdirTemp("", "hang") // 心跳循环收不到停止信号、测试结束后还会写这里：不用 t.TempDir（它的清理会撞上）
	p := pathsIn(dir)
	cfg := liveConfig(aw.URL, ck.URL)
	cfg.PresenceSeconds = 5
	if err := saveJSON(p.config, cfg); err != nil {
		t.Fatal(err)
	}
	go presenceLoop(p, &http.Client{Timeout: 15 * time.Second})
	// 第一拍 + 第二拍（间隔 5 秒）都得在健康 PUT 卡着的时候发出来；同步实现要等 15 秒超时
	waitDeadline := time.Now().Add(9 * time.Second)
	for time.Now().Before(waitDeadline) && len(ck.posts("/activity/presence")) < 2 {
		time.Sleep(100 * time.Millisecond)
	}
	if n := len(ck.posts("/activity/presence")); n < 2 {
		t.Fatalf("presence beats while health hangs: %d", n)
	}
	// observe 本身不阻塞
	e := testEnv(t, true, &spawnLog{})
	go e.h.loop()
	start := time.Now()
	for i := 0; i < 3; i++ {
		e.h.observe(cfg, "ok")
	}
	if time.Since(start) > time.Second {
		t.Fatal("observe blocked")
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

	// 一次抖动不拉，连续两次才拉；探测到接口起来了才算 started
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	sp.onRun = func() { e.reach.Store(true) }
	e.h.auto(cfg, "unreachable")
	e.h.auto(cfg, "ok")
	e.h.auto(cfg, "unreachable")
	if sp.n() != 0 {
		t.Fatalf("blip spawned: %v", sp.all())
	}
	e.h.auto(cfg, "unreachable")
	waitFor(t, "auto started", func() bool { e.h.mu.Lock(); defer e.h.mu.Unlock(); return e.h.autoState == "started" })
	if sp.n() != 1 || !reflect.DeepEqual(sp.all()[0], cfg.AwStartCommand) {
		t.Fatalf("calls=%v", sp.all())
	}

	// 与按钮共用 30 秒
	e.clk.add(10 * time.Second)
	e.h.restart(cfg, "n1")
	e.settle()
	if sp.n() != 1 {
		t.Fatal("button within 30s of auto spawned")
	}
	sp2 := &spawnLog{}
	e2 := testEnv(t, false, sp2)
	e2.h.restart(cfg, "n1")
	e2.waitResult(t)
	e2.clk.add(5 * time.Second)
	e2.h.auto(cfg, "unreachable")
	e2.h.auto(cfg, "unreachable")
	e2.settle()
	if sp2.n() != 1 {
		t.Fatalf("auto within 30s of button spawned: %d", sp2.n())
	}

	// 拉起了但接口没起来 = 失败；连 3 次失败后停手（带原因），AW 回来后恢复
	sp3 := &spawnLog{}
	e3 := testEnv(t, false, sp3)
	for i := 0; i < 8; i++ {
		e3.clk.add(31 * time.Second)
		e3.h.auto(cfg, "unreachable")
		e3.settle()
	}
	e3.h.mu.Lock()
	gaveUp, st, why := e3.h.gaveUp, e3.h.autoState, e3.h.autoReason
	e3.h.mu.Unlock()
	if sp3.n() != 3 || !gaveUp || st != "gave_up" || why != "not_up" {
		t.Fatalf("calls=%d gaveUp=%v state=%q reason=%q", sp3.n(), gaveUp, st, why)
	}
	e3.h.auto(cfg, "ok")
	e3.clk.add(61 * time.Second)
	e3.h.auto(cfg, "ok") // 连续连得上超过 60 秒才算恢复
	e3.reach.Store(false)
	for i := 0; i < 2; i++ {
		e3.clk.add(31 * time.Second)
		e3.h.auto(cfg, "unreachable")
	}
	e3.settle()
	if sp3.n() != 4 {
		t.Fatalf("should resume after reachable: calls=%d", sp3.n())
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
		e := testEnv(t, false, sp)
		for i := 0; i < 5; i++ {
			e.clk.add(time.Minute)
			e.h.auto(c.cfg, c.st)
		}
		e.settle()
		if sp.n() != 0 {
			t.Errorf("%s spawned", c.name)
		}
		if c.name == "off" && e.h.autoState != "off" {
			t.Errorf("off state %q", e.h.autoState)
		}
	}

	// 没有可运行的命令：不拉，报 unsupported
	sp4 := &spawnLog{}
	e4 := testEnv(t, false, sp4)
	e4.h.resolve = func() []string { return nil }
	for i := 0; i < 4; i++ {
		e4.clk.add(time.Minute)
		e4.h.auto(Config{Enabled: true}, "unreachable")
	}
	if sp4.n() != 0 || e4.h.autoState != "unsupported" {
		t.Fatalf("calls=%v state=%q", sp4.all(), e4.h.autoState)
	}

	// 没有桌面会话：不拉，3 次后停手，原因 no_display
	sp5 := &spawnLog{}
	e5 := testEnv(t, false, sp5)
	e5.h.goos, e5.h.getenv = "linux", func(string) string { return "" }
	for i := 0; i < 6; i++ {
		e5.clk.add(31 * time.Second)
		e5.h.auto(cfg, "unreachable")
		e5.settle()
	}
	if sp5.n() != 0 || e5.h.autoState != "gave_up" || e5.h.autoReason != "no_display" {
		t.Fatalf("no display: calls=%d state=%q reason=%q", sp5.n(), e5.h.autoState, e5.h.autoReason)
	}
}

// 自动拉起的状态与原因随健康报告上去。
func TestAutoStartReported(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	for i := 0; i < 2; i++ {
		e.h.auto(cfg, "unreachable")
	}
	waitFor(t, "auto failed", func() bool { e.h.mu.Lock(); defer e.h.mu.Unlock(); return e.h.autoReason != "" })
	e.h.report(cfg, "unreachable")
	if ck.last()["autoReason"] != "not_up" || ck.last()["autoStart"] != "idle" || ck.last()["awState"] != "unreachable" {
		t.Fatalf("body: %v", ck.last())
	}
}

// 真的拉一个小命令（测试自己的 argv，不是产品用 shell）：输出进 aw-start.log、权限 0600、轮转成 .1、退出码给出来。
func TestSpawnDetachedWritesLogAndRotates(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("用 sh")
	}
	logPath := filepath.Join(t.TempDir(), "aw-start.log")
	run := func(script string) int {
		ch, err := spawnDetached([]string{"sh", "-c", script}, logPath)
		if err != nil {
			t.Fatal(err)
		}
		select {
		case c := <-ch:
			return c
		case <-time.After(5 * time.Second):
			t.Fatal("child did not exit")
		}
		return -1
	}
	if c := run("echo first; echo oops >&2; exit 3"); c != 3 {
		t.Fatalf("exit %d", c)
	}
	b, _ := os.ReadFile(logPath)
	if !strings.Contains(string(b), "first") || !strings.Contains(string(b), "oops") {
		t.Fatalf("log: %q", b)
	}
	if fi, _ := os.Stat(logPath); fi.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", fi.Mode())
	}
	run("echo second")
	cur, _ := os.ReadFile(logPath)
	prev, _ := os.ReadFile(logPath + ".1")
	if strings.Contains(string(cur), "first") || !strings.Contains(string(cur), "second") || !strings.Contains(string(prev), "first") {
		t.Fatalf("rotation: cur=%q prev=%q", cur, prev)
	}
	if _, err := spawnDetached([]string{"/nonexistent/aw-qt"}, logPath); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("missing binary: %v", err)
	}
}

// 整条链：真拉一个马上退出的命令 → exited_early + 退出码 + 记录；status 输出里有记录和日志末尾。
func TestStatusShowsLastStartAndLogTail(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("用 sh")
	}
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	e.h.spawn = spawnDetached
	e.h.probeWait = 2 * time.Second
	var lines []string
	for i := 1; i <= 25; i++ {
		lines = append(lines, fmt.Sprintf("echo line%02d", i))
	}
	cfg := Config{AwStartCommand: []string{"sh", "-c", strings.Join(lines, "; ") + "; exit 3"}}
	e.h.restart(cfg, "n1")
	if r := e.waitResult(t); r.result != "failed" || r.reason != "exited_early" {
		t.Fatalf("%+v", r)
	}
	e.settle()
	var out strings.Builder
	printLastStart(&out, e.lastStart(t), e.h.logPath)
	s := out.String()
	for _, want := range []string{"网页上的「重启 ActivityWatch」按钮", "程序启动后马上退出了", "退出码  3", "aw-start.log", "| line25", "| line06", "看下面的输出"} {
		if !strings.Contains(s, want) {
			t.Errorf("status lacks %q:\n%s", want, s)
		}
	}
	if strings.Contains(s, "| line05") {
		t.Errorf("more than 20 lines:\n%s", s)
	}
}

// 状态文件的读—改—写：所有写者经 updateState（一把锁），整份原子落盘（临时文件 + rename）。-race 下并发写不丢字段、读到的永远是完整 JSON。
func TestStateConcurrentWriters(t *testing.T) {
	p := pathsIn(t.TempDir())
	var wg sync.WaitGroup
	for w := 0; w < 4; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < 40; i++ {
				switch w {
				case 0: // 同步一轮的回写（照 runOnce）
					st := State{Cursor: time.Unix(int64(i), 0)}
					_ = updateState(p.state, func(s *State) { ag, hs := s.Agents, s.Health; *s = st; s.Agents, s.Health = ag, hs })
				case 1: // 状态文件桥
					_ = updateState(p.state, func(s *State) { s.Agents = map[string]*agentRun{fmt.Sprint(i): {}} })
				case 2: // 健康报告
					_ = updateState(p.state, func(s *State) { s.Health.RestartNonce = fmt.Sprint(i) })
				default: // 读者
					var st State
					if err := loadJSON(p.state, &st); err != nil && !errors.Is(err, os.ErrNotExist) {
						t.Errorf("torn read: %v", err)
					}
				}
			}
		}(w)
	}
	wg.Wait()
	var st State
	if err := loadJSON(p.state, &st); err != nil || st.Health.RestartNonce != "39" || len(st.Agents) != 1 || st.Cursor.Unix() != 39 {
		t.Fatalf("lost update: %v %+v", err, st)
	}
}

// 一闪一灭的 ActivityWatch：每次只亮一拍就死，不能每次都把「已拉起几次」清零（评审里模拟过一小时拉了 90 次）。
func TestFlappingActivityWatchGivesUp(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
	for i := 0; i < 720; i++ { // 一小时，每 5 秒一拍：亮 1 拍、死 5 拍
		e.clk.add(5 * time.Second)
		if i%6 == 0 {
			e.h.auto(cfg, "ok")
		} else {
			e.h.auto(cfg, "unreachable")
		}
	}
	e.settle()
	e.h.mu.Lock()
	defer e.h.mu.Unlock()
	if sp.n() > autoMaxTries || !e.h.gaveUp || e.h.autoState != "gave_up" {
		t.Fatalf("spawns=%d gaveUp=%v state=%q", sp.n(), e.h.gaveUp, e.h.autoState)
	}
}

// 被 30 秒限频挡住的拍既不记入尝试次数，也不会提前触发「停手」。
func TestRateLimitedBeatsDoNotConsumeTries(t *testing.T) {
	cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	e.h.lastRun = e.clk.now()
	for i := 0; i < 10; i++ {
		e.h.auto(cfg, "unreachable")
	}
	e.h.mu.Lock()
	tries := e.h.tries
	e.h.mu.Unlock()
	if sp.n() != 0 || tries != 0 {
		t.Fatalf("rate-limited: spawns=%d tries=%d", sp.n(), tries)
	}
	// 已经 3 次了，但限频没过：先别宣布停手（过了限频才判）
	e.h.mu.Lock()
	e.h.tries = autoMaxTries
	e.h.mu.Unlock()
	e.h.auto(cfg, "unreachable")
	e.h.mu.Lock()
	gaveUp := e.h.gaveUp
	e.h.mu.Unlock()
	if gaveUp {
		t.Fatal("gave up inside the cooldown")
	}
	e.clk.add(31 * time.Second)
	e.h.auto(cfg, "unreachable")
	e.h.mu.Lock()
	defer e.h.mu.Unlock()
	if !e.h.gaveUp || sp.n() != 0 {
		t.Fatalf("should give up after cooldown without spawning: gaveUp=%v spawns=%d", e.h.gaveUp, sp.n())
	}
}

// 中间隔了 paused / disabled 的两次 unreachable 不连续：不拉。
func TestStreakResetsOnPausedOrDisabled(t *testing.T) {
	for _, mid := range []string{"paused", "disabled", ""} {
		sp := &spawnLog{}
		e := testEnv(t, false, sp)
		cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
		e.h.auto(cfg, "unreachable")
		e.h.auto(cfg, mid)
		e.h.auto(cfg, "unreachable")
		e.settle()
		if sp.n() != 0 {
			t.Errorf("unreachable,%q,unreachable spawned", mid)
		}
		e.h.auto(cfg, "unreachable") // 现在才是连续两次
		e.settle()
		if sp.n() != 1 {
			t.Errorf("after %q two consecutive should spawn: %d", mid, sp.n())
		}
	}
}

// 心跳线程说不准 AW 状态（拉网页设置失败）：健康线程自己探一下本机接口，报 ok / unreachable，面板不能因此显示离线。
// 自动拉起不看这个兜底。
func TestUnknownStateFallsBackToOwnProbe(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	sp := &spawnLog{}
	e := testEnv(t, true, sp)
	e.h.observe(cfg, "")
	e.h.flush()
	if ck.n() != 1 || ck.last()["awState"] != "ok" {
		t.Fatalf("reachable: n=%d %v", ck.n(), ck.bodies)
	}
	e.reach.Store(false)
	for i := 0; i < 4; i++ {
		e.clk.add(5 * time.Second)
		e.h.observe(cfg, "")
		e.h.flush()
	}
	if ck.last()["awState"] != "unreachable" {
		t.Fatalf("unreachable: %v", ck.last())
	}
	e.settle()
	if sp.n() != 0 {
		t.Fatal("auto-start fired from the fallback state")
	}
}

// 观察超过 60 秒没更新（本机配置读坏了，心跳线程不再 observe）：不再替它续报 ok。
func TestStaleObservationIsNotRenewed(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	e := testEnv(t, true, &spawnLog{})
	e.h.observe(cfg, "ok")
	e.h.flush()
	if ck.n() != 1 {
		t.Fatal("first report missing")
	}
	e.clk.add(30 * time.Second)
	e.h.flush()
	if ck.n() != 2 { // 30 秒：观察还新鲜，照常 15 秒一报
		t.Fatalf("fresh observation not reported: %d", ck.n())
	}
	e.clk.add(31 * time.Second) // 距观察 61 秒
	e.h.flush()
	e.clk.add(20 * time.Second)
	e.h.flush()
	if ck.n() != 2 {
		t.Fatalf("stale observation renewed: %d PUTs", ck.n())
	}
	e.h.observe(cfg, "ok") // 心跳线程回来了
	e.h.flush()
	if ck.n() != 3 {
		t.Fatal("reporting did not resume")
	}
}

// 「连续连得上」的计时在 paused / disabled / 说不准 时断开：中间隔着停用的两个 ok 不连续。
func TestUpSinceClearedByPausedDisabledUnknown(t *testing.T) {
	for _, mid := range []string{"paused", "disabled", ""} {
		e := testEnv(t, false, &spawnLog{})
		cfg := Config{Enabled: true}
		e.h.mu.Lock()
		e.h.tries, e.h.gaveUp, e.h.autoState = 3, true, "gave_up"
		e.h.mu.Unlock()
		e.h.auto(cfg, "ok")
		e.clk.add(2 * time.Minute)
		e.h.auto(cfg, mid) // 停用 2 分钟，AW 其实一直是死的
		e.clk.add(2 * time.Minute)
		e.h.auto(cfg, "ok") // 恢复后第一拍
		e.h.mu.Lock()
		still := e.h.gaveUp && e.h.tries == 3
		e.h.mu.Unlock()
		if !still {
			t.Errorf("%q: cleared without 60s of observed reachability", mid)
		}
	}
}

// 稳定门槛就是 60 秒：连续连得上 59 秒再掉线不清零，61 秒清零。
func TestStabilityThresholdIs60Seconds(t *testing.T) {
	cfg := Config{Enabled: true}
	for _, c := range []struct {
		up    time.Duration
		reset bool
	}{{59 * time.Second, false}, {61 * time.Second, true}} {
		e := testEnv(t, false, &spawnLog{})
		e.h.mu.Lock()
		e.h.tries, e.h.gaveUp = 3, true
		e.h.mu.Unlock()
		e.h.auto(cfg, "ok")
		e.clk.add(c.up)
		e.h.auto(cfg, "ok")
		e.h.mu.Lock()
		cleared := !e.h.gaveUp && e.h.tries == 0
		e.h.mu.Unlock()
		if cleared != c.reset {
			t.Errorf("up %v: cleared=%v want %v", c.up, cleared, c.reset)
		}
		if !c.reset { // 59 秒后掉线：计时作废，再亮一拍不能凑数
			e.h.auto(cfg, "unreachable")
			e.clk.add(5 * time.Second)
			e.h.auto(cfg, "ok")
			e.clk.add(5 * time.Second)
			e.h.auto(cfg, "ok")
			e.h.mu.Lock()
			if !e.h.gaveUp {
				t.Error("down in between did not restart the timer")
			}
			e.h.mu.Unlock()
		}
	}
}

// 被 30 秒限频挡住的 nonce 不必等下一次从服务端取：限频一过，健康线程就接着处理。
func TestHeldNonceHandledWhenCooldownEnds(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	cfg.AwStartCommand = []string{"/opt/aw/aw-qt"}
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	for i := 0; i < 2; i++ {
		e.h.auto(cfg, "unreachable") // 自动拉起一次
	}
	waitFor(t, "auto spawn", func() bool { return sp.n() == 1 })
	e.clk.add(2 * time.Second)
	ck.set(`{"restartNonce":"n1"}`, 0)
	e.h.observe(cfg, "unreachable")
	e.h.flush() // PUT 响应带回 n1：被限频挡住
	e.settle()
	if sp.n() != 1 || e.result() != nil {
		t.Fatalf("handled inside cooldown: spawns=%d", sp.n())
	}
	ck.set(`{"restartNonce":"n1"}`, 0)
	e.clk.add(27 * time.Second) // 距自动拉起 29 秒
	e.h.observe(cfg, "unreachable")
	e.h.flush()
	if sp.n() != 1 {
		t.Fatal("handled before the cooldown ended")
	}
	e.clk.add(2 * time.Second) // 31 秒
	e.h.flush()                // 不需要服务端再给 nonce
	if r := e.waitResult(t); r.nonce != "n1" || sp.n() != 2 {
		t.Fatalf("result=%+v spawns=%d", r, sp.n())
	}
}

// 结果送不出去（服务端一直 500）：按 15 秒退避，5 分钟后丢弃，不再带 restart 字段。
func TestResultRetryBacksOffAndExpires(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	e := testEnv(t, true, &spawnLog{})
	ck.set("", 500)
	e.h.setResult("n1", "started", "")
	for i := 0; i < 600; i++ { // 10 分钟，每秒一次 flush（loop 的节拍），心跳线程每 5 秒观察一次
		e.clk.add(time.Second)
		if i%5 == 0 {
			e.h.observe(cfg, "ok")
		}
		e.h.flush()
	}
	if n := ck.n(); n > 45 || n < 15 {
		t.Fatalf("%d PUTs in 10 minutes", n)
	}
	ck.mu.Lock()
	defer ck.mu.Unlock()
	withResult := 0
	for _, b := range ck.bodies {
		if _, ok := b["restart"]; ok {
			withResult++
		}
	}
	if withResult == 0 || withResult > 22 { // 5 分钟 / 15 秒 ≈ 20
		t.Fatalf("PUTs carrying the result: %d", withResult)
	}
	if _, ok := ck.bodies[len(ck.bodies)-1]["restart"]; ok {
		t.Fatal("expired result still sent")
	}
}

// 发送期间别处有了新结果：发完不能把新结果一起清掉。
func TestNewerResultNotLostWhileSending(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	cfg := liveConfig("", ck.URL)
	e := testEnv(t, true, &spawnLog{})
	e.h.setResult("n1", "started", "")
	ck.onPut = func() { e.h.setResult("n2", "failed", "not_up") }
	e.h.report(cfg, "ok")
	if r := e.result(); r == nil || r.nonce != "n2" {
		t.Fatalf("newer result lost: %+v", r)
	}
	ck.onPut = nil
	e.clk.add(time.Second)
	e.h.report(cfg, "ok")
	if got := ck.last()["restart"]; !reflect.DeepEqual(got, map[string]any{"nonce": "n2", "result": "failed", "reason": "not_up"}) {
		t.Fatalf("second send: %v", got)
	}
}

// 24 小时上限：「亮 61 秒 / 死 10 秒」的循环一天能拉 700 多次，现在至多 20 次，窗口空出来才继续。
func TestAutoStartDailyCap(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
	step := func() { // 一个周期 75 秒：ok 13 拍（65 秒）、unreachable 2 拍
		for i := 0; i < 15; i++ {
			e.clk.add(5 * time.Second)
			if i < 13 {
				e.h.auto(cfg, "ok")
			} else {
				e.h.auto(cfg, "unreachable")
			}
		}
	}
	for e.clk.now().Sub(testStart(e)) < 23*time.Hour {
		step()
	}
	e.settle()
	e.h.mu.Lock()
	st := e.h.autoState
	e.h.mu.Unlock()
	e.h.mu.Lock()
	why := e.h.autoReason
	e.h.mu.Unlock()
	if sp.n() != autoDayMax || st != "gave_up" || why != "daily_cap" {
		t.Fatalf("spawns=%d state=%q reason=%q", sp.n(), st, why)
	}
	for e.clk.now().Sub(testStart(e)) < 25*time.Hour { // 24 小时的窗口滑过去，又能拉了
		step()
	}
	e.settle()
	if sp.n() <= autoDayMax || sp.n() > 2*autoDayMax { // 最早那 20 次一起满 24 小时，窗口空出来又能拉 20 次

		t.Fatalf("after the window slid: %d", sp.n())
	}
}

var startTimes sync.Map

func testStart(e *env) time.Time {
	v, _ := startTimes.LoadOrStore(e, e.clk.now())
	return v.(time.Time)
}

// 没拉起进程的失败（找不到 / 没桌面 / 状态写不了 / 启动出错）：status 不贴 aw-start.log（那是更早一次的）。
func TestStatusOmitsLogTailWhenNothingSpawned(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "aw-start.log")
	_ = os.WriteFile(logPath, []byte("OLD-RUN-OUTPUT\n"), 0o600)
	for _, reason := range []string{"not_found", "spawn_error", "no_display", "state_unwritable"} {
		var out strings.Builder
		printLastStart(&out, &startRecord{At: time.Now(), Trigger: "auto", Argv: []string{"x"}, Outcome: "failed", Reason: reason}, logPath)
		if strings.Contains(out.String(), "OLD-RUN-OUTPUT") || !strings.Contains(out.String(), "可以试试") {
			t.Errorf("%s:\n%s", reason, out.String())
		}
	}
	var out strings.Builder
	printLastStart(&out, &startRecord{At: time.Now(), Trigger: "auto", Argv: []string{"x"}, Outcome: "failed", Reason: "exited_early"}, logPath)
	if !strings.Contains(out.String(), "OLD-RUN-OUTPUT") {
		t.Errorf("exited_early should show the log:\n%s", out.String())
	}
}

// 被限频挡住的 nonce 超过 60 秒（墙钟）就过期：笔记本睡了一觉回来，不能为服务端早已作废的请求去拉 AW。
func TestHeldNonceExpiresByWallClock(t *testing.T) {
	cfg := Config{AwStartCommand: []string{"/opt/aw/aw-qt"}}
	for _, c := range []struct {
		name  string
		jump  time.Duration
		spawn int
	}{{"40 秒后限频结束", 40 * time.Second, 2}, {"睡了 10 分钟", 10 * time.Minute, 1}} {
		sp := &spawnLog{}
		e := testEnv(t, false, sp)
		wall := time.Now().Round(0)
		e.h.wall = func() time.Time { return wall }
		e.h.restart(cfg, "n1")
		e.waitResult(t)
		e.clk.add(5 * time.Second)
		e.h.restart(cfg, "n2")      // 限频内：被留住
		e.clk.add(26 * time.Second) // 单调钟只走了 31 秒（挂起期间它不走）
		wall = wall.Add(c.jump)     // 墙钟跳了
		e.h.mu.Lock()
		e.h.obs = observation{ok: false}
		e.h.mu.Unlock()
		e.h.flush()
		e.settle()
		if sp.n() != c.spawn {
			t.Errorf("%s: spawns=%d want %d", c.name, sp.n(), c.spawn)
		}
		if c.spawn == 1 { // 过期的 nonce 记成已处理：服务端再发也不理
			e.clk.add(time.Minute)
			e.h.restart(cfg, "n2")
			e.settle()
			var st State
			_ = loadJSON(e.p.state, &st)
			if sp.n() != 1 || st.Health.RestartNonce != "n2" {
				t.Errorf("expired nonce re-handled: spawns=%d state=%q", sp.n(), st.Health.RestartNonce)
			}
		}
	}
}

// 24 小时窗口的边界：满 24 小时整才放行，差 1 纳秒还不行。
func TestDailyCapWindowEdge(t *testing.T) {
	sp := &spawnLog{}
	e := testEnv(t, false, sp)
	cfg := Config{Enabled: true, AwStartCommand: []string{"/opt/aw/aw-qt"}}
	t0 := e.clk.now()
	e.h.mu.Lock()
	e.h.autoTimes = append(e.h.autoTimes, t0)
	for i := 1; i < autoDayMax; i++ {
		e.h.autoTimes = append(e.h.autoTimes, t0.Add(time.Hour))
	}
	e.h.mu.Unlock()
	e.clk.mu.Lock()
	e.clk.t = t0.Add(24*time.Hour - time.Nanosecond)
	e.clk.mu.Unlock()
	e.h.auto(cfg, "unreachable")
	e.h.auto(cfg, "unreachable")
	e.settle()
	e.h.mu.Lock()
	reason, state := e.h.autoReason, e.h.autoState
	e.h.mu.Unlock()
	if sp.n() != 0 || reason != "daily_cap" || state != "gave_up" {
		t.Fatalf("at 24h-1ns: spawns=%d state=%q reason=%q", sp.n(), state, reason)
	}
	e.clk.add(time.Nanosecond)
	e.h.auto(cfg, "unreachable")
	e.settle()
	e.h.mu.Lock()
	reason = e.h.autoReason
	e.h.mu.Unlock()
	if sp.n() != 1 || reason == "daily_cap" {
		t.Fatalf("at exactly 24h: spawns=%d reason=%q", sp.n(), reason)
	}
}

// 没拉起进程的失败不动上一次的日志：内容不变，也不被挤到 .1。
func TestFailedSpawnKeepsPreviousLog(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "aw-start.log")
	_ = os.WriteFile(logPath, []byte("REAL-LOG\n"), 0o600)
	for i := 0; i < 2; i++ {
		if _, err := spawnDetached([]string{"/nonexistent/aw-qt"}, logPath); err == nil {
			t.Fatal("spawn should fail")
		}
		b, err := os.ReadFile(logPath)
		if err != nil || string(b) != "REAL-LOG\n" {
			t.Fatalf("attempt %d: log=%q err=%v", i, b, err)
		}
	}
	if runtime.GOOS != "windows" {
		if _, err := spawnDetached([]string{"sh", "-c", "echo NEW"}, logPath); err != nil {
			t.Fatal(err)
		}
		waitFor(t, "new log", func() bool { b, _ := os.ReadFile(logPath); return string(b) == "NEW\n" })
		if b, _ := os.ReadFile(logPath + ".1"); string(b) != "REAL-LOG\n" {
			t.Fatalf(".1 = %q", b)
		}
	}
}

// 默认的墙钟读数不带单调部分：Linux 上挂起期间单调钟不走，带着它 Sub 会把睡了 10 分钟算成 30 秒。
func TestDefaultWallClockHasNoMonotonicReading(t *testing.T) {
	h := newHealthReporter(pathsIn(t.TempDir()), http.DefaultClient)
	if s := h.wall().String(); strings.Contains(s, "m=") {
		t.Fatalf("wall clock carries a monotonic reading: %s", s)
	}
}
