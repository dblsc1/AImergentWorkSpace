package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// 按标签页分段（契约「按标签页分段」）。

const term = "org.gnome.Ptyxis"

// 仓主的真实用法：一个终端、很多标签页来回切。A 5 分 → B 20 秒 → A 3 分 → C 4 分 → A 2 分。
func interleaved() []awEvent {
	return []awEvent{
		win(0, 5, term, "✳ Cockpit-Pub-Coder1"),
		win(5, 1.0/3, term, "GardenV0.2 Dev"),
		win(5+1.0/3, 3, term, "⠋ Cockpit-Pub-Coder1"), // 转圈动画换了开头的符号：还是同一个标签页
		win(8+1.0/3, 4, term, "AImergent教育部门技术主管"),
		win(12+1.0/3, 2, term, "Cockpit-Pub-Coder1 - Ptyxis"),
	}
}

// plain：同样的切换节奏，标题不带状态符号（v1.1 的键认不出符号，会把同一个标签页拆开）。
func plain() []awEvent {
	return []awEvent{
		win(0, 5, term, "Cockpit-Pub-Coder1"), win(5, 1.0/3, term, "GardenV0.2 Dev"), win(5+1.0/3, 3, term, "Cockpit-Pub-Coder1"),
		win(8+1.0/3, 4, term, "AImergent教育部门技术主管"), win(12+1.0/3, 2, term, "Cockpit-Pub-Coder1"),
	}
}

func segsOf(evs []awEvent, cfg Config, to float64) []segment {
	return segments(buildFragments(awData{window: evs}, at(0), at(to), newRedactor(cfg)), G)
}

// brief 把段压成「标题|开始分钟|结束分钟|在电脑前秒数」，好一眼比对。
func brief(segs []segment) []string {
	var out []string
	for _, s := range segs {
		out = append(out, fmt.Sprintf("%s|%.2f|%.2f|%.0f", s.Title, s.Start.Sub(t0).Minutes(), s.End.Sub(t0).Minutes(), s.Active.Seconds()))
	}
	return out
}

func same(a, b []string) bool { return strings.Join(a, "\n") == strings.Join(b, "\n") }

func TestTabsEachGetTheirOwnSegment(t *testing.T) {
	all := segsOf(interleaved(), Config{}, 60)
	want := []string{
		"✳ Cockpit-Pub-Coder1|0.00|14.33|600", // 墙钟盖着 B、C，时长只有自己的 5+3+2 分
		"GardenV0.2 Dev|5.00|5.33|20",
		"AImergent教育部门技术主管|8.33|12.33|240",
	}
	if !same(brief(all), want) {
		t.Fatalf("got  %q\nwant %q", brief(all), want)
	}
	up, cur := settle(all, at(60), G, 3*time.Minute)
	if len(up) != 2 || up[0].Active != 10*time.Minute || up[1].Active != 4*time.Minute || cur != at(55) {
		t.Fatalf("B (20s) must be dropped, A and C kept: %q cur=%v", brief(up), cur)
	}
	// 重算一遍一模一样（map 遍历顺序不影响）：重发幂等的前提。
	for i := 0; i < 20; i++ {
		if !same(brief(segsOf(interleaved(), Config{}, 60)), want) {
			t.Fatal("segments must be a pure function of fragments")
		}
	}
}

func TestTabKeyNormalization(t *testing.T) {
	app := normApp(term)
	for _, c := range []struct{ a, b string }{
		{"✳ Cockpit-Pub-Coder1", "cockpit-pub-coder1"},
		{"⠋ Cockpit-Pub-Coder1", "cockpit-pub-coder1"},
		{"  Cockpit-Pub-Coder1  ", "cockpit-pub-coder1"},
		{"● * Cockpit-Pub-Coder1", "cockpit-pub-coder1"},
		{"(3) GardenV0.2   Dev", "gardenv0.2 dev"},
		{"GardenV0.2 Dev - Ptyxis", "gardenv0.2 dev"},
		{"GardenV0.2 Dev — org.gnome.Ptyxis", "gardenv0.2 dev"},
		{"AImergent教育部门技术主管", "aimergent教育部门技术主管"},
		{"notes - draft", "notes - draft"}, // 结尾不是程序名：不动
		{"build - ok", "build - ok"},       // 「ok」不足 3 个字符，不当程序名
		{"✳", ""},
	} {
		if got := tabKey(c.a, app); got != c.b {
			t.Errorf("tabKey(%q) = %q, want %q", c.a, got, c.b)
		}
	}
	// 不同的标签页不能撞到一起
	if tabKey("Cockpit-Pub-Coder1", app) == tabKey("Cockpit-Pub-Coder2", app) {
		t.Fatal("different tabs share a key")
	}
}

