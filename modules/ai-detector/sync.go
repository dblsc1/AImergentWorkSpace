package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

// isoTime：本机时区偏移、精确到秒。显式写 -07:00 而不用 RFC3339，是为了 UTC 机器上
// 也输出 +00:00 而不是 Z——两种都合法，但统一一种写法，人看日志 / 抓包时不迷惑。
func isoTime(t time.Time) string {
	return t.Local().Truncate(time.Second).Format("2006-01-02T15:04:05-07:00")
}

func do(c *http.Client, req *http.Request) ([]byte, int, error) {
	resp, err := c.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode/100 != 2 {
		return b, resp.StatusCode, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return b, resp.StatusCode, nil
}

type uploadSegment struct {
	StartAt         string     `json:"startAt"`
	EndAt           string     `json:"endAt"`
	DurationSeconds int64      `json:"durationSeconds"`
	App             string     `json:"app"`
	Title           string     `json:"title"`
	Suggestion      suggestion `json:"suggestion"`
}

type uploadBody struct {
	DeviceID string          `json:"deviceId"`
	Segments []uploadSegment `json:"segments"`
}

// tick 跑一轮，改写 st（调用方负责存盘）。返回一句给人看的结果。
// now 由调用方给：测试可以固定时间，run 循环用 time.Now()。
func tick(cfg Config, st *State, now time.Time, hc *http.Client) (string, error) {
	st.LastRunAt = now
	active := cfg.Enabled && !cfg.Paused
	wasActive := st.Active
	st.Active = active
	if !active {
		// 这里之前不许有任何网络请求（连本机 ActivityWatch 都不读）——测试锁住。
		if !cfg.Enabled {
			return "同步未开启（enabled=false），不读取、不上传", nil
		}
		return "已暂停（paused=true），不读取、不上传", nil
	}
	if cfg.CockpitURL == "" || cfg.DeviceToken == "" || cfg.DeviceID == "" {
		// 配不全等于还没真正开起来：记成不活跃，配好之后从那一刻开始记，不补传之前的。
		st.Active = false
		return "", fmt.Errorf("配置不全：cockpitUrl / deviceToken / deviceId 都要有，不上传")
	}
	if !wasActive || st.Cursor.IsZero() {
		st.Cursor = now
		return "刚开启（或刚恢复），从现在开始记录；之前的活动不上传", nil
	}
	if back := minutes(cfg.MaxBacklogHours * 60); back > 0 && now.Sub(st.Cursor) > back {
		log.Printf("积压超过 %.0f 小时，丢弃 %s 到 %s 之间的活动", cfg.MaxBacklogHours, isoTime(st.Cursor), isoTime(now.Add(-back)))
		st.Cursor = now.Add(-back)
	}

	gap, minSeg := minutes(cfg.MergeGapMinutes), minutes(cfg.MinSegmentMinutes)
	aw := awClient{base: cfg.ActivityWatchURL, http: hc}
	data, err := aw.fetch(st.Cursor, now)
	if err != nil {
		return "", err
	}
	frags := buildFragments(data, st.Cursor, now, newRedactor(cfg))
	segs, next := settle(merge(frags, gap), now, gap, minSeg)
	// 游标只进不退：退回去会重新读到「开启之前」的活动。
	if next.Before(st.Cursor) {
		next = st.Cursor
	}
	if len(segs) == 0 {
		st.Cursor = next
		return fmt.Sprintf("没有收口的段（%d 个碎片）", len(frags)), nil
	}

	rules, err := loadRules(cfg.RulesFile)
	if err != nil {
		// 规则写坏了就不上传：带着「全部认不出」上传不丢数据，但用户会以为规则失效了还没察觉。
		// 停在这里，日志里天天报，游标不动，改好规则后一次补上。
		return "", err
	}
	base := strings.TrimRight(cfg.CockpitURL, "/")
	tasks := func() ([]taskRef, error) {
		req, _ := http.NewRequest(http.MethodGet, base+"/api/core/views/tree", nil)
		req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
		b, _, err := do(hc, req)
		if err != nil {
			return nil, err
		}
		return tasksFromTree(b)
	}
	post := func(u string, body []byte) ([]byte, error) {
		b, _, err := postJSON(hc, cfg.DeviceToken, u, body)
		return b, err
	}
	sugs := classify(segs, rules, cfg.ClassifierURL, tasks, post)

	body := uploadBody{DeviceID: cfg.DeviceID}
	for i, s := range segs {
		body.Segments = append(body.Segments, uploadSegment{
			StartAt: isoTime(s.Start), EndAt: isoTime(s.End),
			DurationSeconds: int64(s.Active.Seconds()),
			App:             s.App, Title: s.Title, Suggestion: sugs[i],
		})
	}
	b, _ := json.Marshal(body)
	_, code, err := postJSON(hc, cfg.DeviceToken, base+"/api/core/activity/suggestions", b)
	if err != nil {
		// 游标不动：下一轮从同一处重算，得到同样的 startAt，服务端防重，重发安全。
		if code == http.StatusUnauthorized || code == http.StatusForbidden {
			return "", fmt.Errorf("上传被拒（%d）：设备令牌无效或已吊销，请重新生成", code)
		}
		return "", fmt.Errorf("上传失败，下一轮重试：%w", err)
	}
	st.Cursor = next
	return fmt.Sprintf("已上传 %d 段待确认建议", len(segs)), nil
}
