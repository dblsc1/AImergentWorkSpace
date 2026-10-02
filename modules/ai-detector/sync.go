package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
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
	Idle            bool       `json:"idle,omitempty"` // upload.v1 v1.1
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
	base := strings.TrimRight(cfg.CockpitURL, "/")
	cockpit := withPolicy(hc, hc.Timeout, sameHostOnly)
	// 网页上设过隐私 / 离开选项就用网页的（整节替换）；拉不到（不是 404）这一轮不上传。
	if err := applyRemoteSettings(&cfg, cockpit, base); err != nil {
		return "", err
	}
	if err := cfg.Privacy.check(); err != nil {
		return "", err
	}
	if !st.SentReady {
		// 升级后的第一轮按老切法：见 State.SentReady。
		off := false
		cfg.SegmentByTitle = &off
	}
	// 隐私 / 离开选项也决定段怎么切（合并键、离开扣不扣），和 G / M 一样要记进失败参数。
	sb, _ := json.Marshal([]any{cfg.Privacy, cfg.Idle, on(cfg.SegmentByTitle), tabApps(cfg)})
	params := fmt.Sprintf("G=%v分钟,M=%v分钟,设置#%08x", cfg.MergeGapMinutes, cfg.MinSegmentMinutes, crc32.ChecksumIEEE(sb))
	if st.FailedParams != "" && st.FailedParams != params {
		log.Printf("上一轮上传失败时的合并参数是 %s，现在是 %s：从 %s 起重算的段起点可能和失败那轮不同，"+
			"如果那轮其实已经到达服务端，待确认列表里可能出现重叠的建议，确认时留意", st.FailedParams, params, isoTime(st.Cursor))
	}

	gap, minSeg := minutes(cfg.MergeGapMinutes), minutes(cfg.MinSegmentMinutes)
	aw := awClient{base: cfg.ActivityWatchURL, http: hc}
	// 离开区间要看它**原本**多长、从哪开始（阈值、阅读程序上限都按整段算），而 ActivityWatch 会把事件
	// 裁到查询区间。往前多取一段：比这更早开始的离开，裁剪后也不短于阈值、阅读上限也早已用完，结论不变。
	// 窗口事件照样只从游标算起（buildFragments 裁到 [游标, 现在]）。
	lookback := time.Duration(0)
	if cfg.Idle.AfkThresholdMinutes > 0 {
		lookback = minutes(cfg.Idle.AfkThresholdMinutes)
	}
	if cfg.Idle.FocusAppsEnabled {
		lookback = max(lookback, minutes(focusMax(cfg.Idle)))
	}
	data, err := aw.fetch(st.Cursor.Add(-lookback), now, cfg.WindowBucket, cfg.AfkBucket)
	if err != nil {
		return "", err
	}
	// 游标之后已经送达的活动先挖掉（几条流交叠、游标停在没收口的段的开始处时才会有，见 State.Sent）。
	frags := unsent(buildFragments(data, st.Cursor, now, newRedactor(cfg)), st.Sent)
	// 普通、无操作、各标签页的流各自合并，段的墙钟跨度彼此可能交叠。
	segs, next := settle(segments(frags, gap), now, gap, minSeg)
	// 游标只进不退：退回去会重新读到「开启之前」的活动。
	if next.Before(st.Cursor) {
		next = st.Cursor
	}
	if len(segs) == 0 {
		markSent(st, nil, next)
		st.SentReady = true
		return fmt.Sprintf("没有收口的段（%d 个碎片）", len(frags)), nil
	}

	// 网页上存过规则就用网页的（detector.rules.v1），否则 / 拉不到用本机 rules.json。
	rules, err := rulesForRound(cfg, cockpit, base)
	if err != nil {
		// 本机规则写坏了就不上传：带着「全部认不出」上传不丢数据，但用户会以为规则失效了还没察觉。
		// 停在这里，日志里天天报，游标不动，改好规则后一次补上。
		return "", err
	}
	// 离开本机的标题：换代号（合并之后换，段的切法不受影响）+ 再过一遍强制脱敏。对照表先存盘再上传。
	titles := make([]string, len(segs))
	for i := range segs {
		titles[i] = segs[i].Title
	}
	sent, err := sendTitles(cfg, titles)
	if err != nil {
		return "", err
	}
	for i := range segs {
		segs[i].Sent = sent[i]
	}
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
	var batch []segment
	post := func(u string, body []byte) ([]byte, error) {
		if serviceErr != nil {
			return nil, serviceErr
		}
		// 分类服务是用户填的任意地址：只带它自己的 classifierToken，HoneyComb 的设备令牌不给它。
		b, _, err := postJSON(service, cfg.ClassifierToken, u, body)
		serviceErr = err
		archiveClassifier(cfg.dir, time.Now(), batch, body, b, err)
		return b, err
	}

	// 分批上传：积压很久时一次性发几千段，请求大、慢，超时后下一轮又是同样大，永远发不出去。
	// 每批成功后游标挪到下一批第一段的开始——从段的开始处重算，得到的段和这次一模一样
	// （同 settle 对没收口段的处理），startAt 不变，防重照样成立；已送达的批次记在 st.Sent 里，不会再算一遍。
	for k := 0; k < len(segs); k += uploadBatch {
		batch = segs[k:min(k+uploadBatch, len(segs))]
		sugs := classify(batch, rules, cfg.ClassifierURL, tasks, post)
		body := uploadBody{DeviceID: cfg.DeviceID}
		rec := archiveRec{To: "cockpit"}
		for i, s := range batch {
			if s.Idle {
				// 无操作段只是「可能」：把握夹到 0.3，理由里写明，由人决定（契约「离开判定」）。
				sugs[i].Confidence = min(sugs[i].Confidence, 0.3)
				sugs[i].Reason = "无操作，可能在阅读；" + sugs[i].Reason
			}
			// 理由可能来自外部分类服务：同样过强制脱敏再上传、再留档。
			sugs[i].Reason = cleanReason(scrubSecrets(sugs[i].Reason))
			u := uploadSegment{
				StartAt: isoTime(s.Start), EndAt: isoTime(s.End),
				DurationSeconds: int64(s.Active.Seconds()),
				// 最后一道：离开本机的每个字符串再过一遍强制脱敏（幂等）。
				App: scrubSecrets(s.App), Title: scrubSecrets(s.Sent), Suggestion: sugs[i], Idle: s.Idle,
			}
			body.Segments = append(body.Segments, u)
			sg := sugs[i]
			rec.Segments = append(rec.Segments, archiveSeg{u.StartAt, u.EndAt, u.DurationSeconds, u.App, s.Raw, u.Title, s.Idle, &sg})
		}
		b, _ := json.Marshal(body)
		resp, code, err := postJSON(uploader, cfg.DeviceToken, base+"/api/core/activity/suggestions", b)
		rec.OK, rec.Result = err == nil, uploadSummary(resp, err)
		appendArchive(cfg.dir, time.Now(), rec)
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
		// 这一批送达了：记下来，游标挪到下一批第一段的开始（全部送完则到 next）。
		// 下一批的第一段可能比某个没收口的段开始得晚（几条流交叠）：游标不能越过 next。
		c := next
		if k+uploadBatch < len(segs) && segs[k+uploadBatch].Start.Before(c) {
			c = segs[k+uploadBatch].Start
		}
		markSent(st, batch, c)
	}
	st.SentReady = true
	return fmt.Sprintf("已上传 %d 段待确认建议", len(segs)), nil
}

