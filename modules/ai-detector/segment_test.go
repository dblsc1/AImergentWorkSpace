package main

import (
	"testing"
	"time"
)

var t0 = time.Date(2026, 9, 26, 10, 0, 0, 0, time.UTC)

func at(min float64) time.Time { return t0.Add(minutes(min)) }

// f 造一个碎片：[a,b) 分钟，键就用 app+title。
func f(a, b float64, app, title string) fragment {
	return fragment{Start: at(a), End: at(b), App: app, Title: title, Key: app + "\x00" + title}
}

const G = 5 * time.Minute

func TestMergeContiguousSameKey(t *testing.T) {
	s := merge([]fragment{f(0, 10, "code", "x"), f(10, 20, "code", "x")}, G)
	if len(s) != 1 || !s[0].Start.Equal(at(0)) || !s[0].End.Equal(at(20)) || s[0].Active != 20*time.Minute {
		t.Fatalf("%+v", s)
	}
}

func TestMergeAbsorbsShortInterruption(t *testing.T) {
	// 写代码中途切去聊天 2 分钟再回来：一段，聊天那 2 分钟算在段里。
	s := merge([]fragment{f(0, 10, "code", "x"), f(10, 12, "wechat", ""), f(12, 30, "code", "x")}, G)
	if len(s) != 1 || s[0].App != "code" || !s[0].End.Equal(at(30)) || s[0].Active != 30*time.Minute {
		t.Fatalf("%+v", s)
	}
}

func TestMergeGapBoundary(t *testing.T) {
	// 恰好 G 合并，超过 G 一秒就拆开。
	s := merge([]fragment{f(0, 10, "code", "x"), f(15, 20, "code", "x")}, G)
	if len(s) != 1 {
		t.Fatalf("gap == G should merge: %+v", s)
	}
	fr := []fragment{f(0, 10, "code", "x"), f(15, 20, "code", "x")}
	fr[1].Start = fr[1].Start.Add(time.Second)
	if s := merge(fr, G); len(s) != 2 {
		t.Fatalf("gap > G should split: %+v", s)
	}
}

func TestMergeLongInterruptionSplits(t *testing.T) {
	// 切出去 10 分钟（> G）：三段，中间那段是浏览器自己的。
	s := merge([]fragment{f(0, 10, "code", "x"), f(10, 20, "firefox", "y"), f(20, 30, "code", "x")}, G)
	if len(s) != 3 || s[1].App != "firefox" || s[2].Start != at(20) {
		t.Fatalf("%+v", s)
	}
}

func TestMergeTitlePicksLongest(t *testing.T) {
	fr := []fragment{f(0, 2, "code", "a"), f(2, 10, "code", "b"), f(10, 12, "code", "a")}
	for i := range fr {
		fr[i].Key = "code" // 同键不同标题（titleKey 把它们归到一起的情形）
	}
	s := merge(fr, G)
	if len(s) != 1 || s[0].Title != "b" {
		t.Fatalf("%+v", s)
	}
}

func TestAFKIsSubtractedAndGapCounts(t *testing.T) {
	// 窗口一直开着 0–60，中间 20–45 离开：离开 25 分钟 > G，拆成两段，都不含离开时间。
	d := awData{
		window: []awEvent{{Timestamp: at(0), Duration: 3600, Data: map[string]any{"app": "code", "title": "x"}}},
		afk: []awEvent{
			{Timestamp: at(0), Duration: 1200, Data: map[string]any{"status": "not-afk"}},
			{Timestamp: at(20), Duration: 1500, Data: map[string]any{"status": "afk"}},
		},
	}
	fr := buildFragments(d, at(0), at(60), newRedactor(Config{}))
	s := merge(fr, G)
	if len(s) != 2 || s[0].Active != 20*time.Minute || s[1].Active != 15*time.Minute || s[1].Start != at(45) {
		t.Fatalf("%+v", s)
	}
	// 离开 3 分钟（≤ G）：一段，墙钟 60 分钟，在电脑前 57 分钟。
	d.afk[1].Duration = 180
	s = merge(buildFragments(d, at(0), at(60), newRedactor(Config{})), G)
	if len(s) != 1 || s[0].Active != 57*time.Minute || s[0].End.Sub(s[0].Start) != time.Hour {
		t.Fatalf("%+v", s)
	}
}

func TestClipToWindow(t *testing.T) {
	// 服务端给的事件可能伸出查询区间（旧版不裁）：本地必须裁。
	d := awData{window: []awEvent{{Timestamp: at(-30), Duration: 3600, Data: map[string]any{"app": "code", "title": "x"}}}}
	fr := buildFragments(d, at(0), at(10), newRedactor(Config{}))
	if len(fr) != 1 || fr[0].Start != at(0) || fr[0].End != at(10) {
		t.Fatalf("%+v", fr)
	}
}

func TestMidnightIsOneSegment(t *testing.T) {
	sh, _ := time.LoadLocation("Asia/Shanghai")
	a := time.Date(2026, 9, 26, 23, 40, 0, 0, sh)
	fr := []fragment{{Start: a, End: a.Add(40 * time.Minute), App: "code", Key: "k"}}
	s := merge(fr, G)
	if len(s) != 1 || s[0].Active != 40*time.Minute {
		t.Fatalf("%+v", s)
	}
}

func TestDSTUsesAbsoluteTime(t *testing.T) {
	// 纽约 2026-03-08 02:00 拨快到 03:00。墙钟 01:50–03:10 实际只过了 20 分钟。
	ny, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Skip("no tzdata")
	}
	a := time.Date(2026, 3, 8, 1, 50, 0, 0, ny)
	b := time.Date(2026, 3, 8, 3, 10, 0, 0, ny)
	s := merge([]fragment{{Start: a, End: b, App: "code", Key: "k"}}, G)
	if s[0].Active != 20*time.Minute {
		t.Fatalf("active=%v", s[0].Active)
	}
}

func TestSettle(t *testing.T) {
	segs := merge([]fragment{
		f(0, 2, "chat", ""),       // 短，收口 → 丢
		f(10, 30, "code", "x"),    // 收口 → 传
		f(40, 58, "firefox", "y"), // 58+5 > 60，没收口 → 游标停在 40
	}, G)
	up, cur := settle(segs, at(60), G, 3*time.Minute)
	if len(up) != 1 || up[0].App != "code" || cur != at(40) {
		t.Fatalf("up=%+v cur=%v", up, cur)
	}
	// 全部收口：游标到 now-G。
	up, cur = settle(segs[:2], at(60), G, 3*time.Minute)
	if len(up) != 1 || cur != at(55) {
		t.Fatalf("up=%+v cur=%v", up, cur)
	}
}

func TestResumeFromOpenSegmentIsDeterministic(t *testing.T) {
	// 第一轮没收口的段，下一轮从它的开始处重算，startAt 不变——服务端防重就靠这个。
	all := []fragment{f(0, 20, "code", "x"), f(20, 25, "chat", ""), f(25, 50, "code", "x")}
	_, cur := settle(merge(all[:1], G), at(22), G, 0)
	var rest []fragment
	for _, x := range all {
		if !x.Start.Before(cur) {
			rest = append(rest, x)
		}
	}
	up, _ := settle(merge(rest, G), at(60), G, 0)
	if len(up) != 1 || up[0].Start != at(0) || up[0].End != at(50) {
		t.Fatalf("cur=%v up=%+v", cur, up)
	}
}
