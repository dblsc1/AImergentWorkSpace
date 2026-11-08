package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"testing"
	"time"
)

func srcClient(url string) awClient { return awClient{base: url + "/api/0", http: http.DefaultClient} }

func TestSourceIssues(t *testing.T) {
	now := time.Now()
	ev := func(app, title string) awEvent {
		return awEvent{Timestamp: now.Add(-time.Minute), Duration: 30, Data: map[string]any{"app": app, "title": title}}
	}
	none := func(string) string { return "" }
	wl := func(string) string { return "wayland" }
	cases := []struct {
		name string
		win  []awEvent
		web  []awEvent
		cfg  Config
		goos string
		env  func(string) string
		want []string
	}{
		{"all good", []awEvent{ev("firefox", "x"), ev("kitty", "~/code")}, []awEvent{ev("", "")}, Config{}, "linux", none, []string{}},
		{"wayland empty", nil, nil, Config{}, "linux", wl, []string{issueWindowWayland}},
		{"windows empty", nil, nil, Config{}, "windows", wl, []string{issueWindowMissing}},
		{"browser no extension", []awEvent{ev("firefox", "x")}, nil, Config{}, "linux", none, []string{issueWebExtension}},
		{"titles hidden", []awEvent{ev("code", "x")}, nil, Config{Privacy: Privacy{Titles: "pseudonymize"}}, "linux", none, []string{issueTitlesHidden}},
		{"terminal no path", []awEvent{ev("kitty", "zsh")}, nil, Config{}, "linux", none, []string{issueTerminalNoPath}},
	}
	for _, c := range cases {
		srv := fakeAWWeb(t, c.win, []awEvent{{Timestamp: now.Add(-time.Minute), Duration: 30, Data: map[string]any{"status": "not-afk"}}}, c.web)
		got := sourceIssues(c.cfg, srcClient(srv.URL), now, c.goos, c.env)
		srv.Close()
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

func TestSourceIssuesAwDown(t *testing.T) {
	srv := fakeAWWeb(t, nil, nil, nil)
	srv.Close()
	if got := sourceIssues(Config{}, srcClient(srv.URL), time.Now(), "linux", func(string) string { return "" }); !reflect.DeepEqual(got, []string{issueAwDown}) {
		t.Fatalf("got %v", got)
	}
}

func TestSourceIssuesNoAfkBucket(t *testing.T) {
	host, _ := os.Hostname()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/0/buckets/" {
			json.NewEncoder(w).Encode(map[string]awBucket{"win": {ID: "win", Type: "currentwindow", Hostname: host}})
			return
		}
		w.Write([]byte("[]"))
	}))
	defer srv.Close()
	got := sourceIssues(Config{}, srcClient(srv.URL), time.Now(), "linux", func(string) string { return "" })
	if !reflect.DeepEqual(got, []string{issueWindowMissing, issueAfkMissing}) {
		t.Fatalf("got %v", got)
	}
}

func TestHealthReportCarriesSourceIssues(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	aw := fakeAWWeb(t, nil, nil, nil)
	defer aw.Close()
	cfg := liveConfig(aw.URL, ck.URL)
	cfg.Enabled = true
	e := testEnv(t, true, &spawnLog{})
	e.h.goos, e.h.getenv = "linux", func(string) string { return "wayland" }
	e.h.report(cfg, "ok")
	if got := ck.last()["sourceIssues"]; !reflect.DeepEqual(got, []any{issueWindowWayland}) {
		t.Fatalf("sourceIssues = %v", got)
	}
	// 老服务端 422：去掉字段立刻重发，之后不再带
	ck.set(`{}`, 422)
	e.clk.add(20 * time.Second)
	e.h.sourceAt = time.Time{}
	e.h.report(cfg, "no_window")
	ck.set(`{"restartNonce":null}`, 0)
	e.clk.add(20 * time.Second)
	e.h.report(cfg, "ok")
	if _, has := ck.last()["sourceIssues"]; has {
		t.Fatalf("still sending sourceIssues to an old server")
	}
}
