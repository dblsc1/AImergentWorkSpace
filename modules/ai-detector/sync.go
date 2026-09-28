package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
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

	if cfg.ClassifierURL != "" {
		if err := checkClassifierURL(cfg.ClassifierURL); err != nil {
			return "", err
		}
	}
	params := fmt.Sprintf("G=%v分钟,M=%v分钟", cfg.MergeGapMinutes, cfg.MinSegmentMinutes)
	if st.FailedParams != "" && st.FailedParams != params {
		log.Printf("上一轮上传失败时的合并参数是 %s，现在是 %s：从 %s 起重算的段起点可能和失败那轮不同，"+
			"如果那轮其实已经到达服务端，待确认列表里可能出现重叠的建议，确认时留意", st.FailedParams, params, isoTime(st.Cursor))
	}

	gap, minSeg := minutes(cfg.MergeGapMinutes), minutes(cfg.MinSegmentMinutes)
	aw := awClient{base: cfg.ActivityWatchURL, http: hc}
	data, err := aw.fetch(st.Cursor, now, cfg.WindowBucket, cfg.AfkBucket)
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
	cockpit := withPolicy(hc, hc.Timeout, sameHostOnly)
	uploader := withPolicy(hc, uploadTimeout, sameHostOnly)
	service := withPolicy(hc, hc.Timeout, noRedirect)
	tasks := sync.OnceValues(func() ([]taskRef, error) {
		req, _ := http.NewRequest(http.MethodGet, base+"/api/core/views/tree", nil)
		req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
		b, _, err := do(cockpit, req)
		if err != nil {
			return nil, err
		}
		return tasksFromTree(b)
	})
	// 分类服务一轮里挂过一次就不再叫它：每批都等一次超时，积压多时要白等 N×超时才轮到上传。
	// 规则照用，没命中的段按「分类服务不可用」上传。
	var serviceErr error
	post := func(u string, body []byte) ([]byte, error) {
		if serviceErr != nil {
			return nil, serviceErr
		}
		// 分类服务是用户填的任意地址：只带它自己的 classifierToken，HoneyComb 的设备令牌不给它。
		b, _, err := postJSON(service, cfg.ClassifierToken, u, body)
		serviceErr = err
		return b, err
	}

	// 分批上传：积压很久时一次性发几千段，请求大、慢，超时后下一轮又是同样大，永远发不出去。
	// 每批成功后游标挪到下一批第一段的开始——从段的开始处重算，得到的段和这次一模一样
	// （同 settle 对没收口段的处理），startAt 不变，防重照样成立。
	for k := 0; k < len(segs); k += uploadBatch {
		batch := segs[k:min(k+uploadBatch, len(segs))]
		sugs := classify(batch, rules, cfg.ClassifierURL, tasks, post)
		body := uploadBody{DeviceID: cfg.DeviceID}
		for i, s := range batch {
			body.Segments = append(body.Segments, uploadSegment{
				StartAt: isoTime(s.Start), EndAt: isoTime(s.End),
				DurationSeconds: int64(s.Active.Seconds()),
				App:             s.App, Title: s.Title, Suggestion: sugs[i],
			})
		}
		b, _ := json.Marshal(body)
		resp, code, err := postJSON(uploader, cfg.DeviceToken, base+"/api/core/activity/suggestions", b)
		if err != nil {
			// 这一批没确认收到：游标停在这一批的开始（已确认的批次之后），下一轮从这里重算重发。
			st.FailedParams = params
			if code == http.StatusUnauthorized || code == http.StatusForbidden {
				return "", fmt.Errorf("上传被拒（%d）：设备令牌无效或已吊销，请重新生成", code)
			}
			return "", fmt.Errorf("上传失败（已送达 %d/%d 段），下一轮重试：%w", k, len(segs), err)
		}
		st.FailedParams = ""
		logRejected(resp, k)
		if k+uploadBatch < len(segs) {
			st.Cursor = segs[k+uploadBatch].Start
		}
	}
	st.Cursor = next
	return fmt.Sprintf("已上传 %d 段待确认建议", len(segs)), nil
}

// logRejected：2xx 里服务端逐段拒收的段不会重发（游标照常前进），至少在日志里留个数。
// 只记条数和第一条理由（理由是服务端写的校验说明）；不记标题——标题可能含隐私。
func logRejected(resp []byte, offset int) {
	var r struct {
		Rejected []struct {
			Index  int    `json:"index"`
			Reason string `json:"reason"`
		} `json:"rejected"`
	}
	if json.Unmarshal(resp, &r) != nil || len(r.Rejected) == 0 {
		return
	}
	first := r.Rejected[0]
	log.Printf("服务端拒收 %d 段（不会重发）；第一条：第 %d 段，%s", len(r.Rejected), offset+first.Index, first.Reason)
}

const (
	uploadBatch   = 200
	uploadTimeout = 60 * time.Second
)

// withPolicy 复制一份 client（共用底层 Transport，测试替换 Transport 照样生效），换超时与重定向策略。
func withPolicy(base *http.Client, timeout time.Duration, redirect func(*http.Request, []*http.Request) error) *http.Client {
	c := *base
	c.Timeout = timeout
	c.CheckRedirect = redirect
	return &c
}

// sameHostOnly：cockpit 的请求带设备令牌，只许在同一主机内跟随重定向（比如整站子路径补斜杠），
// 不许跨主机、不许 https 降成 http；也不许把 POST 改成 GET（301/302/303 会这样做，
// 请求体就丢了，而 GET 返回的 2xx 会被当成「上传成功」推进游标）。
func sameHostOnly(req *http.Request, via []*http.Request) error {
	first := via[0]
	switch {
	case len(via) >= 5:
		return errors.New("重定向次数太多")
	case req.URL.Host != first.URL.Host:
		return fmt.Errorf("拒绝跨主机重定向（%s → %s）", first.URL.Host, req.URL.Host)
	case first.URL.Scheme == "https" && req.URL.Scheme != "https":
		return errors.New("拒绝从 https 重定向到 http")
	case req.Method != first.Method:
		return fmt.Errorf("拒绝把 %s 重定向成 %s", first.Method, req.Method)
	}
	return nil
}

func noRedirect(*http.Request, []*http.Request) error {
	return errors.New("分类服务返回重定向，按契约不跟随")
}

// checkClassifierURL：段的内容（哪怕脱敏过）不能明文走网络，只有本机回环地址可以用 http。
func checkClassifierURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return fmt.Errorf("classifierUrl 不是合法地址")
	}
	h := u.Hostname()
	ip := net.ParseIP(h)
	if u.Scheme == "https" || (u.Scheme == "http" && (strings.EqualFold(h, "localhost") || (ip != nil && ip.IsLoopback()))) {
		return nil
	}
	return fmt.Errorf("classifierUrl 必须是 https（本机 localhost / 127.0.0.1 / ::1 除外），不上传；改好配置后自动补上")
}
