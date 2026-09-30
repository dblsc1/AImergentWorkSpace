package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

func bp(b bool) *bool { return &b }

func red(p Privacy) redactor {
	r := newRedactor(Config{Privacy: p})
	r.literals = nil // 本机用户名 / 主机名因机器而异，单独测
	return r
}

func TestPrivacyOptions(t *testing.T) {
	const path = "vim /home/alice/work/garden/plot.gd"
	cases := []struct {
		name string
		p    Privacy
		in   string
		want string
	}{
		{"paths full", Privacy{}, path, "vim plot.gd"},
		{"paths half", Privacy{Paths: "half"}, path, "vim garden/plot.gd"},
		{"paths half windows", Privacy{Paths: "half"}, `打开 C:\Users\张三\work\garden\plot.gd`, "打开 garden/plot.gd"},
		{"paths half home file", Privacy{Paths: "half"}, "cat /home/alice/a.txt", "cat a.txt"},
		{"paths half unc", Privacy{Paths: "half"}, `\\fs01\财务\2026\工资.xlsx`, "2026/工资.xlsx"},
		{"paths half file url", Privacy{Paths: "half"}, "file:///C:/Users/zhang/docs/合同.pdf", "docs/合同.pdf"},
		{"paths off keeps, usernames hides", Privacy{Paths: "off"}, path, "vim /home/[用户]/work/garden/plot.gd"},
		{"paths off usernames off", Privacy{Paths: "off", Usernames: bp(false)}, path, path},
		{"whitelist keeps fragment", Privacy{PathWhitelist: []string{`garden/\S+`}}, "vim /home/alice/garden/plot.gd", "vim [路径]garden/plot.gd"},
		{"whitelist whole path", Privacy{PathWhitelist: []string{`/home/alice/garden/\S+`}}, "vim /home/alice/garden/plot.gd", "vim /home/alice/garden/plot.gd"},
		{"whitelist cannot keep a secret", Privacy{PathWhitelist: []string{`token=\S+`}}, "x token=" + "ghp_" + fill(36), "x token=[已隐藏:密钥]"},
		{"emails on", Privacy{}, "回复 alice@corp.com", "回复 [邮箱]"},
		{"emails off", Privacy{Emails: bp(false)}, "回复 alice@corp.com", "回复 alice@corp.com"},
		{"phones off", Privacy{Phones: bp(false), LongNumbers: bp(false)}, "客户 13812345678", "客户 13812345678"},
		{"address", Privacy{}, "寄到北京市朝阳区建国路88号SOHO现代城5号楼1203室", "寄到北京[地址]"},
		{"address estate", Privacy{}, "送到阳光小区3栋2单元501", "[地址]"},
		{"address off", Privacy{Addresses: bp(false)}, "建国路88号", "建国路88号"},
		{"address no number untouched", Privacy{}, "建国路上的咖啡店", "建国路上的咖啡店"},
		{"ipv4", Privacy{}, "ssh 192.168.1.20", "ssh [IP]"},
		{"ipv4 not version", Privacy{}, "v1.2.3.4 and 10.0.19045.1", "v1.2.3.4 and 10.0.19045.1"},
		{"ipv6", Privacy{}, "ping fe80::1ff:fe23:4567:890a", "ping [IP]"},
		{"not ipv6", Privacy{}, "std::vector 10:30:45", "std::vector 10:30:45"},
		{"ips off", Privacy{IPs: bp(false)}, "ssh 192.168.1.20", "ssh 192.168.1.20"},
		{"url host is ip", Privacy{}, "http://10.0.0.5:8080/admin", "[IP]"},
		{"prompt", Privacy{}, "alice@thinkpad: ~/work", "[用户]@[主机]: work"},
		{"long numbers off", Privacy{LongNumbers: bp(false)}, "订单 20260926000123", "订单 20260926000123"},
	}
	for _, c := range cases {
		r := red(c.p)
		if got, _ := r.window("code", c.in, nil); got != c.want {
			t.Errorf("%s: %q → %q, want %q", c.name, c.in, got, c.want)
		}
	}
}

