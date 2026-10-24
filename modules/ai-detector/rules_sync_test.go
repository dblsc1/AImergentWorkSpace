package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// detector.rules.v1「四」：服务端存过（version > 0）就只用服务端的；没存过 / 404 / 拉不到用本机 rules.json。
func TestRulesForRound(t *testing.T) {
	dir := t.TempDir()
	local := filepath.Join(dir, "rules.json")
	os.WriteFile(local, []byte(`{"rules":[{"app":"code","taskId":"t_local"}]}`), 0o600)
	seg := segment{App: "code", Title: "garden — main.go"}

	cases := []struct {
		name, body string
		status     int
		wantTask   string // "" = 没有规则命中
		wantReason string
	}{
		{"server authoritative", `{"version":3,"rules":[
			{"id":"a","app":null,"title":"blog","taskId":"t_blog","confidence":0.9,"enabled":true},
			{"id":"b","app":"CODE","title":null,"taskId":"t_off","confidence":0.9,"enabled":false},
			{"id":"c","app":"(?=x)","title":null,"taskId":"t_bad","confidence":0.9,"enabled":true},
			{"id":"d","app":"code","title":"GARDEN","taskId":"t_srv","confidence":0.7,"enabled":true}]}`,
			200, "t_srv", "网页规则 #4 命中"},
		{"server empty set still wins", `{"version":2,"rules":[]}`, 200, "", ""},
		{"never saved uses local", `{"version":0,"rules":[]}`, 200, "t_local", "规则 #1 命中"},
		{"old nexus-core 404 uses local", `{"detail":"Not Found"}`, 404, "t_local", "规则 #1 命中"},
		{"server error falls back to local", `oops`, 500, "t_local", "规则 #1 命中"},
		{"bad json falls back to local", `{"version":`, 200, "t_local", "规则 #1 命中"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var auth string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/core/detector/rules" || r.Method != http.MethodGet {
					t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
				}
				auth = r.Header.Get("Authorization")
				w.WriteHeader(tc.status)
				io.WriteString(w, tc.body)
			}))
			defer srv.Close()
			cfg := Config{DeviceToken: "tok", RulesFile: local}
			rules, err := rulesForRound(cfg, srv.Client(), srv.URL)
			if err != nil {
				t.Fatal(err)
			}
			if auth != "Bearer tok" {
				t.Errorf("auth = %q", auth)
			}
			sg, ok := matchRules(rules, seg)
			if tc.wantTask == "" {
				if ok {
					t.Fatalf("want no match, got %+v", sg)
				}
				return
			}
			if !ok || *sg.TaskID != tc.wantTask || sg.Reason != tc.wantReason {
				t.Fatalf("got %+v ok=%v", sg, ok)
			}
		})
	}
}

