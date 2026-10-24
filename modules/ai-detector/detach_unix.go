//go:build !windows

package main

import (
	"os/exec"
	"syscall"
)

// detach：新会话，不随本进程（及其终端）退出而被杀。
func detach(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true} }
