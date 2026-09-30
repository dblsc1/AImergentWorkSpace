//go:build !windows

package main

import (
	"errors"
	"os"
	"syscall"
)

// lockFile：非阻塞独占 flock。锁跟着打开的文件走，进程没了系统就收回。
func lockFile(f *os.File) error {
	err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if errors.Is(err, syscall.EWOULDBLOCK) {
		return errLocked
	}
	return err
}
