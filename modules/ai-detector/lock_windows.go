package main

import (
	"os"
	"syscall"
	"unsafe"
)

// 标准库 syscall 没包 LockFileEx，直接从 kernel32 取（不为一个函数引 golang.org/x/sys）。
var procLockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")

const (
	lockfileFailImmediately = 0x1
	lockfileExclusiveLock   = 0x2
	errorLockViolation      = syscall.Errno(33)
)

// lockFile：非阻塞独占锁。锁的是文件 4 GiB 处的 1 个字节（不是内容所在的区间），
// 别的进程照样能读出里面的 pid 给人看。句柄关了 / 进程没了系统就收回。
func lockFile(f *os.File) error {
	ol := syscall.Overlapped{OffsetHigh: 1}
	r, _, err := procLockFileEx.Call(f.Fd(), lockfileExclusiveLock|lockfileFailImmediately, 0, 1, 0, uintptr(unsafe.Pointer(&ol)))
	if r != 0 {
		return nil
	}
	if err == errorLockViolation {
		return errLocked
	}
	return err
}