func TestUsernameLiterals(t *testing.T) {
	r := red(Privacy{})
	r.literals = []literal{{regexp.MustCompile(`(?i)(^|[^\p{L}\p{N}_])` + regexp.QuoteMeta("zhangsan") + `($|[^\p{L}\p{N}_])`), "[用户]"}}
	if got, _ := r.window("code", "zhangsan 的笔记 — notzhangsan2", nil); got != "[用户] 的笔记 — notzhangsan2" {
		t.Fatalf("%q", got)
	}
}

func TestTitlesAppOnlyBrowserOptions(t *testing.T) {
	tab := &webTab{URL: "https://github.com/a/b/pull/1?tab=files#x", Title: "Fix #1"}
	cases := []struct {
		name, app, title string
		tab              *webTab
		p                Privacy
		want             string
	}{
		{"titles drop", "code", "plot.gd — garden", nil, Privacy{Titles: "drop"}, ""},
		{"appOnly on", "WeChat.exe", "张三：合同", nil, Privacy{}, ""},
		{"appOnly off", "WeChat.exe", "张三：合同", nil, Privacy{AppOnly: bp(false)}, "张三：合同"},
		{"appOnly custom list", "code", "x", nil, Privacy{AppOnlyApps: []string{"Code"}}, ""},
		{"browser domain", "firefox", "Fix #1 — Mozilla Firefox", tab, Privacy{}, "github.com · Fix #1"},
		{"browser full strips query", "firefox", "Fix #1 — Mozilla Firefox", tab, Privacy{Browser: "full"}, "https://github.com/a/b/pull/1 · Fix #1"},
		{"browser full keeps query", "firefox", "Fix #1 — Mozilla Firefox", tab, Privacy{Browser: "full", QueryStrings: bp(false)}, "https://github.com/a/b/pull/1?tab=files#x · Fix #1"},
		{"browser full secret in query", "firefox", "Fix #1 — Mozilla Firefox",
			&webTab{URL: "https://x.io/cb?access_token=" + "ghp_" + fill(36), Title: "Fix #1"}, Privacy{Browser: "full", QueryStrings: bp(false)}, ""},
		{"browser off", "firefox", "Fix #1 — Mozilla Firefox", tab, Privacy{Browser: "off"}, ""},
		{"browser full no tab", "firefox", "https://bank.example/acct - Firefox", nil, Privacy{Browser: "full"}, ""},
		{"browser full incognito", "firefox", "Fix #1", &webTab{URL: "https://a.io", Title: "Fix #1", Incognito: true}, Privacy{Browser: "full"}, ""},
	}
	for _, c := range cases {
		got, _ := red(c.p).window(c.app, c.title, c.tab)
		if c.name == "browser full secret in query" {
			if strings.Contains(got, "ghp_") || !strings.Contains(got, "[已隐藏:密钥]") {
				t.Errorf("%s: %q", c.name, got)
			}
			continue
		}
		if got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

func TestPrivacyCheck(t *testing.T) {
	for _, p := range []Privacy{{Paths: "none"}, {Titles: "hide"}, {Browser: "url"}, {PathWhitelist: []string{"("}}} {
		if p.check() == nil {
			t.Errorf("%+v should fail", p)
		}
	}
	if (Privacy{Paths: "half", Titles: "pseudonymize", Browser: "full", PathWhitelist: []string{`\w+`}}).check() != nil {
		t.Error("valid privacy rejected")
	}
}

// 强制脱敏关不掉：配置里写什么键都只是警告，所有可选项全关也照样盖。
func TestMandatoryScrubCannotBeDisabledByConfig(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("AI_DETECTOR_HOME", dir)
	p := pathsIn(dir)
	secret := "ghp_" + fill(36)
	aw := fakeAW(t, []awEvent{win(0, 30, "code", "deploy token="+secret+" card 4111 1111 1111 1111")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	b, _ := json.Marshal(cfg)
	var doc map[string]any
	json.Unmarshal(b, &doc)
	doc["privacy"] = map[string]any{
		"paths": "off", "titles": "keep", "appOnly": false, "browser": "full", "queryStrings": false,
		"emails": false, "phones": false, "addresses": false, "ips": false, "usernames": false, "longNumbers": false,
		"secrets": false, "mandatory": false, "passwords": false, "bankCards": false, "scrubSecrets": false,
	}
	doc["idle"] = map[string]any{"disableScrub": true}
	b, _ = json.Marshal(doc)
	os.WriteFile(p.config, b, 0o600)

	got, err := readConfig(p)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"idle.disableScrub", "privacy.bankCards", "privacy.mandatory", "privacy.passwords", "privacy.scrubSecrets", "privacy.secrets"}
	if strings.Join(got.warnings, ",") != strings.Join(want, ",") {
		t.Fatalf("warnings=%v", got.warnings)
	}
	var out bytes.Buffer
	if err := status(p, &out); err != nil || !strings.Contains(out.String(), "privacy.secrets 不认识") {
		t.Fatalf("status: %v\n%s", err, out.String())
	}
	if _, err := tick(got, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	body := string(ck.bodies[0])
	if strings.Contains(body, secret) || strings.Contains(body, "4111") || !strings.Contains(body, "[已隐藏:密钥]") || !strings.Contains(body, "[已隐藏:银行卡]") {
		t.Fatalf("mandatory scrub bypassed: %s", body)
	}
	// 留档里的 raw 也是强制脱敏过的。
	arch, _ := os.ReadFile(filepath.Join(dir, "archive", time.Now().Format("2006-01-02")+".jsonl"))
	if strings.Contains(string(arch), secret) || !strings.Contains(string(arch), `"raw"`) {
		t.Fatalf("archive: %s", arch)
	}
}

func TestPseudonymsStableAcrossRestarts(t *testing.T) {
	dir := t.TempDir()
	run := func(events []awEvent) []string {
		aw := fakeAW(t, events, nil)
		defer aw.Close()
		ck := fakeCockpit(t)
		defer ck.Close()
		cfg := testConfig(aw.URL, ck.URL)
		cfg.dir = dir
		cfg.Privacy = Privacy{Titles: "pseudonymize", Paths: "off", Usernames: bp(false)}
		if _, err := tick(cfg, activeState(), at(120), http.DefaultClient); err != nil {
			t.Fatal(err)
		}
		var body uploadBody
		json.Unmarshal(ck.bodies[0], &body)
		var out []string
		for _, s := range body.Segments {
			out = append(out, s.Title)
		}
		return out
	}
	first := run([]awEvent{win(0, 20, "code", "garden notes"), win(30, 20, "vim", "vim /home/a/x.txt")})
	if strings.Join(first, ",") != "窗口名1,路径1" {
		t.Fatalf("first=%v", first)
	}
	// 「重启」：新的一轮从磁盘重新读对照表。旧标题拿旧代号，新标题接着编号。
	second := run([]awEvent{win(0, 20, "code", "other"), win(30, 20, "code", "garden notes")})
	if strings.Join(second, ",") != "窗口名2,窗口名1" {
		t.Fatalf("second=%v", second)
	}
	fi, err := os.Stat(filepath.Join(dir, "pseudonyms.json"))
	if err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("pseudonyms.json: %v %v", fi, err)
	}
	var out bytes.Buffer
	printPseudonyms(filepath.Join(dir, "pseudonyms.json"), &out)
	if !strings.Contains(out.String(), "窗口名1  garden notes") || !strings.Contains(out.String(), "路径1") {
		t.Fatalf("%s", out.String())
	}
	// 坏掉的对照表：不上传（重新编号会让旧代号指向新标题）。
	os.WriteFile(filepath.Join(dir, "pseudonyms.json"), []byte("{"), 0o600)
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.dir, cfg.Privacy.Titles = dir, "pseudonymize"
	if _, err := tick(cfg, activeState(), at(120), http.DefaultClient); err == nil || len(ck.bodies) != 0 {
		t.Fatalf("corrupt table must stop upload: %v", err)
	}
}

// 规则匹配真实标题；分类服务拿到的与上传的一模一样（代号），且过了强制脱敏（含候选任务路径）。
func TestClassifierPayloadIsScrubbedAndPseudonymized(t *testing.T) {
	var got []byte
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, _ = io.ReadAll(r.Body)
		io.WriteString(w, `{"suggestions":[]}`)
	}))
	defer svc.Close()
	secret := "sk-" + fill(40)
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "garden "+secret), win(30, 20, "blender", "scene")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	dir := t.TempDir()
	rules := filepath.Join(dir, "rules.json")
	os.WriteFile(rules, []byte(`{"rules":[{"title":"^garden","taskId":"t_a1"}]}`), 0o600)
	cfg := testConfig(aw.URL, ck.URL)
	cfg.dir, cfg.RulesFile, cfg.ClassifierURL = dir, rules, svc.URL
	cfg.Privacy.Titles = "pseudonymize"
	if _, err := tick(cfg, activeState(), at(120), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var body uploadBody
	json.Unmarshal(ck.bodies[0], &body)
	if body.Segments[0].Suggestion.Classifier != "rules" || body.Segments[0].Title != "窗口名1" {
		t.Fatalf("rules must match the real title, upload the pseudonym: %+v", body.Segments[0])
	}
	if !strings.Contains(string(got), `"title":"窗口名2"`) || strings.Contains(string(got), "scene") || strings.Contains(string(got), secret) {
		t.Fatalf("classifier payload: %s", got)
	}
	arch, _ := readArchive(dir, time.Now().Format("2006-01-02"))
	if len(arch) != 2 || arch[0].To != "classifier" || arch[0].Segments[0].Raw != "scene" || arch[0].Segments[0].Sent != "窗口名2" {
		t.Fatalf("archive %+v", arch)
	}
}

