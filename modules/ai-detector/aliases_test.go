package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// 新建一个带配置目录的脱敏器；「重启」= 清掉进程内共享的表，下一次从磁盘读。
func aliasRed(t *testing.T, dir string, p Privacy) redactor {
	t.Helper()
	aliasMu.Lock()
	aliasCache = map[string]*aliases{}
	aliasMu.Unlock()
	cfg := Config{Privacy: p}
	cfg.dir = dir
	r := newRedactor(cfg)
	r.literals = nil
	return r
}

func TestNumberedAliasIPClasses(t *testing.T) {
	r := aliasRed(t, t.TempDir(), Privacy{})
	in := "127.0.0.1 ::1 10.1.2.3 172.16.0.9 192.168.1.2 169.254.9.9 fc00::5 fe80::1ff:fe23:4567:890a 100.64.0.1 100.127.255.1 8.8.8.8 1.1.1.1 10.1.2.3 100.128.0.1"
	want := "本机IP1 本机IP2 局域网IP1 局域网IP2 局域网IP3 局域网IP4 局域网IP5 局域网IP6 组网IP1 组网IP2 公网IP1 公网IP2 局域网IP1 公网IP3"
	if got := r.text(in); got != want {
		t.Fatalf("\n got %q\nwant %q", got, want)
	}
	if got := r.text("v1.2.3.4 and 10.0.19045.1 std::vector"); got != "v1.2.3.4 and 10.0.19045.1 std::vector" {
		t.Fatalf("version-like text must stay: %q", got)
	}
}

func TestNumberedAliasStableAcrossRestarts(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{Privacy: Privacy{}}
	cfg.dir = dir
	r := aliasRed(t, dir, Privacy{})
	if got := r.text("a 8.8.8.8 b 192.168.0.1 c bob@x.io"); got != "a 公网IP1 b 局域网IP1 c 邮箱1" {
		t.Fatal(got)
	}
	if err := flushAliases(cfg); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(filepath.Join(dir, "aliases.json"))
	if err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("aliases.json: %v %v", fi, err)
	}
	var disk map[string]map[string]string
	b, _ := os.ReadFile(filepath.Join(dir, "aliases.json"))
	if json.Unmarshal(b, &disk) != nil || disk["公网IP"]["8.8.8.8"] != "公网IP1" || disk["邮箱"]["bob@x.io"] != "邮箱1" {
		t.Fatalf("%s", b)
	}
	// 重启：旧值拿旧号，新值接着编；邮箱大小写算同一个。
	r = aliasRed(t, dir, Privacy{})
	if got := r.text("1.1.1.1 8.8.8.8 BOB@x.io carol@x.io"); got != "公网IP2 公网IP1 邮箱1 邮箱2" {
		t.Fatal(got)
	}
}

func TestNumberedAliasPerTypeCounters(t *testing.T) {
	r := aliasRed(t, t.TempDir(), Privacy{})
	got := r.text("a@x.io b@x.io 8.8.8.8 alice@thinkpad: bob@desk: ~/x")
	if got != "邮箱1 邮箱2 公网IP1 用户1@主机1: 用户2@主机2: x" {
		t.Fatal(got)
	}
}

func TestNumberedAliasesDisabledAndMasked(t *testing.T) {
	const in = "ssh 8.8.8.8 mail a@x.io alice@box: ~"
	r := aliasRed(t, t.TempDir(), Privacy{NumberedAliases: []string{}})
	if got := r.text(in); got != "ssh [IP] mail [邮箱] [用户]@[主机]: ~" {
		t.Fatal(got)
	}
	// 只列 ips：邮箱照旧方括号。
	r = aliasRed(t, t.TempDir(), Privacy{NumberedAliases: []string{"ips"}})
	if got := r.text(in); got != "ssh 公网IP1 mail [邮箱] [用户]@[主机]: ~" {
		t.Fatal(got)
	}
	// 列了 ips 但 ips 遮盖关着：原样，不编号，也不分配。
	r = aliasRed(t, t.TempDir(), Privacy{NumberedAliases: []string{"ips"}, IPs: bp(false)})
	if got := r.text("ssh 8.8.8.8"); got != "ssh 8.8.8.8" {
		t.Fatal(got)
	}
	if len(r.al.m) != 0 {
		t.Fatalf("assigned: %v", r.al.m)
	}
}

