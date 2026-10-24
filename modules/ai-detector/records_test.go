package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

func sec(s float64) time.Time { return t0.Add(time.Duration(s * float64(time.Second))) }

// rf 造一个碎片：[a,b) 秒，键 = app+title。
func rf(a, b float64, app, title string) fragment {
	return fragment{Start: sec(a), End: sec(b), App: app, Title: title, Key: app + "\x00" + title}
}

func TestJoinRecords(t *testing.T) {
	// 间隔恰好 1 秒接上，超过 1 秒不接。
	rs := joinRecords([]fragment{rf(0, 5, "code", "x"), rf(6, 9, "code", "x"), rf(10.5, 12, "code", "x")})
	if len(rs) != 2 || !rs[0].End.Equal(sec(9)) || !rs[1].Start.Equal(sec(10.5)) {
		t.Fatalf("%+v", rs)
	}
	// 不跨键吸收：中间夹一个别的窗口，两头同键也不接。
	rs = joinRecords([]fragment{rf(0, 5, "code", "x"), rf(5, 6, "wechat", ""), rf(6, 10, "code", "x")})
	if len(rs) != 3 {
		t.Fatalf("absorbed across keys: %+v", rs)
	}
	// 无操作碎片不算。
	idle := rf(5, 8, "code", "x")
	idle.Idle = true
	rs = joinRecords([]fragment{rf(0, 5, "code", "x"), idle})
	if len(rs) != 1 || !rs[0].End.Equal(sec(5)) {
		t.Fatalf("idle counted: %+v", rs)
	}
	// 同键不同标题：标题取停留最久的。
	a, b := rf(0, 2, "code", "a"), rf(2, 10, "code", "b")
	a.Key, b.Key = "code", "code"
	if rs = joinRecords([]fragment{a, b}); len(rs) != 1 || rs[0].Title != "b" {
		t.Fatalf("%+v", rs)
	}
}

type recCockpit struct {
	*httptest.Server
	mu        sync.Mutex
	recStatus int
	recReply  string
	records   [][]byte
	segs      [][]byte
	segStatus int
	settings  string // detector/settings 的响应体，空 = 404
	rules     string // detector/rules 的响应体，空 = 404
	tiers     string // activity/settings 的响应体；缺省 {"tiers":true}，"404" = 404
}

// Close 顺带清掉 tiers 缓存（端口会被后面的测试复用）。
func (c *recCockpit) Close() { resetTiersCache(); c.Server.Close() }

func newRecCockpit() *recCockpit {
	resetTiersCache()
	c := &recCockpit{recStatus: 200, segStatus: 200, tiers: `{"tiers":true}`}
	c.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c.mu.Lock()
		defer c.mu.Unlock()
		b, _ := io.ReadAll(r.Body)
		switch r.URL.Path {
		case "/Cockpit/api/core/activity/records":
			c.records = append(c.records, b)
			w.WriteHeader(c.recStatus)
			io.WriteString(w, c.recReply)
		case "/Cockpit/api/core/activity/suggestions":
			c.segs = append(c.segs, b)
			w.WriteHeader(c.segStatus)
		case "/Cockpit/api/core/detector/settings":
			if c.settings == "" {
				w.WriteHeader(404)
			}
			io.WriteString(w, c.settings)
		case "/Cockpit/api/core/activity/settings":
			if c.tiers == "404" {
				w.WriteHeader(404)
				return
			}
			io.WriteString(w, c.tiers)
		case "/Cockpit/api/core/detector/rules":
			if c.rules == "" {
				w.WriteHeader(404)
			}
			io.WriteString(w, c.rules)
		default:
			w.WriteHeader(404)
		}
	}))
	return c
}

type recBody struct {
	DeviceID string `json:"deviceId"`
	Records  []struct {
		StartAt, EndAt, App, Title string
		TitleIsPseudonym           bool
		Block                      *string
	} `json:"records"`
}

func recState() *State {
	s := activeState()
	s.RecordsCursor = at(0)
	return s
}