func TestArchiveWritePrintPurge(t *testing.T) {
	dir := t.TempDir()
	aw := fakeAW(t, []awEvent{win(0, 30, "code", "C:\\Users\\bob\\plan.docx - Word")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	ck.reply = `{"accepted":1,"duplicates":0,"rejected":[]}`
	cfg := testConfig(aw.URL, ck.URL)
	cfg.dir = dir
	if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	day := time.Now().Format("2006-01-02")
	f := filepath.Join(dir, "archive", day+".jsonl")
	if fi, err := os.Stat(f); err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("%v %v", fi, err)
	}
	recs, _ := readArchive(dir, day)
	if len(recs) != 1 || !recs[0].OK || recs[0].Result != "accepted=1 duplicates=0 rejected=0" ||
		recs[0].Segments[0].Raw != `C:\Users\bob\plan.docx - Word` || recs[0].Segments[0].Sent != "plan.docx - Word" {
		t.Fatalf("%+v", recs)
	}
	var out bytes.Buffer
	if err := printArchive(dir, day, &out); err != nil || !strings.Contains(out.String(), "原始：C:\\Users\\bob\\plan.docx - Word") ||
		!strings.Contains(out.String(), "发出：plan.docx - Word") {
		t.Fatalf("%v\n%s", err, out.String())
	}
	// 失败的上传只记一行，不记内容。
	ck.status = 500
	tick(cfg, activeState(), at(60), http.DefaultClient)
	recs, _ = readArchive(dir, day)
	if len(recs) != 2 || recs[1].OK || recs[1].Segments != nil || !strings.Contains(recs[1].Result, "1 段") {
		t.Fatalf("%+v", recs[1])
	}
	// 保留期：留今天起的 archiveDays 天。
	now := time.Now()
	for _, d := range []int{-2, -3, -40} {
		os.WriteFile(filepath.Join(dir, "archive", now.AddDate(0, 0, d).Format("2006-01-02")+".jsonl"), []byte("{}\n"), 0o600)
	}
	os.WriteFile(filepath.Join(dir, "archive", "notes.txt"), nil, 0o600)
	purgeArchive(dir, 3, now)
	ents, _ := os.ReadDir(filepath.Join(dir, "archive"))
	var names []string
	for _, e := range ents {
		names = append(names, e.Name())
	}
	want := []string{now.AddDate(0, 0, -2).Format("2006-01-02") + ".jsonl", day + ".jsonl", "notes.txt"}
	if strings.Join(names, ",") != strings.Join(want, ",") {
		t.Fatalf("after purge %v, want %v", names, want)
	}
	// preview：按现在的选项重做 raw，与当时发出的并排。
	out.Reset()
	cfg.Privacy.Paths = "off"
	cfg.Privacy.Usernames = bp(false)
	preview(cfg, 5, &out)
	if !strings.Contains(out.String(), "当时：plan.docx - Word") || !strings.Contains(out.String(), `现在：C:\Users\bob\plan.docx - Word`) {
		t.Fatalf("%s", out.String())
	}
}

func TestRemoteSettingsOverrideLocal(t *testing.T) {
	aw := fakeAW(t, []awEvent{win(0, 30, "code", "secret project")}, nil)
	defer aw.Close()
	var reply string
	var status int
	var gotAuth string
	var uploads [][]byte
	ck := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/api/core/detector/settings") {
			gotAuth = r.Header.Get("Authorization")
			if r.URL.Query().Get("deviceId") != "dev_test" {
				t.Errorf("deviceId %q", r.URL.Query().Get("deviceId"))
			}
			w.WriteHeader(status)
			io.WriteString(w, reply)
			return
		}
		if r.Method == http.MethodGet { // 分类规则（detector.rules.v1）：老服务端，用本机
			w.WriteHeader(404)
			return
		}
		b, _ := io.ReadAll(r.Body)
		uploads = append(uploads, b)
	}))
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	upload := func() (string, error) {
		uploads = nil
		_, err := tick(cfg, activeState(), at(60), http.DefaultClient)
		if len(uploads) == 0 {
			return "", err
		}
		var b uploadBody
		json.Unmarshal(uploads[0], &b)
		return b.Segments[0].Title, err
	}
	status, reply = 200, `{"deviceId":"dev_test","settings":{"schemaVersion":1,"privacy":{"titles":"drop","appOnlyApps":null,"futureKey":1},"idle":{}},"updatedAt":"x"}`
	if title, err := upload(); err != nil || title != "" || gotAuth != "Bearer test-device-token" {
		t.Fatalf("server drop must win: %q %v %q", title, err, gotAuth)
	}
	status, reply = 200, `{"deviceId":"dev_test","settings":null,"updatedAt":null}`
	if title, err := upload(); err != nil || title != "secret project" {
		t.Fatalf("null → local: %q %v", title, err)
	}
	status, reply = 404, `{"detail":"Not Found"}`
	if title, err := upload(); err != nil || title != "secret project" {
		t.Fatalf("404 → local: %q %v", title, err)
	}
	for _, c := range []struct {
		code int
		body string
	}{{500, ""}, {401, ""}, {200, "not json"}, {200, `{"settings":{"privacy":{"titles":"bogus"}}}`}} {
		status, reply = c.code, c.body
		if _, err := upload(); err == nil || len(uploads) != 0 {
			t.Fatalf("%d %q must stop the upload: %v", c.code, c.body, err)
		}
	}
}

