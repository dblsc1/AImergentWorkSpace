package main

import (
	"strings"
	"testing"
)

func TestScrub(t *testing.T) {
	cases := []struct{ in, want string }{
		{"回复 alice.w+tag@example.co.uk 的邮件", "回复 [邮箱] 的邮件"},
		{"客户 +86 138 1234 5678 回电", "客户 [电话] 回电"},
		{"13812345678 未接来电", "[电话] 未接来电"},
		{"call 415-555-0199 now", "call [电话] now"},
		{"订单 20260926000123456789", "订单 [数字]"},
		{"身份证 110101199001011234", "身份证 [数字]"},
		{"会议 2026-09-28 10:00 第 12 周", "会议 2026-09-28 10:00 第 12 周"}, // 日期、短数字不动
		{"https://mail.example.com/u/0/?q=secret#inbox - 页面", "mail.example.com - 页面"},
		{"see http://a.b.cn/x?page=2&id=987654321", "see a.b.cn"},
	}
	for _, c := range cases {
		if got := scrub(c.in); got != c.want {
			t.Errorf("scrub(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestAppOnlyKeepsOnlyAppName(t *testing.T) {
	r := newRedactor(Config{AppOnlyApps: defaultAppOnly, BrowserApps: defaultBrowsers})
	for _, app := range []string{"WeChat.exe", "TelegramDesktop", "OUTLOOK.EXE", "Microsoft Outlook", "KeePassXC", "1Password", "钉钉", "ms-teams.exe"} {
		title, key := r.window(app, "张三：明天把合同发我 zhang@corp.com", nil)
		if title != "" || strings.Contains(key, "张三") {
			t.Errorf("%s: title=%q key=%q", app, title, key)
		}
	}
	// 不在名单里的照常保留（脱敏后的）标题。
	if title, _ := r.window("code", "main.go — garden", nil); title != "main.go — garden" {
		t.Errorf("code title=%q", title)
	}
}

func TestBrowserKeepsDomainAndTitleOnly(t *testing.T) {
	r := newRedactor(Config{AppOnlyApps: defaultAppOnly, BrowserApps: defaultBrowsers})
	tab := &webTab{URL: "https://github.com/dblsc1/repo/pull/12?diff=split&token=xyz", Title: "Fix merge by someone@x.io"}
	title, key := r.window("firefox", "Fix merge — Mozilla Firefox", tab)
	if title != "github.com · Fix merge by [邮箱]" {
		t.Fatalf("title=%q", title)
	}
	if strings.Contains(title, "pull") || strings.Contains(title, "token") || strings.Contains(key, "pull") {
		t.Fatalf("URL path/query leaked: %q %q", title, key)
	}
	// 同域名换页面：键相同，能合成一段。
	_, key2 := r.window("firefox", "", &webTab{URL: "https://github.com/other", Title: "Other"})
	if key != key2 {
		t.Fatalf("keys differ: %q %q", key, key2)
	}
	// 无痕窗口：只留程序名。
	if title, _ := r.window("chrome.exe", "secret", &webTab{URL: "https://x.com", Title: "secret", Incognito: true}); title != "" {
		t.Fatalf("incognito title=%q", title)
	}
	// 没装浏览器扩展：标题里的网址也只剩域名。
	if title, _ := r.window("Google Chrome", "https://bank.example.com/acct?id=1 - Google Chrome", nil); title != "bank.example.com - Google Chrome" {
		t.Fatalf("no-ext title=%q", title)
	}
}

func TestTitleKey(t *testing.T) {
	same := [][2]string{
		{"● main.go — garden — Visual Studio Code", "plot.gd — garden — Visual Studio Code"},
		{"(3) Inbox", "(12) Inbox"},
		{"notes.md*", "notes.md"},
	}
	for _, p := range same {
		if titleKey(p[0]) != titleKey(p[1]) {
			t.Errorf("%q vs %q: %q != %q", p[0], p[1], titleKey(p[0]), titleKey(p[1]))
		}
	}
	if titleKey("a.go — garden — Code") == titleKey("a.go — other — Code") {
		t.Error("different projects must not merge")
	}
}