func ev(startSec, durSec float64, app, title string) awEvent {
	return awEvent{Timestamp: sec(startSec), Duration: durSec, Data: map[string]any{"app": app, "title": title}}
}

func decode(t *testing.T, b []byte) recBody {
	var r recBody
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestRecordsTickShapeBlockRedactionAndSegmentsUnaffected(t *testing.T) {
	recordsGone.Store(false)
	// 20 分钟的 code（够成段）+ 0.5 秒的闪现（丢）+ 3 秒 chrome（含邮箱，要脱敏）+ 间隔 0.8 秒后的 code 又 2 秒（同键，接上）。
	aw := fakeAW(t, []awEvent{
		ev(0, 1200, "code", "main.go — garden — Code"),
		ev(1200, 0.5, "wechat", "hi"),
		ev(1200.5, 3, "chrome", "mail bob@x.com"),
		ev(1204, 2, "code", "plot.gd"),
	}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	ck.rules = `{"version":2,"rules":[
		{"id":"a","kind":"block","block":"花园","app":null,"title":"main","enabled":true},
		{"id":"b","kind":"block","block":"晚到的","app":"code","title":null,"enabled":true},
		{"id":"c","app":"code","title":null,"taskId":"t_x","confidence":0.8,"enabled":true}]}`
	cfg := testConfig(aw.URL, ck.URL)
	cfg.Privacy.Titles = "pseudonymize"
	cfg.dir = t.TempDir()
	st := recState()
	if _, err := tick(cfg, st, at(30), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if len(ck.records) != 1 {
		t.Fatalf("record posts: %d", len(ck.records))
	}
	rb := decode(t, ck.records[0])
	if rb.DeviceID != "dev_test" || len(rb.Records) != 3 {
		t.Fatalf("%s", ck.records[0])
	}
	r0, r1, r2 := rb.Records[0], rb.Records[1], rb.Records[2]
	if r0.App != "code" || r1.App != "chrome" || r2.App != "code" {
		t.Fatalf("%s", ck.records[0])
	}
	// 毫秒 + 偏移
	if !strings.Contains(r1.StartAt, ".500") || len(r1.StartAt) != len("2006-01-02T15:04:05.000-07:00") {
		t.Fatalf("startAt %q", r1.StartAt)
	}
	// 块名：匹配的是换代号前的标题；第一条命中的第一级规则；旧式规则 c 不参与。
	if r0.Block == nil || *r0.Block != "花园" || r1.Block != nil || r2.Block == nil || *r2.Block != "晚到的" {
		t.Fatalf("blocks %v %v %v", r0.Block, r1.Block, r2.Block)
	}
	body := string(ck.records[0])
	if strings.Contains(body, "bob@x.com") || strings.Contains(body, "main.go") || strings.Contains(body, "plot.gd") {
		t.Fatalf("raw title leaked: %s", body)
	}
	if !r0.TitleIsPseudonym || !r2.TitleIsPseudonym || !strings.Contains(body, `"block":null`) {
		t.Fatalf("flags: %s", body)
	}
	// 本机留档：记实际发出的 sent（kind record），不含原始标题。
	raw, _ := os.ReadFile(archiveDir(cfg.dir) + "/" + time.Now().Local().Format("2006-01-02") + ".jsonl")
	n := 0
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.Contains(line, `"kind":"record"`) {
			n++
			if strings.Contains(line, "main.go") || strings.Contains(line, "bob@x.com") {
				t.Fatalf("raw title archived: %s", line)
			}
		}
	}
	if n != 1 {
		t.Fatalf("archive record rows %d: %s", n, raw)
	}
	// 与段同一个代号。
	if len(ck.segs) != 1 {
		t.Fatalf("segment posts %d", len(ck.segs))
	}
	var sb struct{ Segments []struct{ Title string } }
	json.Unmarshal(ck.segs[0], &sb)
	if len(sb.Segments) != 1 || sb.Segments[0].Title != r0.Title || r0.Title == "" {
		t.Fatalf("seg %s vs rec %q", ck.segs[0], r0.Title)
	}
	// 段的形状不变，里面没有 records 的东西；旧式规则照给建议。
	if strings.Contains(string(ck.segs[0]), "block") || !strings.Contains(string(ck.segs[0]), `"t_x"`) {
		t.Fatalf("segment body: %s", ck.segs[0])
	}
}

func TestRecordsCursorDedupeAndRetry(t *testing.T) {
	recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a"), ev(20, 5, "code", "b")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	st := recState()
	now := sec(100)

	ck.recStatus = 503
	tick(cfg, st, now, http.DefaultClient)
	if !st.RecordsCursor.Equal(sec(0)) {
		t.Fatalf("cursor moved on failure: %v", st.RecordsCursor)
	}
	ck.recStatus = 200
	tick(cfg, st, now, http.DefaultClient)
	if len(ck.records) != 2 || string(ck.records[0]) != string(ck.records[1]) {
		t.Fatalf("resend differs or missing: %d", len(ck.records))
	}
	if !st.RecordsCursor.Equal(now.Add(-time.Second)) {
		t.Fatalf("cursor %v", st.RecordsCursor)
	}
	// 重跑：同样的事件，不再上传。
	tick(cfg, st, now.Add(time.Minute), http.DefaultClient)
	if len(ck.records) != 2 {
		t.Fatalf("duplicate upload: %d", len(ck.records))
	}
	// 没收口的不传，游标停在它的开始：窗口一直开到 now。
	aw2 := fakeAW(t, []awEvent{ev(0, 100, "code", "a")}, nil)
	defer aw2.Close()
	cfg.ActivityWatchURL = aw2.URL + "/api/0"
	st = recState()
	ck.records = nil
	tick(cfg, st, sec(100), http.DefaultClient)
	if len(ck.records) != 0 || !st.RecordsCursor.Equal(sec(0)) {
		t.Fatalf("open record uploaded or cursor wrong: %d %v", len(ck.records), st.RecordsCursor)
	}
	tick(cfg, st, sec(102), http.DefaultClient)
	if len(ck.records) != 1 {
		t.Fatalf("closed record not uploaded: %d", len(ck.records))
	}
}

func TestRecordsBatchesAtMost500(t *testing.T) {
	recordsGone.Store(false)
	var evs []awEvent
	for i := 0; i < 1200; i++ { // 交替两个窗口，每条 2 秒，互不相接
		evs = append(evs, ev(float64(i*2), 2, []string{"code", "chrome"}[i%2], "t"))
	}
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	st := recState()
	if _, err := tick(testConfig(aw.URL, ck.URL), st, sec(2500), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var n []int
	for _, b := range ck.records {
		n = append(n, len(decode(t, b).Records))
	}
	if len(n) != 3 || n[0] != 500 || n[1] != 500 || n[2] != 200 {
		t.Fatalf("batches %v", n)
	}
}

// 第二批失败：已送达的第一批不重发，游标停在第二批第一条的开始。
func TestRecordsPartialBatchFailureKeepsCursor(t *testing.T) {
	recordsGone.Store(false)
	var evs []awEvent
	for i := 0; i < 600; i++ {
		evs = append(evs, ev(float64(i*2), 2, []string{"code", "chrome"}[i%2], "t"))
	}
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	n := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/Cockpit/api/core/activity/records" {
			if n++; n == 2 {
				w.WriteHeader(500)
			}
			return
		}
		w.WriteHeader(404)
	}))
	defer srv.Close()
	st := recState()
	cfg := testConfig(aw.URL, srv.URL)
	on := true
	cfg.records = &on
	tick(cfg, st, sec(1500), http.DefaultClient)
	if n != 2 || !st.RecordsCursor.Equal(sec(1000)) {
		t.Fatalf("posts=%d cursor=%v", n, st.RecordsCursor)
	}
}

