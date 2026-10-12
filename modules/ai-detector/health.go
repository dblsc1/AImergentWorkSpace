package main

// v0.4：检测程序健康状态（nexus-core v2.27）。每次在场心跳尝试之后，把「检测程序活着 + ActivityWatch 怎么样」
// 报给服务端；网页上点「重启 ActivityWatch」时，响应里带一个不透明的 nonce，本机据此用**本机配置**的命令拉起 ActivityWatch。
//
// 安全底线：服务端发来的任何东西都不是命令、也不拼进命令——它只给一个 nonce，原样回报结果。
// 命令只来自本机配置的 awStartCommand（或按系统的缺省）。

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

var (
	errAWDown   = errors.New("连不上 ActivityWatch")
	errNoWindow = errors.New("ActivityWatch 最近一分钟没有窗口记录")
)

const (
	healthEvery  = 15 * time.Second // 状态没变时至多这么久报一次（面板最多等 75 秒，要赶得上取 nonce）
	restartEvery = 30 * time.Second // 两次拉起 ActivityWatch 至少隔这么久，不管是按钮还是自动、不管 nonce 换了几个
	autoStreak   = 2                // 连续这么多次心跳尝试都 unreachable 才自动拉起（一次抖动不算）
	autoMaxTries = 3                // 连续自动拉起这么多次还没把 ActivityWatch 拉回来就停手
	probeWait    = 20 * time.Second // 拉起后最多等这么久，接口起来了才算 started
	probeEvery   = time.Second
	obsFresh     = time.Minute     // 心跳线程这么久没更新观察（比如本机配置写坏了），就不再替它报，面板上自然显示离线
	resultTTL    = 5 * time.Minute // 回报不出去的重启结果留这么久（服务端那边请求早已作废）
	autoDayMax   = 20              // 滚动 24 小时内自动拉起的上限
	stableAfter  = time.Minute     // ActivityWatch 要连续连得上这么久，才清零「已自动拉起几次 / 已停手」（一闪一灭不算恢复）
)

// 服务端的 nonce 是 16 位十六进制；这里放宽到 1–64 位字母数字 - _，其余一律不理（也就不会原样回报）。
var nonceRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// awStateOf：这一拍的结果 → 契约里的 awState。"" = 说不准（比如拉设置失败），这一拍不报。
func awStateOf(cfg Config, beatErr error, sent bool, reachable func() bool) string {
	switch {
	case !cfg.Enabled:
		return "disabled"
	case cfg.Paused:
		return "paused"
	case errors.Is(beatErr, errAWDown):
		return "unreachable"
	case errors.Is(beatErr, errNoWindow):
		return "no_window"
	case beatErr != nil:
		return ""
	case sent || reachable():
		return "ok"
	}
	return "unreachable" // 心跳没开（没碰 ActivityWatch）、探一下也连不上
}

// awReachable：ActivityWatch 的 HTTP 接口有回应就算在（不看状态码、不读内容）。
func awReachable(cfg Config, hc *http.Client) bool {
	// 本机接口，要么立刻有回应要么立刻拒绝；超时取短，别让一个卡住的 ActivityWatch 拖住调用方
	resp, err := withPolicy(hc, 3*time.Second, nil).Get(strings.TrimRight(cfg.ActivityWatchURL, "/") + "/info")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return true
}

// defaultAwStart：本机没配 awStartCommand 时按系统找。找不到 = nil（报 unsupported）。
func defaultAwStart(goos string, getenv func(string) string, lookPath func(string) (string, error)) []string {
	switch goos {
	case "windows":
		if d := getenv("LOCALAPPDATA"); d != "" {
			exe := filepath.Join(d, "Programs", "ActivityWatch", "aw-qt.exe")
			if _, err := os.Stat(exe); err == nil {
				return []string{exe}
			}
		}
	case "darwin":
		return []string{"open", "-a", "ActivityWatch"}
	default:
		if p, err := lookPath("aw-qt"); err == nil {
			return []string{p}
		}
	}
	return nil
}

// startRecord：最近一次拉起的记录，只在本机（状态文件、`ai-detector status`），不上服务端。
type startRecord struct {
	At       time.Time `json:"at"`
	Trigger  string    `json:"trigger"` // auto | button
	Argv     []string  `json:"argv"`
	Outcome  string    `json:"outcome"` // started | failed
	Reason   string    `json:"reason,omitempty"`
	Error    string    `json:"error,omitempty"`
	ExitCode *int      `json:"exitCode,omitempty"`
}

