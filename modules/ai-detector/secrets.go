package main

// 强制脱敏：密码、密钥、私钥、银行卡号、身份证号。不管隐私选项怎么选都跑，**配置文件里没有开关**——
// 写什么都关不掉（只会在日志 / status 里警告一句，测试锁住）；设置页上是灰的。
// 规则清单是规范性的，见 module_docs/contract.md「强制脱敏」。
//
// 密钥规则是从 gitleaks（https://github.com/gitleaks/gitleaks，config/gitleaks.toml）
// 默认规则里挑出来、改写成 Go RE2 正则的一小部分。只挑窗口标题里现实中可能出现的；
// 不引整个 gitleaks 模块：它会把 viper、zerolog 等几十个依赖拉进这个零依赖的单文件程序，
// 二进制大好几倍，而我们要的只是十几条正则。gitleaks 以 MIT 许可发布，原许可声明如下：
//
//   MIT License
//
//   Copyright (c) 2019 Zachary Rice
//
//   Permission is hereby granted, free of charge, to any person obtaining a copy
//   of this software and associated documentation files (the "Software"), to deal
//   in the Software without restriction, including without limitation the rights
//   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//   copies of the Software, and to permit persons to whom the Software is
//   furnished to do so, subject to the following conditions:
//
//   The above copyright notice and this permission notice shall be included in all
//   copies or substantial portions of the Software.
//
//   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//   SOFTWARE.

import (
	"math"
	"regexp"
	"strings"
)

// ======================== 强制脱敏开关（只能改源码）========================
// 这五项故意不做成配置：配置文件谁都能改（包括误操作、别的程序），改源码再编译至少要懂行。
// 自己编译的人确实要关某一项：把下面对应的 true 改成 false，重新 go build（README「自己构建」）。
// 关掉之后该类信息会原样进入上传内容和本机留档，后果自负。官方发布的程序这里全是 true。
const (
	mandatoryPasswords   = true // password= / pwd: / 密码： / 口令： 后面的值
	mandatorySecrets     = true // API key / 访问令牌（gitleaks 规则子集）、Bearer 令牌、网址里的「用户:密码@」
	mandatoryPrivateKeys = true // -----BEGIN … PRIVATE KEY-----
	mandatoryBankCards   = true // 13~19 位、发卡行前缀 + Luhn 校验通过的卡号
	mandatoryCNIDs       = true // 18 位居民身份证号（校验位 + 出生日期）
)

// mandatoryItems 给 status 和设置页显示（灰色、不可点）。
var mandatoryItems = []struct {
	Name string `json:"name"`
	On   bool   `json:"on"`
}{
	{"密码（password= / pwd: / 密码： / 口令：后面的值）", mandatoryPasswords},
	{"密钥 / 访问令牌（AWS、GitHub、GitLab、OpenAI / Anthropic / DeepSeek sk-、Slack、Google、Stripe、npm、PyPI、Hugging Face、JWT、Bearer、通用 key=value）", mandatorySecrets},
	{"私钥（-----BEGIN … PRIVATE KEY-----）", mandatoryPrivateKeys},
	{"银行卡号（Luhn 校验）", mandatoryBankCards},
	{"身份证号（校验位 + 出生日期）", mandatoryCNIDs},
}

const (
	hiddenPassword = "[已隐藏:密码]"
	hiddenSecret   = "[已隐藏:密钥]"
	hiddenKey      = "[已隐藏:私钥]"
	hiddenCard     = "[已隐藏:银行卡]"
	hiddenID       = "[已隐藏:身份证]"
)

