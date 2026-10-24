package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// 原始记录流（ai-detector 契约「原始记录」，nexus-core v2.38）：buildFragments 的产物，不跨窗口吸收，
// 只把同一合并键、首尾间隔 ≤ 1 秒的相邻片段接起来；< 1 秒丢；收口（结束 + 1 秒 ≤ 现在）才传。

const (
	recordGap   = time.Second
	recordBatch = 500
)

// recordsGone：服务端回过 404（没有这个端点），这次运行内不再传。
var recordsGone atomic.Bool

type record struct {
	Start, End time.Time
	App, Title string // Title：隐私选项处理后、换代号前
}

type uploadRecord struct {
	StartAt string `json:"startAt"`
	EndAt   string `json:"endAt"`
	App     string `json:"app"`
	Title   string `json:"title"`
	// titleIsPseudonym：标题是不是代号（titles: "pseudonymize" 且标题非空）。
	TitleIsPseudonym bool    `json:"titleIsPseudonym"`
	Block            *string `json:"block"`
}

func isoMs(t time.Time) string { return t.Local().Format("2006-01-02T15:04:05.000-07:00") }

// joinRecords：frags 按 Start 排序、互不重叠（buildFragments 保证）。无操作碎片不算（契约：已去掉离开与空闲）。
// 返回全部候选（含太短、没收口的），取舍在 recordsRound。纯函数。
func joinRecords(frags []fragment) []record {
	var out []record
	for i := 0; i < len(frags); {
		if frags[i].Idle {
			i++
			continue
		}
		j := i
		for j+1 < len(frags) && !frags[j+1].Idle && frags[j+1].Key == frags[i].Key && frags[j+1].Start.Sub(frags[j].End) <= recordGap {
			j++
		}
		// 标题取同键碎片里停留最久的，并列取先出现的（同 merge）。
		byTitle := map[string]time.Duration{}
		for _, f := range frags[i : j+1] {
			byTitle[f.Title] += f.End.Sub(f.Start)
		}
		r := record{Start: frags[i].Start.Truncate(time.Millisecond), End: frags[j].End.Truncate(time.Millisecond), App: frags[i].App}
		best := time.Duration(-1)
		for _, f := range frags[i : j+1] {
			if byTitle[f.Title] > best {
				r.Title, best = f.Title, byTitle[f.Title]
			}
		}
		out = append(out, r)
		i = j + 1
	}
	return out
}

// blockFor：服务端规则集里第一条命中的第一级规则的块名，没命中 nil。匹配隐私处理后、换代号前的标题。
func blockFor(rules []rule, app, title string) *string {
	for _, r := range rules {
		if r.Block != "" && (r.app == nil || r.app.MatchString(app)) && (r.title == nil || r.title.MatchString(title)) {
			b := r.Block
			return &b
		}
	}
	return nil
}

// recordsWanted：records 为 true 传、false 不传；null 跟随账号的三级分类开关（GET activity/settings 的 tiers），
// 拿不到 / 404 / 解析失败 = 不传（缺省不多传任何东西）。
//
// known=false：这一轮拿不到答案（GET 失败）——不传，也不动游标。
func recordsWanted(cfg Config, c *http.Client, base string, now time.Time) (want, known bool) {
	if recordsGone.Load() {
		return false, true
	}
	if cfg.records != nil {
		return *cfg.records, true
	}
	return tiersOn(cfg, c, base, now)
}

// tiers 的答案缓存约 60 秒：不是每轮都 GET。失败不缓存。
const tiersTTL = time.Minute

var tiersCache struct {
	sync.Mutex
	key string
	at  time.Time
	on  bool
}

func resetTiersCache() {
	tiersCache.Lock()
	tiersCache.key = ""
	tiersCache.Unlock()
}

func tiersOn(cfg Config, c *http.Client, base string, now time.Time) (on, known bool) {
	tiersCache.Lock()
	defer tiersCache.Unlock()
	key := base + "\x00" + cfg.DeviceToken
	if d := now.Sub(tiersCache.at); tiersCache.key == key && d >= 0 && d < tiersTTL {
		return tiersCache.on, true
	}
	req, _ := http.NewRequest(http.MethodGet, base+"/api/core/activity/settings", nil)
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	b, _, err := do(c, req)
	var s struct {
		Tiers bool `json:"tiers"`
	}
	if err != nil || json.Unmarshal(b, &s) != nil {
		return false, false
	}
	tiersCache.key, tiersCache.at, tiersCache.on = key, now, s.Tiers
	return s.Tiers, true
}

