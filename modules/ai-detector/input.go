package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os/exec"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// 键盘鼠标活动（ai-detector 契约「键盘鼠标活动」）：只存「这 10 秒有没有碰过」，不读任何按键内容。
// Linux GNOME：读 org.gnome.Mutter.IdleMonitor 的 GetIdletime（gdbus，不加依赖）。

const (
	inputBin       = 10 * time.Second
	inputKeep      = 6 * time.Hour
	inputFlushEach = time.Minute
	inputMaxBins   = 8640
	// gdbus 卡住（会话总线不响应）不能拖住采样：超时按「这一格无法判断」处理，不记活动。
	inputCmdTimeout = 2 * time.Second
)

// remoteInput：网页设置里的 inputActivity（没设 / 老 nexus-core = false）。applyRemoteSettings 更新。
var remoteInput atomic.Bool

// idleTime 读当前空闲时间；测试换掉。命令不存在 → exec.ErrNotFound。
var idleTime = func() (time.Duration, error) {
	ctx, cancel := context.WithTimeout(context.Background(), inputCmdTimeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, "gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
		"--object-path", "/org/gnome/Mutter/IdleMonitor/Core", "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime").Output()
	if err != nil {
		return 0, err
	}
	return parseIdle(string(out))
}

var inputGOOS = runtime.GOOS

// parseIdle：`(uint64 1234,)` → 1234 毫秒。
func parseIdle(s string) (time.Duration, error) {
	var ms uint64
	if _, err := fmt.Sscanf(strings.TrimSpace(s), "(uint64 %d,)", &ms); err != nil {
		return 0, fmt.Errorf("GetIdletime 输出看不懂：%q", strings.TrimSpace(s))
	}
	return time.Duration(ms) * time.Millisecond, nil
}

type inputTracker struct {
	mu          sync.Mutex
	bins        map[int64]bool // 有活动、还没确认送达的格（UNIX 秒）
	unsupported bool
	gone        bool
	q           quietLog
}

func newInputTracker() *inputTracker { return &inputTracker{bins: map[int64]bool{}} }

// sample 读一次空闲时间，空闲 < 一格 = 这一格有活动。
func (t *inputTracker) sample(now time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.unsupported || t.gone {
		return
	}
	if inputGOOS != "linux" {
		log.Print("此系统暂不支持键盘鼠标活动")
		t.unsupported = true
		return
	}
	idle, err := idleTime()
	if errors.Is(err, exec.ErrNotFound) {
		log.Print("此系统暂不支持键盘鼠标活动")
		t.unsupported = true
		return
	}
	if err != nil {
		t.q.printf("读不到键盘鼠标空闲时间（跳过这一格）：%v", err)
		return
	}
	t.q.last = ""
	if idle < inputBin { // 记最后一次活动所在的格，不是采样时刻所在的格
		t.bins[now.Add(-idle).Unix()/10*10] = true
	}
	t.trim(now)
}

func (t *inputTracker) trim(now time.Time) {
	old := now.Add(-inputKeep).Unix()
	for b := range t.bins {
		if b < old || b > now.Unix()+60 { // 服务端要求格在 [现在-7天, 现在+60秒] 内，越界整批 422
			delete(t.bins, b)
		}
	}
}

func (t *inputTracker) reset() {
	t.mu.Lock()
	t.bins = map[int64]bool{}
	t.mu.Unlock()
}

// flush 把攒着的格传上去（每次 ≤ 8640 个）；只在 2xx 后删。404 → 这次运行内不再传。
// 请求期间不持锁：采样不被网络拖住。
func (t *inputTracker) flush(cfg Config, c *http.Client, base string, now time.Time) {
	t.mu.Lock()
	t.trim(now)
	all := make([]int64, 0, len(t.bins))
	for b := range t.bins {
		all = append(all, b)
	}
	gone := t.gone
	t.mu.Unlock()
	if gone || len(all) == 0 {
		return
	}
	sort.Slice(all, func(i, j int) bool { return all[i] < all[j] })
	for k := 0; k < len(all); k += inputMaxBins {
		part := all[k:min(k+inputMaxBins, len(all))]
		b, _ := json.Marshal(map[string]any{"deviceId": cfg.DeviceID, "bins": part})
		_, code, err := postJSON(c, cfg.DeviceToken, base+"/api/core/activity/input", b)
		t.mu.Lock()
		if code == http.StatusNotFound {
			log.Print("服务端没有键盘鼠标活动端点（404），这次运行内不再传")
			t.gone = true
			t.bins = map[int64]bool{}
			t.mu.Unlock()
			return
		}
		if err != nil {
			t.mu.Unlock()
			log.Printf("键盘鼠标活动没传出去（留着下次再传，至多 6 小时）：%v", err)
			return
		}
		for _, x := range part {
			delete(t.bins, x)
		}
		t.mu.Unlock()
	}
}

// inputLoop：采样用固定 Ticker（不漂移），上传（拉设置 + POST）在另一个 goroutine 里，互不阻塞。
func inputLoop(p paths, hc *http.Client) {
	t := newInputTracker()
	done := make(chan struct{})
	go func() {
		defer close(done)
		tk := time.NewTicker(inputFlushEach)
		defer tk.Stop()
		for {
			select {
			case <-loopsStop:
				return
			case <-tk.C:
			}
			cfg, err := readConfig(p)
			if err != nil || !active(cfg) {
				continue
			}
			base := strings.TrimRight(cfg.CockpitURL, "/")
			cockpit := withPolicy(hc, hc.Timeout, sameHostOnly)
			// 传之前再拉一次设置：网页刚关掉就不传、也不留。
			if err := applyRemoteSettings(&cfg, cockpit, base); err == nil {
				if cfg.inputActivity {
					t.flush(cfg, cockpit, base, time.Now())
				} else {
					t.reset()
				}
			}
		}
	}()
	tk := time.NewTicker(inputBin)
	defer tk.Stop()
	for {
		select {
		case <-loopsStop:
			<-done
			return
		case <-tk.C:
		}
		cfg, err := readConfig(p)
		if err != nil {
			continue
		}
		if !active(cfg) || !remoteInput.Load() {
			t.reset() // 关 / 暂停 / 网页上没开：不留内存里的格
			continue
		}
		t.sample(time.Now())
	}
}
