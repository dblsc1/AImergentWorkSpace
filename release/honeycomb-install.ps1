# HoneyComb 一键安装（Windows）。只需要 Docker Desktop，不需要源码、不需要 Python。
#
#   irm https://github.com/dblsc1/AImergentWorkSpace/releases/download/__TAG__/honeycomb-install.ps1 -OutFile honeycomb-install.ps1
#   powershell -ExecutionPolicy Bypass -File .\honeycomb-install.ps1
#
# 本文件带 UTF-8 BOM：Windows PowerShell 5.1 读无 BOM 的文件会按系统代码页（GBK）解，
# 中文全变乱码。别用编辑器"顺手"去掉它。
#
# 与 honeycomb-install.sh 做同样的事：放下本版 compose；第一次装生成 .env（随机口令，
# 打印一次）；再次运行保留 .env 与数据、只换镜像（升级）；拉镜像、起服务。
#
# 环境变量（都可不设）：HONEYCOMB_DIR（缺省 .\honeycomb）、HONEYCOMB_BIND（缺省 127.0.0.1:8800）
$ErrorActionPreference = 'Stop'

$Tag = '__TAG__'
$Repo = 'https://github.com/dblsc1/AImergentWorkSpace'
$Dir = if ($env:HONEYCOMB_DIR) { $env:HONEYCOMB_DIR } else { Join-Path (Get-Location) 'honeycomb' }
$Bind = if ($env:HONEYCOMB_BIND) { $env:HONEYCOMB_BIND } else { '127.0.0.1:8800' }

function Die($msg) { Write-Host "[错误] $msg" -ForegroundColor Red; exit 1 }

# 跑 docker 这类原生命令：输出（含 stderr）收成文本返回，退出码照常看 $LASTEXITCODE。
# ⚠️ 必须临时把 ErrorActionPreference 放回 Continue：Windows PowerShell 5.1 里，
# 原生命令写到 stderr 的东西一经 2>&1 / *> 重定向就变成 ErrorRecord，在 'Stop' 下
# 直接当终止错误抛出——而 docker 的正常进度本来就走 stderr，于是一拉镜像就崩
# （Codex 审核 v0.2.1 指出；PowerShell/PowerShell#4002）。PowerShell 7 不受影响。
function Invoke-Native([scriptblock]$Cmd) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { return (& $Cmd 2>&1 | ForEach-Object { "$_" }) }
  finally { $ErrorActionPreference = $old }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Die '没找到 docker。先装 Docker Desktop：https://docs.docker.com/desktop/install/windows-install/'
}
$null = Invoke-Native { docker compose version }; if ($LASTEXITCODE -ne 0) { Die '没找到 docker compose（v2）。更新 Docker Desktop。' }
$null = Invoke-Native { docker info }; if ($LASTEXITCODE -ne 0) { Die 'docker 在，但连不上。Docker Desktop 启动了吗？' }

New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'extra') | Out-Null
Push-Location $Dir
try {

if ($env:HONEYCOMB_ASSETS) {
  Copy-Item (Join-Path $env:HONEYCOMB_ASSETS 'docker-compose.yml') 'docker-compose.yml'
} else {
  Invoke-WebRequest -UseBasicParsing "$Repo/releases/download/$Tag/docker-compose.yml" -OutFile 'docker-compose.yml'
}

function Rand {  # 32 位十六进制；这种写法 PowerShell 5.1 与 7 都有
  $b = New-Object byte[] 16
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  -join ($b | ForEach-Object { '{0:x2}' -f $_ })
}

$fresh = $false
if (-not (Test-Path '.env')) {
  $fresh = $true
  $pw = Rand
  # UTF-8 无 BOM：compose 读 .env 时 BOM 会粘在第一个变量名上
  $lines = @(
    '# HoneyComb 配置。改完 docker compose up -d 生效。',
    '# 登录口令（共享口令：一个口令、一份数据）。要多人各用各的，见 README「多个账号」。',
    "HONEYCOMB_PASSWORD=$pw",
    '# 签登录会话用，别外传；换掉它 = 所有人重新登录。',
    "AUTH_SECRET=$(Rand)",
    '# 只本机能访问。要放到局域网改成 0.0.0.0:8800，但先在前面加 TLS（见 README）。',
    "HONEYCOMB_BIND=$Bind",
    'HONEYCOMB_TZ=Asia/Shanghai'
  )
  [System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), ($lines -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
  # 里面是口令和会话密钥：只给当前用户（同 Linux 版的 umask 077）。装在共享目录时
  # 别人读不到。非 Windows 上跑 pwsh 时没有 icacls，用 chmod。
  if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    icacls .env /inheritance:r /grant:r "${env:USERNAME}:(F)" | Out-Null
  } else {
    chmod 600 .env
  }
}

Write-Host '拉镜像（第一次要几分钟）……'
# compose 的 -q 压不住逐层进度（走 stderr），整段收起来，出错才打印
if (-not $env:HONEYCOMB_NO_PULL) {
  $pull = Invoke-Native { docker compose pull -q }
  if ($LASTEXITCODE -ne 0) { $pull | Write-Host; Die '拉镜像失败，检查网络。' }
}
$log = Invoke-Native { docker compose up -d --wait --wait-timeout 300 }
if ($LASTEXITCODE -ne 0) { $log | Write-Host; Invoke-Native { docker compose ps } | Write-Host; Die "没起来。看日志：cd $Dir; docker compose logs" }

# 升级时以 .env 里的为准（可能改过端口）
$m = Select-String -Path '.env' -Pattern '^HONEYCOMB_BIND=(.+)$' | Select-Object -Last 1
if ($m) { $Bind = $m.Matches[0].Groups[1].Value }
$port = $Bind.Split(':')[-1]
Write-Host ''
Write-Host "[完成] HoneyComb $Tag 已经跑起来了：http://127.0.0.1:$port/"
if ($fresh) {
  Write-Host "   登录口令：$pw"
  Write-Host "   （也存在 $Dir\.env 里。演示数据、多个账号、升级与卸载见 README。）"
} else {
  Write-Host '   沿用原来的 .env 和数据（这次是升级）。'
}
Write-Host "   停：cd $Dir; docker compose down"
} finally { Pop-Location }