// 失败原因：严格枚举，随健康报告上去（没有自由文本）。
const (
	rNotFound   = "not_found"
	rSpawnError = "spawn_error"
	rExited     = "exited_early"
	rNotUp      = "not_up"
	rNoDisplay  = "no_display"
	rStateRO    = "state_unwritable"
)

// spawnDetached：不经 shell、不继承检测程序自己的标准输入输出、脱离本进程（detach_*.go），检测程序退出后它照样活着。
// 子进程的 stdout / stderr 写到 logPath（0600；每次先把上一份挪成 .1）。返回的通道在子进程退出时给出退出码。
func spawnDetached(argv []string, logPath string) (<-chan int, error) {
	cmd := exec.Command(argv[0], argv[1:]...)
	var f *os.File
	if logPath != "" {
		_ = os.Rename(logPath, logPath+".1")
		var err error
		if f, err = os.OpenFile(logPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600); err != nil {
			log.Printf("aw-start.log 开不了（子进程的输出不留）：%v", err)
			f = nil
		} else {
			_ = f.Chmod(0o600)
			cmd.Stdout, cmd.Stderr = f, f
		}
	}
	detach(cmd)
	err := cmd.Start()
	if f != nil {
		f.Close() // 子进程已经有自己的一份
	}
	if err != nil {
		return nil, err
	}
	ch := make(chan int, 1)
	go func() { // 兼收尸，免得留僵尸进程
		_ = cmd.Wait()
		code := -1
		if cmd.ProcessState != nil {
			code = cmd.ProcessState.ExitCode()
		}
		ch <- code
	}()
	return ch, nil
}

// healthReporter 的字段由三处并发用：心跳 goroutine（observe / auto）、健康 goroutine（report / restart）、
// 拉起后的探测 goroutine。全部在 mu 里读写；mu 里不做网络请求。
type healthReporter struct {
	hc      *http.Client
	state   string // paths.state
	logPath string // <检测程序目录>/aw-start.log

	mu      sync.Mutex
	last    string    // 上一次成功报出的 状态/自动结果
	lastAt  time.Time // 上一次尝试报的时刻
	dead    bool      // 老服务端（404）：不再试，直到进程重启
	failed  bool      // 当前处于失败连续段（只记一次日志）
	result  *restartResult
	handled string    // 最后处理的 nonce（内存里一份，状态文件里一份）
	lastRun time.Time // 上一次动手拉起 ActivityWatch 的时刻（按钮和自动共用），取自动手那一刻的时钟

	// 自动拉起（awAutoStart）：autoState / autoReason 是最近一次自动动作的结果，随健康报告上去（枚举，无自由文本）
	autoState   string
	autoReason  string
	streak      int         // 连续 unreachable 的次数
	tries       int         // 自动拉起了几次、ActivityWatch 还没回来
	autoTimes   []time.Time // 滚动 24 小时内自动拉起的时刻
	capped      bool        // 当前因为 24 小时上限而停手（窗口空出来就自动解除）
	staleLogged bool
	held        string // 被 30 秒限频挡住、等限频过了再处理的 nonce
	heldCfg     Config
	gaveUp      bool      // 已停手，直到 ActivityWatch 连续连得上 stableAfter、或进程重启
	upSince     time.Time // 连续连得上的起点；一次 unreachable 就清空
	noCmdLogged bool

	obs  observation
	wake chan struct{}

	spawn      func(argv []string, logPath string) (<-chan int, error)
	reachable  func(Config) bool
	resolve    func() []string
	clock      func() time.Time
	goos       string
	getenv     func(string) string
	probeWait  time.Duration
	probeEvery time.Duration
}

type restartResult struct {
	nonce, result, reason string
	at                    time.Time
}

type observation struct {
	cfg Config
	st  string
	ok  bool
	at  time.Time
}

func newHealthReporter(p paths, hc *http.Client) *healthReporter {
	return &healthReporter{hc: hc, state: p.state, logPath: filepath.Join(filepath.Dir(p.state), "aw-start.log"),
		wake: make(chan struct{}, 1), spawn: spawnDetached, clock: time.Now, goos: runtime.GOOS, getenv: os.Getenv,
		probeWait: probeWait, probeEvery: probeEvery,
		reachable: func(c Config) bool { return awReachable(c, hc) },
		resolve:   func() []string { return defaultAwStart(runtime.GOOS, os.Getenv, exec.LookPath) }}
}

