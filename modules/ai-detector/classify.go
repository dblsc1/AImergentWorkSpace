package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

// 分类：先规则（本机、确定、可解释），规则没认出来的才交给可选的外部分类服务
// （contracts/activity.classifier.v1）。无论哪条路，产出都只是「建议」。

type suggestion struct {
	TaskID     *string `json:"taskId"`
	Confidence float64 `json:"confidence"`
	Reason     string  `json:"reason"`
	Classifier string  `json:"classifier"` // "rules" | "service"
}

type rule struct {
	App        string  `json:"app"`
	Title      string  `json:"title"`
	TaskID     string  `json:"taskId"`
	Confidence float64 `json:"confidence"`
	app, title *regexp.Regexp
	reason     string // 空 = 「规则 #N 命中」；网页规则填「网页规则 #N 命中」（N 是服务端数组里的位置）
}

// loadRules：文件不存在不算错（没写规则 = 全部认不出），写错了算错——
// 静默吞掉会让用户以为规则在生效。
func loadRules(path string) ([]rule, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) || path == "" {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var f struct {
		Rules []rule `json:"rules"`
	}
	if err := json.Unmarshal(b, &f); err != nil {
		return nil, fmt.Errorf("规则文件 %s 不是合法 JSON：%w", path, err)
	}
	for i := range f.Rules {
		r := &f.Rules[i]
		if r.TaskID == "" || (r.App == "" && r.Title == "") {
			return nil, fmt.Errorf("规则 #%d：要有 taskId，app / title 至少写一个", i+1)
		}
		if r.app, err = compileCI(r.App); err != nil {
			return nil, fmt.Errorf("规则 #%d 的 app 正则：%w", i+1, err)
		}
		if r.title, err = compileCI(r.Title); err != nil {
			return nil, fmt.Errorf("规则 #%d 的 title 正则：%w", i+1, err)
		}
		if r.Confidence <= 0 || r.Confidence > 1 {
			r.Confidence = 0.9
		}
	}
	return f.Rules, nil
}

func compileCI(p string) (*regexp.Regexp, error) {
	if p == "" {
		return nil, nil
	}
	return regexp.Compile("(?i)" + p)
}

func matchRules(rules []rule, s segment) (suggestion, bool) {
	for i, r := range rules {
		if (r.app == nil || r.app.MatchString(s.App)) && (r.title == nil || r.title.MatchString(s.Title)) {
			id, reason := r.TaskID, r.reason
			if reason == "" {
				reason = fmt.Sprintf("规则 #%d 命中", i+1)
			}
			return suggestion{TaskID: &id, Confidence: r.Confidence, Reason: reason, Classifier: "rules"}, true
		}
	}
	return suggestion{}, false
}

type taskRef struct {
	ID   string `json:"id"`
	Path string `json:"path"`
}

// tasksFromTree 把 views/tree 拼成「分区 / 项目 / 任务」路径。只拿没完成的任务：
// 已完成的任务不该再被建议挂时间。
func tasksFromTree(body []byte) ([]taskRef, error) {
	var t struct {
		Zones []struct {
			ID, Name string
		} `json:"zones"`
		Projects []struct {
			ZoneID string `json:"zoneId"`
			Name   string `json:"name"`
			Tasks  []struct {
				ID   string `json:"id"`
				Name string `json:"name"`
				Done bool   `json:"done"`
			} `json:"tasks"`
		} `json:"projects"`
	}
	if err := json.Unmarshal(body, &t); err != nil {
		return nil, err
	}
	zone := map[string]string{}
	for _, z := range t.Zones {
		zone[z.ID] = z.Name
	}
	var out []taskRef
	for _, p := range t.Projects {
		for _, k := range p.Tasks {
			if !k.Done {
				out = append(out, taskRef{k.ID, zone[p.ZoneID] + " / " + p.Name + " / " + k.Name})
			}
		}
	}
	return out, nil
}

func none(reason, by string) suggestion {
	return suggestion{TaskID: nil, Confidence: 0, Reason: reason, Classifier: by}
}