// 服务端存过规则时，本机 rules.json 写坏了也不影响（根本不读）；没存过时照旧报错、这一轮不上传。
func TestRulesForRoundLocalBrokenOnlyMattersWithoutServer(t *testing.T) {
	local := filepath.Join(t.TempDir(), "rules.json")
	os.WriteFile(local, []byte(`{broken`), 0o600)
	body := `{"version":1,"rules":[]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
	defer srv.Close()
	cfg := Config{DeviceToken: "tok", RulesFile: local}
	if _, err := rulesForRound(cfg, srv.Client(), srv.URL); err != nil {
		t.Fatalf("server rules should win: %v", err)
	}
	body = `{"version":0,"rules":[]}`
	if _, err := rulesForRound(cfg, srv.Client(), srv.URL); err == nil {
		t.Fatal("broken local rules.json must still be an error when the server has none")
	}
}

// detector.rules.v1 v1.1：规则的目标可以是项目——taskId 与 projectId 恰好一个；命中时建议带 projectId、不带 taskId。
func TestProjectTargetRules(t *testing.T) {
	local := filepath.Join(t.TempDir(), "rules.json")
	for _, bad := range []string{
		`{"rules":[{"app":"code"}]}`,
		`{"rules":[{"app":"code","taskId":"t_1","projectId":"p_1"}]}`,
	} {
		os.WriteFile(local, []byte(bad), 0o600)
		if _, err := loadRules(local); err == nil {
			t.Fatalf("want error for %s", bad)
		}
	}
	os.WriteFile(local, []byte(`{"rules":[{"title":"blog","projectId":"p_local"}]}`), 0o600)
	body := `{"version":2,"rules":[
		{"id":"a","app":null,"title":"both","taskId":"t_x","projectId":"p_x","confidence":0.9,"enabled":true},
		{"id":"b","app":null,"title":"neither","taskId":null,"confidence":0.9,"enabled":true},
		{"id":"c","app":null,"title":"blog","taskId":null,"projectId":"p_srv","confidence":0.8,"enabled":true}]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
	defer srv.Close()
	for _, want := range []string{"p_srv", "p_local"} {
		rules, err := rulesForRound(Config{DeviceToken: "tok", RulesFile: local}, srv.Client(), srv.URL)
		if err != nil || len(rules) != 1 {
			t.Fatalf("rules=%v err=%v", rules, err) // 两个目标都有 / 都没有的网页规则跳过
		}
		sg, ok := matchRules(rules, segment{App: "firefox", Title: "my blog"})
		if !ok || sg.TaskID != nil || sg.ProjectID == nil || *sg.ProjectID != want || sg.Classifier != "rules" {
			t.Fatalf("got %+v ok=%v", sg, ok)
		}
		b, _ := json.Marshal(sg)
		if got := string(b); !strings.Contains(got, `"taskId":null`) || !strings.Contains(got, `"projectId":"`+want+`"`) {
			t.Fatalf("wire %s", got)
		}
		body = `{"version":0,"rules":[]}` // 第二轮：服务端没存过，用本机的
	}
	// 到任务的建议不带 projectId 这个键（与 v1.2 的上传逐字节相同）
	id := "t_1"
	if b, _ := json.Marshal(suggestion{TaskID: &id, Classifier: "rules"}); strings.Contains(string(b), "projectId") {
		t.Fatalf("wire %s", b)
	}
}

// detector.rules.v1 v1.4：mode "ai" 的规则命中 = 不带目标、把握 0、needsAI；先命中先赢（压过后面更宽的规则）；带目标的 ai 规则跳过。
func TestAIModeRules(t *testing.T) {
	body := `{"version":2,"rules":[
		{"id":"a","app":"kitty","title":null,"taskId":null,"mode":"ai","confidence":0.9,"enabled":true},
		{"id":"b","app":null,"title":"bad","taskId":"t_x","mode":"ai","confidence":0.9,"enabled":true},
		{"id":"c","app":null,"title":".","taskId":"t_all","confidence":0.9,"enabled":true}]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
	defer srv.Close()
	rules, err := rulesForRound(Config{DeviceToken: "tok"}, srv.Client(), srv.URL)
	if err != nil || len(rules) != 2 {
		t.Fatalf("rules=%v err=%v", rules, err) // 带目标的 ai 规则跳过
	}
	sg, ok := matchRules(rules, segment{App: "kitty", Title: "work"})
	if !ok || !sg.NeedsAI || sg.TaskID != nil || sg.ProjectID != nil || sg.Confidence != 0 {
		t.Fatalf("got %+v ok=%v", sg, ok)
	}
	if b, _ := json.Marshal(sg); !strings.Contains(string(b), `"needsAI":true`) {
		t.Fatalf("wire %s", b)
	}
	if sg, _ := matchRules(rules, segment{App: "code", Title: "x"}); sg.NeedsAI || sg.TaskID == nil {
		t.Fatalf("普通规则不受影响：%+v", sg)
	}
	if b, _ := json.Marshal(suggestion{Classifier: "rules"}); strings.Contains(string(b), "needsAI") {
		t.Fatalf("wire %s", b)
	}
}

// detector.rules.v1 v1.5：kind "block" 只给原始记录的块名，不参与建议匹配，也不当坏规则刷日志。
func TestBlockRulesDoNotAffectSuggestions(t *testing.T) {
	body := `{"version":4,"rules":[
		{"id":"a","kind":"block","block":" 花园 ","app":null,"title":"garden","enabled":true},
		{"id":"b","kind":"block","block":"代码","app":"code","title":null,"enabled":true},
		{"id":"c","kind":"block","block":"","app":"code","title":null,"enabled":true},
		{"id":"d","kind":null,"app":"code","title":null,"taskId":"t_old","confidence":0.8,"enabled":true}]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, body) }))
	defer srv.Close()
	var logs strings.Builder
	log.SetOutput(&logs)
	defer log.SetOutput(io.Discard)
	rules, err := rulesForRound(Config{DeviceToken: "tok"}, srv.Client(), srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	// 旧式规则：第一级规则排在它前面也不抢先；理由里的序号仍是服务端数组位置。
	sg, ok := matchRules(rules, segment{App: "code", Title: "garden"})
	if !ok || sg.TaskID == nil || *sg.TaskID != "t_old" || sg.Reason != "网页规则 #4 命中" {
		t.Fatalf("%+v", sg)
	}
	if _, ok := matchRules(rules[:2], segment{App: "code", Title: "garden"}); ok {
		t.Fatal("block rules matched as suggestions")
	}
	// 块名：数组顺序第一条命中；去首尾空白；空块名那条是真坏规则，跳过并记日志。
	if b := blockFor(rules, "code", "my garden"); b == nil || *b != "花园" {
		t.Fatalf("%v", b)
	}
	if b := blockFor(rules, "code", "x"); b == nil || *b != "代码" {
		t.Fatalf("%v", b)
	}
	if b := blockFor(rules, "firefox", "x"); b != nil {
		t.Fatalf("%v", *b)
	}
	if strings.Count(logs.String(), "本程序用不了") != 1 || strings.Contains(logs.String(), "#1") || strings.Contains(logs.String(), "#2") {
		t.Fatalf("log: %q", logs.String())
	}
}
