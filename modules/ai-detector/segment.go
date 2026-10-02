package main

import (
	"sort"
	"time"
)

// segment 是合并后的一段，也是唯一会离开本机的东西（再加上分类建议）。
type segment struct {
	Start, End time.Time
	Active     time.Duration // 段内在电脑前的时间（碎片时长之和，含被吸收的短暂切换）
	App, Title string        // Title：隐私选项处理后、换代号前（规则分类匹配它）
	Raw        string        // 本机原始标题（只过强制脱敏），只进留档
	Sent       string        // 真正离开本机的标题（换过代号的就是代号）；tick 在分类前填
	Idle       bool
}

// merge 把碎片合成段，规则见 contract.md「合并」。frags 必须按 Start 排序、互不重叠
// （buildFragments 保证）。纯函数：同样的碎片永远得到同样的段——这是「重算重发
// 幂等」的前提，服务端靠 startAt 防重。
//
// 返回的是全部候选段，包括太短、没收口的；取舍在 settle 里做，好分开测。
func merge(frags []fragment, gap time.Duration) []segment {
	var out []segment
	for i := 0; i < len(frags); {
		first := frags[i]
		last := i
		for j := i + 1; j < len(frags); j++ {
			// 距本段最后一个同键碎片的结束超过 G：中间那些（切出去 / 离开）太久了，收段。
			if frags[j].Start.Sub(frags[last].End) > gap {
				break
			}
			if frags[j].Key == first.Key {
				last = j
			}
		}
		seg := segment{Start: first.Start, End: frags[last].End, App: first.App, Idle: first.Idle}
		byTitle := map[string]time.Duration{}
		for _, f := range frags[i : last+1] {
			d := f.End.Sub(f.Start)
			seg.Active += d
			if f.Key == first.Key {
				byTitle[f.Title] += d
			}
		}
		// 段的标题取同键碎片里停留最久的那个；并列取先出现的，保证确定性。
		var best time.Duration = -1
		for _, f := range frags[i : last+1] {
			if f.Key == first.Key && byTitle[f.Title] > best {
				seg.Title, seg.Raw, best = f.Title, f.Raw, byTitle[f.Title]
			}
		}
		out = append(out, seg)
		i = last + 1
	}
	return out
}

// segments 把一轮的碎片合成全部候选段，按开始时刻排。三种流各自合并、互不吸收：
//   - 普通碎片：merge 的老规则（中间夹着的异键碎片被吸收）；
//   - 无操作碎片：同上，只在无操作碎片之间——混在一起会被当成「短暂切出去」算成在电脑前；
//   - 标签页碎片：每个键一条流，单键的 merge 就是「间隔 ≤ G 接上」，Active 只有自己的碎片。
//
// 不同流的段墙钟跨度可以交叠。短于 1 秒的碎片不要：每段都从 ≥ 1 秒的碎片开始、碎片互不重叠，
// 任何两段的 startAt（精确到秒）就一定不同，服务端按它防重。纯函数，同 merge。
func segments(frags []fragment, gap time.Duration) []segment {
	var act, idle []fragment
	tabs := map[string][]fragment{}
	for _, f := range frags {
		switch {
		case f.End.Sub(f.Start) < time.Second:
		case f.Idle:
			idle = append(idle, f)
		case f.Tab:
			tabs[f.Key] = append(tabs[f.Key], f)
		default:
			act = append(act, f)
		}
	}
	out := append(merge(act, gap), merge(idle, gap)...)
	for _, fs := range tabs {
		out = append(out, merge(fs, gap)...)
	}
	// 开始时刻各不相同，所以 map 的遍历顺序不影响结果。
	sort.Slice(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out
}

// settle 决定这一轮上传哪些段、游标挪到哪。
//   - 收口 = End+G ≤ now：之后不可能再有碎片并进来，这段定了。
//   - 下一轮从最早的没收口段的开始处重算（几条流交叠时，没收口的不一定在尾部）。
//   - 短于 M 的收口段直接丢掉（游标越过它）。
func settle(segs []segment, now time.Time, gap, min time.Duration) (upload []segment, cursor time.Time) {
	cursor = now.Add(-gap)
	for _, s := range segs {
		if s.End.Add(gap).After(now) {
			if s.Start.Before(cursor) {
				cursor = s.Start
			}
			continue
		}
		if s.Active >= min {
			upload = append(upload, s)
		}
	}
	return upload, cursor
}
