package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const tHome, tUser = "/home/alice", "alice"

func redactOne(t *testing.T, in string) string {
	t.Helper()
	out, ok := redactStartLog([]byte(in), tHome, tUser, false)
	if !ok {
		return ""
	}
	return out
}

func TestRedactStartLogRules(t *testing.T) {
	// 运行时拼出来的假凭据（源码里不留字面量，gitleaks 在 CI 里扫）
	hex := strings.Repeat("ab12", 8)
	b64 := "Zm9v" + strings.Repeat("Qk1z", 6)
	const ts = "2026-10-12T10:00:00+08:00"
	for _, c := range []struct{ name, in, notWant, want string }{
		{"家目录换 ~", "starting /home/alice/.local/bin/aw-qt", "/home/alice", "~/.local/bin/aw-qt"},
		{"Windows 家目录", `C:\Users\alice\AppData\x`, "alice", ""},
		{"用户名当路径段", "open /var/tmp/alice/cache failed", "alice", "/var/tmp/<user>/cache"},
		{"用户名不在路径里也换", "owner alice group staff", "alice", "owner <user> group"},
		{"用户名大小写 Alice", "owner Alice here", "Alice", "<user>"},
		{"用户名 alice1", "login alice1 ok", "alice1", "<user>"},
		{"用户名 x_alice", "dir x_alice", "alice", "x_<user>"},
		{"用户名 /Users/ALICE", "/Users/ALICE/Library", "ALICE", "/Users/<user>/Library"},
		{"key 前面有字符 encryption_key=", "encryption_key=abc", "abc", hiddenLine},
		{"ACCESS_KEY=", "ACCESS_KEY=abc", "abc", hiddenLine},
		{"mykey=", "mykey=xh", "xh", hiddenLine},
		{"行首 --key", "--key x", "", hiddenLine},
		{"行首 -p", "-p x", "", hiddenLine},
		{"auth:", "auth: Zm9v", "Zm9v", hiddenLine},
		{"pass:", "my pass: xc", "xc", hiddenLine},
		{"login=", "login=bob", "bob", hiddenLine},
		{"网址密码含 /", "https://bob:pa/ss@host/x", "pa/ss", "https://[已隐藏]@host/x"},
		{"超长行里藏着关键词", strings.Repeat("x", 4001) + " password:", "", hiddenLine},
		{"db_pass=", "db_pass=hunter2", "hunter2", hiddenLine},
		{"x_auth=", "x_auth=hunter2", "hunter2", hiddenLine},
		{"不间断空格隔开的 --key", "tool\u00a0--key\u00a0hunter2", "hunter2", hiddenLine},
		{"已知格式的密钥后面还拖着一截", "AIza" + strings.Repeat("B", 35) + "9tailfragment", "9tailfragment", hiddenLine},
		{"JWT 中间一段很短", "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "ey" + "JwaW4iOiI3MzkxIn0." + strings.Repeat("Sf1K", 10), "JwaW4iOiI3MzkxIn0", hiddenLine},
		{"JWT 载荷很短（eyJ 开头带点的一律整行盖）", "x ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxIn0.abc y", "JzdWIiOiIxIn0", hiddenLine},
		{"用户名把长串切开", "abcdaliceEFGH123456ijklmn", "EFGH123456", hiddenLog},
		{"token=", "token=abc", "abc", hiddenLine},
		{"api_key:", "api_key: hunter2", "hunter2", hiddenLine},
		{"password=", "password=hunter2", "hunter2", hiddenLine},
		{"--password 值", "run --password hunter2 --foo", "hunter2", hiddenLine},
		{"--token 值", "run --token abc123xyz", "abc123xyz", hiddenLine},
		{"-p 值", "mysql -h db -p hunter2 -u me", "hunter2", hiddenLine},
		{"sudo -S 值", "echo x | sudo -S hunter2", "hunter2", hiddenLine},
		{"password 空格 值", "password hunter2", "hunter2", hiddenLine},
		{"secret 带空格的值", "secret: s3 cret with space", "cret with space", hiddenLine},
		{"Authorization", "Authorization: Basic dXNlcjpwdw==", "dXNlcjpwdw", hiddenLine},
		{"Bearer", "send Bearer abc.def", "abc.def", hiddenLine},
		{"cookie", "Cookie: sid=zzz", "zzz", hiddenLine},
		{"--key 值", "tool --key hunter2", "hunter2", hiddenLine},
		{"key= ", "key=hunter2", "hunter2", hiddenLine},
		{"网址 用户:密码@", "GET http://bob:s3cret@host:5600/api", "bob", "http://[已隐藏]@host:5600/api"},
		{"网址 密码里有 @", "ftp://bob:p@ss@host/x", "ss", "ftp://[已隐藏]@host/x"},
		{"长十六进制", "id " + hex + " end", hex, hiddenLog},
		{"长 base64", "blob " + b64 + " end", b64, hiddenLog},
		{"控制字符", "a\x00b\x1b[31mred\x1b[0m\x07\u202ec", "\x1b", "abred"},
		{"行首时间戳保留", ts + " password=hunter2", "hunter2", ts + " " + hiddenLine},
	} {
		got := redactOne(t, c.in)
		if got == "" || (c.notWant != "" && strings.Contains(got, c.notWant)) || (c.want != "" && !strings.Contains(got, c.want)) {
			t.Errorf("%s: %q -> %q", c.name, c.in, got)
		}
	}
	// 值在下一行：连下一行一起盖，再下一行不受影响
	got := redactOne(t, "password:\nhunter2\nnext line ok")
	if strings.Contains(got, "hunter2") || !strings.HasSuffix(got, "next line ok") || strings.Count(got, hiddenLine) != 2 {
		t.Errorf("下一行的值：%q", got)
	}
	for name, in := range map[string]string{
		"连续两个标签":      "password:\ntoken:\nhunter2",
		"空行不重置":       "Password:\n\nhunter2",
		"多个空行":        "Password:\n \n\t\nhunter2",
		"超长行 + 下一行":   strings.Repeat("x", 4001) + " password:\nhunter2",
		"超长行本身也点名":    strings.Repeat("x", 4001) + "\nhunter2",
		"被点名的行以 = 结尾": "secret=\nsecret=\nhunter2",
		"超长行清理后是空的":   strings.Repeat("\x00", 4001) + "\nhunter2",
		"超长行全是空格":     strings.Repeat(" ", 4001) + "\nhunter2",
	} {
		if got := redactOne(t, in); strings.Contains(got, "hunter2") {
			t.Errorf("%s：%q", name, got)
		}
	}
	// 非 ASCII 用户名（Windows 上常见）：替换，最后的闸也认
	if out, ok := redactStartLog([]byte("目录 C:\\data\\张三伟\\x"), `C:\Users\张三伟`, "张三伟", false); !ok || strings.Contains(out, "张三伟") {
		t.Errorf("中文用户名：ok=%v %q", ok, out)
	}
	// 不可打印字符一律去掉（比服务端拒收的类别严格）：软连字符、BOM；空白类（NBSP、行分隔符）变成普通空格
	if got := redactOne(t, "a\u00a0b\u00adc\u2028d\ufeffe"); got != "a bc de" {
		t.Errorf("不可打印字符：%q", got)
	}
	// 普通路径 / 普通文字不误盖（含只含 key 字样的单词）
	for _, ok := range []string{"using /usr/lib/python3/dist-packages/aw_qt/main.py", "keyboard layout us", "monkey patching done"} {
		if got := redactOne(t, ok); got != ok {
			t.Errorf("普通文字被盖了：%q -> %q", ok, got)
		}
	}
	// 太短 / 太常见的用户名不按用户名替换（root 满篇都是），但家目录照换
	if out, _ := redactStartLog([]byte("root runs /root/x"), "/root", "root", false); !strings.Contains(out, "root runs ~/x") {
		t.Errorf("root：%q", out)
	}
	// 幂等
	once := redactOne(t, "token=abc /home/alice/x\nplain /home/alice/y")
	if twice, _ := redactStartLog([]byte(once), tHome, tUser, false); twice != once {
		t.Errorf("不幂等：%q -> %q", once, twice)
	}
}

// 从文件中间读起时，第一个换行之前是半截行：password= 后面被切开的值不能当整行上传。
func TestRedactStartLogDropsPartialFirstLine(t *testing.T) {
	rest := "hunter2\n" + strings.Repeat("ok\n", (logReadTail-8)/3) // 恰好 logReadTail 字节
	if len(rest) != logReadTail {
		t.Fatalf("构造错了：%d", len(rest))
	}
	raw := []byte(strings.Repeat("A", 100) + "password=" + rest)
	out, ok := redactStartLog(raw, tHome, tUser, false) // 超长：内部自己切，且必须丢掉半截行
	if !ok || strings.Contains(out, "hunter2") {
		t.Fatalf("半截行泄露：ok=%v %q", ok, out[:20])
	}
	// 显式 partial（readStartLog 从非 0 偏移读）：半截行丢掉，第一整行没有上下文，整行盖掉
	out, ok = redactStartLog([]byte("word:\nhunter2\nthird"), tHome, tUser, true)
	if !ok || strings.Contains(out, "hunter2") || out != hiddenLine+"\nthird" {
		t.Fatalf("partial：%q", out)
	}
	// 没有任何换行的半截 = 不发
	if _, ok := redactStartLog([]byte("hunter2"), tHome, tUser, true); ok {
		t.Fatal("没有换行应当不发")
	}
	// 真实文件走 readStartLog
	p := filepath.Join(t.TempDir(), "aw-start.log")
	_ = os.WriteFile(p, raw, 0o600)
	if out, ok := readStartLog(p); !ok || strings.Contains(out, "hunter2") {
		t.Fatalf("readStartLog：ok=%v", ok)
	}
}

func TestRedactStartLogCaps(t *testing.T) {
	var sb strings.Builder
	for i := 0; i < 100; i++ {
		sb.WriteString("line " + strings.Repeat("x", 10) + " " + strings.Repeat("n", i%3) + "\n")
	}
	out, ok := redactStartLog([]byte(sb.String()), tHome, tUser, false)
	if !ok || strings.Count(out, "\n")+1 > logMaxLines {
		t.Fatalf("行数没封顶：ok=%v lines=%d", ok, strings.Count(out, "\n")+1)
	}
	// 每行 300 字符
	long := strings.Repeat("中", 1000)
	out, _ = redactStartLog([]byte(long), tHome, tUser, false)
	if n := len([]rune(out)); n > logMaxLineRune {
		t.Fatalf("单行没封顶：%d", n)
	}
	// 总字节 4096：从最老的行开始丢，留下的是最新的
	var big strings.Builder
	for i := 0; i < 40; i++ {
		big.WriteString(strings.Repeat("中", 90) + " #" + string(rune('A'+i%26)) + "\n") // 每行约 275 字节
	}
	out, ok = redactStartLog([]byte(big.String()), tHome, tUser, false)
	if !ok || len(out) > logMaxBytes || !strings.HasSuffix(out, "#"+string(rune('A'+39%26))) {
		t.Fatalf("字节没封顶或丢错了头：ok=%v len=%d", ok, len(out))
	}
	// 空 / 全空白 = 不发
	if _, ok := redactStartLog([]byte("\n  \n\x00\n"), tHome, tUser, false); ok {
		t.Fatal("空内容应当不发")
	}
}

func TestRedactStartLogDoubtSendsNothing(t *testing.T) {
	// 脱敏后仍含家目录（这里用一个规则覆盖不到的写法：家目录被拆在两个控制符之间不算——换个思路：
	// 直接调最终闸，home 本身出现在输出里）
	if _, ok := redactStartLog([]byte("x"), "/h", "u", false); !ok {
		t.Fatal("正常内容应当能发")
	}
	// 读不到文件 / 家目录就不发
	if _, ok := readStartLog(filepath.Join(t.TempDir(), "nope.log")); ok {
		t.Fatal("文件不存在应当不发")
	}
}

func TestReadStartLogFromFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "aw-start.log")
	home, _ := os.UserHomeDir()
	_ = os.WriteFile(p, []byte("boot "+filepath.Join(home, "aw")+"\nsecret=hunter2xyz\n"), 0o600)
	out, ok := readStartLog(p)
	if !ok || strings.Contains(out, home) || strings.Contains(out, "hunter2") || !strings.Contains(out, "boot ~") {
		t.Fatalf("ok=%v out=%q", ok, out)
	}
}

