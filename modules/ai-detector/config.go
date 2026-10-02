package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
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
	// 终端类程序按标签页（程序 + 标题）各自成段，契约「按标签页分段」。nil = 开；名单 nil = defaultTabApps。
	SegmentByTitle     *bool    `json:"segmentByTitle,omitempty"`
	SegmentByTitleApps []string `json:"segmentByTitleApps,omitempty"`
	// Privacy / Idle：与网页设置（contracts/detector.settings.v1）同形状；网页上设过就整节换成网页的。
	Privacy     Privacy `json:"privacy"`
	Idle        Idle    `json:"idle"`
	ArchiveDays int     `json:"archiveDays,omitempty"`
	// v0.3：在场心跳（ai-detector.presence.v1）与状态文件桥（ai-detector.agent-status-bridge.v1），都默认关，只在 run 里跑。
	Presence          bool     `json:"presence"`
	PresenceSeconds   float64  `json:"presenceSeconds,omitempty"` // 5–300，越界按 15
	AgentStatusFile   string   `json:"agentStatusFile"`           // 空 = 关
	AgentStatusIgnore []string `json:"agentStatusIgnore"`         // key 前缀，命中的条目整个忽略

	dir      string   // 配置目录；readConfig 填。空（测试直接构造 Config）= 不写留档、代号只在内存里
	warnings []string // 配置里不认识的 privacy / idle 键（想关强制脱敏也落在这里），readConfig 填
}

// Privacy：可选隐私项，语义见 contracts/detector.settings.v1「privacy 节」。
// 缺省为「开」的布尔用 *bool：零值（没写这个键）必须是更保守的那一边。
type Privacy struct {
	Paths         string   `json:"paths,omitempty"` // "full"（缺省）| "half" | "off"
	PathWhitelist []string `json:"pathWhitelist,omitempty"`
	Titles        string   `json:"titles,omitempty"` // "keep"（缺省）| "pseudonymize" | "drop"
	AppOnly       *bool    `json:"appOnly,omitempty"`
	AppOnlyApps   []string `json:"appOnlyApps,omitempty"` // nil = 用顶层 appOnlyApps
	Browser       string   `json:"browser,omitempty"`     // "domain"（缺省）| "full" | "off"
	QueryStrings  *bool    `json:"queryStrings,omitempty"`
	Emails        *bool    `json:"emails,omitempty"`
	Phones        *bool    `json:"phones,omitempty"`
	Addresses     *bool    `json:"addresses,omitempty"`
	IPs           *bool    `json:"ips,omitempty"`
	Usernames     *bool    `json:"usernames,omitempty"`
	LongNumbers   *bool    `json:"longNumbers,omitempty"`
}

// Idle：离开判定，语义见 contracts/detector.settings.v1「idle 节」。零值 = 全关 = 按 ActivityWatch 原样。
type Idle struct {
	AfkThresholdMinutes float64  `json:"afkThresholdMinutes,omitempty"`
	AudibleAsPresent    bool     `json:"audibleAsPresent,omitempty"`
	FocusAppsEnabled    bool     `json:"focusAppsEnabled,omitempty"`
	FocusApps           []string `json:"focusApps,omitempty"`       // nil = defaultFocusApps
	FocusMaxMinutes     float64  `json:"focusMaxMinutes,omitempty"` // 0 = 60
	IdleSuggestions     bool     `json:"idleSuggestions,omitempty"`
}

func on(b *bool) bool { return b == nil || *b }

func focusMax(i Idle) float64 {
	if i.FocusMaxMinutes <= 0 {
		return 60
	}
	return i.FocusMaxMinutes
}

// check：枚举写错、白名单正则编译不过都算错——这一轮不上传（同规则文件写坏），别静默当缺省用。
func (p Privacy) check() error {
	enum := func(field, v string, ok ...string) error {
		if v == "" {
			return nil
		}
		for _, o := range ok {
			if v == o {
				return nil
			}
		}
		return fmt.Errorf("privacy.%s 只能是 %s，现在是 %q", field, strings.Join(ok, " / "), v)
	}
	if err := enum("paths", p.Paths, "full", "half", "off"); err != nil {
		return err
	}
	if err := enum("titles", p.Titles, "keep", "pseudonymize", "drop"); err != nil {
		return err
	}
	if err := enum("browser", p.Browser, "domain", "full", "off"); err != nil {
		return err
	}
	for i, w := range p.PathWhitelist {
		if _, err := regexp.Compile(w); err != nil {
			return fmt.Errorf("privacy.pathWhitelist 第 %d 条正则写错了：%w", i+1, err)
		}
	}
	return nil
}

