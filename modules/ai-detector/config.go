package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Config 对应 <配置目录>/ai-detector.json，字段语义见 README「配置参考」。
// 程序每轮重读一次：用户手改、或另一个进程执行 pause / resume，下一轮就生效。
type Config struct {
	Enabled           bool    `json:"enabled"`
	Paused            bool    `json:"paused"`
	CockpitURL        string  `json:"cockpitUrl"`
	DeviceToken       string  `json:"deviceToken"`
	DeviceID          string  `json:"deviceId"`
	ActivityWatchURL  string  `json:"activityWatchUrl"`
	IntervalMinutes   float64 `json:"intervalMinutes"`
	MergeGapMinutes   float64 `json:"mergeGapMinutes"`
	MinSegmentMinutes float64 `json:"minSegmentMinutes"`
	MaxBacklogHours   float64 `json:"maxBacklogHours"`
	RulesFile         string  `json:"rulesFile"`
	ClassifierURL     string  `json:"classifierUrl"`
	// ClassifierToken 是分类服务自己的凭据，**与 deviceToken 无关**：分类服务是任意第三方
	// 地址，HoneyComb 的设备令牌绝不发给它。空 = 不带 Authorization 头。
	ClassifierToken string `json:"classifierToken"`
	// WindowBucket / AfkBucket：主机名对不上时手动指定 ActivityWatch 桶 id，空 = 按主机名找。
	WindowBucket string   `json:"windowBucket"`
	AfkBucket    string   `json:"afkBucket"`
	AppOnlyApps  []string `json:"appOnlyApps"`
	BrowserApps  []string `json:"browserApps"`
}

// 默认名单：写的是各系统上 ActivityWatch 实际报出来的程序名（Windows 是 exe 名，
// Linux 是 wm_class，macOS 是应用名），经 normApp 归一后比较，大小写 / .exe / 空格都无所谓。
var defaultAppOnly = []string{
	// 聊天
	"WeChat", "Weixin", "QQ", "TIM", "Telegram", "TelegramDesktop", "WhatsApp", "Signal",
	"Slack", "Discord", "DingTalk", "钉钉", "Feishu", "Lark", "飞书", "Teams", "ms-teams",
	"Microsoft Teams", "Skype", "Messages", "Element", "Zoom",
	// 邮件
	"Thunderbird", "Outlook", "Microsoft Outlook", "olk", "Mail", "Foxmail", "Evolution", "Geary", "Spark",
	// 密码管理器
	"1Password", "Bitwarden", "KeePass", "KeePassXC", "LastPass", "Enpass", "Dashlane",
	"Keychain Access", "seahorse", "Passwords",
}

var defaultBrowsers = []string{
	"chrome", "Google Chrome", "google-chrome", "chromium", "chromium-browser", "firefox",
	"firefox-esr", "msedge", "Microsoft Edge", "microsoft-edge", "Safari", "brave", "Brave Browser",
	"brave-browser", "opera", "vivaldi", "vivaldi-stable", "Arc", "zen", "librewolf",
}

func configDir() (string, error) {
	if d := os.Getenv("AI_DETECTOR_HOME"); d != "" {
		return d, nil
	}
	// os.UserConfigDir 正好就是约定的三处：%AppData% / ~/Library/Application Support /
	// $XDG_CONFIG_HOME 或 ~/.config。
	d, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(d, "honeycomb"), nil
}

func defaultConfig(dir string) (Config, error) {
	b := make([]byte, 8)
	// deviceId 是服务端防重键的一部分：随机源坏了宁可 init 失败，也不能生成一个全零、
	// 与别的设备撞车的 id。
	if _, err := rand.Read(b); err != nil {
		return Config{}, fmt.Errorf("生成 deviceId 失败：%w", err)
	}
	return Config{
		Enabled:           false, // 隐私默认值：不显式打开就不同步
		CockpitURL:        "http://localhost:8800",
		DeviceID:          "dev_" + hex.EncodeToString(b),
		ActivityWatchURL:  "http://localhost:5600/api/0",
		IntervalMinutes:   5,
		MergeGapMinutes:   5,
		MinSegmentMinutes: 3,
		MaxBacklogHours:   72,
		RulesFile:         filepath.Join(dir, "rules.json"),
		AppOnlyApps:       defaultAppOnly,
		BrowserApps:       defaultBrowsers,
	}, nil
}

func minutes(m float64) time.Duration { return time.Duration(m * float64(time.Minute)) }

func loadJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