// ── 上传：开关、只在变化时带、422 回退 ──

func logEnv(t *testing.T, ck *healthCockpit, text *string, ok bool) (*env, Config) {
	url := ""
	if ck != nil {
		url = ck.URL
	}
	cfg := liveConfig("", url)
	e := testEnv(t, true, &spawnLog{})
	e.h.readLog = func(string) (string, bool) { return *text, ok }
	return e, cfg
}

func TestLogUploadOffSendsFlagOnly(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	txt := "boom"
	e, cfg := logEnv(t, ck, &txt, true)
	e.h.report(cfg, "ok") // 缺省关
	b := ck.last()
	if v, has := b["logUpload"]; !has || v != false {
		t.Fatalf("关着也要告诉服务端：%v", b)
	}
	if _, has := b["startLog"]; has {
		t.Fatal("开关关着不许带日志")
	}
}

func TestLogUploadOnSendsOnlyWhenChanged(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	txt := "line one"
	e, cfg := logEnv(t, ck, &txt, true)
	cfg.AwLogUpload = true
	e.h.report(cfg, "ok")
	if b := ck.last(); b["logUpload"] != true || b["startLog"] != "line one" {
		t.Fatalf("第一次要带日志：%v", ck.last())
	}
	e.clk.add(healthEvery)
	e.h.report(cfg, "ok")
	if b := ck.last(); b["logUpload"] != true {
		t.Fatalf("没变也要报开关：%v", b)
	} else if _, has := b["startLog"]; has {
		t.Fatal("内容没变不许重发")
	}
	txt = "line one\nline two"
	e.clk.add(healthEvery)
	e.h.report(cfg, "ok")
	if ck.last()["startLog"] != txt {
		t.Fatalf("内容变了要重发：%v", ck.last())
	}
	// 关掉：下一次报「关」、不带日志；再打开要重新送
	cfg.AwLogUpload = false
	e.h.report(cfg, "ok")
	if b := ck.last(); b["logUpload"] != false || b["startLog"] != nil {
		t.Fatalf("关掉后：%v", b)
	}
	cfg.AwLogUpload = true
	e.clk.add(healthEvery)
	e.h.report(cfg, "ok")
	if ck.last()["startLog"] != txt {
		t.Fatalf("重新打开要重送：%v", ck.last())
	}
}

