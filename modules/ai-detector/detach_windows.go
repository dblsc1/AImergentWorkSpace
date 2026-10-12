package main

import (
	"os/exec"
	"syscall"
)

// detach：脱离控制台与进程组（DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP），不弹黑窗口，检测程序退出后它还在。
func detach(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x00000008 | 0x00000200, HideWindow: true}
}
