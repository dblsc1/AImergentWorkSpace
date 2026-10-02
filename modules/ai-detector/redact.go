package main

import (
	"net"
	"net/url"
	"os"
	"os/user"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// 本机脱敏的可选部分（隐私选项，contracts/detector.settings.v1）。强制脱敏在 secrets.go。
var (
	// 任意 scheme：http(s)、ftp、smb、ssh、sftp、file……有主机的只留主机，没有主机
	// （file:///C:/…）的整个换成 [路径]。
	reURL = regexp.MustCompile(`\b[A-Za-z][A-Za-z0-9+.-]*://[^\s"'<>]*`)
	// Windows 共享路径 \\server\share\…：服务器名、共享名本身就可能是内部信息，整个不留。
	reUNC = regexp.MustCompile(`\\\\[^\s"'<>]+`)
	// 没写 scheme 的「域名/路径」：github.com/x/y?token=…，只留域名。
	reHostPath = regexp.MustCompile(`\b((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?)/[^\s"'<>]*`)
	// 本机绝对路径（C:\Users\张三\… 、/home/zhangsan/…、~/…）：路径里有用户名和目录结构，
	// 只留最后的文件名——编辑器标题靠文件名认得出是哪件事。
	// 路径前面只要不是字母数字、下划线、斜杠就算开头（「打开/home/…」「(/home/…)」「：/home/…」），
	// RE2 没有后顾断言，所以把前一个字符捕获进 $1 再原样放回。and/or、w/o、10:30/11:00
	// 这种斜杠前是字母数字的不动。以 / 开头的至少要有一层目录，免得「A /B」这类被误伤。
	rePath  = regexp.MustCompile(`(^|[^\w/])(?:(?:[A-Za-z]:|~)[\\/](?:[^\s\\/"'<>]+[\\/])*|/(?:[^\s\\/"'<>]+/)+)([^\s\\/"'<>]*)`)
	reEmail = regexp.MustCompile(`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`)
	// 国际前缀可选 + 3~4 位两组 + 4 位；中间允许空格 / 连字符。
	// 故意不匹配 2026-09-28 这种日期（第二组只有 2 位），日期在标题里很常见，也不敏感。
	rePhone  = regexp.MustCompile(`(\+\d{1,3}[\s-]?)?\b\d{3,4}[\s-]?\d{3,4}[\s-]?\d{4}\b`)
	reDigits = regexp.MustCompile(`\d{6,}`)

	reHomeUser = regexp.MustCompile(`(?i)([\\/](?:home|Users)[\\/])[^\\/\s"'<>]+`)
	// 终端提示符 alice@thinkpad: ~/work
	rePrompt = regexp.MustCompile(`\b[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:`)
	reIPv4   = regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`)
	// IPv6 候选宽松地找，真假交给 net.ParseIP；「10:30:45」解析不了，「std::vector」里的「d::」没有数字，都不动。
	reIPv6 = regexp.MustCompile(`[0-9A-Fa-f]*(?::[0-9A-Fa-f]*){2,8}`)
	// 中文地址（保守）：路名前至多 6 个汉字 + 路/街/… + 门牌 + 号，后面跟着的楼/栋/单元/室一起盖；
	// 或 小区/花园/… + 数字 + 栋/幢/号楼。误伤与漏网见 contracts/detector.settings.v1。
	reAddrStreet = regexp.MustCompile(`\p{Han}{0,6}(?:路|街|大道|巷|弄|胡同)[0-9０-９一二三四五六七八九十百零〇]+号(?:[0-9A-Za-z\p{Han}-]{0,12}?(?:号楼|栋|幢|座|单元|层|室|楼))*`)
	reAddrEstate = regexp.MustCompile(`\p{Han}{1,8}(?:小区|花园|公寓|家园|新村|苑)[0-9一二三四五六七八九十]+(?:栋|幢|号楼)(?:[0-9一二三四五六七八九十]+(?:单元|室|层))*[0-9]*`)
)

// text 是可选隐私项的文字部分（强制脱敏已经做过）。白名单命中的片段原样保留，
// 片段之间的部分各自处理——所以白名单要保整条路径，就得让正则把整条路径包进去。
func (r redactor) text(s string) string {
	var spans [][]int
	for _, w := range r.white {
		for _, m := range w.FindAllStringIndex(s, -1) {
			if m[1] > m[0] {
				spans = append(spans, m)
			}
		}
	}
	if len(spans) == 0 {
		return strings.TrimSpace(r.redact(s))
	}
	sort.Slice(spans, func(i, j int) bool { return spans[i][0] < spans[j][0] })
	var b strings.Builder
	last := 0
	for _, m := range spans {
		if m[1] <= last {
			continue
		}
		if m[0] > last {
			b.WriteString(r.redact(s[last:m[0]]))
		} else {
			m = []int{last, m[1]} // 与上一段重叠：接着上一段的尾巴
		}
		b.WriteString(s[m[0]:m[1]])
		last = m[1]
	}
	b.WriteString(r.redact(s[last:]))
	return strings.TrimSpace(b.String())
}

// redact 按 privacy 各项处理一段文字。顺序有讲究：先各种网址 / 路径（里面可能有邮箱、长数字），
// 再用户名、邮箱、IP、地址、电话，最后长数字——反过来的话，长数字规则会先把电话号吃成 [数字]。
func (r redactor) redact(s string) string {
	paths := r.p.Paths
	s = reURL.ReplaceAllStringFunc(s, func(u string) string {
		p, err := url.Parse(u)
		if err == nil && p.Hostname() != "" && !strings.EqualFold(p.Scheme, "file") {
			return p.Hostname()
		}
		switch {
		case paths == "off":
			return u
		case paths == "half" && err == nil:
			return halfPath(p.Path, 0)
		}
		return "[路径]"
	})
	if paths != "off" {
		s = reUNC.ReplaceAllStringFunc(s, func(m string) string {
			if paths == "half" {
				return halfPath(m, 2) // 服务器名、共享名不留
			}
			return "[路径]"
		})
	}
	s = reHostPath.ReplaceAllString(s, "$1")
	if paths != "off" {
		s = rePath.ReplaceAllStringFunc(s, func(m string) string {
			g := rePath.FindStringSubmatch(m)
			if paths == "half" {
				return g[1] + halfPath(m[len(g[1]):], 0)
			}
			if g[2] == "" {
				return g[1] + "[路径]"
			}
			return g[1] + g[2]
		})
	}
	return r.pii(s)
}

// pii：邮箱、电话、地址、IP、用户名、长数字这些与「网址 / 路径形状」无关的选项。
func (r redactor) pii(s string) string {
	if on(r.p.Usernames) {
		s = reHomeUser.ReplaceAllString(s, "${1}[用户]")
		s = rePrompt.ReplaceAllString(s, "[用户]@[主机]:")
		for _, l := range r.literals {
			s = l.re.ReplaceAllString(s, "${1}"+l.to+"${2}")
		}
	}
	if on(r.p.Emails) {
		s = reEmail.ReplaceAllString(s, "[邮箱]")
	}
	if on(r.p.IPs) {
		s = hideIPs(s)
	}
	if on(r.p.Addresses) {
		s = reAddrStreet.ReplaceAllString(s, "[地址]")
		s = reAddrEstate.ReplaceAllString(s, "[地址]")
	}
	if on(r.p.Phones) {
		s = rePhone.ReplaceAllString(s, "[电话]")
	}
	if on(r.p.LongNumbers) {
		s = reDigits.ReplaceAllString(s, "[数字]")
	}
	return s
}

func hideIPs(s string) string {
	s = reIPv4.ReplaceAllStringFunc(s, func(m string) string {
		for _, part := range strings.Split(m, ".") {
			if n, _ := strconv.Atoi(part); n > 255 {
				return m
			}
		}
		return "[IP]"
	})
	return reIPv6.ReplaceAllStringFunc(s, func(m string) string {
		if net.ParseIP(m) != nil && strings.ContainsAny(m, "0123456789") {
			return "[IP]"
		}
		return m
	})
}

// halfPath：paths="half"。去掉盘符、~、home/Users + 用户名、/root（UNC 另去掉 drop 级：服务器名、共享名），
// 只留最后两级，分隔符统一成 /。什么都不剩 = [路径]。
func halfPath(p string, drop int) string {
	c := strings.FieldsFunc(p, func(r rune) bool { return r == '/' || r == '\\' })
	c = c[min(drop, len(c)):]
	if len(c) > 0 && (c[0] == "~" || (len(c[0]) == 2 && c[0][1] == ':')) {
		c = c[1:]
	}
	if len(c) > 0 && (strings.EqualFold(c[0], "home") || strings.EqualFold(c[0], "users")) {
		c = c[min(2, len(c)):]
	} else if len(c) > 0 && c[0] == "root" {
		c = c[1:]
	}
	if len(c) == 0 {
		return "[路径]"
	}
	return strings.Join(c[max(0, len(c)-2):], "/")
}

// containsPath：标题代号用「路径N」还是「窗口名N」。
func containsPath(s string) bool {
	return reUNC.MatchString(s) || rePath.MatchString(s) || strings.Contains(strings.ToLower(s), "file://")
}

// normApp 把各系统报上来的程序名压成一个可比较的形状：
// Windows 报 "WeChat.exe" / "OUTLOOK.EXE"，Linux 报 wm_class "TelegramDesktop"，
// macOS 报 "Microsoft Outlook"。名单与程序名都过这一道再比，名单就不用每种写法写一遍。
func normApp(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = strings.TrimSuffix(s, ".exe")
	return strings.NewReplacer(" ", "", "-", "", "_", "", ".", "").Replace(s)
}

func nameSet(names []string) map[string]bool {
	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[normApp(n)] = true
	}
	return m
}

type redactor struct {
	p        Privacy
	appOnly  map[string]bool
	browsers map[string]bool
	tabs     map[string]bool // 按标签页分段的程序（已去掉浏览器）；segmentByTitle 关 = 空
	white    []*regexp.Regexp
	literals []literal // 本机用户名、主机名（usernames 选项）
	// 离开判定（idle 节）也放这里：buildFragments 只收一个 redactor，它们和隐私项一样都是「这一轮的设置」。
	idle  Idle
	focus map[string]bool
}

type literal struct {
	re *regexp.Regexp
	to string
}

func newRedactor(c Config) redactor {
	apps := c.Privacy.AppOnlyApps
	if apps == nil {
		apps = c.AppOnlyApps
	}
	if apps == nil {
		apps = defaultAppOnly
	}
	browsers := c.BrowserApps
	if browsers == nil {
		browsers = defaultBrowsers
	}
	focus := c.Idle.FocusApps
	if focus == nil {
		focus = defaultFocusApps
	}
	r := redactor{p: c.Privacy, appOnly: nameSet(apps), browsers: nameSet(browsers), idle: c.Idle, focus: nameSet(focus)}
	if on(c.SegmentByTitle) {
		r.tabs = nameSet(tabApps(c))
		for b := range r.browsers {
			delete(r.tabs, b)
		}
	}
	for _, w := range c.Privacy.PathWhitelist {
		// 编译不过的跳过（少保留 = 更保守）；tick 开头的 check 已经把这种配置报成错了。
		if re, err := regexp.Compile(w); err == nil {
			r.white = append(r.white, re)
		}
	}
	host, _ := os.Hostname()
	name := ""
	if u, err := user.Current(); err == nil {
		name = u.Username[strings.LastIndex(u.Username, "\\")+1:] // Windows 报 DOMAIN\user
	}
	for _, l := range []literal{{nil, "[用户]"}, {nil, "[主机]"}} {
		v := name
		if l.to == "[主机]" {
			v = host
		}
		if len([]rune(v)) < 4 { // 太短的名字（如 xia、pi）当词替换误伤太大
			continue
		}
		l.re = regexp.MustCompile(`(?i)(^|[^\p{L}\p{N}_])` + regexp.QuoteMeta(v) + `($|[^\p{L}\p{N}_])`)
		r.literals = append(r.literals, l)
	}
	return r
}

// webTab 是浏览器扩展桶里与这段窗口时间重叠最多的那条标签页记录，没有就是 nil。
type webTab struct {
	URL, Title string
	Incognito  bool
}

// window 返回可以离开本机的 (标题, 合并键)。app 原样保留——程序名本身不是隐私，
// 而且人确认时要靠它认出这段是什么。第一步永远是强制脱敏。
func (r redactor) window(app, title string, tab *webTab) (string, string) {
	a := normApp(app)
	title = scrubSecrets(title)
	if tab != nil {
		t := *tab
		t.Title, t.URL = scrubSecrets(t.Title), scrubSecrets(t.URL)
		tab = &t
	}
	// 对得上 = 窗口标题以这条标签页的标题开头（浏览器窗口标题一般是「页面标题 - Google Chrome」）。
	// 扩展在无痕窗口里默认不运行，这时重叠的标签页记录可能来自同一浏览器的普通窗口——
	// 标题对不上就不能拿它的域名、标题去描述这个窗口。
	if tab != nil && (strings.TrimSpace(tab.Title) == "" || !strings.HasPrefix(strings.TrimSpace(title), strings.TrimSpace(tab.Title))) {
		tab = nil
	}
	if r.p.Titles == "drop" || (on(r.p.AppOnly) && r.appOnly[a]) {
		return "", a
	}
	if r.browsers[a] {
		// 没有对得上的标签页记录（没装扩展、扩展在无痕窗口里默认不运行、这段时间扩展没报）时，
		// 窗口标题就是唯一信息——而无痕窗口的标题、地址栏里的网址都在里面。分不清是不是无痕，
		// 就按最保守的处理：只留程序名。browser=off 同样只留程序名。
		if tab == nil || tab.Incognito || r.p.Browser == "off" {
			return "", a
		}
		host, loc := "", ""
		if p, err := url.Parse(tab.URL); err == nil {
			host, loc = p.Hostname(), p.Hostname()
			if r.p.Browser == "full" && host != "" {
				p.User = nil
				if on(r.p.QueryStrings) {
					p.RawQuery, p.Fragment, p.RawFragment, p.ForceQuery = "", "", "", false
				}
				loc = p.String()
			}
		}
		if loc != host {
			loc = r.pii(loc) // 完整网址里的邮箱、电话、用户名、长数字……照样按选项处理
		} else if on(r.p.IPs) {
			loc = hideIPs(loc)
		}
		t := r.text(tab.Title)
		if host == "" {
			return t, a + "\x00" + titleKey(t)
		}
		if t == "" {
			return loc, a + "\x00" + host
		}
		return loc + " · " + t, a + "\x00" + host
	}
	t := r.text(title)
	return t, a + "\x00" + titleKey(t)
}

func tabApps(c Config) []string {
	if c.SegmentByTitleApps == nil {
		return defaultTabApps
	}
	return c.SegmentByTitleApps
}

var (
	reTabLead = regexp.MustCompile(`^[^\p{L}\p{N}]+`)
	// 没起名字的 shell 标签页：标题是提示符「用户@主机: 当前目录」，cd 一下就变。
	reTabPrompt = regexp.MustCompile(`^([^\s@]+@[^\s:]+:)(\s|$)`)
)

// tabKey：终端标签页的合并键（契约「按标签页分段」）。t 是隐私选项处理后的标题，app 已过 normApp。
// 状态符号 / 转圈动画一直在变，不去掉的话同一个标签页会碎成很多键。
// ponytail: 只认开头的符号、结尾的程序名和「用户@主机: 目录」形状的提示符；别的在标题中间变的（进度）认不出，
// 真遇到再按程序定制。
func tabKey(t, app string) string {
	t = strings.Join(strings.Fields(t), " ")
	if m := reTabPrompt.FindStringSubmatch(t); m != nil {
		return strings.ToLower(m[1]) // 同一台主机上的 shell 标签页算一件事，换目录不拆
	}
	if m := reTitleSep.FindAllStringIndex(t, -1); len(m) > 0 {
		last := m[len(m)-1]
		if tail := normApp(t[last[1]:]); len(tail) >= 3 && strings.Contains(app, tail) {
			t = t[:last[0]]
		}
	}
	t = reTabLead.ReplaceAllString(reTitleNoise.ReplaceAllString(t, ""), "")
	return strings.ToLower(strings.TrimSpace(t))
}

var (
	reTitleNoise = regexp.MustCompile(`^(\(\d+\)\s*|[●•*]\s*)+|\s*[●•*]$`)
	reTitleSep   = regexp.MustCompile(`\s+[—–|-]\s+`)
)

// titleKey 定义「相近标题」。ponytail: 纯字符串启发式——未读计数、未保存标记、
// 编辑器「文件 — 项目 — 程序」里的文件名都不算区别；更聪明的相似度（编辑距离、
// 按程序定制）等真遇到拆错的例子再加。
func titleKey(t string) string {
	t = strings.ToLower(reTitleNoise.ReplaceAllString(t, ""))
	if parts := reTitleSep.Split(t, -1); len(parts) >= 3 {
		t = strings.Join(parts[1:], " - ")
	}
	return strings.TrimSpace(t)
}
