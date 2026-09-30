package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
)

// 分类规则从 nexus-core 拉（contracts/detector.rules.v1「四」）：服务端存过（version > 0，哪怕是空集）就只用它；
// 从没存过、老 nexus-core（404）、拉不到，都用本机 rules.json。规则只决定建议挂哪个任务、不决定什么离开本机，
// 所以拉不到不必停上传（与隐私设置不同）。

var lastRulesSource string // 规则来源变了才写日志（run 常驻时不每轮刷屏）

func rulesForRound(cfg Config, c *http.Client, base string) ([]rule, error) {
	rules, src, ok, err := remoteRules(cfg, c, base)
	if err != nil {
		log.Printf("拉不到网页上的分类规则（%v），这一轮用本机 rules.json", err)
	}
	if !ok {
		if rules, err = loadRules(cfg.RulesFile); err != nil {
			return nil, err
		}
		src = "本机 rules.json"
	}
	if src != lastRulesSource {
		log.Printf("分类规则：%s", src)
		lastRulesSource = src
	}
	return rules, nil
}

// remoteRules：ok=true 表示以服务端为准（rules 可以为空）；ok=false 用本机文件。
func remoteRules(cfg Config, c *http.Client, base string) (rules []rule, src string, ok bool, err error) {
	req, _ := http.NewRequest(http.MethodGet, base+"/api/core/detector/rules", nil)
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	b, code, err := do(c, req)
	if code == http.StatusNotFound {
		return nil, "", false, nil
	}
	if err != nil {
		return nil, "", false, err
	}
	var r struct {
		Version int `json:"version"`
		Rules   []struct {
			App, Title *string
			TaskID     string  `json:"taskId"`
			Confidence float64 `json:"confidence"`
			Enabled    *bool   `json:"enabled"`
		} `json:"rules"`
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return nil, "", false, fmt.Errorf("响应不是合法 JSON：%w", err)
	}
	if r.Version <= 0 {
		return nil, "", false, nil
	}
	rules = []rule{}
	for i, x := range r.Rules {
		if !on(x.Enabled) {
			continue
		}
		ru := rule{TaskID: x.TaskID, Confidence: x.Confidence, reason: fmt.Sprintf("网页规则 #%d 命中", i+1)}
		if x.App != nil {
			ru.App = *x.App
		}
		if x.Title != nil {
			ru.Title = *x.Title
		}
		var e1, e2 error
		ru.app, e1 = compileCI(ru.App)
		ru.title, e2 = compileCI(ru.Title)
		if e1 != nil || e2 != nil || ru.TaskID == "" || (ru.App == "" && ru.Title == "") {
			// 服务端按 Python 校验过，个别写法 Go 仍可能不认：少一条规则 = 少一个建议，跳过并记日志
			log.Printf("网页规则 #%d 本程序用不了，跳过（app=%v title=%v）", i+1, e1, e2)
			continue
		}
		if ru.Confidence <= 0 || ru.Confidence > 1 {
			ru.Confidence = 0.9
		}
		rules = append(rules, ru)
	}
	return rules, fmt.Sprintf("网页 v%d（%d 条生效）", r.Version, len(rules)), true, nil
}
