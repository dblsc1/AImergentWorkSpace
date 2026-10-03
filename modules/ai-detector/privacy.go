package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ── 标题代号（privacy.titles = "pseudonymize"）──────────────────────────────
// 对照表 pseudonyms.json：{原标题: 代号}，只在本机。契约「标题代号」。

type pseudonyms struct {
	path  string // 空 = 只在内存里（测试直接构造 Config 时）
	m     map[string]string
	next  map[string]int
	dirty bool
}

func loadPseudonyms(path string) (*pseudonyms, error) {
	ps := &pseudonyms{path: path, m: map[string]string{}, next: map[string]int{}}
	if path != "" {
		// 读不了（坏了）就报错不上传：从 1 重新编号会让旧代号指向新标题。
		if err := loadJSON(path, &ps.m); err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("标题代号对照表 %s 读不了（删掉它会从 1 重新编号）：%w", path, err)
		}
	}
	for _, tok := range ps.m {
		kind, n := splitToken(tok)
		ps.next[kind] = max(ps.next[kind], n)
	}
	return ps, nil
}

func splitToken(tok string) (string, int) {
	i := strings.IndexFunc(tok, func(r rune) bool { return r >= '0' && r <= '9' })
	if i < 0 {
		return tok, 0
	}
	n, _ := strconv.Atoi(tok[i:])
	return tok[:i], n
}

func (ps *pseudonyms) token(title string) string {
	if title == "" {
		return ""
	}
	if t, ok := ps.m[title]; ok {
		return t
	}
	kind := "窗口名"
	if containsPath(title) {
		kind = "路径"
	}
	ps.next[kind]++
	t := kind + strconv.Itoa(ps.next[kind])
	ps.m[title] = t
	ps.dirty = true
	return t
}

func (ps *pseudonyms) save() error {
	if ps.path == "" || !ps.dirty {
		return nil
	}
	ps.dirty = false
	return saveJSON(ps.path, ps.m)
}

func printPseudonyms(path string, out io.Writer) error {
	ps, err := loadPseudonyms(path)
	if err != nil {
		return err
	}
	type row struct {
		kind string
		n    int
		tok  string
		orig string
	}
	var rows []row
	for orig, tok := range ps.m {
		k, n := splitToken(tok)
		rows = append(rows, row{k, n, tok, orig})
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].kind != rows[j].kind {
			return rows[i].kind == "窗口名" // 窗口名在前
		}
		return rows[i].n < rows[j].n
	})
	if len(rows) == 0 {
		fmt.Fprintf(out, "还没有代号（%s）。privacy.titles 设成 pseudonymize 后才会分配。\n", path)
	}
	for _, r := range rows {
		fmt.Fprintf(out, "%s  %s\n", r.tok, r.orig)
	}
	return nil
}

// ── 本机留档（ai-detector.archive.v1）──────────────────────────────────────

type archiveSeg struct {
	StartAt         string      `json:"startAt"`
	EndAt           string      `json:"endAt"`
	DurationSeconds int64       `json:"durationSeconds"`
	App             string      `json:"app"`
	Raw             string      `json:"raw"`
	Sent            string      `json:"sent"`
	Idle            bool        `json:"idle"`
	Suggestion      *suggestion `json:"suggestion,omitempty"`
}

type archiveRec struct {
	At       string       `json:"at"`
	To       string       `json:"to"` // "cockpit" | "classifier"
	OK       bool         `json:"ok"`
	Result   string       `json:"result"`
	Segments []archiveSeg `json:"segments,omitempty"`
	Tasks    int          `json:"tasks,omitempty"`
}

func archiveDir(dir string) string { return filepath.Join(dir, "archive") }