// observe：心跳 goroutine 每次尝试之后调。只做本地的事（记下最新状态、决定要不要自动拉起），
// 报告交给 loop 去发——健康服务器卡住也不会拖慢下一拍心跳。
func (h *healthReporter) observe(cfg Config, awState string) {
	h.auto(cfg, awState)
	h.mu.Lock()
	h.obs = observation{cfg, awState, true, h.clock()}
	h.mu.Unlock()
	select {
	case h.wake <- struct{}{}:
	default:
	}
}

// loop：健康报告自己的 goroutine，发最近一次观察到的状态。
func (h *healthReporter) loop() {
	for {
		select {
		case <-loopsStop:
			return
		case <-h.wake:
		case <-time.After(time.Second):
		}
		h.flush()
	}
}

// flush：发最近一次观察到的状态；顺手处理被限频挡住的 nonce。
//   - 观察超过 obsFresh 没更新（心跳线程没在观察了，比如本机配置读不了）就不再替它报：面板显示离线比一直说「在线」诚实；
//   - 心跳线程说不准 AW 状态（比如拉网页设置失败）时，这里自己探一下本机接口，报 ok / unreachable，别让面板以为检测程序死了。
//     自动拉起不看这个兜底的结果（auto 收到的仍是「说不准」，不动）。
func (h *healthReporter) flush() {
	now := h.clock()
	h.mu.Lock()
	o, held, heldCfg := h.obs, h.held, h.heldCfg
	if held != "" && !h.canStart(now) {
		held = ""
	}
	stale := o.ok && now.Sub(o.at) > obsFresh
	first := stale && !h.staleLogged
	h.staleLogged = stale
	h.mu.Unlock()
	if first {
		log.Print("心跳线程超过 1 分钟没有更新状态（本机配置读不了？），暂停报告健康状态")
	}
	if held != "" {
		h.restart(heldCfg, held)
	}
	if !o.ok || stale {
		return
	}
	st := o.st
	if st == "" {
		st = "unreachable"
		if h.reachable(o.cfg) {
			st = "ok"
		}
	}
	h.report(o.cfg, st)
}

