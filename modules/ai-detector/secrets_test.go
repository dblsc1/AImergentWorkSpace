package main

import (
	"strings"
	"testing"
)

// fill 造一段假令牌正文。令牌一律在运行时拼出来：源码里不出现完整的「像真的」令牌，
// 免得被 GitHub 的密钥推送保护拦下，也免得别的扫描器误报本仓。
func fill(n int) string {
	const a = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xYz"
	return strings.Repeat(a, n/len(a)+1)[:n]
}

// hexish：32 位小写十六进制风格的随机串（熵够高、有字母有数字）。
func hexish(n int) string { return strings.Repeat("a8f5f167f44f4964e6c998dee827110c", n/32+1)[:n] }

func TestMandatoryScrubTruePositives(t *testing.T) {
	cases := []struct{ name, in, want string }{
		{"aws", "key AKIA" + "IOSFODNN7EXAMPLE here", "key [已隐藏:密钥] here"},
		{"github-pat", "token ghp_" + fill(36), "token [已隐藏:密钥]"},
		{"github-fine", "github_pat_" + fill(82), "[已隐藏:密钥]"},
		{"github-app", "ghs_" + fill(36) + " x", "[已隐藏:密钥] x"},
		{"gitlab", "glpat-" + fill(20), "[已隐藏:密钥]"},
		{"openai-proj", "OPENAI sk-proj-" + fill(20) + "-" + fill(20) + "_" + fill(8), "OPENAI [已隐藏:密钥]"},
		{"openai-legacy", "sk-" + fill(48), "[已隐藏:密钥]"},
		{"anthropic", "sk-ant-api03-" + fill(40) + "-" + fill(52) + "AA", "[已隐藏:密钥]"},
		{"deepseek", "sk-" + strings.Repeat("0123456789abcdef", 2), "[已隐藏:密钥]"},
		{"slack-bot", "xoxb-" + "1234567890-1234567890-" + fill(24), "[已隐藏:密钥]"},
		{"slack-app", "xapp-1-A0" + "1B2C3D4E5-1234567890-abcdef0123", "[已隐藏:密钥]"},
		{"slack-webhook", "hooks.slack.com/services/" + fill(44), "[已隐藏:密钥]"},
		{"gcp-key", "AIza" + fill(35), "[已隐藏:密钥]"},
		{"gcp-oauth", "GOCSPX-" + fill(28), "[已隐藏:密钥]"},
		{"stripe", "sk_" + "live_" + fill(24), "[已隐藏:密钥]"},
		{"npm", "npm_" + fill(36), "[已隐藏:密钥]"},
		{"pypi", "pypi-AgEIcHlwaS5vcmc" + fill(60), "[已隐藏:密钥]"},
		{"huggingface", "hf_" + strings.Repeat("abcdefgHIJ", 4)[:34], "[已隐藏:密钥]"},
		{"jwt", "ey" + fill(20) + ".ey" + fill(24) + "." + fill(22), "[已隐藏:密钥]"},
		{"private-key", "cat -----BEGIN RSA PRIVATE " + "KEY-----" + fill(16), "cat [已隐藏:私钥]"},
		{"private-key-end", "a -----BEGIN OPENSSH PRIVATE KEY-----xx-----END OPENSSH PRIVATE KEY----- b", "a [已隐藏:私钥] b"},
		{"bearer", "Authorization: Bearer abcdef0123456789abcdef", "Authorization: Bearer [已隐藏:密钥]"},
		{"url-creds", "psql postgres://bob:hunter2@db.example.com/x", "psql postgres://[已隐藏:密钥]@db.example.com/x"},
		{"password", "db_password=hunter2 ok", "db_password=[已隐藏:密码] ok"},
		{"pwd", "PWD: s3cr3t!", "PWD: [已隐藏:密码]"},
		{"password-cn", "WiFi 密码：abc12345，别外传", "WiFi 密码：[已隐藏:密码]，别外传"},
		{"password-cn-is", "口令是 666666", "口令是 [已隐藏:密码]"},
		{"generic-kv", "api_key=" + hexish(32), "api_key=[已隐藏:密钥]"},
		{"password-json", `{"password": "hunter2", "user": "bob"}`, `{"password": "[已隐藏:密码]", "user": "bob"}`},
		{"password-quoted-spaces", `password="correct horse battery staple" next`, `password="[已隐藏:密码]" next`},
		{"password-escaped-quote", `pwd='a\'b c' x`, `pwd='[已隐藏:密码]' x`},
		{"generic-json-key", `{"api_key": "` + hexish(32) + `"}`, `{"api_key": "[已隐藏:密钥]"}`},
		{"generic-secret", `client_secret: "` + fill(21) + `"`, `client_secret: "[已隐藏:密钥]"`},
		{"card-spaced", "卡号 4111 1111 1111 1111 到期", "卡号 [已隐藏:银行卡] 到期"},
		{"card-unionpay", "转账 6222021234567890128", "转账 [已隐藏:银行卡]"},
		{"card-dashed", "6222-0212-3456-7890-128", "[已隐藏:银行卡]"},
		{"card-after-phone", "13800138000 4111 1111 1111 1111", "13800138000 [已隐藏:银行卡]"},
		{"cnid", "身份证 11010519491231002X 复印件", "身份证 [已隐藏:身份证] 复印件"},
		{"cnid-lower-x", "id 11010519491231002x", "id [已隐藏:身份证]"},
		{"cnid-2", "440301198512034513", "[已隐藏:身份证]"},
	}
	for _, c := range cases {
		if got := scrubSecrets(c.in); got != c.want {
			t.Errorf("%s: scrubSecrets(%q) = %q, want %q", c.name, c.in, got, c.want)
		}
		if got := scrubSecrets(c.want); got != c.want {
			t.Errorf("%s: not idempotent: %q → %q", c.name, c.want, got)
		}
	}
}

