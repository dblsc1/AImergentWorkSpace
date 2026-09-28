package main

import (
	"net/url"
	"regexp"
	"strings"
)

// 本机脱敏。规则写在 module_docs/contract.md「隐私默认值」，这里是它的唯一实现。
// 顺序有讲究：先各种网址 / 路径（里面可能有邮箱、长数字），再邮箱，再电话，最后长数字——
// 反过来的话，长数字规则会先把电话号吃成 [数字]，电话规则就永远测不到。
var (
	// 任意 scheme：http(s)、ftp、smb、ssh、sftp、file……有主机的只留主机，没有主机
	// （file:///C:/…）的整个换成 [路径]。
	reURL = regexp.MustCompile(`\b[A-Za-z][A-Za-z0-9+.-]*://[^\s"'<>]*`)
	// Windows 共享路径 \\server\share\…：服务器名、共享名本身就可能是内部信息，整个不留。
	reUNC = regexp.MustCompile(`\\\\[^\s"'<>]+`)
	// 没写 scheme 的「域名/路径」：github.com/x/y?token=…，只留域名。
	reHostPath = regexp.MustCompile(`\b((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?)/[^\s"'<>]*`)
	// 本机绝对路径（C:\Users\张三\… 、/home/zhangsan/…）：路径里有用户名和目录结构，
	// 只留最后的文件名——编辑器标题靠文件名认得出是哪件事。
	rePath  = regexp.MustCompile(`(?:\b[A-Za-z]:[\\/]|(?:^|\s)~?/)(?:[^\s\\/"'<>]+[\\/])+([^\s\\/"'<>]*)`)
	reEmail = regexp.MustCompile(`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`)
	// 国际前缀可选 + 3~4 位两组 + 4 位；中间允许空格 / 连字符。
	// 故意不匹配 2026-09-28 这种日期（第二组只有 2 位），日期在标题里很常见，也不敏感。
	rePhone  = regexp.MustCompile(`(\+\d{1,3}[\s-]?)?\b\d{3,4}[\s-]?\d{3,4}[\s-]?\d{4}\b`)
	reDigits = regexp.MustCompile(`\d{6,}`)
)

func scrub(s string) string {
	s = reURL.ReplaceAllStringFunc(s, func(u string) string {
		if p, err := url.Parse(u); err == nil && p.Hostname() != "" && !strings.EqualFold(p.Scheme, "file") {
			return p.Hostname()
		}
		return "[路径]"
	})
	s = reUNC.ReplaceAllString(s, "[路径]")
	s = reHostPath.ReplaceAllString(s, "$1")
	s = rePath.ReplaceAllStringFunc(s, func(m string) string {
		lead := m[:len(m)-len(strings.TrimLeft(m, " \t"))] // 保留前面那个空白
		base := rePath.FindStringSubmatch(m)[1]
		if base == "" {
			return lead + "[路径]"
		}
		return lead + base
	})
	s = reEmail.ReplaceAllString(s, "[邮箱]")
	s = rePhone.ReplaceAllString(s, "[电话]")
	s = reDigits.ReplaceAllString(s, "[数字]")
	return strings.TrimSpace(s)
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
	appOnly  map[string]bool
	browsers map[string]bool
}

func newRedactor(c Config) redactor {
	return redactor{appOnly: nameSet(c.AppOnlyApps), browsers: nameSet(c.BrowserApps)}
}

// webTab 是浏览器扩展桶里与这段窗口时间重叠最多的那条标签页记录，没有就是 nil。
type webTab struct {
	URL, Title string
	Incognito  bool
}

// window 返回可以离开本机的 (标题, 合并键)。app 原样保留——程序名本身不是隐私，
// 而且人确认时要靠它认出这段是什么。
func (r redactor) window(app, title string, tab *webTab) (string, string) {
	a := normApp(app)
	// 浏览器没有对得上的标签页记录（没装扩展、扩展在无痕窗口里默认不运行、这段时间
	// 扩展没报）时，窗口标题就是唯一信息——而无痕窗口的标题、地址栏里的网址都在里面。
	// 分不清是不是无痕，就按最保守的处理：只留程序名。
	if r.appOnly[a] || (r.browsers[a] && (tab == nil || tab.Incognito)) {
		return "", a
	}
	if r.browsers[a] {
		host := ""
		if p, err := url.Parse(tab.URL); err == nil {
			host = p.Hostname()
		}
		t := scrub(tab.Title)
		if host == "" {
			return t, a + "\x00" + titleKey(t)
		}
		if t == "" {
			return host, a + "\x00" + host
		}
		return host + " · " + t, a + "\x00" + host
	}
	t := scrub(title)
	return t, a + "\x00" + titleKey(t)
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
