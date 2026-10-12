package main

// v0.4：检测程序健康状态（nexus-core v2.27）。每次在场心跳尝试之后，把「检测程序活着 + ActivityWatch 怎么样」
// 报给服务端；网页上点「重启 ActivityWatch」时，响应里带一个不透明的 nonce，本机据此用**本机配置**的命令拉起 ActivityWatch。
//
// 安全底线：服务端发来的任何东西都不是命令、也不拼进命令——它只给一个 nonce，原样回报结果。
// 命令只来自本机配置的 awStartCommand（或按系统的缺省）。

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

var (
	errAWDown   = errors.New("连不上 ActivityWatch")
	errNoWindow = errors.New("ActivityWatch 最近一分钟没有窗口记录")
)

const (
	healthEvery  = 60 * time.Second // 状态没变时至多这么久报一次
	restartEvery = 30 * time.Second // 两次拉起 ActivityWatch 至少隔这么久，不管是按钮还是自动、不管 nonce 换了几个
	autoStreak   = 2                // 连续这么多次心跳尝试都 unreachable 才自动拉起（一次抖动不算）
	autoMaxTries = 3                // 连续自动拉起这么多次还没把 ActivityWatch 拉回来就停手
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
	resp, err := hc.Get(strings.TrimRight(cfg.ActivityWatchURL, "/") + "/info")
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

// spawnDetached：不经 shell、不继承标准输入输出、脱离本进程（detach_*.go），检测程序退出后它照样活着。
func spawnDetached(argv []string) error {
	cmd := exec.Command(argv[0], argv[1:]...)
	detach(cmd)
	if err := cmd.Start(); err != nil {
		return err
	}
	go cmd.Wait() // 收尸，免得留僵尸进程
	return nil
}

type healthReporter struct {
	hc    *http.Client
	state string // paths.state

	last    string    // 上一次成功报出的 awState
	lastAt  time.Time // 上一次尝试报的时刻
	dead    bool      // 老服务端（404）：不再试，直到进程重启
	failed  bool      // 当前处于失败连续段（只记一次日志）
	result  *struct{ nonce, result string }
	lastRun time.Time // 上一次拉起 ActivityWatch 的时刻（按钮和自动共用）

	// 自动拉起（awAutoStart）：autoState 是最近一次自动动作的结果，随健康报告上去（枚举，无自由文本）
	autoState   string
	streak      int  // 连续 unreachable 的次数
	tries       int  // 自动拉起了几次、ActivityWatch 还没回来
	gaveUp      bool // 已停手，直到再看到 ActivityWatch 连得上、或进程重启
	noCmdLogged bool

	spawn     func(argv []string) error
	reachable func(Config) bool
	resolve   func() []string
}

func newHealthReporter(p paths, hc *http.Client) *healthReporter {
	return &healthReporter{hc: hc, state: p.state, spawn: spawnDetached,
		reachable: func(c Config) bool { return awReachable(c, hc) },
		resolve:   func() []string { return defaultAwStart(runtime.GOOS, os.Getenv, exec.LookPath) }}
}

// report：状态变了、或距上次超过 healthEvery、或有结果要回报时才发。失败只记日志，绝不影响调用方。
func (h *healthReporter) report(cfg Config, awState string, now time.Time) {
	if h.dead || awState == "" || cfg.CockpitURL == "" || cfg.DeviceToken == "" || cfg.DeviceID == "" {
		return
	}
	key := awState + "/" + h.autoState
	if key == h.last && h.result == nil && now.Sub(h.lastAt) < healthEvery {
		return
	}
	h.lastAt = now
	body := map[string]any{"awState": awState}
	if h.autoState != "" {
		body["autoStart"] = h.autoState
	}
	if h.result != nil {
		body["restart"] = map[string]string{"nonce": h.result.nonce, "result": h.result.result}
	}
	b, _ := json.Marshal(body)
	c := withPolicy(h.hc, h.hc.Timeout, sameHostOnly)
	req, _ := http.NewRequest(http.MethodPut, strings.TrimRight(cfg.CockpitURL, "/")+"/api/core/detector/health?deviceId="+url.QueryEscape(cfg.DeviceID), strings.NewReader(string(b)))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	resp, code, err := do(c, req)
	if code == http.StatusNotFound {
		h.dead = true
		log.Print("服务端不认识 /detector/health（版本较老），不再报告检测程序状态")
		return
	}
	if err != nil {
		if !h.failed {
			log.Printf("检测程序状态没报出去（不影响同步 / 心跳）：%v", err)
		}
		h.failed = true
		return
	}
	h.failed, h.last, h.result = false, key, nil
	var r struct {
		RestartNonce string `json:"restartNonce"`
	}
	_ = json.Unmarshal(resp, &r)
	h.syncStatus(awState)
	if r.RestartNonce != "" {
		h.restart(cfg, r.RestartNonce, now)
	}
}

func (h *healthReporter) syncStatus(awState string) {
	_ = updateState(h.state, func(s *State) { s.Health.AwState, s.Health.At = awState, time.Now() })
}

// restart：只认 nonce。已经处理过的不再理；ActivityWatch 连得上就说「本来就在运行」；
// 否则用本机命令拉起，30 秒内至多一次。
func (h *healthReporter) restart(cfg Config, nonce string, now time.Time) {
	if !nonceRe.MatchString(nonce) {
		return
	}
	var st State
	_ = loadJSON(h.state, &st)
	if st.Health.RestartNonce == nonce {
		return
	}
	_ = updateState(h.state, func(s *State) { s.Health.RestartNonce = nonce }) // 先记下：崩在中途也不重放
	res := "already_running"
	if !h.reachable(cfg) {
		if argv := h.command(cfg); len(argv) == 0 {
			res = "unsupported"
		} else {
			res = h.start(argv, now)
		}
	}
	h.result = &struct{ nonce, result string }{nonce, res}
}

// command：本机配置的 awStartCommand，没有就按系统缺省找。**只来自本机**，没有服务端的份。
func (h *healthReporter) command(cfg Config) []string {
	if len(cfg.AwStartCommand) > 0 {
		return cfg.AwStartCommand
	}
	return h.resolve()
}

func (h *healthReporter) canStart(now time.Time) bool {
	return h.lastRun.IsZero() || now.Sub(h.lastRun) >= restartEvery
}

// start：按钮和自动拉起共用的唯一一条启动路径。30 秒内已经拉过 → failed（不再拉）。
func (h *healthReporter) start(argv []string, now time.Time) string {
	if !h.canStart(now) {
		return "failed"
	}
	h.lastRun = now
	if err := h.spawn(argv); err != nil {
		log.Printf("拉起 ActivityWatch 失败：%v", err)
		return "failed"
	}
	log.Print("已按本机配置拉起 ActivityWatch")
	return "started"
}

// auto：每次心跳尝试之后调一次（awState 同 report）。连续两次 unreachable 才拉；连拉 3 次没回来就停手，
// 直到又看到 ActivityWatch 连得上；paused / disabled / 配置关掉 / 没有可运行的命令都不拉。
func (h *healthReporter) auto(cfg Config, awState string, now time.Time) {
	if cfg.AwAutoStart != nil && !*cfg.AwAutoStart {
		h.autoState = "off"
		return
	}
	if h.autoState == "" || h.autoState == "off" {
		h.autoState = "idle"
	}
	switch awState {
	case "unreachable":
		h.streak++
	case "ok", "no_window":
		h.streak, h.tries, h.gaveUp, h.noCmdLogged = 0, 0, false, false
		if h.autoState == "gave_up" || h.autoState == "unsupported" {
			h.autoState = "idle"
		}
		return
	default: // paused / disabled / 说不准：不动，也不累计
		h.streak = 0
		return
	}
	if h.streak < autoStreak || h.gaveUp {
		return
	}
	argv := h.command(cfg)
	if len(argv) == 0 {
		h.autoState = "unsupported"
		if !h.noCmdLogged {
			h.noCmdLogged = true
			log.Print("ActivityWatch 连不上，但本机没有可运行的启动命令（配置 awStartCommand），不自动拉起")
		}
		return
	}
	if !h.canStart(now) {
		return
	}
	if h.tries >= autoMaxTries {
		h.gaveUp, h.autoState = true, "gave_up"
		log.Printf("自动拉起了 %d 次 ActivityWatch 仍连不上，不再自动重试（面板上的按钮还能用）", autoMaxTries)
		return
	}
	h.tries++
	if h.start(argv, now) == "started" {
		h.autoState = "started"
	}
}
