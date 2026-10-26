package main

import (
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// ── 编号代号（privacy.numberedAliases）──────────────────────────────────────
// 对照表 aliases.json：{类型: {原值: 代号}}，只在本机。类型就是代号的前缀（本机IP / 局域网IP / 组网IP /
// 公网IP / 邮箱 / 用户 / 主机 / 网站），各自从 1 编号。契约「编号代号」。

var defaultNumbered = []string{"ips", "emails", "users", "hosts"}

var numberedValues = []string{"ips", "emails", "users", "hosts", "domains"}

// numberedSet：缺省（没写）= defaultNumbered；写了 [] = 空。
func (p Privacy) numberedSet() map[string]bool {
	l := p.NumberedAliases
	if l == nil {
		l = defaultNumbered
	}
	m := map[string]bool{}
	for _, v := range l {
		m[v] = true
	}
	return m
}

func (p Privacy) checkNumbered() error {
	for _, v := range p.NumberedAliases {
		ok := false
		for _, o := range numberedValues {
			ok = ok || v == o
		}
		if !ok {
			return fmt.Errorf("privacy.numberedAliases 只能是 %s，现在有 %q", strings.Join(numberedValues, " / "), v)
		}
	}
	return nil
}

type aliases struct {
	mu    sync.Mutex
	path  string // 空 = 只在内存里（预览、测试直接构造 Config）
	m     map[string]map[string]string
	next  map[string]int
	used  map[string]map[string]string // 被用到过的对（预览列出）
	dirty bool
}

func newAliases(path string) *aliases {
	return &aliases{path: path, m: map[string]map[string]string{}, next: map[string]int{}, used: map[string]map[string]string{}}
}

func loadAliases(path string) (*aliases, error) {
	a := newAliases(path)
	// 读不了（坏了）就报错不上传：从 1 重新编号会让旧代号指向新的值。
	if err := loadJSON(path, &a.m); err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("编号代号对照表 %s 读不了（删掉它会从 1 重新编号）：%w", path, err)
	}
	if a.m == nil {
		a.m = map[string]map[string]string{}
	}
	if err := a.validate(); err != nil {
		return nil, fmt.Errorf("编号代号对照表 %s 内容不合法（删掉它会从 1 重新编号）：%w", path, err)
	}
	return a, nil
}

var aliasKinds = []string{"本机IP", "局域网IP", "组网IP", "公网IP", "邮箱", "用户", "主机", "网站"}

// validate：只认已知类型；代号必须是「类型前缀 + 正整数」、同类型内不重复、原值非空；顺便定下一个号。
func (a *aliases) validate() error {
	for kind, t := range a.m {
		if !slices.Contains(aliasKinds, kind) {
			return fmt.Errorf("未知类型 %q", kind)
		}
		seen := map[string]bool{}
		for orig, tok := range t {
			n, err := strconv.Atoi(strings.TrimPrefix(tok, kind))
			if orig == "" || !strings.HasPrefix(tok, kind) || err != nil || n <= 0 || strconv.Itoa(n) != strings.TrimPrefix(tok, kind) || seen[tok] {
				return fmt.Errorf("%s 里有不合法的代号（或原值为空）", kind) // 代号字段可能本身就是原值，不回显
			}
			seen[tok] = true
			a.next[kind] = max(a.next[kind], n)
		}
	}
	return nil
}

// 一个进程里所有脱敏器（同步、在场心跳、状态桥）共用同一张内存表，免得各读各的磁盘、分到同一个号。
var (
	aliasMu    sync.Mutex
	aliasCache = map[string]*aliases{}
)

func sharedAliases(path string) (*aliases, error) {
	aliasMu.Lock()
	defer aliasMu.Unlock()
	if a, ok := aliasCache[path]; ok {
		return a, nil
	}
	a, err := loadAliases(path)
	if err != nil {
		return nil, err
	}
	aliasCache[path] = a
	return a, nil
}

// clone：预览用。带着已有的对，但没有路径，新分的号不落盘。
func (a *aliases) clone() *aliases {
	a.mu.Lock()
	defer a.mu.Unlock()
	c := newAliases("")
	for k, t := range a.m {
		c.m[k] = map[string]string{}
		for o, v := range t {
			c.m[k][o] = v
		}
	}
	for k, n := range a.next {
		c.next[k] = n
	}
	return c
}

func (a *aliases) alias(kind, orig string) string {
	a.mu.Lock()
	defer a.mu.Unlock()
	t := a.m[kind]
	if t == nil {
		t = map[string]string{}
		a.m[kind] = t
	}
	tok, ok := t[orig]
	if !ok {
		a.next[kind]++
		tok = kind + strconv.Itoa(a.next[kind])
		t[orig] = tok
		a.dirty = true
	}
	if a.used[kind] == nil {
		a.used[kind] = map[string]string{}
	}
	a.used[kind][orig] = tok
	return tok
}