// appendArchive：成功的请求记全文，失败的只记一句（契约「本机留档」）。写失败只打日志——
// 已经发出去的收不回来，不能因为记不下就当没发。dir 空 = 不记（测试直接构造 Config）。
func appendArchive(dir string, now time.Time, rec archiveRec) {
	if dir == "" {
		return
	}
	if !rec.OK {
		rec.Result = fmt.Sprintf("%s（%d 段，失败的请求不记内容：下一轮原样重发，成功时再记）", rec.Result, len(rec.Segments))
		rec.Segments = nil
	}
	rec.At = isoTime(now)
	b, _ := json.Marshal(rec)
	d := archiveDir(dir)
	err := os.MkdirAll(d, 0o700)
	if err == nil {
		var f *os.File
		f, err = os.OpenFile(filepath.Join(d, now.Local().Format("2006-01-02")+".jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		if err == nil {
			_, err = f.Write(append(b, '\n'))
			if cerr := f.Close(); err == nil {
				err = cerr
			}
		}
	}
	if err != nil {
		log.Printf("本机留档写失败（不影响上传）：%v", err)
	}
}

// purgeArchive 删掉文件名日期早于「今天 − days + 1」的留档。
func purgeArchive(dir string, days int, now time.Time) {
	if days < 1 {
		days = 30
	}
	y, m, d := now.Local().Date()
	cutoff := time.Date(y, m, d, 0, 0, 0, 0, time.Local).AddDate(0, 0, -days+1)
	ents, _ := os.ReadDir(archiveDir(dir))
	for _, e := range ents {
		day, err := time.ParseInLocation("2006-01-02.jsonl", e.Name(), time.Local)
		if err == nil && day.Before(cutoff) {
			_ = os.Remove(filepath.Join(archiveDir(dir), e.Name()))
		}
	}
}

func readArchive(dir, day string) ([]archiveRec, error) {
	f, err := os.Open(filepath.Join(archiveDir(dir), day+".jsonl"))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []archiveRec
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	for sc.Scan() {
		var r archiveRec
		if json.Unmarshal(sc.Bytes(), &r) == nil {
			out = append(out, r)
		}
	}
	return out, sc.Err()
}

func printArchive(dir, day string, out io.Writer) error {
	if _, err := time.Parse("2006-01-02", day); err != nil {
		return fmt.Errorf("日期写成 YYYY-MM-DD")
	}
	recs, err := readArchive(dir, day)
	if errors.Is(err, os.ErrNotExist) {
		fmt.Fprintf(out, "%s 没有留档（这天没有发出任何东西，或已超过 archiveDays 被清掉）。\n", day)
		return nil
	}
	if err != nil {
		return err
	}
	to := map[string]string{"cockpit": "上传到 HoneyComb", "classifier": "发给分类服务"}
	for _, r := range recs {
		ok := "成功"
		if !r.OK {
			ok = "失败"
		}
		extra := ""
		if r.Tasks > 0 {
			extra = fmt.Sprintf("，附候选任务 %d 条", r.Tasks)
		}
		fmt.Fprintf(out, "%s  %s（%s：%s%s）\n", r.At, to[r.To], ok, r.Result, extra)
		for _, s := range r.Segments {
			idle := ""
			if s.Idle {
				idle = "  [无操作]"
			}
			fmt.Fprintf(out, "  %s → %s  %s  %s%s\n", s.StartAt, s.EndAt, (time.Duration(s.DurationSeconds) * time.Second).String(), s.App, idle)
			fmt.Fprintf(out, "    原始：%s\n    发出：%s\n", s.Raw, s.Sent)
			if s.Suggestion != nil {
				task := "（无）"
				if s.Suggestion.TaskID != nil {
					task = *s.Suggestion.TaskID
				}
				fmt.Fprintf(out, "    建议：%s  %.2f  %s\n", task, s.Suggestion.Confidence, s.Suggestion.Reason)
			}
		}
	}
	return nil
}

// preview：最近 n 段留档的 raw 按当前隐私选项重新处理，与当时发出的 sent 并排。只在本机终端。
func preview(cfg Config, n int, out io.Writer) error {
	r := newRedactor(cfg)
	ps, err := loadPseudonyms("") // 预览不给新标题分配真代号：只在内存里试编
	if err != nil {
		return err
	}
	if cfg.dir != "" {
		if real, err := loadPseudonyms(filepath.Join(cfg.dir, "pseudonyms.json")); err == nil {
			real.path = "" // 不写回
			ps = real
		}
	}
	ents, _ := os.ReadDir(archiveDir(cfg.dir))
	var segs []archiveSeg
	for i := len(ents) - 1; i >= 0 && len(segs) < n; i-- {
		recs, _ := readArchive(cfg.dir, strings.TrimSuffix(ents[i].Name(), ".jsonl"))
		for j := len(recs) - 1; j >= 0 && len(segs) < n; j-- {
			if recs[j].To != "cockpit" {
				continue
			}
			for k := len(recs[j].Segments) - 1; k >= 0 && len(segs) < n; k-- {
				segs = append(segs, recs[j].Segments[k])
			}
		}
	}
	if len(segs) == 0 {
		fmt.Fprintln(out, "留档里还没有上传过的段。可以用 ai-detector preview --app <程序> --title <标题> 试一条。")
		return nil
	}
	fmt.Fprintln(out, "（浏览器段在预览里没有扩展数据，按「对不上标签页」只留程序名）")
	for _, s := range segs {
		fmt.Fprintf(out, "%s  %s\n  原始：%s\n  当时：%s\n  现在：%s\n", s.StartAt, s.App, s.Raw, s.Sent, previewOne(r, ps, s.App, s.Raw))
	}
	return nil
}

func previewOne(r redactor, ps *pseudonyms, app, title string) string {
	t, _ := r.window(app, title, nil)
	if r.p.Titles == "pseudonymize" {
		t = ps.token(t)
	}
	return t
}

// ── 网页设置（detector.settings.v1）──────────────────────────────────────

// applyRemoteSettings：有网页设置就整节替换本机的 privacy / idle；null 或 404（老 nexus-core）用本机；
// 其他失败返回错误——这一轮不上传，不能退回可能更宽松的本机配置。
func applyRemoteSettings(cfg *Config, c *http.Client, base string) error {
	req, _ := http.NewRequest(http.MethodGet, base+"/api/core/detector/settings?deviceId="+url.QueryEscape(cfg.DeviceID), nil)
	req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	b, code, err := do(c, req)
	if code == http.StatusNotFound {
		remotePresence.Store(nil)
		return nil
	}
	if err != nil {
		return fmt.Errorf("拉不到网页上的检测设置（%v），这一轮不上传", err)
	}
	var r struct {
		Settings *struct {
			Privacy Privacy `json:"privacy"`
			Idle    Idle    `json:"idle"`
			// v1.1 追加：null / 没有 = 用本机配置的 presence
			Presence *bool `json:"presence"`
			// v1.2 追加：没有这个键 / 名单 null = 用本机配置
			SegmentByTitle     *bool    `json:"segmentByTitle"`
			SegmentByTitleApps []string `json:"segmentByTitleApps"`
		} `json:"settings"`
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return fmt.Errorf("网页上的检测设置格式不对（%v），这一轮不上传", err)
	}
	remotePresence.Store(nil)
	if r.Settings != nil {
		cfg.Privacy, cfg.Idle = r.Settings.Privacy, r.Settings.Idle
		if r.Settings.SegmentByTitle != nil {
			cfg.SegmentByTitle = r.Settings.SegmentByTitle
		}
		if r.Settings.SegmentByTitleApps != nil {
			cfg.SegmentByTitleApps = r.Settings.SegmentByTitleApps
		}
		if p := r.Settings.Presence; p != nil {
			cfg.Presence = *p
			remotePresence.Store(p)
		}
		// 服务端按 Python 正则校验，个别写法 Go 不认：跳过那一条并写日志（少保留 = 更保守），
		// 不能因为一条白名单让每一轮都卡住（契约 detector.settings.v1「校验」）。
		var ok []string
		for _, w := range cfg.Privacy.PathWhitelist {
			if _, err := regexp.Compile(w); err != nil {
				log.Printf("网页设置里的路径白名单 %q 本程序编译不了，跳过：%v", w, err)
				continue
			}
			ok = append(ok, w)
		}
		cfg.Privacy.PathWhitelist = ok
	}
	return nil
}
