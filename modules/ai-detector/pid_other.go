//go:build !unix && !windows

package main

// 没法探测的平台：当作活着。
func pidAlive(pid int) bool { return true }