func uploadSummary(resp []byte, err error) string {
	if err != nil {
		return err.Error()
	}
	var r struct {
		Accepted   int               `json:"accepted"`
		Duplicates int               `json:"duplicates"`
		Rejected   []json.RawMessage `json:"rejected"`
	}
	_ = json.Unmarshal(resp, &r)
	return fmt.Sprintf("accepted=%d duplicates=%d rejected=%d", r.Accepted, r.Duplicates, len(r.Rejected))
}

// archiveClassifier 把发给分类服务的请求按段记进留档：body 就是真正发出去的字节，
// 从里面取标题（而不是另算一遍），留档与实际发送不会对不上。
func archiveClassifier(dir string, now time.Time, batch []segment, body, resp []byte, err error) {
	var req struct {
		Segments []struct {
			ID, App, Title, StartAt string
			DurationSeconds         int64
		} `json:"segments"`
		Tasks []taskRef `json:"tasks"`
	}
	_ = json.Unmarshal(body, &req)
	rec := archiveRec{To: "classifier", OK: err == nil, Tasks: len(req.Tasks)}
	if err != nil {
		rec.Result = err.Error()
	} else {
		var r struct {
			Suggestions []json.RawMessage `json:"suggestions"`
		}
		_ = json.Unmarshal(resp, &r)
		rec.Result = fmt.Sprintf("收到 %d 条建议", len(r.Suggestions))
	}
	for _, s := range req.Segments {
		var i int
		raw := ""
		if _, e := fmt.Sscanf(s.ID, "seg_%d", &i); e == nil && i >= 0 && i < len(batch) {
			raw = batch[i].Raw
		}
		rec.Segments = append(rec.Segments, archiveSeg{StartAt: s.StartAt, DurationSeconds: s.DurationSeconds, App: s.App, Raw: raw, Sent: s.Title})
	}
	appendArchive(dir, now, rec)
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