// saveJSON 先写同目录下的唯一临时文件再改名：写到一半断电不会留下半个配置 / 半个游标，
// 两个进程同时写也不会互相写进对方的临时文件。
//
// 权限 0600 只在 macOS / Linux 有意义。Windows 上 Go 的文件权限位只管只读属性，
// 保护配置（里面有设备令牌）靠的是 %APPDATA% 目录默认的「仅本用户」访问控制——
// 我们没有另设 ACL，README 与契约里如实写明。
func saveJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	_, werr := f.Write(append(b, '\n'))
	cerr := f.Close()
	if werr == nil {
		werr = cerr
	}
	if werr == nil {
		werr = os.Chmod(tmp, 0o600) // CreateTemp 本来就是 0600，这里是写明意图
	}
	if werr == nil {
		werr = os.Rename(tmp, path)
	}
	if werr != nil {
		os.Remove(tmp)
	}
	return werr
}

// State 是程序自己的记账，不是配置。
type State struct {
	// Cursor 之前的活动都已处理完（上传了，或按规则丢了）。
	Cursor time.Time `json:"cursor"`
	// Active 记上一轮是否处于「开着且没暂停」。从 false 变 true 时游标直接跳到现在：
	// 关着 / 暂停期间的活动以后也不补传（隐私默认值第 2 条）。
	Active      bool      `json:"active"`
	LastRunAt   time.Time `json:"lastRunAt"`
	LastOutcome string    `json:"lastOutcome"`
	// FailedParams：上一轮上传失败时用的合并参数（G / M），成功后清空。失败后改了 G / M，
	// 重算出来的段起点会变，服务端按 startAt 防重就对不上，可能多出重复建议——要在日志里说出来。
	FailedParams string `json:"failedParams,omitempty"`
}

type paths struct{ config, state, log, lock string }

func pathsIn(dir string) paths {
	return paths{
		config: filepath.Join(dir, "ai-detector.json"),
		state:  filepath.Join(dir, "ai-detector.state.json"),
		log:    filepath.Join(dir, "ai-detector.log"),
		lock:   filepath.Join(dir, "ai-detector.lock"),
	}
}

// acquireLock 让同一个配置目录同时只有一个进程在同步：run 常驻时拿着它，once 拿不到就拒绝。
// 两个进程一起跑，会各自从同一个游标出发、各自回写状态，后写的把先写的游标盖回去。
//
// 用「O_EXCL 建 pid 文件」而不是系统文件锁：标准库没有跨平台的文件锁（Windows 要
// LockFileEx，得引 x/sys）。进程崩了留下的旧锁靠「pid 还活着吗」判断后接管。
// ponytail: pid 被系统复用时会误判为「还有人在跑」，删掉 ai-detector.lock 即可；
// 真遇到了再换 x/sys 的文件锁。
func acquireLock(path string) (release func(), err error) {
	for attempt := 0; attempt < 2; attempt++ {
		f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			fmt.Fprintf(f, "%d\n", os.Getpid())
			f.Close()
			return func() { os.Remove(path) }, nil
		}
		if !errors.Is(err, os.ErrExist) {
			return nil, err
		}
		b, _ := os.ReadFile(path)
		pid, perr := strconv.Atoi(strings.TrimSpace(string(b)))
		if perr != nil {
			// 空的 / 读不懂：可能正好是别人刚建好文件、还没写进 pid。新文件不碰，旧的才当残骸接管。
			if fi, err := os.Stat(path); err == nil && time.Since(fi.ModTime()) < lockGrace {
				return nil, fmt.Errorf("另一个 ai-detector 可能正在启动，稍后重试")
			}
		} else if pid == os.Getpid() || processAlive(pid) {
			// pid 是自己：本进程已经拿着（或 pid 被复用），都不能删。
			return nil, fmt.Errorf("另一个 ai-detector（pid %d）正在同步这个配置目录；先停掉它，"+
				"或确认它已退出后删除 %s", pid, path)
		}
		os.Remove(path) // 旧锁：进程已经不在了
	}
	return nil, fmt.Errorf("拿不到锁 %s", path)
}

const lockGrace = 5 * time.Second

func processAlive(pid int) bool {
	p, err := os.FindProcess(pid)
	if err != nil {
		return false // Windows：进程不存在时 FindProcess 就失败
	}
	if runtime.GOOS == "windows" {
		p.Release()
		return true
	}
	// Unix 上 FindProcess 总是成功，发 0 号信号才知道活没活着。
	return p.Signal(syscall.Signal(0)) == nil
}