// unknownKeys 列出 privacy / idle 节里本程序不认识的键。强制脱敏没有配置开关，
// 写 "secrets": false 之类只会落到这里：忽略 + 警告。
func unknownKeys(raw []byte) []string {
	var doc map[string]map[string]json.RawMessage
	_ = json.Unmarshal(raw, &doc) // 整个文件的合法性由 loadJSON 负责
	var out []string
	for sec, known := range map[string]any{"privacy": Privacy{}, "idle": Idle{}} {
		keys := map[string]bool{}
		t := reflect.TypeOf(known)
		for i := 0; i < t.NumField(); i++ {
			keys[strings.Split(t.Field(i).Tag.Get("json"), ",")[0]] = true
		}
		for k := range doc[sec] {
			if !keys[k] {
				out = append(out, sec+"."+k)
			}
		}
	}
	sort.Strings(out)
	return out
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

// defaultFocusApps：阅读器、会议软件（idle.focusApps 为 null 时用）。
var defaultFocusApps = []string{
	"Acrobat", "AcroRd32", "Acrobat Reader", "Adobe Acrobat", "SumatraPDF", "okular", "evince", "Preview",
	"FoxitPDFReader", "Foxit Reader", "wps", "wpspdf", "Zotero", "calibre", "ebook-viewer", "KOReader",
	"wemeet", "WeMeet", "腾讯会议", "TencentMeeting", "Zoom", "zoom.us", "Teams", "ms-teams", "Microsoft Teams",
	"Feishu", "Lark", "飞书", "DingTalk", "钉钉", "Webex", "CiscoWebexStart", "Google Meet",
}

// defaultTabApps：终端（segmentByTitleApps 为 null 时用）。只增。
var defaultTabApps = []string{
	"gnome-terminal", "gnome-terminal-server", "org.gnome.Terminal", "org.gnome.Ptyxis", "ptyxis",
	"org.gnome.Console", "kgx", "kitty", "Alacritty", "wezterm", "wezterm-gui", "org.wezfurlong.wezterm",
	"konsole", "org.kde.konsole", "xterm", "tilix", "com.gexperts.Tilix", "foot", "footclient", "terminator",
	"xfce4-terminal", "ghostty", "com.mitchellh.ghostty", "Terminal", "iTerm2", "iTerm", "WindowsTerminal",
	"cmd", "powershell", "pwsh", "Warp", "dev.warp.Warp", "Hyper", "Tabby",
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
		// 把缺省值写出来，用户打开文件就看得见有哪些可选项。
		Privacy:           Privacy{Paths: "full", Titles: "keep", Browser: "domain"},
		ArchiveDays:       30,
		PresenceSeconds:   15,
		AgentStatusIgnore: []string{},
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
	// SentUntil：上一次整轮成功时的「现在 − G」；结束不晚于它的收口段那一轮已处理过，不再发。
	// SentParams 是当时的合并参数，变了就不跳过（契约「按标签页分段」）。
	SentUntil  time.Time `json:"sentUntil"`
	SentParams string    `json:"sentParams,omitempty"`
	// Agents：状态文件桥的 key → 运行（v0.3）。与上面的游标由不同的 goroutine 写，都经 updateState。
	Agents map[string]*agentRun `json:"agents,omitempty"`
}

var stateMu sync.Mutex

// updateState：读—改—写状态文件。同步一轮（游标）和状态文件桥（Agents）在同一进程的两个 goroutine 里，
// 各自只改自己那几个字段，整份读改写放在一把锁里，谁也不会把对方刚写的盖回旧值。
func updateState(path string, f func(*State)) error {
	stateMu.Lock()
	defer stateMu.Unlock()
	var st State
	_ = loadJSON(path, &st)
	f(&st)
	return saveJSON(path, st)
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
// 用系统的建议锁（Unix flock、Windows LockFileEx，见 lock_*.go），不用「pid 文件 + 看 pid 活没活着」：
// 进程一退出（包括崩溃、断电、容器被杀）系统就收回锁，残留的文件挡不住任何人。pid 判活在容器里必错——
// 程序总是 pid 1，重启后旧文件里的「pid 1」看起来就是自己 / 活着的，于是永远拒绝；实机重启后 pid 复用也一样。
// 文件里的 pid 只用来在报错时告诉人是谁拿着。文件本身不删（Windows 上删不了打开着的文件，也没必要）。
func acquireLock(path string) (release func(), err error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := lockFile(f); err != nil {
		f.Close()
		if errors.Is(err, errLocked) {
			b, _ := os.ReadFile(path)
			return nil, fmt.Errorf("另一个 ai-detector（pid %s）正在同步这个配置目录；先停掉它", strings.TrimSpace(string(b)))
		}
		return nil, fmt.Errorf("拿不到锁 %s：%w", path, err)
	}
	_ = f.Truncate(0)
	_, _ = f.WriteAt([]byte(strconv.Itoa(os.Getpid())+"\n"), 0)
	return func() { f.Close() }, nil // 关掉文件即释放锁
}

var errLocked = errors.New("锁被别的进程拿着")
