package main

// v0.6：信息源体检（ai-detector.source-health.v1）。看 ActivityWatch 里窗口信息的来源齐不齐，
// 把问题编码（枚举，没有自由文本）随健康报告的 sourceIssues 报上去；怎么修由网页按编码写。

import (
	"net/url"
	"os"
	"strings"
	"time"
)

const (
	sourceLookback = 30 * time.Minute
	sourceEvery    = 5 * time.Minute // 体检比健康报告慢：不必每 15 秒多拉一遍 ActivityWatch 事件
)

// 编码只增。
const (
	issueAwDown         = "aw_down"
	issueWindowMissing  = "window_missing"
	issueWindowWayland  = "window_wayland"
	issueAfkMissing     = "afk_missing"
	issueWebExtension   = "web_extension_missing"
	issueTitlesHidden   = "titles_pseudonymized"
	issueTerminalNoPath = "terminal_title_no_path"
)

// sourceIssues：返回问题编码；空切片 = 全好。ActivityWatch 连不上时只报 aw_down（其余都看不见）。
func sourceIssues(cfg Config, c awClient, now time.Time, goos string, getenv func(string) string) []string {
	out := []string{}
	var buckets map[string]awBucket
	if err := c.get("/buckets/", &buckets); err != nil {
		return []string{issueAwDown}
	}
	host, _ := os.Hostname()
	ids := func(typ string) []string {
		var r []string
		for id, b := range buckets {
			if b.Type == typ && b.Hostname == host {
				r = append(r, id)
			}
		}
		return r
	}
	q := "?start=" + url.QueryEscape(now.Add(-sourceLookback).UTC().Format(time.RFC3339Nano)) +
		"&end=" + url.QueryEscape(now.UTC().Format(time.RFC3339Nano)) + "&limit=200"
	events := func(list []string) []awEvent {
		var all []awEvent
		for _, id := range list {
			var evs []awEvent
			if c.get("/buckets/"+url.PathEscape(id)+"/events"+q, &evs) == nil {
				all = append(all, evs...)
			}
		}
		return all
	}
	win := events(ids("currentwindow"))
	if len(win) == 0 {
		if goos == "linux" && getenv("XDG_SESSION_TYPE") == "wayland" {
			out = append(out, issueWindowWayland)
		} else {
			out = append(out, issueWindowMissing)
		}
	}
	if len(ids("afkstatus")) == 0 {
		out = append(out, issueAfkMissing)
	}
	r := newRedactor(cfg)
	browser, term, termPath := false, false, false
	for _, e := range win {
		a := normApp(e.str("app"))
		browser = browser || r.browsers[a]
		if r.terminals[a] {
			term = true
			termPath = termPath || strings.ContainsAny(e.str("title"), `/~\`)
		}
	}
	if browser && len(events(ids("web.tab.current"))) == 0 {
		out = append(out, issueWebExtension)
	}
	if cfg.Privacy.Titles == "pseudonymize" {
		out = append(out, issueTitlesHidden)
	}
	if term && !termPath {
		out = append(out, issueTerminalNoPath)
	}
	return out
}
