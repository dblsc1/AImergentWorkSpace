package main

// v0.6：桌宠事件的两种发送（desktop.pet.v1「三」）：http（原样 POST）与 dsh-pet（work-status / lock→broadcast→release）。
// 这里拿不到 Config：HoneyComb 的设备令牌进不来，也就绝不会发给美术实现。
// 发失败只写日志、不重试。dsh-pet 的请求体见 third_party/dsh-pet/openapi.yaml。

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	dshPrefix   = "/dsh-pet-7340"
	dshTaskMax  = 40 // work-status 的 task 至多 40 个码点
	petSinkWait = 5 * time.Second
	dshFirstPet = "main" // 取不到 /config 时的上游缺省宠物 id
)

func petNoRedirect(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }

// petURLOK：回环（localhost / 127.0.0.0/8 / ::1）或 https；其余不发。
func petURLOK(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return fmt.Errorf("pet.url 不是有效地址：%q", raw)
	}
	switch u.Scheme {
	case "https":
		return nil
	case "http":
		h := u.Hostname()
		if h == "localhost" {
			return nil
		}
		if ip := net.ParseIP(h); ip != nil && ip.IsLoopback() {
			return nil
		}
	}
	return fmt.Errorf("pet.url 只能是本机回环或 https，现在是 %s://%s", u.Scheme, u.Hostname())
}

// petSend：发一条，失败只写日志。
func petSend(hc *http.Client, pc PetConfig, ev PetEvent) {
	if err := petURLOK(pc.target()); err != nil {
		log.Printf("桌宠事件没发（%s）：%v", ev.Type, err)
		return
	}
	c := withPolicy(hc, petSinkWait, petNoRedirect)
	var err error
	if pc.renderer() == "dsh-pet" {
		err = dshSend(c, pc, ev)
	} else {
		err = httpSend(c, pc, ev)
	}
	if err != nil {
		log.Printf("桌宠事件没发出去（%s，不重试）：%v", ev.Type, err)
	}
}

func httpSend(c *http.Client, pc PetConfig, ev PetEvent) error {
	b, _ := json.Marshal(ev)
	req, err := http.NewRequest("POST", pc.target(), bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if pc.Token != nil && *pc.Token != "" {
		req.Header.Set("Authorization", "Bearer "+*pc.Token)
	}
	_, err = petDo(c, req)
	return err
}

// petDo：非 2xx 算错（含不跟随的 3xx）；返回最多 1 MB 的响应体。
func petDo(c *http.Client, req *http.Request) ([]byte, error) {
	resp, err := c.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("%s %s 返回 %d", req.Method, req.URL.Path, resp.StatusCode)
	}
	return body, nil
}

// dshCall：POST JSON；dsh-pet 的业务失败是 200 + {ok:false,reason}，也算错。out 非 nil 时解出响应体。
func dshCall(c *http.Client, method, u string, body any, out any) error {
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, u, rd)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	b, err := petDo(c, req)
	if err != nil {
		return err
	}
	var r struct {
		OK     *bool  `json:"ok"`
		Reason string `json:"reason"`
	}
	if json.Unmarshal(b, &r) == nil && r.OK != nil && !*r.OK {
		return fmt.Errorf("%s %s 被拒：%s", method, req.URL.Path, r.Reason)
	}
	if out != nil {
		return json.Unmarshal(b, out)
	}
	return nil
}

// dshPetID：配了就用配的；没配取 /config 里 main 条目的第一只，取不到用 main。
func dshPetID(c *http.Client, pc PetConfig, base string) string {
	if pc.PetID != nil && *pc.PetID != "" {
		return *pc.PetID
	}
	var cfg map[string]struct {
		Pets []struct {
			ID string `json:"id"`
		} `json:"pets"`
	}
	if dshCall(c, "GET", base+"/config", nil, &cfg) == nil {
		if e := cfg["main"]; len(e.Pets) > 0 && e.Pets[0].ID != "" {
			return e.Pets[0].ID
		}
	}
	return dshFirstPet
}

func dshSend(c *http.Client, pc PetConfig, ev PetEvent) error {
	base := strings.TrimRight(pc.target(), "/") + dshPrefix
	if ev.Type == "focus" && ev.Data["afk"] == true {
		return dshCall(c, "DELETE", base+"/work-status", nil, nil)
	}
	if st := pc.Moods[ev.Mood]; st != nil && *st != "" {
		if err := dshCall(c, "POST", base+"/work-status", map[string]any{"state": *st, "task": petClip(ev.Text, dshTaskMax)}, nil); err != nil {
			return err
		}
	}
	if ev.Type == "focus" {
		return nil
	}
	petID := dshPetID(c, pc, base)
	pet := "?pet=" + url.QueryEscape(petID) + "&surface=any"
	var lock struct {
		LockID string `json:"lockId"`
	}
	if err := dshCall(c, "POST", base+"/lock"+pet, map[string]any{}, &lock); err != nil {
		return err
	}
	if lock.LockID == "" {
		return errors.New("lock 没返回 lockId")
	}
	err := dshCall(c, "POST", base+"/broadcast"+pet+"&lock="+url.QueryEscape(lock.LockID), map[string]any{"text": ev.Text}, nil)
	// 不管气泡成败都放锁（尽力）
	if rerr := dshCall(c, "POST", base+"/lock/release?pet="+url.QueryEscape(petID), map[string]any{"lockId": lock.LockID}, nil); rerr != nil && err == nil {
		err = rerr
	}
	return err
}