func TestIdleOptions(t *testing.T) {
	// 0–60 前台一直是同一个窗口，20–45 离开（25 分钟）。
	d := func(app string) awData {
		return awData{
			window: []awEvent{{Timestamp: at(0), Duration: 3600, Data: map[string]any{"app": app, "title": "x"}}},
			afk:    []awEvent{{Timestamp: at(20), Duration: 1500, Data: map[string]any{"status": "afk"}}},
		}
	}
	active := func(fr []fragment) (a, idle time.Duration) {
		for _, f := range fr {
			if f.Idle {
				idle += f.End.Sub(f.Start)
			} else {
				a += f.End.Sub(f.Start)
			}
		}
		return
	}
	cases := []struct {
		name       string
		app        string
		idle       Idle
		web        []awEvent
		want, wIdl time.Duration
	}{
		{"default", "code", Idle{}, nil, 35 * time.Minute, 0},
		{"threshold above", "code", Idle{AfkThresholdMinutes: 30}, nil, 60 * time.Minute, 0},
		{"threshold below", "code", Idle{AfkThresholdMinutes: 25}, nil, 35 * time.Minute, 0}, // 恰好 25 分钟不短于 25
		{"focus app capped", "SumatraPDF.exe", Idle{FocusAppsEnabled: true, FocusMaxMinutes: 10}, nil, 45 * time.Minute, 0},
		{"focus default cap", "wemeet", Idle{FocusAppsEnabled: true}, nil, 60 * time.Minute, 0},
		{"focus off", "SumatraPDF.exe", Idle{}, nil, 35 * time.Minute, 0},
		{"focus not listed", "code", Idle{FocusAppsEnabled: true}, nil, 35 * time.Minute, 0},
		{"audible browser", "firefox", Idle{AudibleAsPresent: true},
			[]awEvent{{Timestamp: at(25), Duration: 600, Data: map[string]any{"url": "https://v.io", "title": "x", "audible": true}}}, 45 * time.Minute, 0},
		{"audible off", "firefox", Idle{},
			[]awEvent{{Timestamp: at(25), Duration: 600, Data: map[string]any{"url": "https://v.io", "title": "x", "audible": true}}}, 35 * time.Minute, 0},
		{"idle suggestions", "code", Idle{IdleSuggestions: true}, nil, 35 * time.Minute, 25 * time.Minute},
	}
	for _, c := range cases {
		data := d(c.app)
		data.web = c.web
		a, i := active(buildFragments(data, at(0), at(60), newRedactor(Config{Idle: c.idle})))
		if a != c.want || i != c.wIdl {
			t.Errorf("%s: active=%v idle=%v, want %v / %v", c.name, a, i, c.want, c.wIdl)
		}
	}
}