func (a *aliases) save() error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.path == "" || !a.dirty {
		return nil
	}
	if err := saveJSON(a.path, a.m); err != nil {
		return err
	}
	a.dirty = false
	return nil
}

// flushAliases：离开本机之前（sendTitles）调用：表读不了或存不了都返回错误，这一轮不上传。
func flushAliases(cfg Config) error {
	if cfg.dir == "" || len(cfg.Privacy.numberedSet()) == 0 {
		return nil
	}
	a, err := sharedAliases(filepath.Join(cfg.dir, "aliases.json"))
	if err != nil {
		return err
	}
	if err := a.save(); err != nil {
		return fmt.Errorf("编号代号对照表存不了，这一轮不上传：%w", err)
	}
	return nil
}

func (a *aliases) printUsed(out io.Writer) {
	if a == nil {
		return
	}
	type row struct {
		kind, tok, orig string
		n               int
	}
	var rows []row
	for kind, t := range a.used {
		for o, tok := range t {
			_, n := splitToken(tok)
			rows = append(rows, row{kind, tok, o, n})
		}
	}
	if len(rows) == 0 {
		return
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].kind != rows[j].kind {
			return rows[i].kind < rows[j].kind
		}
		return rows[i].n < rows[j].n
	})
	fmt.Fprintln(out, "本次用到的编号代号（预览不分配真代号，新值的号只是试编）：")
	for _, r := range rows {
		fmt.Fprintf(out, "  %s  %s\n", r.tok, r.orig)
	}
}

func ipKind(ip net.IP) string {
	if v4 := ip.To4(); v4 != nil {
		ip = v4
		if v4[0] == 100 && v4[1]&0xC0 == 64 { // 100.64.0.0/10
			return "组网IP"
		}
	}
	switch {
	case ip.IsLoopback():
		return "本机IP"
	case ip.IsPrivate(), ip.IsLinkLocalUnicast():
		return "局域网IP"
	}
	return "公网IP"
}

// ipAlias：s 是 hideIPs 已经认定的 IP 文字。
func (r redactor) ipAlias(s string) string {
	if !r.num["ips"] {
		return "[IP]"
	}
	ip := net.ParseIP(s)
	if ip == nil {
		return r.al.alias("公网IP", s)
	}
	return r.al.alias(ipKind(ip), ip.String())
}

func (r redactor) emailAlias(s string) string {
	if !r.num["emails"] {
		return "[邮箱]"
	}
	return r.al.alias("邮箱", strings.ToLower(s))
}

func (r redactor) userAlias(s string) string {
	if !r.num["users"] {
		return "[用户]"
	}
	return r.al.alias("用户", s)
}

func (r redactor) hostAlias(s string) string {
	if !r.num["hosts"] {
		return "[主机]"
	}
	return r.al.alias("主机", s)
}

// domainAlias：网址 / 域名路径里的主机部分。domains 没列或不算域名（没有点、本身是 IP）= 只留主机（端口去掉，
// 与之前一致）；列了 = 网站n，端口保留（port 带冒号或为空）。
func (r redactor) domainAlias(host, port string) string {
	if !r.num["domains"] || !strings.Contains(host, ".") || net.ParseIP(host) != nil {
		return host
	}
	return r.al.alias("网站", strings.ToLower(host)) + port
}

// initAliases 定 r.num 与 r.al。表读不了时 r.al 为空、r.num 清空（退回方括号遮盖，不会漏原值）；
// 真正拒绝上传在 sendTitles 的 flushAliases。cfg.dir 为空（测试直接构造 Config）= 只在内存里。
func (r *redactor) initAliases(c Config) {
	set := c.Privacy.numberedSet()
	r.num = map[string]bool{}
	// 只对开着遮盖的类型生效；domains 本来不遮，列了就换。
	for k, masked := range map[string]bool{"ips": on(c.Privacy.IPs), "emails": on(c.Privacy.Emails), "users": on(c.Privacy.Usernames), "hosts": on(c.Privacy.Usernames), "domains": true} {
		r.num[k] = set[k] && masked
	}
	r.al = newAliases("")
	anyOn := false
	for _, v := range r.num {
		anyOn = anyOn || v
	}
	if c.dir == "" || !anyOn { // 没有要编号的类型：不碰 aliases.json
		return
	}
	a, err := sharedAliases(filepath.Join(c.dir, "aliases.json"))
	if err != nil {
		r.num = map[string]bool{}
		return
	}
	r.al = a
}

// newPreviewRedactor：预览用的脱敏器——表是副本，不落盘。表读不了返回错误。
func newPreviewRedactor(c Config) (redactor, error) {
	if c.dir != "" && len(c.Privacy.numberedSet()) > 0 {
		a, err := sharedAliases(filepath.Join(c.dir, "aliases.json"))
		if err != nil {
			return redactor{}, err
		}
		r := newRedactor(c)
		r.al = a.clone()
		return r, nil
	}
	return newRedactor(c), nil
}

func colon(port string) string {
	if port == "" {
		return ""
	}
	return ":" + port
}