// recordsRound 跑一轮原始记录。失败只写日志、不返回错误（不挡段的上传）；游标只在 2xx 后前进。
func recordsRound(cfg Config, st *State, now time.Time, data awData, rules func() ([]rule, error), c *http.Client, base string) {
	want, known := recordsWanted(cfg, c, base, now)
	if !known {
		return
	}
	if !want {
		st.RecordsCursor = time.Time{} // 关着不攒积压；再打开从那一刻起记
		return
	}
	if back := minutes(cfg.MaxBacklogHours * 60); back > 0 && !st.RecordsCursor.IsZero() && now.Sub(st.RecordsCursor) > back {
		st.RecordsCursor = now.Add(-back)
	}
	if st.RecordsCursor.IsZero() {
		st.RecordsCursor = now
		return
	}
	cands := joinRecords(buildFragments(data, st.RecordsCursor, now, newRedactor(cfg)))
	next := now.Add(-recordGap)
	var recs []record
	for _, r := range cands {
		if r.End.Add(recordGap).After(now) {
			next = minTime(next, r.Start)
		} else if r.End.Sub(r.Start) >= time.Second {
			recs = append(recs, r)
		}
	}
	next = maxTime(next, st.RecordsCursor)
	if len(recs) == 0 {
		st.RecordsCursor = next
		return
	}
	rs, err := rules()
	if err != nil {
		rs = nil // 块名由服务端按它的第一级规则补（record.block 为 null）；不因此挡住记录
	}
	titles := make([]string, len(recs))
	for i, r := range recs {
		titles[i] = r.Title
	}
	sent, err := sendTitles(cfg, titles)
	if err != nil {
		log.Printf("原始记录这一轮不传：%v", err)
		return
	}
	for k := 0; k < len(recs); k += recordBatch {
		batch := recs[k:min(k+recordBatch, len(recs))]
		body := struct {
			DeviceID string         `json:"deviceId"`
			Records  []uploadRecord `json:"records"`
		}{DeviceID: cfg.DeviceID}
		arch := archiveRec{To: "cockpit", Kind: "record"}
		for i, r := range batch {
			u := uploadRecord{
				StartAt: isoMs(r.Start), EndAt: isoMs(r.End),
				App: scrubSecrets(r.App), Title: scrubSecrets(sent[k+i]), Block: blockFor(rs, r.App, r.Title),
				TitleIsPseudonym: cfg.Privacy.Titles == "pseudonymize" && r.Title != "",
			}
			body.Records = append(body.Records, u)
			arch.Records = append(arch.Records, archiveRaw{u.StartAt, u.EndAt, u.App, u.Title, u.Block})
		}
		b, _ := json.Marshal(body)
		resp, code, err := postJSON(c, cfg.DeviceToken, base+"/api/core/activity/records", b)
		arch.OK, arch.Result = err == nil, uploadSummary(resp, err)
		appendArchive(cfg.dir, time.Now(), arch)
		if code == http.StatusNotFound {
			log.Print("服务端没有原始记录端点（404），这次运行内不再传")
			recordsGone.Store(true)
			st.RecordsCursor = time.Time{}
			return
		}
		if err != nil {
			st.RecordsCursor = batch[0].Start // 已确认的批次之后；下一轮从这里重算重发（服务端防重）
			log.Printf("原始记录没传出去（已送达 %d/%d 条），下一轮重试：%v", k, len(recs), err)
			return
		}
		logRejectedRecords(resp)
		if k+recordBatch < len(recs) {
			st.RecordsCursor = recs[k+recordBatch].Start
		} else {
			st.RecordsCursor = next
		}
	}
}

func logRejectedRecords(resp []byte) {
	var r struct {
		Rejected []struct {
			Index  int    `json:"index"`
			Reason string `json:"reason"`
		} `json:"rejected"`
	}
	if json.Unmarshal(resp, &r) != nil || len(r.Rejected) == 0 {
		return
	}
	log.Printf("服务端拒收 %d 条原始记录（不会重发）；第一条：第 %d 条，%s", len(r.Rejected), r.Rejected[0].Index, strings.TrimSpace(r.Rejected[0].Reason))
}

func minTime(a, b time.Time) time.Time {
	if b.Before(a) {
		return b
	}
	return a
}

func maxTime(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}
