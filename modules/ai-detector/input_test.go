package main

import (
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestParseIdle(t *testing.T) {
	for in, want := range map[string]time.Duration{
		"(uint64 1234,)\n": 1234 * time.Millisecond,
		"(uint64 0,)":      0,
	} {
		if d, err := parseIdle(in); err != nil || d != want {
			t.Errorf("%q: %v %v", in, d, err)
		}
	}
	for _, bad := range []string{"", "Error: no such name", "(1234,)"} {
		if _, err := parseIdle(bad); err == nil {
			t.Errorf("%q parsed", bad)
		}
	}
}

func withIdle(t *testing.T, f func() (time.Duration, error)) {
	oldIdle, oldOS := idleTime, inputGOOS
	idleTime, inputGOOS = f, "linux"
	t.Cleanup(func() { idleTime, inputGOOS = oldIdle, oldOS })
}

func TestInputSampleThresholdAndAlignment(t *testing.T) {
	var idle time.Duration
	withIdle(t, func() (time.Duration, error) { return idle, nil })
	tr := newInputTracker()
	base := time.Unix(1_000_000_000, 0)
	idle = 9999 * time.Millisecond // < 10000：有活动
	tr.sample(base.Add(7 * time.Second))
	idle = 10000 * time.Millisecond // 恰好 10 秒：没有
	tr.sample(base.Add(17 * time.Second))
	idle = 0
	tr.sample(base.Add(31 * time.Second))
	if len(tr.bins) != 2 || !tr.bins[999_999_990] || !tr.bins[1_000_000_030] {
		t.Fatalf("%v", tr.bins)
	}
	for b := range tr.bins {
		if b%10 != 0 {
			t.Fatalf("unaligned %d", b)
		}
	}
}

// 活动格 = 最后一次活动所在的格（now - idle），不是采样时刻所在的格：idle 跨格要记到前一格。
func TestInputBinIsLastActivityBin(t *testing.T) {
	var idle time.Duration
	withIdle(t, func() (time.Duration, error) { return idle, nil })
	tr := newInputTracker()
	base := time.Unix(1_000_000_000, 0)
	idle = 5 * time.Second
	tr.sample(base.Add(3 * time.Second)) // 活动在 base-2s，属于前一格
	idle = 1 * time.Second
	tr.sample(base.Add(13 * time.Second)) // 活动在 base+12s
	if len(tr.bins) != 2 || !tr.bins[999_999_990] || !tr.bins[1_000_000_010] {
		t.Fatalf("%v", tr.bins)
	}
}

// 上传（POST 卡住）不挡采样；404 之后不再起命令采样。
func TestInputFlushDoesNotBlockSamplingAnd404StopsSampling(t *testing.T) {
	calls := 0
	withIdle(t, func() (time.Duration, error) { calls++; return 0, nil })
	release := make(chan struct{})
	entered := make(chan struct{})
	status := 200
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case entered <- struct{}{}:
		default:
		}
		<-release
		w.WriteHeader(status)
	}))
	defer srv.Close()
	tr := newInputTracker()
	now := time.Unix(1_000_000_000, 0)
	tr.sample(now)
	done := make(chan struct{})
	go func() { tr.flush(testConfig("", srv.URL), srv.Client(), srv.URL, now); close(done) }()
	<-entered
	tr.sample(now.Add(10 * time.Second)) // 上传卡着，采样照常返回
	if calls != 2 || len(tr.bins) != 2 {
		t.Fatalf("calls=%d bins=%v", calls, tr.bins)
	}
	status = 404
	close(release)
	<-done
	tr.sample(now.Add(20 * time.Second))
	if calls != 2 || !tr.gone {
		t.Fatalf("sampling continued after 404: calls=%d", calls)
	}
}

