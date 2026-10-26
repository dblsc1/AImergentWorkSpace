package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAliasTableValidation(t *testing.T) {
	bad := map[string]string{
		"unknown kind":    `{"乱写":{"a":"乱写1"}}`,
		"leading zero":    `{"邮箱":{"a@x.io":"邮箱01"}}`,
		"plus sign":       `{"邮箱":{"a@x.io":"邮箱+1"}}`,
		"zero":            `{"邮箱":{"a@x.io":"邮箱0"}}`,
		"duplicate token": `{"邮箱":{"a@x.io":"邮箱1","b@x.io":"邮箱1"}}`,
		"empty orig":      `{"邮箱":{"":"邮箱1"}}`,
		"raw as token":    `{"邮箱":{"a@x.io":"a@x.io"}}`,
		"wrong prefix":    `{"邮箱":{"a@x.io":"用户1"}}`,
		"non-numeric":     `{"邮箱":{"a@x.io":"邮箱x"}}`,
	}
	for name, body := range bad {
		p := filepath.Join(t.TempDir(), "aliases.json")
		os.WriteFile(p, []byte(body), 0o600)
		_, err := loadAliases(p)
		if err == nil {
			t.Errorf("%s: want error", name)
		} else if strings.Contains(err.Error(), "a@x.io") {
			t.Errorf("%s: error leaks original: %v", name, err)
		}
	}
	p := filepath.Join(t.TempDir(), "aliases.json")
	os.WriteFile(p, []byte(`{"邮箱":{"a@x.io":"邮箱2","b@x.io":"邮箱7"}}`), 0o600)
	a, err := loadAliases(p)
	if err != nil {
		t.Fatal(err)
	}
	if got := a.alias("邮箱", "c@x.io"); got != "邮箱8" {
		t.Fatal(got)
	}
}

func TestCodexScanAcrossDays(t *testing.T) {
	now := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
	reset := func() { codexList.Lock(); codexList.home = ""; codexList.Unlock() }
	mk := func(home string, day time.Time, name string, mt time.Time) string {
		p := filepath.Join(home, ".codex", "sessions", day.Format("2006"), day.Format("01"), day.Format("02"), name)
		lwrite(t, p, `{"type":"event_msg","payload":{"type":"task_started"}}`+"\n")
		os.Chtimes(p, mt, mt)
		return p
	}
	t.Cleanup(reset)
	// 10 天前的目录里、刚被恢复的文件，比今天目录里的旧文件新。
	reset()
	home := t.TempDir()
	mk(home, now, "rollout-a.jsonl", now.Add(-5*time.Hour))
	old := mk(home, now.AddDate(0, 0, -10), "rollout-b.jsonl", now.Add(-time.Minute))
	if got, _ := codexSession(home, now); got != old {
		t.Fatalf("resumed old dir: %s", got)
	}
	if got := discoverCodex(home, now); len(got) != 1 || got[0].State != "working" {
		t.Fatalf("%+v", got)
	}
	// 超过 30 个日期目录的不扫（年月循环也停）。
	reset()
	home = t.TempDir()
	for i := 0; i < 31; i++ {
		mk(home, now.AddDate(0, 0, -i-40), fmt.Sprintf("rollout-%d.jsonl", i), now.Add(-time.Minute))
	}
	mk(home, now.AddDate(0, 0, -400), "rollout-far.jsonl", now)
	if fs := codexFiles(home, now); len(fs) != 30 {
		t.Fatalf("cap: %d", len(fs))
	}
	// 清单缓存 30 秒内不重列，之后重列。
	reset()
	home = t.TempDir()
	mk(home, now, "rollout-1.jsonl", now)
	codexFiles(home, now)
	mk(home, now, "rollout-2.jsonl", now)
	if n := len(codexFiles(home, now.Add(10*time.Second))); n != 1 {
		t.Fatalf("cached: %d", n)
	}
	if n := len(codexFiles(home, now.Add(31*time.Second))); n != 2 {
		t.Fatalf("refresh: %d", n)
	}
}

func TestBrowserDomainAliasKeepsPort(t *testing.T) {
	r := newRedactor(Config{Privacy: Privacy{NumberedAliases: []string{"domains"}, Browser: "domain"}})
	r.browsers["chrome"] = true
	got, _ := r.window("chrome", "Dev", &webTab{URL: "http://app.example.com:8080/x", Title: "Dev"})
	if !strings.Contains(got, "网站1:8080") {
		t.Fatal(got)
	}
}