func TestTabsOffOrTitlesDroppedFallBackToAppOnly(t *testing.T) {
	// 关掉：v1.1 的切法——A 开头，B、C 被吸收，一段 14 分 20 秒。
	off := segsOf(plain(), Config{SegmentByTitle: bp(false)}, 60)
	if len(off) != 1 || off[0].Active != 860*time.Second {
		t.Fatalf("off: %q", brief(off))
	}
	// v1.1 的键也不认状态符号：带符号的标题各是各的键（这正是要改的）。
	if s := segsOf(interleaved(), Config{SegmentByTitle: bp(false)}, 60); len(s) != 5 {
		t.Fatalf("off, with glyphs: %q", brief(s))
	}
	// 不在名单里的程序同样照旧；名单可以自己写（空名单 = 谁都不按标签页分）。
	if s := segsOf(plain(), Config{SegmentByTitleApps: []string{}}, 60); len(s) != 1 {
		t.Fatalf("empty list: %q", brief(s))
	}
	if s := segsOf(interleaved(), Config{SegmentByTitleApps: []string{"PTYXIS", "org.gnome.ptyxis"}}, 60); len(s) != 3 {
		t.Fatalf("custom list (case-insensitive): %q", brief(s))
	}
	// titles=drop：标题为空，键里没有标题，只按程序合成一段，不带标题。
	drop := segsOf(interleaved(), Config{Privacy: Privacy{Titles: "drop"}}, 60)
	if len(drop) != 1 || drop[0].Title != "" || drop[0].Active != 860*time.Second {
		t.Fatalf("drop: %q", brief(drop))
	}
	for _, f := range buildFragments(awData{window: interleaved()}, at(0), at(60), newRedactor(Config{Privacy: Privacy{Titles: "drop"}})) {
		if f.Tab || f.Key != normApp(term) {
			t.Fatalf("drop must not key by title: %+v", f)
		}
	}
	// app-only 名单里的程序即使也写进了终端名单，标题照样为空、不按标签页分。
	cfg := Config{SegmentByTitleApps: []string{"WeChat"}}
	if s := segsOf([]awEvent{win(0, 5, "WeChat", "张三"), win(5, 5, "WeChat", "李四")}, cfg, 60); len(s) != 1 || s[0].Title != "" {
		t.Fatalf("app-only wins: %q", brief(s))
	}
}

func TestTabsDoNotMixWithOtherStreams(t *testing.T) {
	// 写代码中途切去终端 2 分钟：代码还是一段，但那 2 分钟不算进去（终端自己不足 M，丢）。
	s := segsOf([]awEvent{win(0, 10, "code", "x"), win(10, 2, term, "proj"), win(12, 18, "code", "x")}, Config{}, 60)
	if !same(brief(s), []string{"x|0.00|30.00|1680", "proj|10.00|12.00|120"}) {
		t.Fatalf("%q", brief(s))
	}
	// 反过来：标签页的段也不吸收中间切出去的浏览器。
	s = segsOf([]awEvent{win(0, 5, term, "proj"), win(5, 1, "code", "y"), win(6, 5, term, "proj")}, Config{}, 60)
	if !same(brief(s), []string{"proj|0.00|11.00|600", "y|5.00|6.00|60"}) {
		t.Fatalf("%q", brief(s))
	}
	// 同一个标签页隔了超过 G 才回来：两段。
	s = segsOf([]awEvent{win(0, 5, term, "proj"), win(5, 6, term, "other"), win(11, 5, term, "proj")}, Config{}, 60)
	if len(s) != 3 || s[2].Start != at(11) {
		t.Fatalf("%q", brief(s))
	}
}