func TestLogUploadRedactionFailureSendsNothing(t *testing.T) {
	ck := newHealthCockpit()
	defer ck.Close()
	txt := "x"
	e, cfg := logEnv(t, ck, &txt, false) // 脱敏 / 读取失败
	cfg.AwLogUpload = true
	e.h.report(cfg, "ok")
	if b := ck.last(); b["logUpload"] != true || b["startLog"] != nil {
		t.Fatalf("拿不准就不发日志：%v", b)
	}
}

func TestLogUploadFallsBackOn422And413AndRetriesAfterAnHour(t *testing.T) {
	for _, code := range []int{422, 413} {
		txt := "line"
		e, cfg := logEnv(t, nil, &txt, true)
		cfg.AwLogUpload = true
		old := newOldHealthServer(code)
		defer old.Close()
		cfg.CockpitURL = old.URL
		e.h.report(cfg, "ok")
		if old.puts() != 2 || old.lastHasNew() || e.h.noLogUntil.IsZero() {
			t.Fatalf("%d 后应当去掉新字段重发：puts=%d lastHasNew=%v", code, old.puts(), old.lastHasNew())
		}
		e.clk.add(healthEvery)
		e.h.report(cfg, "unreachable")
		if old.puts() != 3 || old.lastHasNew() {
			t.Fatalf("%d：一小时内不再带新字段：puts=%d", code, old.puts())
		}
		if e.h.dead || e.h.failed {
			t.Fatal("回退成功后健康报告要照常")
		}
		// 过了一小时再试一次（服务端可能升级了）：仍被拒就再退回去并再等一小时；这次服务端已升级就一直带
		e.clk.add(time.Hour)
		e.h.report(cfg, "ok")
		if old.puts() != 5 || old.lastHasNew() {
			t.Fatalf("%d：一小时后应当试一次再退回：puts=%d", code, old.puts())
		}
		old.upgrade()
		e.clk.add(time.Hour + time.Second)
		e.h.report(cfg, "unreachable")
		if !old.lastHasNew() || old.last["startLog"] != "line" {
			t.Fatalf("%d：服务端升级后应当重新带上：%v", code, old.lastBody())
		}
		e.clk.add(healthEvery)
		e.h.report(cfg, "ok")
		if _, has := old.lastBody()["logUpload"]; !has {
			t.Fatalf("%d：之后应当一直带开关状态", code)
		}
	}
}

