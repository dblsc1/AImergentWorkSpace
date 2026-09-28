package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// Config 对应 <配置目录>/ai-detector.json，字段语义见 README「配置参考」。
// 程序每轮重读一次：用户手改、或另一个进程执行 pause / resume，下一轮就生效。
type Config struct {
	Enabled           bool     `json:"enabled"`
	Paused            bool     `json:"paused"`
	CockpitURL        string   `json:"cockpitUrl"`
	DeviceToken       string   `json:"deviceToken"`
	DeviceID          string   `json:"deviceId"`
	ActivityWatchURL  string   `json:"activityWatchUrl"`
	IntervalMinutes   float64  `json:"intervalMinutes"`
	MergeGapMinutes   float64  `json:"mergeGapMinutes"`
	MinSegmentMinutes float64  `json:"minSegmentMinutes"`
	MaxBacklogHours   float64  `json:"maxBacklogHours"`
	RulesFile         string   `json:"rulesFile"`
	ClassifierURL     string   `json:"classifierUrl"`
	AppOnlyApps       []string `json:"appOnlyApps"`
	BrowserApps       []string `json:"browserApps"`
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

func defaultConfig(dir string) Config {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
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
	}
}

func minutes(m float64) time.Duration { return time.Duration(m * float64(time.Minute)) }

func loadJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

// saveJSON 先写临时文件再改名：写到一半断电不会留下半个配置 / 半个游标。
// 0600：配置里有设备令牌。
func saveJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
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
}

type paths struct{ config, state, log string }

func pathsIn(dir string) paths {
	return paths{
		config: filepath.Join(dir, "ai-detector.json"),
		state:  filepath.Join(dir, "ai-detector.state.json"),
		log:    filepath.Join(dir, "ai-detector.log"),
	}
}