// classify 给每段一条建议（下标对齐）。外部服务任何失败都退化成「无建议」，从不返回错误——
// 分类是锦上添花，不能卡住上传。
func classify(segs []segment, rules []rule, serviceURL string, tasks func() ([]taskRef, error), post func(url string, body []byte) ([]byte, error)) []suggestion {
	out := make([]suggestion, len(segs))
	var pending []int
	for i, s := range segs {
		if sg, ok := matchRules(rules, s); ok {
			out[i] = sg
			continue
		}
		out[i] = none("没有规则命中", "rules")
		pending = append(pending, i)
	}
	if serviceURL == "" || len(pending) == 0 {
		return out
	}
	// reason 会随段一起上传，所以只放固定文案；错误细节（可能含完整地址、查询串）
	// 只进本机日志。
	fail := func(reason string, detail error) []suggestion {
		log.Printf("%s：%v", reason, detail)
		for _, i := range pending {
			out[i] = none(reason, "service")
		}
		return out
	}
	ts, err := tasks()
	if err != nil {
		return fail("分类服务未调用：取不到任务树", err)
	}
	type reqSeg struct {
		ID              string `json:"id"`
		App             string `json:"app"`
		Title           string `json:"title"`
		StartAt         string `json:"startAt"`
		DurationSeconds int64  `json:"durationSeconds"`
	}
	req := struct {
		Segments []reqSeg  `json:"segments"`
		Tasks    []taskRef `json:"tasks"`
	}{Tasks: ts}
	req.Tasks = []taskRef{}
	// 发给分类服务的与上传的完全相同（Sent，换过代号的就是代号），并且每个字符串再过一遍强制脱敏——
	// 候选任务路径来自 cockpit，也可能被人写进了密钥。
	for _, t := range ts {
		req.Tasks = append(req.Tasks, taskRef{t.ID, scrubSecrets(t.Path)})
	}
	for _, i := range pending {
		s := segs[i]
		req.Segments = append(req.Segments, reqSeg{fmt.Sprintf("seg_%d", i), scrubSecrets(s.App), scrubSecrets(s.Sent), isoTime(s.Start), int64(s.Active.Seconds())})
	}
	body, _ := json.Marshal(req)
	resp, err := post(serviceURL, body)
	if err != nil {
		return fail("分类服务不可用", err)
	}
	var r struct {
		Suggestions []struct {
			SegmentID  string  `json:"segmentId"`
			TaskID     *string `json:"taskId"`
			Confidence float64 `json:"confidence"`
			Reason     string  `json:"reason"`
		} `json:"suggestions"`
	}
	if err := json.Unmarshal(resp, &r); err != nil {
		return fail("分类服务响应格式不对", err)
	}
	known := map[string]bool{}
	for _, t := range ts {
		known[t.ID] = true
	}
	for _, i := range pending {
		out[i] = none("分类服务认不出", "service")
	}
	// 服务是外部的，不信任它：不认识的段 id 丢掉，不在候选里的 taskId 当 null，把握夹到 [0,1]。
	for _, s := range r.Suggestions {
		var i int
		if _, err := fmt.Sscanf(s.SegmentID, "seg_%d", &i); err != nil || i < 0 || i >= len(segs) || out[i].Classifier != "service" {
			continue
		}
		sg := suggestion{Confidence: min(max(s.Confidence, 0), 1), Reason: cleanReason(s.Reason), Classifier: "service"}
		if s.TaskID != nil && known[*s.TaskID] {
			id := *s.TaskID
			sg.TaskID = &id
		} else {
			sg.Confidence = 0
		}
		out[i] = sg
	}
	return out
}

// cleanReason：服务给的理由会上传并显示在界面上。去掉控制字符，截到 200 字节以内
// （按字符边界截，不切坏 UTF-8）。
func cleanReason(s string) string {
	s = strings.Map(func(r rune) rune {
		if unicode.IsControl(r) || r == utf8.RuneError {
			return -1
		}
		return r
	}, s)
	if len(s) <= 200 {
		return s
	}
	cut := 200
	for cut > 0 && !utf8.RuneStart(s[cut]) {
		cut--
	}
	return s[:cut]
}

// postJSON 发一个 JSON POST，非 2xx 算错。token 为空就不带 Authorization 头。
func postJSON(c *http.Client, token, url string, body []byte) ([]byte, int, error) {
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	return do(c, req)
}
