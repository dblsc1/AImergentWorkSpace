// ai-detector：读本机 ActivityWatch，合并成段、本机脱敏、附分类建议，
// 上传成 HoneyComb 的「待确认建议」。契约见 module_docs/contract.md。
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode"
)

const usage = `用法: ai-detector <命令>

  init                 写一份默认配置（同步默认关闭）和规则文件样例
  run                  常驻：每 intervalMinutes 分钟同步一轮
  once                 只跑一轮（调试用）
  status               看配置与上一轮结果
  enable | disable     打开 / 关闭同步
  pause | resume       暂停 / 恢复（暂停期间的活动以后也不补传）
  autostart install    开机自启（Windows 启动文件夹 / macOS LaunchAgent / Linux autostart）
  autostart uninstall  取消开机自启
  archive [YYYY-MM-DD] 看某天（缺省今天）发出去了什么：原始标题（只过强制脱敏）与实际发出的并排
  preview [N]          最近 N 段（缺省 20）按现在的隐私选项会变成什么（只在本机，不联网）
  preview --app <程序> --title <标题>   试一条样例标题
  pseudonyms           看标题代号对照表（只在本机）

配置目录：%APPDATA%\honeycomb、~/Library/Application Support/honeycomb 或 ~/.config/honeycomb，
环境变量 AI_DETECTOR_HOME 可覆盖。
`

func main() {
	if err := cli(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "错误：", err)
		os.Exit(1)
	}
}

func cli(args []string, out io.Writer) error {
	if len(args) == 0 {
		fmt.Fprint(out, usage)
		return nil
	}
	dir, err := configDir()
	if err != nil {
		return err
	}
	p := pathsIn(dir)
	hc := &http.Client{Timeout: 15 * time.Second}

	switch args[0] {
	case "init":
		if _, err := os.Stat(p.config); err == nil {
			fmt.Fprintf(out, "配置已存在，没有覆盖：%s\n", p.config)
			return nil
		}
		cfg, err := defaultConfig(dir)
		if err != nil {
			return err
		}
		if err := saveJSON(p.config, cfg); err != nil {
			return err
		}
		if _, err := os.Stat(cfg.RulesFile); errors.Is(err, os.ErrNotExist) {
			_ = os.WriteFile(cfg.RulesFile, []byte(rulesExample), 0o600)
		}
		fmt.Fprintf(out, "已写入 %s\n同步默认关闭：填好 cockpitUrl 和 deviceToken 后执行 ai-detector enable。\n", p.config)
		return nil
	case "once", "run":
		// 同一个配置目录只许一个进程同步：run 常驻期间一直拿着锁，once 拿不到就拒绝。
		release, err := acquireLock(p.lock)
		if err != nil {
			return err
		}
		defer release()
		if args[0] == "once" {
			return runOnce(p, hc, out)
		}
		return runLoop(p, hc)
	case "status":
		return status(p, out)
	case "archive":
		day := time.Now().Format("2006-01-02")
		if len(args) > 1 {
			day = args[1]
		}
		return printArchive(dir, day, out)
	case "pseudonyms":
		return printPseudonyms(filepath.Join(dir, "pseudonyms.json"), out)
	case "preview":
		cfg, err := readConfig(p)
		if err != nil {
			return err
		}
		if err := cfg.Privacy.check(); err != nil {
			return err
		}
		fs := flag.NewFlagSet("preview", flag.ContinueOnError)
		fs.SetOutput(out)
		app := fs.String("app", "", "程序名")
		title := fs.String("title", "", "窗口标题")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if *title != "" || *app != "" {
			ps, _ := loadPseudonyms("")
			fmt.Fprintf(out, "原始（强制脱敏后）：%s\n发出：%s\n", scrubSecrets(*title), previewOne(newRedactor(cfg), ps, *app, *title))
			return nil
		}
		n := 20
		if fs.NArg() > 0 {
			if n, err = strconv.Atoi(fs.Arg(0)); err != nil || n < 1 {
				return fmt.Errorf("用法: ai-detector preview [N]")
			}
		}
		fmt.Fprintln(out, "注意：只按本机配置文件预览；网页上设过的话，以网页为准（run 每轮会拉）。")
		return preview(cfg, n, out)
	case "enable", "disable", "pause", "resume":
		cfg, err := readConfig(p)
		if err != nil {
			return err
		}
		switch args[0] {
		case "enable":
			cfg.Enabled = true
		case "disable":
			cfg.Enabled = false
		case "pause":
			cfg.Paused = true
		case "resume":
			cfg.Paused = false
		}
		if err := saveJSON(p.config, cfg); err != nil {
			return err
		}
		// 关掉 / 暂停时顺手把状态记成「不活跃」：常驻进程要是这会儿没在跑，
		// 下次恢复时游标照样会跳到现在，关着的那段不会被补传。
		//
		// run 正在跑（锁被占着）时不碰状态文件：它下一轮读到新配置自己会记，两个进程
		// 同时写状态反而可能把它刚推进的游标写回旧值。
		if !cfg.Enabled || cfg.Paused {
			release, lerr := acquireLock(p.lock)
			if lerr != nil {
				fmt.Fprintf(out, "enabled=%v paused=%v（常驻进程下一轮生效）\n", cfg.Enabled, cfg.Paused)
				return nil
			}
			defer release()
			var st State
			_ = loadJSON(p.state, &st)
			st.Active = false
			if err := saveJSON(p.state, st); err != nil {
				return err
			}
		}
		fmt.Fprintf(out, "enabled=%v paused=%v\n", cfg.Enabled, cfg.Paused)
		return nil
	case "autostart":
		if len(args) < 2 || (args[1] != "install" && args[1] != "uninstall") {
			return fmt.Errorf("用法: ai-detector autostart install|uninstall")
		}
		exe, err := os.Executable()
		if err != nil {
			return err
		}
		path, content, err := autostartFile(runtime.GOOS, exe)
		if err != nil {
			return err
		}
		if args[1] == "uninstall" {
			if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
				return err
			}
			fmt.Fprintf(out, "已删除 %s\n", path)
			return nil
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			return err
		}
		fmt.Fprintf(out, "已写入 %s，下次登录时自动启动。\n", path)
		return nil
	}
	fmt.Fprint(out, usage)
	return fmt.Errorf("不认识的命令：%s", args[0])
}