func TestBrowserAndEditorSegmentationUnchanged(t *testing.T) {
	// 浏览器即使被写进终端名单也仍按「程序 + 域名」：没有对得上的标签页记录时只留程序名。
	cfg := Config{SegmentByTitleApps: []string{"firefox", term}}
	fr := buildFragments(awData{window: []awEvent{win(0, 5, "firefox", "a"), win(5, 5, "firefox", "b")}}, at(0), at(60), newRedactor(cfg))
	for _, f := range fr {
		if f.Tab || f.Key != "firefox" || f.Title != "" {
			t.Fatalf("%+v", f)
		}
	}
	// 编辑器：同项目换文件不拆段、短暂切去聊天被吸收——和 v1.1 一样。
	s := segsOf([]awEvent{
		win(0, 10, "code", "main.go — garden — Code"), win(10, 2, "WeChat", "张三"), win(12, 18, "code", "plot.gd — garden — Code"),
	}, Config{}, 60)
	if len(s) != 1 || s[0].Active != 30*time.Minute || s[0].Title != "plot.gd — garden — Code" {
		t.Fatalf("%q", brief(s))
	}
}

func TestPseudonymizedTitlesCutTheSameSegments(t *testing.T) {
	keep := segsOf(interleaved(), Config{}, 60)
	ps := segsOf(interleaved(), Config{Privacy: Privacy{Titles: "pseudonymize"}}, 60)
	if !same(brief(keep), brief(ps)) {
		t.Fatalf("keep %q\npseudonymize %q", brief(keep), brief(ps))
	}
	// 同一个标签页不同轮次拿到同一个代号。
	cfg := Config{Privacy: Privacy{Titles: "pseudonymize"}, dir: t.TempDir()}
	a, _ := sendTitles(cfg, []string{ps[0].Title, ps[2].Title})
	b, _ := sendTitles(cfg, []string{ps[2].Title, ps[0].Title})
	if a[0] != "窗口名1" || a[1] != "窗口名2" || b[0] != a[1] || b[1] != a[0] {
		t.Fatalf("%v %v", a, b)
	}
}

func TestIdleFragmentsOfTerminalsStayInIdleStream(t *testing.T) {
	d := awData{
		window: []awEvent{win(0, 60, term, "proj")},
		afk:    []awEvent{{Timestamp: at(20), Duration: 1500, Data: map[string]any{"status": "afk"}}},
	}
	fr := buildFragments(d, at(0), at(60), newRedactor(Config{Idle: Idle{IdleSuggestions: true}}))
	var idle int
	for _, f := range fr {
		if f.Idle {
			idle++
			if f.Tab || f.Key != normApp(term)+"\x00"+titleKey("proj") { // 键也是老的
				t.Fatalf("idle fragment must not be a tab fragment: %+v", f)
			}
		}
	}
	s := segments(fr, G)
	if idle != 1 || len(s) != 3 || !s[1].Idle || s[1].Active != 25*time.Minute || s[0].Idle || s[2].Idle {
		t.Fatalf("idle=%d %q", idle, brief(s))
	}
}

func TestStartAtIsUniquePerSecond(t *testing.T) {
	// 两个标签页在同一秒里先后开始（第一个只闪了 0.3 秒）：不到 1 秒的碎片不算，起点不会撞在同一秒。
	fr := []fragment{
		{Start: t0.Add(100 * time.Millisecond), End: t0.Add(400 * time.Millisecond), App: term, Title: "a", Key: "a", Tab: true},
		{Start: t0.Add(400 * time.Millisecond), End: at(4), App: term, Title: "b", Key: "b", Tab: true},
		{Start: at(4), End: at(8), App: term, Title: "a", Key: "a", Tab: true},
	}
	s := segments(fr, G)
	if len(s) != 2 || isoTime(s[0].Start) == isoTime(s[1].Start) || s[1].Title != "a" || s[1].Start != at(4) {
		t.Fatalf("%q", brief(s))
	}
}

