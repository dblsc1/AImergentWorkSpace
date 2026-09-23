## 装

只需要 Docker（Windows / macOS 用 Docker Desktop）。

**Linux / macOS**

```sh
curl -fsSL https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.sh | sh
```

**Windows**（PowerShell）

```powershell
irm https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.ps1 -OutFile honeycomb-install.ps1
powershell -ExecutionPolicy Bypass -File .\honeycomb-install.ps1
```

装完打开 <http://127.0.0.1:8800/>，登录口令脚本会打印一次，也存在 `honeycomb/.env` 里。
再跑一次同一个脚本 = 升级到这一版（保留配置与数据）。

> Windows 脚本在 CI 里用 PowerShell 7 真跑过；Windows 自带的 PowerShell 5.1 尚未在真机上验证。

想看源码、自己改：clone 仓库，照 README「三十秒跑起来」走源码路线。

## 附件

| 文件 | 用途 |
|---|---|
| `honeycomb-install.sh` / `.ps1` | 一键安装 / 升级 |
| `docker-compose.yml` | 本版组装（镜像 `ghcr.io/dblsc1/honeycomb-*`），安装脚本会下载它 |