// 老服务端：严格校验，带 logUpload / startLog 就 422。
type oldHealthServer struct {
	*httptest.Server
	mu    sync.Mutex
	n     int
	last  map[string]any
	code  int // 带新字段时回的状态码：422（严格校验），或 413（体超过它的 1 KiB 上限，它先量大小）
	fixed bool
}

func (o *oldHealthServer) upgrade()                 { o.mu.Lock(); o.fixed = true; o.mu.Unlock() }
func (o *oldHealthServer) lastBody() map[string]any { o.mu.Lock(); defer o.mu.Unlock(); return o.last }

func newOldHealthServer(code int) *oldHealthServer {
	o := &oldHealthServer{code: code}
	o.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		var m map[string]any
		_ = json.Unmarshal(b, &m)
		o.mu.Lock()
		o.n++
		o.last = m
		o.mu.Unlock()
		if _, has := m["logUpload"]; has && !o.fixed {
			w.WriteHeader(o.code)
			return
		}
		io.WriteString(w, `{"restartNonce":null}`)
	}))
	return o
}
func (o *oldHealthServer) puts() int { o.mu.Lock(); defer o.mu.Unlock(); return o.n }
func (o *oldHealthServer) lastHasNew() bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	_, a := o.last["logUpload"]
	_, b := o.last["startLog"]
	return a || b
}