func TestSettleWithOpenSegmentInTheMiddle(t *testing.T) {
	// A 一直没收口（58 分还在用），C 早就收口：C 要传，游标停在 A 的开始。
	evs := append(interleaved(), win(16, 42, term, "Cockpit-Pub-Coder1"))
	up, cur := settle(segsOf(evs, Config{}, 60), at(60), G, 3*time.Minute)
	if len(up) != 1 || up[0].Title != "AImergent教育部门技术主管" || cur != at(0) {
		t.Fatalf("%q cur=%v", brief(up), cur)
	}
}

// 一轮一轮跑：A 没收口期间游标停在它的开始，早已发过的 C 不再重发；A 收口后带着最初的 startAt 传上去。
func TestOpenTabPinsCursorWithoutResending(t *testing.T) {
	evs := append(interleaved(), win(16, 24, term, "Cockpit-Pub-Coder1"), win(40, 30, "code", "x"))
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	var uploads []uploadBody
	fail := false
	ck := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.WriteHeader(404)
			return
		}
		if fail {
			w.WriteHeader(502)
			return
		}
		var b uploadBody
		raw, _ := io.ReadAll(r.Body)
		json.Unmarshal(raw, &b)
		uploads = append(uploads, b)
	}))
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	st := activeState()
	run := func(now float64) {
		t.Helper()
		if _, err := tick(cfg, st, at(now), http.DefaultClient); err != nil {
			t.Fatal(err)
		}
	}
	titles := func(b uploadBody) (out []string) {
		for _, s := range b.Segments {
			out = append(out, s.Title+"@"+s.StartAt[11:19])
		}
		return out
	}

	run(20) // C（8:20–12:20）已收口；A 还开着
	if len(uploads) != 1 || len(uploads[0].Segments) != 1 || uploads[0].Segments[0].Title != "AImergent教育部门技术主管" ||
		uploads[0].Segments[0].DurationSeconds != 240 || !st.Cursor.Equal(at(0)) {
		t.Fatalf("round 1: %v cursor=%v", uploads, st.Cursor)
	}
	run(25)
	run(30) // A 还开着：游标不动，C 不重发
	if len(uploads) != 1 || !st.Cursor.Equal(at(0)) {
		t.Fatalf("C was resent or cursor moved: %d uploads, cursor=%v", len(uploads), st.Cursor)
	}
	// 游标之后送达过的只有 C 那 4 分钟
	if len(st.Sent) != 1 || !st.Sent[0][0].Equal(at(8+1.0/3)) || !st.Sent[0][1].Equal(at(12+1.0/3)) {
		t.Fatalf("sent=%v", st.Sent)
	}
	fail = true // A 收口的那一轮上传失败：游标、已送达的记录都不动
	if _, err := tick(cfg, st, at(50), http.DefaultClient); err == nil || !st.Cursor.Equal(at(0)) || len(st.Sent) != 1 {
		t.Fatalf("failed round: err=%v cursor=%v sent=%v", err, st.Cursor, st.Sent)
	}
	fail = false
	run(50) // 重试：只发 A，startAt 是它最初的开始，时长只有自己的 5+3+2+24 分
	if len(uploads) != 2 || len(uploads[1].Segments) != 1 {
		t.Fatalf("round A: %v", uploads)
	}
	a := uploads[1].Segments[0]
	if a.Title != "Cockpit-Pub-Coder1" || a.StartAt != isoTime(at(0)) || a.EndAt != isoTime(at(40)) || a.DurationSeconds != 34*60 {
		t.Fatalf("A: %+v", a)
	}
	if !st.Cursor.Equal(at(40)) || len(st.Sent) != 0 { // 游标越过 A，停在没收口的 code 段的开始；之前的记录用不着了
		t.Fatalf("cursor=%v sent=%v", st.Cursor, st.Sent)
	}
	run(80)
	if len(uploads) != 3 || !same(titles(uploads[2]), []string{"x@" + isoTime(at(40))[11:19]}) {
		t.Fatalf("round 3: %v", uploads)
	}
}

