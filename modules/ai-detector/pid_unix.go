//go:build unix

package main

import (
	"errors"
	"syscall"
)

// pidAlive：信号 0 只探测。没权限发（EPERM）也说明进程在。
func pidAlive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}
