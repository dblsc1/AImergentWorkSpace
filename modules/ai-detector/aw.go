package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"
)

// ActivityWatch 本地 REST 接口（只读）。形状出处：
//   - 路由：aw-server/aw_server/rest.py —— GET /api/0/buckets/ 返回 {桶id: 桶元数据}；
//     GET /api/0/buckets/<id>/events?start=&end=&limit=（limit 缺省 -1 = 不限）。
//   - 事件序列化：aw-core/aw_core/models.py Event.to_json_dict —— timestamp 是
//     astimezone(utc).isoformat()，duration 是 total_seconds() 浮点秒。
//   - 筛选语义：aw-core peewee 存储与 aw-server-rust datastore.rs 都按「与区间有重叠」筛
//     （endtime >= start AND starttime <= end），并把事件裁到区间内；返回按开始时刻倒序。
//   - 桶类型与 data：docs.activitywatch.net「Buckets and events」——currentwindow {app,title}、
//     afkstatus {status:"afk"|"not-afk"}、web.tab.current {url,title,audible,incognito}。
// 我们不依赖「服务端已裁剪 / 已排序」：本地再裁、再排，换哪个版本的 aw-server 结果都一样。

type awEvent struct {
	Timestamp time.Time      `json:"timestamp"`
	Duration  float64        `json:"duration"`
	Data      map[string]any `json:"data"`
}

func (e awEvent) end() time.Time {
	return e.Timestamp.Add(time.Duration(e.Duration * float64(time.Second)))
}

func (e awEvent) str(k string) string {
	s, _ := e.Data[k].(string)
	return s
}

type awBucket struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Hostname string `json:"hostname"`
}

type awClient struct {
	base string // 形如 http://localhost:5600/api/0
	http *http.Client
}