// secretRules：命中即整段换成 hiddenSecret。名字对应 gitleaks 的规则 id（测试按名字列正反例）。
var secretRules = []struct {
	name string
	re   *regexp.Regexp
}{
	{"aws-access-token", regexp.MustCompile(`\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b`)},
	{"github-token", regexp.MustCompile(`\b(?:gh[pousr]_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{82})\b`)},
	{"gitlab-pat", regexp.MustCompile(`\bglpat-[0-9A-Za-z_-]{20,}`)},
	// OpenAI（sk-proj- / sk-svcacct- / sk-admin- / 旧式 sk-）、Anthropic（sk-ant-api03-）、
	// DeepSeek 等「sk-」开头的 API key。第二条要求有一段 ≥20 位不带连字符的字母数字，
	// 「sk-learn-tutorial」这种连字符拼起来的普通词不算。
	{"sk-api-key", regexp.MustCompile(`\bsk-(?:ant-(?:api|admin)\d\d-|proj-|svcacct-|admin-)[0-9A-Za-z_-]{20,}`)},
	{"sk-api-key", regexp.MustCompile(`\bsk-(?:[0-9A-Za-z]+-){0,3}[0-9A-Za-z_]{20,}[0-9A-Za-z_-]*`)},
	{"slack-token", regexp.MustCompile(`\bxox[abposr]-[0-9A-Za-z-]{10,}`)},
	{"slack-app-token", regexp.MustCompile(`\bxapp-\d-[A-Z0-9]+-\d+-[a-z0-9]+`)},
	{"slack-webhook", regexp.MustCompile(`hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9+/]{43,56}`)},
	{"gcp-api-key", regexp.MustCompile(`\bAIza[0-9A-Za-z_-]{35}`)},
	{"gcp-oauth", regexp.MustCompile(`\b(?:GOCSPX-[0-9A-Za-z_-]{28}|ya29\.[0-9A-Za-z_-]{20,})`)},
	{"stripe-key", regexp.MustCompile(`\b(?:sk|rk)_(?:test|live|prod)_[0-9A-Za-z]{10,99}`)},
	{"npm-token", regexp.MustCompile(`\bnpm_[0-9A-Za-z]{36}\b`)},
	{"pypi-token", regexp.MustCompile(`pypi-AgEIcHlwaS5vcmc[0-9A-Za-z_-]{50,}`)},
	{"huggingface-token", regexp.MustCompile(`\bhf_[A-Za-z]{34}\b`)},
	{"jwt", regexp.MustCompile(`\bey[A-Za-z0-9]{17,}\.ey[A-Za-z0-9/_-]{17,}\.(?:[A-Za-z0-9/_-]{10,}={0,2})?`)},
}

// quotedOrBare：带引号的值（引号内任意字符，支持 \ 转义）或不带引号的一段。
const quotedOrBare = `"(?:[^"\\]|\\.){1,150}"|'(?:[^'\\]|\\.){1,150}'|[^\s"'，。；,;]{1,150}`

// hideValue 盖掉值，引号留着；已经盖过的原样返回（幂等）。
func hideValue(v, token string) string {
	inner, q := v, ""
	if len(v) >= 2 && (v[0] == '"' || v[0] == '\'') {
		inner, q = v[1:len(v)-1], v[:1]
	}
	if strings.HasPrefix(inner, "[已隐藏:") {
		return v
	}
	return q + token + q
}

var (
	// 私钥：标题里只可能出现开头一行，从 BEGIN 盖到 END 或行尾。
	rePrivateKey = regexp.MustCompile(`(?s)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----|$)`)
	// 密码：关键词 + 赋值符 + 值，值一律盖（不看熵，宁可多盖）。留下「password=」让人看得懂。
	// 键可以带引号（JSON 的 "password": "…"），值带引号时整段引号内都盖（含空格、转义）。
	rePassword = regexp.MustCompile(`(?i)(\b[\w.-]{0,20}?(?:passw(?:or)?d|passwd|pwd)[\w.-]{0,20}["']?\s{0,3}(?:=|:=|=>|:)\s{0,3}|(?:密码|口令)\s{0,3}(?:[:：=]|是|为)\s{0,3})(` + quotedOrBare + `)`)
	// Bearer <token>：留下 "Bearer "，盖掉令牌。
	reBearer = regexp.MustCompile(`(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]{16,}=*`)
	// 网址里的「用户:密码@」：整个用户信息换掉（留着用户名也是信息）。
	reURLCreds = regexp.MustCompile(`([A-Za-z][A-Za-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@`)
	// gitleaks generic-api-key 的改写：关键词 + 赋值符 + 值。只盖值，留下「api_key=」让人看得懂。
	reGenericKV = regexp.MustCompile(`(?i)(\b[\w.-]{0,40}?(?:access|auth|api|credential|creds|key|secret|token)[\w.-]{0,20}["']?\s{0,3}(?:=|:=|=>|:)\s{0,3})("(?:[^"\\]|\\.){8,150}"|'(?:[^'\\]|\\.){8,150}'|[A-Za-z0-9_.+/=~@#$%^&*!-]{8,150})`)
	// 身份证：17 位数字 + 校验位（数字或 X）。
	reCNID = regexp.MustCompile(`\b\d{17}[\dXx]\b`)
	// 银行卡候选：13~19 位数字，组间可以有空格 / 连字符。真假靠 cardSpan 里的前缀 + Luhn。
	reCardCand = regexp.MustCompile(`\b\d+(?:[ -]\d+)*\b`)
)

