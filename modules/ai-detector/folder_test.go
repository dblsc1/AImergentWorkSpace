package main

import (
	"encoding/json"
	"strings"
	"testing"
)

// folder（契约 onboarding.v1「二·2」）：终端标题里第一条本机路径的最后一层目录名。
func TestFolderOf(t *testing.T) {
	cases := []struct{ in, want string }{
		{"~/garden", "garden"},
		{"/home/xia/garden", "garden"},
		{`C:\Users\张三\proj`, "proj"},
		{"~", "~"},
		{"/home/xia/garden/", "garden"},
		{"/home/xia/" + strings.Repeat("a", 64), strings.Repeat("a", 64)},
		{"/home/xia/" + strings.Repeat("a", 65), strings.Repeat("a", 64)},
		{"user@host: ~/garden", "garden"},
		{"/home/a/one — /home/b/two", "one"},
		{"Coder", ""},
		{"/", ""},
		{`C:\`, ""},
		{"and/or w/o 10:30/11:00 TCP/IP", ""},
	}
	for _, c := range cases {
		if got := folderOf(c.in); got != c.want {
			t.Errorf("folderOf(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestFolderFor(t *testing.T) {
	cases := []struct {
		name string
		p    Privacy
		app  string
		want string
	}{
		{"终端，缺省隐私", Privacy{}, "ptyxis", "garden"},
		{"paths half 照发", Privacy{Paths: "half"}, "ptyxis", "garden"},
		{"paths off 不发", Privacy{Paths: "off"}, "ptyxis", ""},
		{"titles drop 不发", Privacy{Titles: "drop"}, "ptyxis", ""},
		{"titles pseudonymize 不发", Privacy{Titles: "pseudonymize"}, "ptyxis", ""},
		{"不在终端名单（编辑器）", Privacy{}, "code", ""},
		{"Windows 程序名按名单比", Privacy{}, "WindowsTerminal.exe", "garden"},
	}
	for _, c := range cases {
		r := newRedactor(Config{Privacy: c.p})
		if got := r.folderFor(c.app, "~/garden", "garden"); got != c.want {
			t.Errorf("%s: folderFor = %q, want %q", c.name, got, c.want)
		}
	}
	// 默认终端名单里的每个程序都按终端处理。
	r := newRedactor(Config{})
	for _, app := range defaultTabApps {
		if got := r.folderFor(app, "~/garden", "garden"); got != "garden" {
			t.Errorf("%s: folderFor = %q", app, got)
		}
	}
}

// 发出的标题是空的（只发程序名 appOnly、titles drop 等）或程序是浏览器：不给 folder；
// 家目录本身（/home/<用户名>）按 usernames 选项换成代号，不发真用户名。
func TestFolderForPrivacyGates(t *testing.T) {
	r := newRedactor(Config{})
	if got := r.folderFor("ptyxis", "~/garden", ""); got != "" {
		t.Errorf("empty sent title: folder = %q", got)
	}
	for _, b := range defaultBrowsers {
		if got := r.folderFor(b, "~/garden", "x"); got != "" {
			t.Errorf("browser %s: folder = %q", b, got)
		}
	}
	if got := r.folderFor("ptyxis", "/home/alice", "x"); got == "alice" {
		t.Errorf("home dir leaked username: %q", got)
	}
}

// Raw 是脱敏前（只过强制脱敏）的标题，跟着 Title 同一条合并结果走。
func TestJoinRecordsKeepsRawOfChosenTitle(t *testing.T) {
	a := rf(0, 2, "ptyxis", "x")
	a.Raw = "~/a"
	b := rf(2, 10, "ptyxis", "y")
	b.Key = a.Key
	b.Raw = "~/garden"
	rs := joinRecords([]fragment{a, b})
	if len(rs) != 1 || rs[0].Title != "y" || rs[0].Raw != "~/garden" {
		t.Fatalf("%+v", rs)
	}
}

// 没有 folder 时上传记录里不带这个键（AD-F08 / AD-F16）。
func TestUploadRecordFolderKey(t *testing.T) {
	b, _ := json.Marshal(uploadRecord{StartAt: "s", EndAt: "e", App: "ptyxis", Title: "x"})
	if strings.Contains(string(b), `"folder"`) {
		t.Fatalf("folder key present without folder: %s", b)
	}
	b, _ = json.Marshal(uploadRecord{StartAt: "s", EndAt: "e", App: "ptyxis", Title: "x", Folder: "garden"})
	if !strings.Contains(string(b), `"folder":"garden"`) {
		t.Fatalf("folder missing: %s", b)
	}
}