func TestRecords404StopsForThisRun(t *testing.T) {
	recordsGone.Store(false)
	defer recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	ck.recStatus = 404
	cfg := testConfig(aw.URL, ck.URL)
	st := recState()
	if _, err := tick(cfg, st, sec(100), http.DefaultClient); err != nil {
		t.Fatalf("404 must not fail the round: %v", err)
	}
	tick(cfg, st, sec(200), http.DefaultClient)
	if len(ck.records) != 1 || !recordsGone.Load() || !st.RecordsCursor.IsZero() {
		t.Fatalf("posts=%d gone=%v cursor=%v", len(ck.records), recordsGone.Load(), st.RecordsCursor)
	}
	if len(ck.segs) != 0 { // 这段只有 10 秒，不够成段；确认段那条流没被误伤
		t.Fatalf("segs %d", len(ck.segs))
	}
}

func TestRecordsSettingFalseAndTrueAndNull(t *testing.T) {
	recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	for _, tc := range []struct {
		settings string
		posts    int
	}{
		{`{"settings":{"records":false}}`, 0},
		{`{"settings":{"records":true}}`, 1},
		{`{"settings":{"records":null}}`, 1},
		{`{"settings":{"inputActivity":true}}`, 1}, // 没有 records 键 = 缺省 null
	} {
		ck.mu.Lock()
		ck.settings, ck.records = tc.settings, nil
		ck.mu.Unlock()
		st := recState()
		tick(cfg, st, sec(100), http.DefaultClient)
		if len(ck.records) != tc.posts {
			t.Fatalf("%s: posts=%d", tc.settings, len(ck.records))
		}
		if tc.posts == 0 && !st.RecordsCursor.IsZero() {
			t.Fatalf("cursor kept while off: %v", st.RecordsCursor)
		}
	}
}

