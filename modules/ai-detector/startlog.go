package main

// v0.4：aw-start.log 的脱敏尾巴（本机开关 awLogUpload 打开时才随健康报告上传）。
//
// 底线：只上传「脱敏后的末尾几行」，硬上限 40 行 / 4096 字节 / 每行 300 字符；
// 任何一步拿不准（读不到家目录 / 用户名、脱敏后仍含它们、不是合法 UTF-8 且无法修复）都不发，宁缺毋滥。

import (
	"os"
	"os/user"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

const (
	logMaxLines    = 40
	logMaxBytes    = 4096
	logMaxLineRune = 300
	logReadTail    = 32 * 1024 // 只读文件最后这么多字节
	logLineIn      = 4000      // 脱敏前每行最多看这么多字节（再长的是二进制 / 压缩输出）
	hiddenLog      = "[已隐藏]"
)

var (
	reANSI = regexp.MustCompile("\x1b\\[[0-9;?]*[ -/]*[@-~]")
	// 含这些词的整行不上传（值的形状千变万化：--password x、password x、下一行才是值……不追形状）。
	// key / auth / pass / login 只认后面紧跟 = 或 : 的写法（前面是什么都算：encryption_key=、ACCESS_KEY=），
	// 免得 keyboard、monkey patching 这样的普通文字整行被抹掉；--key / -key 选项也算。
	reSensitive = regexp.MustCompile(`(?i)password|passwd|pwd|passphrase|token|secret|credential|api[-_ ]?key|authorization|bearer|private[-_ ]key|cookie|session[-_ ]?id|key["']?\s*[=:]|(?:^|\s)--?key\b|auth["']?\s*[=:]|pass["']?\s*[=:]|login["']?\s*[=:]|eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]`)
	// 命令行里的 -p <值> / -S <值>（sudo -S、mysql -p 之类，行首也算）：尽力而为
	reShortFlag = regexp.MustCompile(`(?:^|\s)-[pPS]\s+\S`)
	// 行首时间戳（尽量保守）：整行被抹掉时留着它，排查时还能知道发生在什么时候
	reStamp = regexp.MustCompile(`^\s*[\[(]?\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?[\])]?`)
	// 网址里的 userinfo：:// 之后、同一段非空白里的**最后一个** @ 之前全盖（密码里有 / 也算）
	reURLUser = regexp.MustCompile(`([A-Za-z][A-Za-z0-9+.-]*://)\S*@`)
	reLongRun = regexp.MustCompile(`[A-Za-z0-9+_=-]{24,}`)
)

const hiddenLine = "[整行已隐藏：可能含凭据]"

// isHexRun：全是十六进制字符。
func isHexRun(s string) bool {
	for _, r := range s {
		if !strings.ContainsRune("0123456789abcdefABCDEF", r) {
			return false
		}
	}
	return true
}

// maskLongRun：≥ 24 字符的一段，全十六进制、或字母数字都有的（像 base64 / 令牌）盖掉；
// 纯字母的长词、纯数字不动。路径里的 "/" 把长路径切成短段，不会被整条盖掉。
func maskLongRun(s string) string {
	if isHexRun(s) {
		return hiddenLog
	}
	if strings.ContainsAny(s, "0123456789") && strings.ContainsAny(strings.ToLower(s), "abcdefghijklmnopqrstuvwxyz") {
		return hiddenLog
	}
	return s
}

// unsafeRune：只留 unicode.IsPrint 的字符（含 ASCII 空格）。比服务端拒收的类别（Cc Cf Co Cs Zl Zp）严格得多，
// 两边 Unicode 表版本不同也不会因为差一两个字符被 422。
func unsafeRune(r rune) bool { return !unicode.IsPrint(r) }

// cleanLogLine：去控制 / 格式 / 私用区字符与 ANSI 转义，截到 logLineIn 字节。
func cleanLogLine(l string) string {
	l = strings.ToValidUTF8(l, "")
	l = reANSI.ReplaceAllString(l, "")
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) { // tab、不间断空格、行分隔符……一律当普通空格：删掉会把 --key 和它的值粘成一串
			return ' '
		}
		if unsafeRune(r) {
			return -1
		}
		return r
	}, l)
}

// commonName：这些用户名替换了反而把日志毁掉（root / user / admin 满篇都是），不按用户名替换
// （家目录 ~ 替换仍然照做）。
var commonName = map[string]bool{"root": true, "user": true, "admin": true, "administrator": true}

func endsOpen(l string) bool {
	t := strings.TrimSpace(l)
	return strings.HasSuffix(t, ":") || strings.HasSuffix(t, "=")
}