// 游标停着的时候改了设置（关掉按标签页分段）：已经送达的 B、C 不会被重算的普通段再吸收一遍。
func TestSettingsChangeWhileCursorPinnedDoesNotDoubleCount(t *testing.T) {
	evs := []awEvent{
		win(0, 3, "code", "x"), win(3, 3, term, "B"), win(6, 1, "code", "x"), win(7, 3, term, "C"),
		win(10, 1, "code", "x"), win(11, 3, term, "B"), win(14, 16, "code", "x"),
	}
	aw := fakeAW(t, evs, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	st := activeState()
	if _, err := tick(cfg, st, at(20), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	cfg.SegmentByTitle = bp(false)
	if _, err := tick(cfg, st, at(40), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var total int64
	var got []string
	for _, raw := range ck.bodies {
		var b uploadBody
		json.Unmarshal(raw, &b)
		for _, s := range b.Segments {
			total += s.DurationSeconds
			got = append(got, fmt.Sprintf("%s|%s|%d", s.Title, s.StartAt[14:19], s.DurationSeconds))
		}
	}
	// B 6 分、C 3 分、code 3+1+1+16 = 21 分：合计正好是在电脑前的 30 分钟，一秒不多。
	if total != 30*60 || !same(got, []string{"B|03:00|360", "C|07:00|180", "x|00:00|1260"}) || !st.Cursor.Equal(at(35)) || len(st.Sent) != 0 {
		t.Fatalf("total=%d %q cursor=%v sent=%v", total, got, st.Cursor, st.Sent)
	}
}

func TestMarkSentAndUnsent(t *testing.T) {
	sp := func(a, b float64) span { return span{at(a), at(b)} }
	st := &State{Sent: [][2]time.Time{{at(20), at(25)}}}
	markSent(st, []segment{{parts: []span{sp(0, 5), sp(8, 10)}}, {parts: []span{sp(5, 8), sp(24, 30)}}}, at(4))
	// 相接、交叠的并成一条；游标之前的丢掉（跨着游标的留着）。
	if len(st.Sent) != 2 || st.Sent[0] != [2]time.Time{at(0), at(10)} || st.Sent[1] != [2]time.Time{at(20), at(30)} || st.Cursor != at(4) {
		t.Fatalf("%v", st.Sent)
	}
	got := unsent([]fragment{f(4, 12, "a", "x"), f(12, 22, "b", "y"), f(22, 28, "c", "z")}, st.Sent)
	if len(got) != 2 || got[0].Start != at(10) || got[0].End != at(12) || got[0].Title != "x" || got[1].Start != at(12) || got[1].End != at(20) {
		t.Fatalf("%+v", got)
	}
	markSent(st, nil, at(30))
	if len(st.Sent) != 0 {
		t.Fatalf("%v", st.Sent)
	}
}

func TestRemoteSettingsCanTurnTabsOff(t *testing.T) {
	aw := fakeAW(t, plain(), nil)
	defer aw.Close()
	reply := ""
	var got uploadBody
	ck := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/api/core/detector/settings"):
			io.WriteString(w, reply)
		case r.Method == http.MethodGet:
			w.WriteHeader(404)
		default:
			raw, _ := io.ReadAll(r.Body)
			json.Unmarshal(raw, &got)
		}
	}))
	defer ck.Close()
	for _, c := range []struct {
		name, settings string
		local          *bool
		want           int
	}{
		{"web settings from v1.1 (no key) → local default on", `{"schemaVersion":1,"privacy":{},"idle":{}}`, nil, 2},
		{"web off wins over local on", `{"schemaVersion":1,"privacy":{},"idle":{},"segmentByTitle":false,"segmentByTitleApps":null}`, bp(true), 1},
		{"web on wins over local off", `{"schemaVersion":1,"privacy":{},"idle":{},"segmentByTitle":true}`, bp(false), 2},
		{"web list replaces the built-in one", `{"schemaVersion":1,"privacy":{},"idle":{},"segmentByTitle":true,"segmentByTitleApps":["kitty"]}`, nil, 1},
		{"no web settings → local off", `null`, bp(false), 1},
	} {
		reply, got = `{"deviceId":"dev_test","settings":`+c.settings+`}`, uploadBody{}
		cfg := testConfig(aw.URL, ck.URL)
		cfg.SegmentByTitle = c.local
		if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil || len(got.Segments) != c.want {
			t.Errorf("%s: %d segments, err=%v", c.name, len(got.Segments), err)
		}
	}
}