func TestInputUnsupported(t *testing.T) {
	var logs strings.Builder
	log.SetOutput(&logs)
	defer log.SetOutput(io.Discard)
	calls := 0
	withIdle(t, func() (time.Duration, error) { calls++; return 0, exec.ErrNotFound })
	tr := newInputTracker()
	for i := 0; i < 3; i++ {
		tr.sample(time.Unix(1_000_000_000+int64(i*10), 0))
	}
	if calls != 1 || strings.Count(logs.String(), "此系统暂不支持键盘鼠标活动") != 1 || len(tr.bins) != 0 {
		t.Fatalf("calls=%d log=%q", calls, logs.String())
	}
	// 非 Linux：命令根本不跑。
	logs.Reset()
	calls = 0
	inputGOOS = "windows"
	tr = newInputTracker()
	tr.sample(time.Now())
	tr.sample(time.Now())
	if calls != 0 || strings.Count(logs.String(), "此系统暂不支持键盘鼠标活动") != 1 {
		t.Fatalf("calls=%d log=%q", calls, logs.String())
	}
	// 别的失败（Mutter 不在）只是这一格跳过，不算不支持。
	inputGOOS = "linux"
	idleTime = func() (time.Duration, error) { return 0, errors.New("boom") }
	tr = newInputTracker()
	tr.sample(time.Now())
	if tr.unsupported {
		t.Fatal("transient failure marked unsupported")
	}
}

func TestInputFlushRetainsOnFailureCapsAt6hAnd404Stops(t *testing.T) {
	var got [][]int64
	status := 500
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/Cockpit/api/core/activity/input" {
			t.Errorf("path %s", r.URL.Path)
		}
		var b struct {
			DeviceID string
			Bins     []int64
		}
		json.NewDecoder(r.Body).Decode(&b)
		if b.DeviceID != "dev_test" {
			t.Errorf("device %q", b.DeviceID)
		}
		got = append(got, b.Bins)
		w.WriteHeader(status)
	}))
	defer srv.Close()
	cfg := testConfig("", srv.URL)
	base := srv.URL + "/Cockpit"
	now := time.Unix(1_000_100_000, 0)
	tr := newInputTracker()
	tr.bins[now.Unix()/10*10-7*3600] = true // 超过 6 小时：丢
	tr.bins[now.Unix()/10*10-3600] = true
	tr.bins[now.Unix()/10*10+120] = true // 未来格（超过现在+60秒）：丢，不发
	tr.bins[now.Unix()/10*10-10] = true
	tr.flush(cfg, srv.Client(), base, now)
	if len(got) != 1 || len(got[0]) != 2 || got[0][0] > got[0][1] || len(tr.bins) != 2 {
		t.Fatalf("got=%v bins=%v", got, tr.bins)
	}
	status = 200
	tr.flush(cfg, srv.Client(), base, now)
	if len(tr.bins) != 0 || len(got) != 2 {
		t.Fatalf("bins=%v posts=%d", tr.bins, len(got))
	}
	tr.flush(cfg, srv.Client(), base, now) // 没有格不发请求
	if len(got) != 2 {
		t.Fatal("empty flush posted")
	}
	status = 404
	tr.bins[now.Unix()/10*10] = true
	tr.flush(cfg, srv.Client(), base, now)
	tr.bins[now.Unix()/10*10] = true
	tr.flush(cfg, srv.Client(), base, now)
	if len(got) != 3 || !tr.gone {
		t.Fatalf("404 did not stop: posts=%d", len(got))
	}
}

func TestInputActivitySettingParsing(t *testing.T) {
	defer remoteInput.Store(false)
	for body, want := range map[string]bool{
		`{"settings":{"inputActivity":true}}`:  true,
		`{"settings":{"inputActivity":false}}`: false,
		`{"settings":{}}`:                      false, // 缺省关
		`{"settings":null}`:                    false,
	} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
		remoteInput.Store(!want)
		cfg := Config{DeviceToken: "t"}
		if err := applyRemoteSettings(&cfg, srv.Client(), srv.URL); err != nil {
			t.Fatal(err)
		}
		srv.Close()
		if remoteInput.Load() != want || cfg.inputActivity != want && body != `{"settings":null}` {
			t.Errorf("%s: remote=%v cfg=%v", body, remoteInput.Load(), cfg.inputActivity)
		}
	}
	// 老 nexus-core（404）：关。
	srv := httptest.NewServer(http.NotFoundHandler())
	defer srv.Close()
	remoteInput.Store(true)
	applyRemoteSettings(&Config{}, srv.Client(), srv.URL)
	if remoteInput.Load() {
		t.Fatal("404 left input on")
	}
}