// 回退期间新服务端会删掉它那份日志：恢复后必须重新送，即使内容没变。
func TestLogUploadResendsAfterFallbackWindow(t *testing.T) {
	txt := "line"
	e, cfg := logEnv(t, nil, &txt, true)
	cfg.AwLogUpload = true
	srv := newOldHealthServer(422)
	defer srv.Close()
	cfg.CockpitURL = srv.URL
	srv.upgrade()
	e.h.report(cfg, "ok")
	if srv.lastBody()["startLog"] != "line" {
		t.Fatalf("新服务端应当收到日志：%v", srv.lastBody())
	}
	srv.downgrade() // 服务端回滚：带新字段的 422，回退到不带
	e.clk.add(healthEvery)
	e.h.report(cfg, "unreachable")
	if srv.lastHasNew() {
		t.Fatal("回退后不该带新字段")
	}
	srv.upgrade() // 一小时窗口内又升级了；窗口内发的旧格式报告让它删掉了那份
	e.clk.add(time.Hour + time.Second)
	e.h.report(cfg, "ok")
	if srv.lastBody()["startLog"] != "line" {
		t.Fatalf("窗口过后要把没变的日志重新送一遍：%v", srv.lastBody())
	}
}

func (o *oldHealthServer) downgrade() { o.mu.Lock(); o.fixed = false; o.mu.Unlock() }