func readConfig(p paths) (Config, error) {
	var cfg Config
	if err := loadJSON(p.config, &cfg); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return cfg, fmt.Errorf("还没有配置，先执行 ai-detector init")
		}
		return cfg, fmt.Errorf("配置 %s 读不了：%w", p.config, err)
	}
	cfg.dir = filepath.Dir(p.config)
	if b, err := os.ReadFile(p.config); err == nil {
		cfg.warnings = unknownKeys(b)
	}
	return cfg, nil
}

func runOnce(p paths, hc *http.Client, out io.Writer) error {
	cfg, err := readConfig(p)
	if err != nil {
		return err
	}
	for _, k := range cfg.warnings {
		log.Printf("配置里的 %s 不认识，已忽略（强制脱敏没有配置开关，见 README）", k)
	}
	purgeArchive(cfg.dir, cfg.ArchiveDays, time.Now())
	var st State
	_ = loadJSON(p.state, &st) // 没有状态文件 = 第一次跑
	msg, err := tick(cfg, &st, time.Now(), hc)
	if err != nil {
		st.LastOutcome = "失败：" + err.Error()
	} else {
		st.LastOutcome = msg
	}
	if serr := saveJSON(p.state, st); serr != nil {
		return serr
	}
	if out == nil {
		log.Print(st.LastOutcome)
	} else if err == nil {
		fmt.Fprintln(out, msg)
	}
	return err
}