// report：状态变了、或距上次超过 healthEvery、或有结果要回报时才发。失败只记日志，绝不影响调用方。
func (h *healthReporter) report(cfg Config, awState string) {
	now := h.clock()
	h.mu.Lock()
	if h.dead || awState == "" || cfg.CockpitURL == "" || cfg.DeviceToken == "" || cfg.DeviceID == "" {
		h.mu.Unlock()
		return
	}
	if h.result != nil && now.Sub(h.result.at) > resultTTL { // 服务端那边请求早已作废：别无限期带着
		log.Printf("重启结果 %s 超过 %v 没送出去，丢弃", h.result.result, resultTTL)
		h.result = nil
	}
	key := awState + "/" + h.autoState + "/" + h.autoReason
	if now.Sub(h.lastAt) < healthEvery && (h.failed || (key == h.last && h.result == nil)) { // 失败后也按 15 秒退避
		h.mu.Unlock()
		return
	}
	h.lastAt = now
	body := map[string]any{"awState": awState}
	if h.autoState != "" {
		body["autoStart"] = h.autoState
	}
	if h.autoReason != "" {
		body["autoReason"] = h.autoReason
	}
	res := h.result
	if res != nil {
		r := map[string]string{"nonce": res.nonce, "result": res.result}
		if res.reason != "" {
			r["reason"] = res.reason
		}
		body["restart"] = r
	}
	h.mu.Unlock()

	b, _ := json.Marshal(body)
	c := withPolicy(h.hc, h.hc.Timeout, sameHostOnly)
	req, _ := http.NewRequest(http.MethodPut, strings.TrimRight(cfg.CockpitURL, "/")+"/api/core/detector/health?deviceId="+url.QueryEscape(cfg.DeviceID), strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	resp, code, err := do(c, req)

	h.mu.Lock()
	if code == http.StatusNotFound {
		h.dead = true
		h.mu.Unlock()
		log.Print("服务端不认识 /detector/health（版本较老），不再报告检测程序状态")
		return
	}
	if err != nil {
		quiet := h.failed
		h.failed = true
		h.mu.Unlock()
		if !quiet {
			log.Printf("检测程序状态没报出去（不影响同步 / 心跳）：%v", err)
		}
		return
	}
	h.failed, h.last = false, key
	if h.result == res { // 发的过程中结果可能已经换成新的了
		h.result = nil
	}
	h.mu.Unlock()
	var r struct {
		RestartNonce string `json:"restartNonce"`
	}
	_ = json.Unmarshal(resp, &r)
	_ = updateState(h.state, func(s *State) { s.Health.AwState, s.Health.At = awState, time.Now() })
	if r.RestartNonce != "" {
		h.restart(cfg, r.RestartNonce)
	}
}

func (h *healthReporter) setResult(nonce, result, reason string) {
	h.mu.Lock()
	h.result = &restartResult{nonce, result, reason, h.clock()}
	h.mu.Unlock()
}

// restart：只认 nonce。已经处理过的不再理；ActivityWatch 连得上就说「本来就在运行」；
// 否则用本机命令拉起（30 秒内已拉过就先不理这个 nonce——服务端会继续给它，限频过了再处理）。
// 先把 nonce 记进状态文件再动手：记不下来就不拉（否则重启后会重放）。
func (h *healthReporter) restart(cfg Config, nonce string) {
	if !nonceRe.MatchString(nonce) {
		return
	}
	var st State
	_ = loadJSON(h.state, &st)
	h.mu.Lock()
	dup := h.handled == nonce || st.Health.RestartNonce == nonce
	if h.held == nonce {
		h.held = "" // 取出来处理了；限频还没过的话下面会再放回去
	}
	h.mu.Unlock()
	if dup {
		return
	}
	persist := func() error {
		h.handled = nonce
		return updateState(h.state, func(s *State) { s.Health.RestartNonce = nonce })
	}
	if h.reachable(cfg) {
		h.mu.Lock()
		if err := persist(); err != nil {
			log.Printf("重启请求的 nonce 记不进状态文件（%v）", err)
		}
		h.mu.Unlock()
		h.setResult(nonce, "already_running", "")
		return
	}
	argv := h.command(cfg)
	if len(argv) == 0 {
		h.mu.Lock()
		_ = persist()
		h.mu.Unlock()
		h.setResult(nonce, "unsupported", "")
		return
	}
	// 按钮在检测程序暂停 / 没开同步时也照样能用：这是人明确点的，而且它只检测并重启 ActivityWatch
	ok := h.launch(cfg, argv, "button", persist, func(outcome, reason string) {
		if outcome == "started" {
			h.setResult(nonce, "started", "")
		} else {
			h.setResult(nonce, "failed", reason)
		}
	})
	if !ok { // 30 秒限频内：先放着，限频一过 flush 就接着处理（不必等下一次从服务端取）
		h.mu.Lock()
		h.held, h.heldCfg = nonce, cfg
		h.mu.Unlock()
	}
}

// command：本机配置的 awStartCommand，没有就按系统缺省找。**只来自本机**，没有服务端的份。
func (h *healthReporter) command(cfg Config) []string {
	if len(cfg.AwStartCommand) > 0 {
		return cfg.AwStartCommand
	}
	return h.resolve()
}

// canStart：调用方持有 mu。
func (h *healthReporter) canStart(now time.Time) bool {
	return h.lastRun.IsZero() || now.Sub(h.lastRun) >= restartEvery
}

// launch：按钮和自动拉起共用的唯一一条启动路径。30 秒限频内返回 false（什么都没做，调用方稍后再试）。
// 时钟在动手这一刻取（不是更早、网络请求之前的 now）。pre 在动手前（持锁）调，出错 = 不拉、原因 state_unwritable。
// 拉起之后在单独的 goroutine 里最多等 probeWait 看接口起没起来，完事调 done(outcome, reason)。
func (h *healthReporter) launch(cfg Config, argv []string, trigger string, pre func() error, done func(outcome, reason string)) bool {
	h.mu.Lock()
	t := h.clock()
	if !h.canStart(t) {
		h.mu.Unlock()
		return false
	}
	rec := &startRecord{At: t, Trigger: trigger, Argv: argv}
	reason := ""
	var wait <-chan int
	if pre != nil {
		if err := pre(); err != nil {
			reason, rec.Error = rStateRO, err.Error()
		}
	}
	if reason == "" {
		h.lastRun = t
		if h.goos == "linux" && h.getenv("DISPLAY") == "" && h.getenv("WAYLAND_DISPLAY") == "" {
			reason = rNoDisplay // 不在桌面会话里：拉起的图形程序开不了窗口，不白拉
		} else {
			var err error
			if wait, err = h.spawn(argv, h.logPath); err != nil {
				reason, rec.Error = rSpawnError, err.Error()
				if errors.Is(err, exec.ErrNotFound) || errors.Is(err, fs.ErrNotExist) {
					reason = rNotFound
				}
			}
		}
	}
	h.mu.Unlock()
	if reason != "" {
		h.finish(rec, "failed", reason, done)
		return true
	}
	go h.probe(cfg, rec, wait, done)
	return true
}

// probe：拉起后看接口起来没有。进程提前以非 0 退出 = exited_early；退出码 0（比如 macOS 的 open）继续等接口。
func (h *healthReporter) probe(cfg Config, rec *startRecord, wait <-chan int, done func(outcome, reason string)) {
	deadline := time.Now().Add(h.probeWait)
	for {
		if h.reachable(cfg) {
			h.finish(rec, "started", "", done)
			return
		}
		select {
		case code := <-wait:
			rec.ExitCode = &code
			if code != 0 {
				h.finish(rec, "failed", rExited, done)
				return
			}
			wait = nil
		case <-time.After(h.probeEvery):
		}
		if time.Now().After(deadline) {
			h.finish(rec, "failed", rNotUp, done)
			return
		}
	}
}

func (h *healthReporter) finish(rec *startRecord, outcome, reason string, done func(outcome, reason string)) {
	rec.Outcome, rec.Reason = outcome, reason
	log.Printf("拉起 ActivityWatch（%s）：命令 %q → %s %s %s", rec.Trigger, rec.Argv, outcome, reason, rec.Error)
	if err := updateState(h.state, func(s *State) { r := *rec; s.Health.LastStart = &r }); err != nil {
		log.Printf("拉起记录写不进状态文件：%v", err)
	}
	done(outcome, reason)
}

// auto：每次心跳尝试之后调一次（awState 同 report）。连续两次 unreachable 才拉；连拉 3 次没把它拉回来就停手，
// 直到又看到 ActivityWatch 连得上；paused / disabled / 配置关掉 / 没有可运行的命令都不拉。
// “拉回来”以拉起后的接口探测为准（launch）。
func (h *healthReporter) auto(cfg Config, awState string) {
	h.mu.Lock()
	if cfg.AwAutoStart != nil && !*cfg.AwAutoStart {
		h.autoState = "off"
		h.mu.Unlock()
		return
	}
	if h.autoState == "" || h.autoState == "off" {
		h.autoState = "idle"
	}
	switch awState {
	case "unreachable":
		h.streak++
		h.upSince = time.Time{}
	case "ok", "no_window":
		h.streak = 0
		now := h.clock()
		if h.upSince.IsZero() {
			h.upSince = now
		}
		if now.Sub(h.upSince) >= stableAfter {
			h.tries, h.gaveUp, h.noCmdLogged, h.autoReason = 0, false, false, ""
			if h.autoState == "gave_up" || h.autoState == "unsupported" {
				h.autoState = "idle"
			}
		}
		h.mu.Unlock()
		return
	default: // paused / disabled / 说不准：不动，也不累计；「连续连得上」的计时也断开
		h.streak = 0
		h.upSince = time.Time{}
		h.mu.Unlock()
		return
	}
	if h.streak < autoStreak || h.gaveUp {
		h.mu.Unlock()
		return
	}
	argv := h.command(cfg)
	if len(argv) == 0 {
		h.autoState = "unsupported"
		first := !h.noCmdLogged
		h.noCmdLogged = true
		h.mu.Unlock()
		if first {
			log.Print("ActivityWatch 连不上，但本机没有可运行的启动命令（配置 awStartCommand），不自动拉起")
		}
		return
	}
	if !h.canStart(h.clock()) {
		h.mu.Unlock()
		return
	}
	now := h.clock()
	for len(h.autoTimes) > 0 && now.Sub(h.autoTimes[0]) >= 24*time.Hour {
		h.autoTimes = h.autoTimes[1:]
	}
	if len(h.autoTimes) >= autoDayMax { // 24 小时上限：再怎么「恢复又掉线」也不拉了（按钮不受影响）
		if !h.capped {
			log.Printf("24 小时内已自动拉起 %d 次 ActivityWatch，今天不再自动拉起（面板上的按钮还能用）", autoDayMax)
		}
		h.capped, h.autoState = true, "gave_up"
		h.mu.Unlock()
		return
	}
	if h.capped {
		h.capped = false
		if h.autoState == "gave_up" && !h.gaveUp {
			h.autoState = "idle"
		}
	}
	if h.tries >= autoMaxTries {
		h.gaveUp, h.autoState = true, "gave_up"
		h.mu.Unlock()
		log.Printf("自动拉起了 %d 次 ActivityWatch 仍连不上，不再自动重试（面板上的按钮还能用；原因见 ai-detector status）", autoMaxTries)
		return
	}
	h.mu.Unlock()
	if h.launch(cfg, argv, "auto", nil, h.autoDone) {
		h.mu.Lock()
		h.tries++
		h.autoTimes = append(h.autoTimes, h.clock())
		h.mu.Unlock()
	}
}

func (h *healthReporter) autoDone(outcome, reason string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if outcome == "started" {
		h.autoState, h.autoReason = "started", ""
		return
	}
	h.autoReason = reason
	if h.autoState == "started" {
		h.autoState = "idle"
	}
}

var reasonText = map[string][2]string{ // 原因 → (是怎么回事, 可以试试)
	rNotFound:   {"找不到启动命令指向的程序", "装 ActivityWatch，或在配置里写 awStartCommand"},
	rSpawnError: {"启动命令执行出错", "看上面的错误信息；确认 awStartCommand 指向的文件能运行"},
	rExited:     {"程序启动后马上退出了", "看下面的输出（aw-start.log）"},
	rNotUp:      {"程序在运行，但 ActivityWatch 的接口 20 秒内没起来", "看 ActivityWatch 自己的日志"},
	rNoDisplay:  {"检测程序不在桌面会话里运行（没有 DISPLAY / WAYLAND_DISPLAY），没有尝试启动", "开机自启用 ai-detector autostart install，让它跟着桌面登录启动"},
	rStateRO:    {"检测程序目录写不进去，没有尝试启动", "检查配置目录的权限和磁盘空间"},
}

// 这些原因下没有拉起进程，aw-start.log 的内容跟这次无关。
var noProcess = map[string]bool{rNotFound: true, rSpawnError: true, rNoDisplay: true, rStateRO: true}

// printLastStart：`ai-detector status` 里最近一次拉起 ActivityWatch 的情况（全是本机信息）。
func printLastStart(out io.Writer, r *startRecord, logPath string) {
	if r == nil {
		return
	}
	who := map[string]string{"auto": "自动（连不上时检测程序自己拉起）", "button": "网页上的「重启 ActivityWatch」按钮"}[r.Trigger]
	fmt.Fprintf(out, "上次拉起 ActivityWatch\n  时间  %s\n  触发  %s\n  命令  %q\n", isoTime(r.At), who, r.Argv)
	if r.Outcome == "started" {
		fmt.Fprintln(out, "  结果  成功：ActivityWatch 的接口起来了")
	} else {
		rt := reasonText[r.Reason]
		fmt.Fprintf(out, "  结果  失败：%s（%s）\n", rt[0], r.Reason)
	}
	if r.Error != "" {
		fmt.Fprintf(out, "  错误  %s\n", r.Error)
	}
	if r.ExitCode != nil {
		fmt.Fprintf(out, "  退出码  %d\n", *r.ExitCode)
	}
	fmt.Fprintf(out, "  日志  %s\n", logPath)
	if noProcess[r.Reason] { // 这次根本没拉起进程：aw-start.log 是更早一次的，贴出来会误导
		fmt.Fprintln(out, "  （这次没有拉起任何进程，所以没有新的输出；aw-start.log 里是更早一次的，不显示）")
	} else if b, err := os.ReadFile(logPath); err == nil {
		ls := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
		if len(ls) > 20 {
			ls = ls[len(ls)-20:]
		}
		if len(b) > 0 {
			fmt.Fprintf(out, "  日志最后 %d 行：\n", len(ls))
			for _, l := range ls {
				fmt.Fprintf(out, "    | %s\n", l)
			}
		}
	}
	if rt, ok := reasonText[r.Reason]; ok && r.Outcome != "started" {
		fmt.Fprintf(out, "  可以试试  %s\n", rt[1])
	}
}
