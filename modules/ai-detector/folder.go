package main

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

// 终端标题里的本机路径：盘符 / ~ 开头、单独的 ~，或 / 开头至少两级（user@host: ~/x 也在里面）。
// 前一个字符不能是字母数字、下划线、斜杠（同 rePath：and/or、10:30/11:00 不算）。
var reFolderPath = regexp.MustCompile(`(^|[^\w/\\])((?:[A-Za-z]:|~)[\\/][^\s"'<>]*|~(?:$|[\s"'<>])|/(?:[^\s\\/"'<>]+[\\/])+[^\s"'<>]*)`)

// folderOf：标题里第一条本机路径的最后一层目录名，超过 64 个字符截到 64 个。没有、或是根目录 = ""。
// 传进来的标题要已经过强制脱敏（fragment.Raw）。
func folderOf(title string) string {
	m := reFolderPath.FindStringSubmatch(title)
	if m == nil {
		return ""
	}
	var last string
	for _, c := range strings.FieldsFunc(m[2], func(r rune) bool { return r == '/' || r == '\\' }) {
		last = c
	}
	last = strings.Trim(last, " \t\"'<>")
	if last == "" || strings.HasSuffix(last, ":") { // C:\ 这种根
		return ""
	}
	if utf8.RuneCountInString(last) > 64 {
		last = string([]rune(last)[:64])
	}
	return last
}

// folderFor：上传记录的 folder（契约「二·2」）。程序不在终端名单、是浏览器，隐私选项会把文件名也去掉
// （paths off、titles drop / pseudonymize），或这条记录实际发出的标题是空的（如只发程序名）时不给。
// raw 是脱敏前的标题；取目录名前先过用户名 / 主机名代号（同标题的 pii 处理）。
func (r redactor) folderFor(app, raw, sent string) string {
	a := normApp(app)
	if !r.terminals[a] || r.browsers[a] || sent == "" || r.p.Paths == "off" || r.p.Titles == "drop" || r.p.Titles == "pseudonymize" {
		return ""
	}
	return folderOf(r.pii(raw))
}