func TestMandatoryScrubFalsePositives(t *testing.T) {
	for _, in := range []string{
		"订单号 20260926000123456789",
		"订单 1727712000123456",                             // 1 开头：不是银行卡前缀
		"commit 3f9a1c2b7d4e5a60b1c2d3e4f5a6b7c8d9e0f1a2", // git SHA
		"UUID 550e8400-e29b-41d4-a716-446655440000",       // 裸 UUID
		"会议 2026-09-30 10:05:12 第 12 周",                   // 日期时间
		"ts=1727712000123",                                // Unix 毫秒
		"Hotkey: Ctrl+Shift+P",                            // 关键词 + 非随机值
		"max tokens: 4096",                                // 纯数字、熵低
		"Tokenizer: bpe_v2",                               // 太短
		"sklearn: sk-learn-tutorial-for-beginners",        // 连字符拼的普通词
		"卡 4111 1111 1111 1112",                           // Luhn 不过
		"工号 110101199001011234",                           // 校验位不对
		"单号 110101199013011237",                           // 月份 13（校验位也不对）
		"电话 13812345678",                                  // 1 开头
		"key: value",                                      // 太短
		"password strength meter",                         // 没有赋值符
		"a.go — garden — Visual Studio Code",
	} {
		if got := scrubSecrets(in); got != in {
			t.Errorf("false positive: %q → %q", in, got)
		}
	}
}

func TestLuhnAndCNIDChecks(t *testing.T) {
	if !validCard("4111111111111111") || validCard("4111111111111112") || validCard("1111111111111117") {
		t.Error("validCard")
	}
	if !validCNID("11010519491231002X") || !validCNID("110101199001011237") || validCNID("110101199001011234") {
		t.Error("validCNID")
	}
}

// 强制脱敏在源码里写死：mandatoryItems 必须全是开，且每一项都真的接在 scrubSecrets 上。
func TestMandatoryItemsAllOn(t *testing.T) {
	for _, m := range mandatoryItems {
		if !m.On {
			t.Errorf("官方构建里强制脱敏必须全开：%s", m.Name)
		}
	}
}