func runLoop(p paths, hc *http.Client) error {
	// 常驻模式通常没有终端（开机自启），日志同时写一份文件；超过 5 MB 启动时清空，
	// 免得跑一年撑满磁盘。
	if fi, err := os.Stat(p.log); err == nil && fi.Size() > 5<<20 {
		_ = os.Truncate(p.log, 0)
	}
	if f, err := os.OpenFile(p.log, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err == nil {
		defer f.Close()
		log.SetOutput(io.MultiWriter(os.Stderr, f))
	}
	log.Print("ai-detector 启动")
	for {
		_ = runOnce(p, hc, nil) // 失败已写进日志与状态；常驻进程不因一轮失败退出
		interval := 5.0
		if cfg, err := readConfig(p); err == nil && cfg.IntervalMinutes > 0 {
			interval = cfg.IntervalMinutes
		}
		time.Sleep(minutes(interval))
	}
}

func status(p paths, out io.Writer) error {
	cfg, err := readConfig(p)
	if err != nil {
		return err
	}
	var st State
	_ = loadJSON(p.state, &st)
	tok := "（未设置）"
	if cfg.DeviceToken != "" {
		tok = "（已设置）"
	}
	fmt.Fprintf(out, "配置      %s\nenabled   %v\npaused    %v\ncockpit   %s\n设备令牌  %s\ndeviceId  %s\n",
		p.config, cfg.Enabled, cfg.Paused, cfg.CockpitURL, tok, cfg.DeviceID)
	pr := cfg.Privacy
	orDefault := func(v, d string) string {
		if v == "" {
			return d
		}
		return v
	}
	fmt.Fprintf(out, "隐私选项  路径=%s 标题=%s 浏览器=%s（本机配置；网页上设过的话以网页为准）\n",
		orDefault(pr.Paths, "full"), orDefault(pr.Titles, "keep"), orDefault(pr.Browser, "domain"))
	if err := pr.check(); err != nil {
		fmt.Fprintf(out, "配置错误  %v（这样的配置不会上传）\n", err)
	}
	fmt.Fprintln(out, "强制脱敏（关不掉）：")
	for _, m := range mandatoryItems {
		state := "开"
		if !m.On {
			state = "关（本程序是改过源码自己编译的）"
		}
		fmt.Fprintf(out, "  %s  %s\n", state, m.Name)
	}
	for _, k := range cfg.warnings {
		fmt.Fprintf(out, "警告      配置里的 %s 不认识，已忽略\n", k)
	}
	if !st.LastRunAt.IsZero() {
		fmt.Fprintf(out, "上一轮    %s  %s\n", isoTime(st.LastRunAt), st.LastOutcome)
	}
	if !st.Cursor.IsZero() {
		fmt.Fprintf(out, "已处理到  %s\n", isoTime(st.Cursor))
	}
	return nil
}

const rulesExample = `{
  "rules": [
    { "app": "code|goland|idea", "title": "garden", "taskId": "把这里换成任务 id", "confidence": 0.9 }
  ]
}
`

// autostartFile 返回该系统的自启文件路径和内容。按 goos 分支而不是 build tag：
// 一个函数三种输出，测试在任何系统上都能把三种都测到。
func autostartFile(goos, exe string) (string, string, error) {
	// 程序路径要原样嵌进三种文件格式（VBScript 字符串、plist XML、Desktop Entry 的 Exec），
	// 每种的转义规则都不同。与其逐种转义出错，不如拒绝会出问题的字符：正常安装路径不含它们。
	// Desktop Entry 规范里 Exec 的双引号内 " ` $ \ 要反斜杠转义、% 要写成 %%，一律不收。
	bad := "\"`$%\\"
	if goos != "linux" && goos != "freebsd" && goos != "openbsd" && goos != "netbsd" {
		bad = "\"" // Windows / macOS 路径里的反斜杠、$、% 在各自格式里都安全，只有引号不行
	}
	for _, r := range exe {
		if unicode.IsControl(r) || strings.ContainsRune(bad, r) {
			return "", "", fmt.Errorf("程序路径 %q 含有 %q，写进自启文件不安全；把程序挪到普通路径（不含引号、$、%%、反斜杠、控制字符）再装", exe, r)
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", "", err
	}
	switch goos {
	case "windows":
		// 用 .vbs 而不是 .cmd：.cmd 开机会弹一个黑窗口挂着。Run 的第二个参数 0 = 隐藏窗口。
		appdata := os.Getenv("APPDATA")
		if appdata == "" {
			appdata = filepath.Join(home, "AppData", "Roaming")
		}
		path := filepath.Join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "honeycomb-ai-detector.vbs")
		return path, "CreateObject(\"WScript.Shell\").Run \"\"\"" + exe + "\"\" run\", 0, False\r\n", nil
	case "darwin":
		path := filepath.Join(home, "Library", "LaunchAgents", "com.honeycomb.ai-detector.plist")
		return path, `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.honeycomb.ai-detector</string>
  <key>ProgramArguments</key><array><string>` + xmlEscape(exe) + `</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
`, nil
	default:
		cfg := os.Getenv("XDG_CONFIG_HOME")
		if cfg == "" {
			cfg = filepath.Join(home, ".config")
		}
		path := filepath.Join(cfg, "autostart", "honeycomb-ai-detector.desktop")
		return path, "[Desktop Entry]\nType=Application\nName=HoneyComb ai-detector\nExec=\"" + exe + "\" run\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n", nil
	}
}

// xmlEscape：控制字符已在 autostartFile 开头拒掉，这里只剩 XML 的三个特殊字符。
func xmlEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(s)
}