func TestIdleSegmentsUploadedSeparately(t *testing.T) {
	aw := fakeAW(t, []awEvent{win(0, 60, "code", "garden")},
		[]awEvent{{Timestamp: at(20), Duration: 1500, Data: map[string]any{"status": "afk"}}})
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	dir := t.TempDir()
	rules := filepath.Join(dir, "rules.json")
	os.WriteFile(rules, []byte(`{"rules":[{"title":"garden","taskId":"t_a1"}]}`), 0o600)
	cfg := testConfig(aw.URL, ck.URL)
	cfg.RulesFile = rules
	cfg.Idle.IdleSuggestions = true
	if _, err := tick(cfg, activeState(), at(120), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	var raw struct {
		Segments []map[string]any `json:"segments"`
	}
	json.Unmarshal(ck.bodies[0], &raw)
	if len(raw.Segments) != 3 {
		t.Fatalf("want 2 active + 1 idle: %s", ck.bodies[0])
	}
	var idle map[string]any
	for _, s := range raw.Segments {
		if s["idle"] == true {
			idle = s
		} else if _, has := s["idle"]; has {
			t.Fatalf("normal segment must not carry idle: %v", s)
		}
	}
	sg := idle["suggestion"].(map[string]any)
	if idle["durationSeconds"].(float64) != 25*60 || sg["confidence"].(float64) != 0.3 ||
		!strings.HasPrefix(sg["reason"].(string), "无操作，可能在阅读") || sg["taskId"] != "t_a1" {
		t.Fatalf("idle segment %v", idle)
	}
}

func TestBrowserFullURLStillFiltersPII(t *testing.T) {
	tab := &webTab{URL: "https://example.com/contact/alice@example.com/orders/20260926000123", Title: "Orders"}
	got, _ := red(Privacy{Browser: "full"}).window("firefox", "Orders — Firefox", tab)
	if got != "https://example.com/contact/[邮箱]/orders/[数字] · Orders" {
		t.Fatalf("%q", got)
	}
}

func TestClassifierReasonIsScrubbedBeforeUpload(t *testing.T) {
	secret := "ghp_" + fill(36)
	svc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"suggestions":[{"segmentId":"seg_0","taskId":"t_a1","confidence":0.8,"reason":"saw token `+secret+`"}]}`)
	}))
	defer svc.Close()
	aw := fakeAW(t, []awEvent{win(0, 20, "code", "x")}, nil)
	defer aw.Close()
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.dir, cfg.ClassifierURL = t.TempDir(), svc.URL
	if _, err := tick(cfg, activeState(), at(60), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	arch, _ := os.ReadFile(filepath.Join(cfg.dir, "archive", time.Now().Format("2006-01-02")+".jsonl"))
	if strings.Contains(string(ck.bodies[0]), secret) || strings.Contains(string(arch), secret) || !strings.Contains(string(ck.bodies[0]), "[已隐藏:密钥]") {
		t.Fatalf("reason leaked: %s\n%s", ck.bodies[0], arch)
	}
}

func TestSafeCursorNeverLandsInsideASegment(t *testing.T) {
	segs := []segment{{Start: at(0), End: at(30)}, {Start: at(20), End: at(50)}, {Start: at(60), End: at(70)}}
	for _, c := range []struct{ in, want float64 }{{25, 0}, {45, 0}, {55, 55}, {60, 60}, {65, 60}} {
		if got := safeCursor(at(c.in), segs); !got.Equal(at(c.want)) {
			t.Errorf("safeCursor(%v) = %v, want %v", c.in, got, at(c.want))
		}
	}
}

// 离开区间被裁到游标时，阈值、阅读上限仍按整段算；正在延续的离开照算离开。
func TestIdleUsesWholeAFKSpan(t *testing.T) {
	var gotStart string
	aw := fakeAW(t, []awEvent{win(0, 120, "code", "x")},
		[]awEvent{{Timestamp: at(10), Duration: 40 * 60, Data: map[string]any{"status": "afk"}}})
	defer aw.Close()
	orig := aw.Config.Handler
	aw.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "afk") && strings.Contains(r.URL.Path, "events") {
			gotStart = r.URL.Query().Get("start")
		}
		orig.ServeHTTP(w, r)
	})
	ck := fakeCockpit(t)
	defer ck.Close()
	cfg := testConfig(aw.URL, ck.URL)
	cfg.Idle.AfkThresholdMinutes = 30
	st := &State{Cursor: at(40), Active: true} // 离开 10–50 被游标切成 40–50（10 分钟 < 30）
	if _, err := tick(cfg, st, at(130), http.DefaultClient); err != nil {
		t.Fatal(err)
	}
	if want := at(10).UTC().Format(time.RFC3339Nano); gotStart != want {
		t.Fatalf("afk fetched from %s, want %s", gotStart, want)
	}
	var body uploadBody
	json.Unmarshal(ck.bodies[0], &body)
	if body.Segments[0].StartAt != isoTime(at(50)) {
		t.Fatalf("40–50 must still be away: %+v", body.Segments[0])
	}
	// 一直延续到「现在」的离开：不知道最后多长，照算离开。
	d := awData{window: []awEvent{win(0, 60, "code", "x")},
		afk: []awEvent{{Timestamp: at(50), Duration: 600, Data: map[string]any{"status": "afk"}}}}
	var active time.Duration
	for _, f := range buildFragments(d, at(0), at(60), newRedactor(Config{Idle: Idle{AfkThresholdMinutes: 30}})) {
		active += f.End.Sub(f.Start)
	}
	if active != 50*time.Minute {
		t.Fatalf("ongoing afk counted as present: %v", active)
	}
}

func TestRemoteWhitelistGoCannotCompileIsSkipped(t *testing.T) {
	ck := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"settings":{"schemaVersion":1,"privacy":{"pathWhitelist":["(?<=a)b","garden"]}}}`)
	}))
	defer ck.Close()
	cfg := Config{DeviceID: "d", DeviceToken: "t"}
	if err := applyRemoteSettings(&cfg, http.DefaultClient, ck.URL); err != nil || strings.Join(cfg.Privacy.PathWhitelist, ",") != "garden" || cfg.Privacy.check() != nil {
		t.Fatalf("%v %v", err, cfg.Privacy.PathWhitelist)
	}
}