// redactLogLine：一行 → (脱敏后的一行, 下一行要不要整行盖掉)。空行不产出、也不改变 mask 状态。
// 整行策略：含敏感词的整行、超长行、以及被上一行点名的行，换成固定占位（保留行首时间戳）；
// 这样的行如果以 : 或 = 结尾（值在下一行）或本身超长，连下一行一起盖。拿不准就多盖。
func redactLogLine(l, home, usr string, mask bool) (string, bool) {
	long := len(l) > logLineIn // 太长的行拿不准：整行盖、下一行也盖（哪怕它全是空白 / 清理后是空的）
	if strings.TrimSpace(l) == "" {
		return "", mask || long
	}
	l = cleanLogLine(l)
	if strings.TrimSpace(l) == "" {
		return "", mask || long
	}
	// scrubSecrets 认出了已知格式的密钥 / 卡号 = 这一行有凭据：也整行盖。只换掉认出的那一段的话，
	// 它和长串规则谁先谁后都会留下半截（JWT 的短段、密钥后面拖着的尾巴）。
	if long || mask || reSensitive.MatchString(l) || reShortFlag.MatchString(l) || scrubbed(l) {
		return strings.TrimSpace(reStamp.FindString(l) + " " + hiddenLine), long || endsOpen(l)
	}
	if home != "" {
		for _, h := range []string{home, strings.ReplaceAll(home, `\`, "/"), strings.ReplaceAll(home, "/", `\`)} {
			l = replaceFold(l, h, "~")
		}
	}
	l = reURLUser.ReplaceAllString(l, "${1}"+hiddenLog+"@")
	l = reLongRun.ReplaceAllStringFunc(l, maskLongRun) // 先于用户名替换：不然长串被 <user> 从中间切开，两半都不够长
	if re := userRe(usr); re != nil {
		l = re.ReplaceAllString(l, "<user>")
	}
	if utf8.RuneCountInString(l) > logMaxLineRune {
		l = string([]rune(l)[:logMaxLineRune-1]) + "…"
	}
	return strings.TrimSpace(l), false
}

// scrubbed：scrubSecrets 在这一行里认出了东西。网址的 用户:密码@ 不算（那个只盖 userinfo，留着主机名好排查）。
func scrubbed(l string) bool {
	u := reURLUser.ReplaceAllString(l, "${1}"+hiddenLog+"@")
	return scrubSecrets(u) != u
}

// userRe：用户名按子串替换（不分大小写、不看词边界：非 ASCII 用户名、alice1、x_alice、/Users/ALICE 都算；多盖不要紧）。
// 短于 3 个字符、或 root / user / admin 这类满篇都是的名字不按用户名替换，返回 nil。
func userRe(usr string) *regexp.Regexp {
	if utf8.RuneCountInString(usr) < 3 || commonName[strings.ToLower(usr)] {
		return nil
	}
	return regexp.MustCompile(`(?i)` + regexp.QuoteMeta(usr))
}

// replaceFold：不分大小写地替换（Windows 路径大小写不敏感）。
func replaceFold(s, old, repl string) string {
	if old == "" {
		return s
	}
	return regexp.MustCompile(`(?i)`+regexp.QuoteMeta(old)).ReplaceAllLiteralString(s, repl)
}

// redactStartLog：日志原文 → 可上传的文本；ok=false = 不发（没内容 / 拿不准）。
// partial：raw 不是从文件开头读的——第一个换行之前的是半截行（比如 password= 后面的值），整段丢掉。
func redactStartLog(raw []byte, home, usr string, partial bool) (string, bool) {
	if len(raw) > logReadTail {
		raw, partial = raw[len(raw)-logReadTail:], true
	}
	if partial {
		i := strings.IndexByte(string(raw), '\n')
		if i < 0 {
			return "", false
		}
		raw = raw[i+1:]
	}
	var lines []string
	maskNext := partial // 切点可能落在 password: 里：丢掉半截行后，第一整行没有上下文，整行盖掉
	for _, l := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		var r string
		r, maskNext = redactLogLine(l, home, usr, maskNext)
		if r != "" {
			lines = append(lines, r)
		}
	}
	if len(lines) > logMaxLines {
		lines = lines[len(lines)-logMaxLines:]
	}
	for len(lines) > 0 && len(strings.Join(lines, "\n")) > logMaxBytes { // 超字节就从最老的一行开始丢，不截断单行
		lines = lines[1:]
	}
	if len(lines) == 0 {
		return "", false
	}
	out := strings.Join(lines, "\n")
	// 最后一道闸：脱敏后还留着家目录 / 用户名（整词）= 拿不准，不发
	// （先去掉我们自己放的占位，免得短用户名碰巧是 "use" 之类就永远发不出去）
	chk := strings.NewReplacer("<user>", "", hiddenLine, "", hiddenLog, "").Replace(out)
	if (home != "" && len(home) > 1 && strings.Contains(strings.ToLower(chk), strings.ToLower(home))) ||
		(userRe(usr) != nil && userRe(usr).MatchString(chk)) {
		return "", false
	}
	return out, true
}

// readStartLog：读 aw-start.log 的尾巴并脱敏。读不到家目录 / 用户名 = 拿不准 = 不发。
func readStartLog(path string) (string, bool) {
	home, err := os.UserHomeDir()
	u, err2 := user.Current()
	if err != nil || err2 != nil || home == "" || u.Username == "" {
		return "", false
	}
	name := u.Username
	if i := strings.LastIndexAny(name, `\`); i >= 0 { // Windows：DOMAIN\user
		name = name[i+1:]
	}
	f, err := os.Open(path)
	if err != nil {
		return "", false
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "", false
	}
	off := int64(0)
	if st.Size() > logReadTail {
		off = st.Size() - logReadTail
	}
	buf := make([]byte, st.Size()-off)
	n, _ := f.ReadAt(buf, off)
	return redactStartLog(buf[:n], home, name, off > 0)
}