func TestRecordsRejectedNotResentAndIndependentFailures(t *testing.T) {
	recordsGone.Store(false)
	var logs strings.Builder
	log.SetOutput(&logs)
	defer log.SetOutput(io.Discard)
	aw := fakeAW(t, []awEvent{ev(0, 600, "code", "a"), ev(600, 10, "chrome", "b")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	ck.recReply = `{"accepted":1,"duplicates":0,"ignored":0,"rejected":[{"index":1,"reason":"seconds 超出范围"}]}`
	cfg := testConfig(aw.URL, ck.URL)
	st := recState()
	if _, err := tick(cfg, st, sec(700), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(logs.String(), "拒收 1 条原始记录") || len(ck.records) != 1 {
		t.Fatalf("log=%q posts=%d", logs.String(), len(ck.records))
	}
	tick(cfg, st, sec(800), http.DefaultClient)
	if len(ck.records) != 1 {
		t.Fatalf("rejected resent: %d", len(ck.records))
	}

	// 记录失败不挡段：段照传，段的游标照走。
	ck.records, ck.segs = nil, nil
	ck.recStatus = 500
	st = recState()
	if _, err := tick(cfg, st, sec(1000), http.DefaultClient); err != nil {
		t.Fatalf("records failure blocked segments: %v", err)
	}
	if len(ck.segs) != 1 || st.Cursor.Before(sec(600)) {
		t.Fatalf("segs=%d cursor=%v", len(ck.segs), st.Cursor)
	}
	// 段失败不挡记录。
	ck.records, ck.segs = nil, nil
	ck.recStatus, ck.segStatus = 200, 503
	st = recState()
	if _, err := tick(cfg, st, sec(1000), http.DefaultClient); err == nil {
		t.Fatal("want segment error")
	}
	if len(ck.records) != 1 || st.RecordsCursor.Equal(sec(0)) {
		t.Fatalf("records blocked by segment failure: %d %v", len(ck.records), st.RecordsCursor)
	}
}

// records=null 跟随账号三级分类开关：tiers=false / 404 / 坏响应都不传；缺省不多传。
func TestRecordsNullFollowsTiers(t *testing.T) {
	recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	for tiers, posts := range map[string]int{`{"tiers":true}`: 1, `{"tiers":false}`: 0, "404": 0, `oops`: 0, `{}`: 0} {
		ck.mu.Lock()
		ck.tiers, ck.records = tiers, nil
		ck.mu.Unlock()
		resetTiersCache()
		st := recState()
		tick(cfg, st, sec(100), http.DefaultClient)
		if len(ck.records) != posts {
			t.Fatalf("tiers=%s: posts=%d", tiers, len(ck.records))
		}
		// 只有明确拿到 tiers=false 才清游标；拿不到 / 解析失败保持不动。
		explicitOff := tiers == `{"tiers":false}` || tiers == `{}`
		if explicitOff != st.RecordsCursor.IsZero() && posts == 0 {
			t.Fatalf("tiers=%s: cursor=%v", tiers, st.RecordsCursor)
		}
	}
	// records=true 不看 tiers。
	ck.tiers = "404"
	ck.settings = `{"settings":{"records":true}}`
	ck.records = nil
	tick(cfg, recState(), sec(100), http.DefaultClient)
	if len(ck.records) != 1 {
		t.Fatal("records=true ignored")
	}
}

// 暂停 / 关闭 / 配置不全 / 刚开启：游标清零，恢复后不补传这段时间的活动。
func TestRecordsNoBackfillAfterPause(t *testing.T) {
	recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a")}, nil)
	defer aw.Close()
	ck := newRecCockpit()
	defer ck.Close()
	for name, mod := range map[string]func(*Config){
		"paused":     func(c *Config) { c.Paused = true },
		"disabled":   func(c *Config) { c.Enabled = false },
		"incomplete": func(c *Config) { c.DeviceToken = "" },
	} {
		cfg := testConfig(aw.URL, ck.URL)
		mod(&cfg)
		st := recState()
		tick(cfg, st, sec(100), http.DefaultClient)
		if !st.RecordsCursor.IsZero() {
			t.Fatalf("%s: cursor kept %v", name, st.RecordsCursor)
		}
		// 恢复：第一轮只是定下起点，不传暂停前的活动；
		cfg = testConfig(aw.URL, ck.URL)
		tick(cfg, st, sec(200), http.DefaultClient)
		tick(cfg, st, sec(300), http.DefaultClient)
		if len(ck.records) != 0 {
			t.Fatalf("%s: backfilled %d", name, len(ck.records))
		}
	}
}

// tiers 的答案缓存：一分钟内不重复 GET；GET 失败不传、游标不动。
func TestTiersCacheAndFailureKeepsCursor(t *testing.T) {
	recordsGone.Store(false)
	aw := fakeAW(t, []awEvent{ev(0, 10, "code", "a")}, nil)
	defer aw.Close()
	gets := 0
	fail := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/Cockpit/api/core/activity/settings" {
			gets++
			if fail {
				w.WriteHeader(500)
				return
			}
			io.WriteString(w, `{"tiers":true}`)
			return
		}
		if r.URL.Path == "/Cockpit/api/core/activity/records" {
			return
		}
		w.WriteHeader(404)
	}))
	defer srv.Close()
	defer resetTiersCache()
	resetTiersCache()
	cfg := testConfig(aw.URL, srv.URL)
	st := recState()
	tick(cfg, st, sec(100), http.DefaultClient)
	tick(cfg, st, sec(130), http.DefaultClient)
	if gets != 1 {
		t.Fatalf("gets=%d within TTL", gets)
	}
	tick(cfg, st, sec(170), http.DefaultClient)
	if gets != 2 {
		t.Fatalf("gets=%d after TTL", gets)
	}
	fail = true
	before := st.RecordsCursor
	tick(cfg, st, sec(300), http.DefaultClient)
	if gets != 3 || !st.RecordsCursor.Equal(before) {
		t.Fatalf("gets=%d cursor %v -> %v", gets, before, st.RecordsCursor)
	}
}