func (c awClient) get(path string, out any) error {
	resp, err := c.http.Get(strings.TrimRight(c.base, "/") + path)
	if err != nil {
		return fmt.Errorf("连不上 ActivityWatch（%s）：%w", c.base, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("ActivityWatch %s 返回 %d", path, resp.StatusCode)
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// awData 是一轮要用的三类事件。web 可能为空（没装浏览器扩展）。
type awData struct {
	window, afk, web []awEvent
}

// fetch 取 [from,to] 的三类事件。windowBucket / afkBucket 非空时直接用它们（桶 id），
// 否则按本机主机名找。
func (c awClient) fetch(from, to time.Time, windowBucket, afkBucket string) (awData, error) {
	var buckets map[string]awBucket
	if err := c.get("/buckets/", &buckets); err != nil {
		return awData{}, err
	}
	host, _ := os.Hostname()
	// 只认本机的桶。ActivityWatch 可以在多台设备间同步，同类型的桶可能有好几台机器的；
	// 猜错一个就会把别人的电脑活动当成本机的上传。所以对不上主机名就报错，不猜——
	// 主机名改过的话，在配置里写明桶 id。
	pick := func(typ string) []string {
		var ids []string
		for id, b := range buckets {
			if b.Type == typ && b.Hostname == host {
				ids = append(ids, id)
			}
		}
		sort.Strings(ids)
		return ids
	}
	one := func(typ, explicit, field string) (string, error) {
		if explicit != "" {
			if _, ok := buckets[explicit]; !ok {
				return "", fmt.Errorf("配置的 %s=%q 在 ActivityWatch 里不存在", field, explicit)
			}
			return explicit, nil
		}
		ids := pick(typ)
		if len(ids) == 0 {
			var others []string
			for id, b := range buckets {
				if b.Type == typ {
					others = append(others, id)
				}
			}
			sort.Strings(others)
			return "", fmt.Errorf("ActivityWatch 里没有本机（主机名 %q）的 %s 桶；现有同类桶 %v。"+
				"记录器没在跑，或主机名改过——后者请在配置里写 %s", host, typ, others, field)
		}
		return ids[0], nil
	}
	q := "?start=" + url.QueryEscape(from.UTC().Format(time.RFC3339Nano)) +
		"&end=" + url.QueryEscape(to.UTC().Format(time.RFC3339Nano))
	events := func(ids ...string) ([]awEvent, error) {
		var out []awEvent
		for _, id := range ids {
			var evs []awEvent
			if err := c.get("/buckets/"+url.PathEscape(id)+"/events"+q, &evs); err != nil {
				return nil, err
			}
			out = append(out, evs...)
		}
		return out, nil
	}
	win, err := one("currentwindow", windowBucket, "windowBucket")
	if err != nil {
		return awData{}, err
	}
	afk, err := one("afkstatus", afkBucket, "afkBucket")
	if err != nil {
		return awData{}, err
	}
	var d awData
	if d.window, err = events(win); err != nil {
		return d, err
	}
	if d.afk, err = events(afk); err != nil {
		return d, err
	}
	// 浏览器扩展每个浏览器一个桶，本机的全要；它们只用来给浏览器窗口找域名，不会叠时间。
	if d.web, err = events(pick("web.tab.current")...); err != nil {
		return d, err
	}
	return d, nil
}

// fragment 是脱敏之后、合并之前的一小段：一次窗口停留扣掉离开时间剩下的部分。
type fragment struct {
	Start, End time.Time
	App, Title string // Title 已脱敏
	Raw        string // 原始窗口标题，只过了强制脱敏：只进本机留档，从不上传
	Key        string // 合并键，见 redactor.window
	Idle       bool   // 无操作碎片（idle.idleSuggestions），只和无操作碎片合并
}

type span struct{ start, end time.Time }

// subtract 从 s 里挖掉 holes（holes 已按开始排序），返回剩下的若干段。
func subtract(s span, holes []span) []span {
	out := []span{}
	cur := s.start
	for _, h := range holes {
		if !h.end.After(cur) || !h.start.Before(s.end) {
			continue
		}
		if h.start.After(cur) {
			out = append(out, span{cur, h.start})
		}
		if h.end.After(cur) {
			cur = h.end
		}
	}
	if cur.Before(s.end) {
		out = append(out, span{cur, s.end})
	}
	return out
}

func clip(s span, from, to time.Time) (span, bool) {
	if s.start.Before(from) {
		s.start = from
	}
	if s.end.After(to) {
		s.end = to
	}
	return s, s.end.After(s.start)
}

// buildFragments：窗口事件裁到 [from,to] → 扣掉离开（按 idle 节缩减过的）→ 脱敏。
// 原始标题在这一步之后只剩强制脱敏过的 Raw（留档用）。
func buildFragments(d awData, from, to time.Time, r redactor) []fragment {
	th := minutes(r.idle.AfkThresholdMinutes)
	var afk, audible []span
	for _, e := range d.afk {
		// afkThresholdMinutes：短于阈值的离开整段不算离开。还没结束的离开（一直延续到现在）不知道最后多长，
		// 照算离开——否则一段正在变长的离开会在它还短的时候被当成在电脑前传上去。
		ongoing := !e.end().Before(to.Add(-time.Minute))
		if e.str("status") == "afk" && (ongoing || e.end().Sub(e.Timestamp) >= th) {
			afk = append(afk, span{e.Timestamp, e.end()})
		}
	}
	sort.Slice(afk, func(i, j int) bool { return afk[i].start.Before(afk[j].start) })
	if r.idle.AudibleAsPresent {
		for _, e := range d.web {
			if a, _ := e.Data["audible"].(bool); a {
				audible = append(audible, span{e.Timestamp, e.end()})
			}
		}
		sort.Slice(audible, func(i, j int) bool { return audible[i].start.Before(audible[j].start) })
	}
	focusCap := minutes(focusMax(r.idle))

	// 窗口事件理论上首尾相接，实际会重叠（记录器重启、数据恢复 / 导入）。重叠不裁掉的话，
	// 同一分钟算两遍 Active，而且段的起点会随「这一轮从哪读起」漂移，服务端按 startAt
	// 防重就失效了。按 (开始, 结束) 排好后，每条的开始不早于前面的最晚结束。
	win := append([]awEvent(nil), d.window...)
	sort.SliceStable(win, func(i, j int) bool {
		if !win[i].Timestamp.Equal(win[j].Timestamp) {
			return win[i].Timestamp.Before(win[j].Timestamp)
		}
		return win[i].end().Before(win[j].end())
	})
	var out []fragment
	covered := from
	for _, e := range win {
		s, ok := clip(span{e.Timestamp, e.end()}, covered, to)
		if !ok {
			continue
		}
		covered = s.end
		app := normApp(e.str("app"))
		holes := afk
		// 这个窗口在前台时，哪些离开不算离开（契约「离开判定」）。
		if len(audible) > 0 && r.browsers[app] {
			var h2 []span
			for _, h := range holes {
				h2 = append(h2, subtract(h, audible)...)
			}
			holes = h2
		}
		if r.idle.FocusAppsEnabled && r.focus[app] {
			var h2 []span
			for _, h := range holes {
				if h.start = h.start.Add(focusCap); h.end.After(h.start) {
					h2 = append(h2, h)
				}
			}
			holes = h2
		}
		raw := scrubSecrets(e.str("title"))
		emit := func(p span, idle bool) {
			title, key := r.window(e.str("app"), e.str("title"), bestTab(d.web, p))
			out = append(out, fragment{Start: p.start, End: p.end, App: e.str("app"), Title: title, Raw: raw, Key: key, Idle: idle})
		}
		present := subtract(s, holes)
		for _, p := range present {
			emit(p, false)
		}
		if r.idle.IdleSuggestions {
			// 前台窗口没换、但算离开的部分：不丢，做成无操作碎片。
			for _, p := range subtract(s, present) {
				emit(p, true)
			}
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out
}

// bestTab 找与 p 重叠最久的浏览器标签页记录。ponytail: 线性扫，O(窗口碎片×标签页事件)，
// 一轮只有几分钟的数据，够用；积压很久一次补传时慢一点也无所谓。
func bestTab(web []awEvent, p span) *webTab {
	var best *awEvent
	var bestOv time.Duration
	for i := range web {
		s, ok := clip(span{web[i].Timestamp, web[i].end()}, p.start, p.end)
		if !ok {
			continue
		}
		if ov := s.end.Sub(s.start); ov > bestOv {
			best, bestOv = &web[i], ov
		}
	}
	if best == nil {
		return nil
	}
	inc, _ := best.Data["incognito"].(bool)
	return &webTab{URL: best.str("url"), Title: best.str("title"), Incognito: inc}
}