func TestNumberedAliasDomains(t *testing.T) {
	r := aliasRed(t, t.TempDir(), Privacy{NumberedAliases: []string{"domains"}})
	cases := [][2]string{
		{"https://mail.example.com/u/0/?q=1 - 页面", "网站1 - 页面"},
		{"http://a.b.cn:8080/x?id=1", "网站2:8080"},
		{"github.com/dblsc1/repo - 标题", "网站3 - 标题"},
		{"see https://MAIL.example.com/x and github.com:22/y", "see 网站1 and 网站3:22"},
		{"localhost:8800/Cockpit/hive/", "localhost:8800/Cockpit/hive/"},
		{"http://10.0.0.5:8080/admin", "[IP]"},
	}
	for _, c := range cases {
		in, want := c[0], c[1]
		if got := r.text(in); got != want {
			t.Errorf("%q → %q, want %q", in, got, want)
		}
	}
	// 缺省（没列 domains）：域名照旧，只留主机、端口去掉。
	r = aliasRed(t, t.TempDir(), Privacy{})
	if got := r.text("http://a.b.cn:8080/x"); got != "a.b.cn" {
		t.Fatal(got)
	}
}

func TestNumberedAliasesCheck(t *testing.T) {
	if err := (Privacy{NumberedAliases: []string{"ips", "domains"}}).check(); err != nil {
		t.Fatal(err)
	}
	if err := (Privacy{NumberedAliases: []string{"ips", "phones"}}).check(); err == nil || !strings.Contains(err.Error(), "numberedAliases") {
		t.Fatalf("unknown value must be rejected: %v", err)
	}
	// 网页设置不带这一项：本机的值保留。
	cfg := Config{Privacy: Privacy{NumberedAliases: []string{}}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"settings":{"privacy":{"paths":"off"},"idle":{}}}`))
	}))
	defer srv.Close()
	if err := applyRemoteSettings(&cfg, http.DefaultClient, srv.URL); err != nil {
		t.Fatal(err)
	}
	if cfg.Privacy.Paths != "off" || cfg.Privacy.NumberedAliases == nil || len(cfg.Privacy.NumberedAliases) != 0 {
		t.Fatalf("%+v", cfg.Privacy)
	}
}

func TestNumberedAliasUploadAndCorruptTable(t *testing.T) {
	dir := t.TempDir()
	run := func() (*cockpit, error) {
		aliasRed(t, dir, Privacy{}) // 清掉进程内的表，模拟重启
		aw := fakeAW(t, []awEvent{win(0, 20, "code", "ssh root@10.1.1.1 and 8.8.4.4")}, nil)
		defer aw.Close()
		ck := fakeCockpit(t)
		cfg := testConfig(aw.URL, ck.URL)
		cfg.dir = dir
		_, err := tick(cfg, activeState(), at(120), http.DefaultClient)
		return ck, err
	}
	ck, err := run()
	if err != nil {
		t.Fatal(err)
	}
	defer ck.Close()
	body := string(ck.bodies[0])
	if !strings.Contains(body, "局域网IP1") || !strings.Contains(body, "公网IP1") || strings.Contains(body, "10.1.1.1") || strings.Contains(body, "[IP]") {
		t.Fatalf("%s", body)
	}
	if _, err := os.Stat(filepath.Join(dir, "aliases.json")); err != nil {
		t.Fatal(err)
	}
	// 坏掉的对照表：不上传（重新编号会让旧代号指向新值）。
	os.WriteFile(filepath.Join(dir, "aliases.json"), []byte("{"), 0o600)
	ck2, err := run()
	defer ck2.Close()
	if err == nil || len(ck2.bodies) != 0 || !strings.Contains(err.Error(), "aliases.json") {
		t.Fatalf("corrupt table must stop upload: %v", err)
	}
}

func TestNumberedAliasPreviewDoesNotPersist(t *testing.T) {
	dir := t.TempDir()
	r := aliasRed(t, dir, Privacy{})
	r.text("8.8.8.8")
	cfg := Config{}
	cfg.dir = dir
	if err := flushAliases(cfg); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(filepath.Join(dir, "aliases.json"))

	pr, err := newPreviewRedactor(Config{dir: dir})
	if err != nil {
		t.Fatal(err)
	}
	if got := pr.text("1.1.1.1 8.8.8.8 a@x.io"); got != "公网IP2 公网IP1 邮箱1" {
		t.Fatal(got)
	}
	var out bytes.Buffer
	pr.al.printUsed(&out)
	for _, w := range []string{"公网IP1  8.8.8.8", "公网IP2  1.1.1.1", "邮箱1  a@x.io"} {
		if !strings.Contains(out.String(), w) {
			t.Fatalf("missing %q in %s", w, out.String())
		}
	}
	if err := flushAliases(cfg); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(filepath.Join(dir, "aliases.json"))
	if string(before) != string(after) {
		t.Fatalf("preview persisted: %s → %s", before, after)
	}
	// 真表里也没有被预览试编的号：下一个真值仍是公网IP2。
	if got := r.text("9.9.9.9"); got != "公网IP2" {
		t.Fatal(got)
	}
}
