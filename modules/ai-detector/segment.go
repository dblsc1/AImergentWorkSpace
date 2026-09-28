package main

import "time"

// segment 是合并后的一段，也是唯一会离开本机的东西（再加上分类建议）。
type segment struct {
	Start, End time.Time
	Active     time.Duration // 段内在电脑前的时间（碎片时长之和，含被吸收的短暂切换）
	App, Title string
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
		seg := segment{Start: first.Start, End: frags[last].End, App: first.App}
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
				seg.Title, best = f.Title, byTitle[f.Title]
			}
		}
		out = append(out, seg)
		i = last + 1
	}
	return out
}

// settle 决定这一轮上传哪些段、游标挪到哪。
//   - 收口 = End+G ≤ now：之后不可能再有碎片并进来，这段定了。
//   - 没收口的段一定是尾部（段按时间先后排、互不重叠），下一轮从第一个没收口段的开始处重算。
//   - 短于 M 的收口段直接丢掉（游标越过它）。
func settle(segs []segment, now time.Time, gap, min time.Duration) (upload []segment, cursor time.Time) {
	cursor = now.Add(-gap)
	for _, s := range segs {
		if s.End.Add(gap).After(now) {
			if s.Start.Before(cursor) {
				cursor = s.Start
			}
			break
		}
		if s.Active >= min {
			upload = append(upload, s)
		}
	}
	return upload, cursor
}