// scrubSecrets 是强制脱敏的唯一入口。幂等：替换出来的「[已隐藏:…]」不会再被任何规则命中。
// 顺序：私钥（最长）→ 具体厂商的令牌 → 网址凭据 / Bearer → 密码 → 通用 key=value → 身份证 → 银行卡。
// 身份证在银行卡前：18 位身份证号也可能碰巧过 Luhn。
func scrubSecrets(s string) string {
	if mandatoryPrivateKeys {
		s = rePrivateKey.ReplaceAllString(s, hiddenKey)
	}
	if mandatorySecrets {
		for _, r := range secretRules {
			s = r.re.ReplaceAllString(s, hiddenSecret)
		}
		s = reURLCreds.ReplaceAllString(s, "${1}"+hiddenSecret+"@")
		s = reBearer.ReplaceAllString(s, "${1}"+hiddenSecret)
	}
	if mandatoryPasswords {
		s = rePassword.ReplaceAllStringFunc(s, func(m string) string {
			g := rePassword.FindStringSubmatch(m)
			return g[1] + hideValue(g[2], hiddenPassword)
		})
	}
	if mandatorySecrets {
		s = reGenericKV.ReplaceAllStringFunc(s, func(m string) string {
			g := reGenericKV.FindStringSubmatch(m)
			if looksRandom(strings.Trim(g[2], `"'`)) {
				return g[1] + hideValue(g[2], hiddenSecret)
			}
			return m
		})
	}
	if mandatoryCNIDs {
		s = reCNID.ReplaceAllStringFunc(s, func(m string) string {
			if validCNID(m) {
				return hiddenID
			}
			return m
		})
	}
	if mandatoryBankCards {
		s = reCardCand.ReplaceAllStringFunc(s, scrubCards)
	}
	return s
}

// looksRandom：token、key、auth 这类关键词后面的值要像随机串才盖——同时有字母和数字、
// 熵 ≥ 3.5（gitleaks generic-api-key 的门槛），否则「Hotkey: Ctrl+Shift+P」
// 「max tokens: 4096」「Tokenizer: bpe」都会被误盖。
func looksRandom(v string) bool {
	return strings.ContainsAny(v, "0123456789") &&
		strings.ContainsAny(strings.ToLower(v), "abcdefghijklmnopqrstuvwxyz") && entropy(v) >= 3.5
}

func entropy(s string) float64 {
	n := map[rune]float64{}
	for _, r := range s {
		n[r]++
	}
	total := float64(len([]rune(s)))
	e := 0.0
	for _, c := range n {
		p := c / total
		e -= p * math.Log2(p)
	}
	return e
}

// validCNID：GB 11643 校验位 + 出生日期看起来像日期（18xx~20xx 年、01~12 月、01~31 日）。
// 只查校验位的话，任意 18 位数字（订单号）有 1/11 的机会被误认。
func validCNID(s string) bool {
	w := []int{7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2}
	sum := 0
	for i := 0; i < 17; i++ {
		sum += int(s[i]-'0') * w[i]
	}
	if "10X98765432"[sum%11] != strings.ToUpper(s[17:])[0] {
		return false
	}
	y, m, d := s[6:10], s[10:12], s[12:14]
	return (y[:2] == "18" || y[:2] == "19" || y[:2] == "20") && m >= "01" && m <= "12" && d >= "01" && d <= "31"
}

// scrubCards 在一串用空格 / 连字符分组的数字里找银行卡：任意连续几组拼起来 13~19 位、
// 发卡行前缀像银行卡（2~6 开头：Visa 4、万事达 5 / 2、银联 62、运通 34/37、JCB 35、Discover 6）、
// 过 Luhn 校验，就把这几组换掉。按组找而不是整串判断，是为了「电话 13 位 + 卡号」
// 这种挨在一起的串里照样认得出卡号。组内不拆：卡号不会和别的数字无分隔地粘在一起。
func scrubCards(m string) string {
	var groups []string // 交替：数字组、分隔符、数字组……
	start := 0
	for i := 0; i < len(m); i++ {
		if m[i] == ' ' || m[i] == '-' {
			groups = append(groups, m[start:i], m[i:i+1])
			start = i + 1
		}
	}
	groups = append(groups, m[start:])
	var out strings.Builder
	for i := 0; i < len(groups); i += 2 {
		hit := -1
		digits := ""
		for j := i; j < len(groups); j += 2 {
			digits += groups[j]
			if len(digits) > 19 {
				break
			}
			if len(digits) >= 13 && validCard(digits) {
				hit = j // 取最长的一段（继续往后看）
			}
		}
		if hit < 0 {
			out.WriteString(groups[i])
			if i+1 < len(groups) {
				out.WriteString(groups[i+1])
			}
			continue
		}
		out.WriteString(hiddenCard)
		if hit+1 < len(groups) {
			out.WriteString(groups[hit+1])
		}
		i = hit
	}
	return out.String()
}

func validCard(d string) bool {
	if d[0] < '2' || d[0] > '6' {
		return false
	}
	sum := 0
	for i := len(d) - 1; i >= 0; i-- {
		n := int(d[i] - '0')
		if (len(d)-1-i)%2 == 1 {
			n *= 2
			if n > 9 {
				n -= 9
			}
		}
		sum += n
	}
	return sum%10 == 0
}
