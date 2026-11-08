﻿# HoneyComb 一键连接脚本（Windows）。在你自己的电脑上一条命令装好客户端这一侧的全部东西，可重复跑。
#
#   powershell -ExecutionPolicy Bypass -File .\honeycomb-connect.ps1      （交互输入服务器地址与设备令牌，令牌不回显）
#   令牌也可先放进环境变量 HONEYCOMB_DEVICE_TOKEN（用 Read-Host -AsSecureString 取，别把令牌直接敲在命令行里，会进历史）
#   powershell -ExecutionPolicy Bypass -File .\honeycomb-connect.ps1 -Uninstall      （把下面做的全部撤回）
#
# 本文件带 UTF-8 BOM：Windows PowerShell 5.1 读无 BOM 的文件会按系统代码页（GBK）解，
# 中文全变乱码。别用编辑器"顺手"去掉它。
#
# 与 honeycomb-connect.sh 做同样的事（不要管理员；每步打一行中文结果）：
#   1. 终端标题（当前文件夹）写进 PowerShell 的 $PROFILE（标记两行之间；再跑 = 替换这一段）
#   2. ai-detector：从同版本 Release 下载、校验 SHA256、装到 %LOCALAPPDATA%\HoneyComb\、写配置、
#      在「启动」文件夹放自启脚本、立刻启动
#   3. ActivityWatch：检测；缺就问你要不要用 winget 装（没有 winget 给下载链接）；打印浏览器扩展链接
#   4. Claude Code 钩子：脚本放到用户目录，合并进 ~\.claude\settings.json（不动别的钩子；要 Python 3）
#   5. 桌宠：下载、校验、解压到 %LOCALAPPDATA%\HoneyComb\pet\、写 config.json、放自启脚本、启动
#   6. 桌宠令牌（%APPDATA%\honeycomb-desktop-pet\pet-token）写进 ai-detector 配置的 pet.token
# 选项：-NoDetector -NoHooks -NoPet -NoShell 跳过对应部分（对 -Uninstall 同样生效）。
# 环境变量：HONEYCOMB_SERVER（缺省 http://127.0.0.1:8800）、HONEYCOMB_DEVICE_TOKEN（不设就交互输入，不回显）、
#   HONEYCOMB_ASSETS（目录：从这里取文件，不联网下载；测试 / 离线用）、
#   AI_DETECTOR_HOME（同 ai-detector）、HONEYCOMB_PET_HOME（覆盖桌宠数据目录，测试用）、
#   HONEYCOMB_PROFILE（覆盖 $PROFILE 路径，测试用）、HONEYCOMB_STARTUP_DIR（覆盖「启动」文件夹，测试用）、
#   HONEYCOMB_NO_START（测试用：不真启动）、CLAUDE_CONFIG_DIR（同 Claude Code）
param([switch]$Uninstall, [switch]$NoDetector, [switch]$NoHooks, [switch]$NoPet, [switch]$NoShell)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = 'https://github.com/dblsc1/AImergentWorkSpace'
$Tag = '__TAG__'
$Start = '# >>> honeycomb terminal title >>>'
$End = '# <<< honeycomb terminal title <<<'

$Appdata = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }
$LocalApp = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path (Join-Path $HOME '.local') 'share' }
$AppDir = Join-Path $LocalApp 'HoneyComb'
$DetectorDir = if ($env:AI_DETECTOR_HOME) { $env:AI_DETECTOR_HOME } else { Join-Path $Appdata 'honeycomb' }
$PetDir = if ($env:HONEYCOMB_PET_HOME) { $env:HONEYCOMB_PET_HOME } else { Join-Path $Appdata 'honeycomb-desktop-pet' }
$DetectorCfg = Join-Path $DetectorDir 'ai-detector.json'
$PetCfg = Join-Path $PetDir 'config.json'
$HooksCfgDir = if ($env:HONEYCOMB_AGENT_HOOKS_HOME) { Join-Path $env:HONEYCOMB_AGENT_HOOKS_HOME 'config' } else { Join-Path $Appdata 'honeycomb' }
$HooksCfg = Join-Path $HooksCfgDir 'agent-hooks.json'
$ClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$ClaudeSettings = Join-Path $ClaudeDir 'settings.json'
$Bin = Join-Path $AppDir 'ai-detector.exe'
$HookDir = Join-Path $AppDir 'agent-hooks'
$PetAppDir = Join-Path $AppDir 'pet'
$StartupDir = if ($env:HONEYCOMB_STARTUP_DIR) { $env:HONEYCOMB_STARTUP_DIR } else { [Environment]::GetFolderPath('Startup') }
$DetectorVbs = Join-Path $StartupDir 'honeycomb-ai-detector.vbs'
$PetVbs = Join-Path $StartupDir 'honeycomb-desktop-pet.vbs'
$ProfilePath = if ($env:HONEYCOMB_PROFILE) { $env:HONEYCOMB_PROFILE } else { $PROFILE }

$script:Done = @(); $script:Todo = @(); $script:Tok = ''; $script:Srv = ''; $script:PetStarted = $false
function Add-Done($s) { $script:Done += "  - $s" }
function Add-Todo($s) { $script:Todo += "  - $s" }
function Test-ReleaseUnset { return ($Tag.StartsWith('__T') -and -not $env:HONEYCOMB_ASSETS) }
function Test-Interactive { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) }
function Step($label, [scriptblock]$Body) {
  try { & $Body } catch { Write-Host "[失败] ${label}：$($_.Exception.Message)" }
}

$Block = @'
# >>> honeycomb terminal title >>>
if (-not (Test-Path Function:\__hc_orig_prompt)) { Copy-Item Function:\prompt Function:\__hc_orig_prompt }
function global:prompt {
  $p = $PWD.Path
  if ($p -eq $HOME -or $p.StartsWith($HOME + [IO.Path]::DirectorySeparatorChar)) { $p = '~' + $p.Substring($HOME.Length) }
  $p = -join ($p.ToCharArray() | Where-Object { [int]$_ -gt 31 -and [int]$_ -ne 127 })
  $Host.UI.RawUI.WindowTitle = $p
  & __hc_orig_prompt
}
# <<< honeycomb terminal title <<<
'@

# 合并 / 摘除 Claude Code 的 settings.json 里本脚本装的钩子（与 .sh 同一段 python；只认命令里带我们装的那个路径的条目）
$PyHooks = @'
import json,os,sys
f,op,hook,py=sys.argv[1:5]
A={"async":True}
EV=[("SessionStart",{}),("SessionEnd",{"timeout":5})]+[(e,A) for e in ("UserPromptSubmit","PermissionRequest","Notification","PostToolUse","PostToolUseFailure","Stop","StopFailure","SubagentStart","SubagentStop")]
cmd=py+" \""+hook+"\""
o=json.load(open(f,encoding="utf-8-sig")) if os.path.exists(f) else {}
h=o.get("hooks")
if not isinstance(h,dict): h={}
for ev in list(h):
    gs=[]
    for g in h[ev] if isinstance(h[ev],list) else []:
        inner=[x for x in g.get("hooks",[]) if not (isinstance(x,dict) and hook in str(x.get("command","")))] if isinstance(g,dict) else None
        if inner is None: gs.append(g)
        elif inner: g["hooks"]=inner; gs.append(g)
    if gs: h[ev]=gs
    else: del h[ev]
if op=="set":
    for ev,extra in EV:
        e=dict(type="command",command=cmd); e.update(extra)
        h.setdefault(ev,[]).append({"hooks":[e]})
if h: o["hooks"]=h
else: o.pop("hooks",None)
os.makedirs(os.path.dirname(f),exist_ok=True)
open(f,"w",encoding="utf-8").write(json.dumps(o,indent=2,ensure_ascii=False)+"\n")
'@

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 读文本，顺带告诉调用方原文件有没有 BOM（写回时保持）
function Read-Text($path) {
  if (-not (Test-Path $path)) { return @{ Text = ''; Bom = $false } }
  $bytes = [IO.File]::ReadAllBytes($path)
  $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
  return @{ Text = [IO.File]::ReadAllText($path); Bom = $bom }
}

# 原生命令：吞掉 stderr（5.1 在 'Stop' 下会把它当终止错误），只返回退出码
function Invoke-Quiet($exe, [string[]]$a) {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $null = & $exe @a 2>&1 } finally { $ErrorActionPreference = $old }
  return $LASTEXITCODE
}

# ---- 1. 终端标题 ----
function Set-TitleBlock($path) {
  if ($Uninstall -and -not (Test-Path $path)) { return }
  $dir = Split-Path -Parent $path
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $r = Read-Text $path
  $lines = @(); if ($r.Text -ne '') { $lines = $r.Text -split "`r?`n"; if ($lines.Count -gt 0 -and $lines[-1] -eq '') { $lines = @($lines | Select-Object -First ($lines.Count - 1)) } }
  $hasStart = $lines -contains $Start; $hasEnd = $lines -contains $End
  if ($hasStart -and -not $hasEnd) { Write-Host "[跳过] $path 里有开始标记没有结束标记，不敢动，请手工处理"; return }
  $out = New-Object System.Collections.Generic.List[string]
  $skip = $false; $done = $false
  foreach ($l in $lines) {
    if ($l -eq $Start) {
      if (-not $done) { if (-not $Uninstall) { foreach ($b in ($Block -split "`r?`n")) { $out.Add($b) } }; $done = $true }
      $skip = $true; continue
    }
    if ($skip) { if ($l -eq $End) { $skip = $false }; continue }
    $out.Add($l)
  }
  if (-not $done -and -not $Uninstall) { foreach ($b in ($Block -split "`r?`n")) { $out.Add($b) } }
  $nl = if ($r.Text -match "`r`n" -or $r.Text -eq '') { "`r`n" } else { "`n" }
  $text = if ($out.Count) { ($out -join $nl) + $nl } else { '' }
  [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($r.Bom)))
  if ($Uninstall) { Write-Host "终端标题：已从 $path 删除" } else { Write-Host "终端标题：已写入 $path" }
}

function Set-TitleBlocks {
  $targets = @($ProfilePath)
  if (-not $env:HONEYCOMB_PROFILE) {
    # Windows PowerShell 5.1 与 PowerShell 7 各有各的配置文件；当前这个一定写，另一个只在它的文件夹已存在（装过）时写
    $docs = [Environment]::GetFolderPath('MyDocuments')
    foreach ($c in @((Join-Path (Join-Path $docs 'WindowsPowerShell') 'Microsoft.PowerShell_profile.ps1'), (Join-Path (Join-Path $docs 'PowerShell') 'Microsoft.PowerShell_profile.ps1'))) {
      if ($targets -notcontains $c -and (Test-Path (Split-Path -Parent $c))) { $targets += $c }
    }
  }
  foreach ($t in $targets) { Set-TitleBlock $t }
  if (-not $Uninstall) {
    Add-Done '终端标题显示当前文件夹（新开的 PowerShell 窗口生效）'
    $pol = ''; try { $pol = [string](Get-ExecutionPolicy) } catch { }
    if ($pol -eq 'Restricted' -or $pol -eq 'AllSigned') { Add-Todo '终端标题要允许运行本地脚本：在 PowerShell 里运行  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned' }
  }
}

# ---- JSON：只改一个键，保序（PSCustomObject 保持键序） ----
# Edit-JsonKey <文件> set|unset <点分键路径> [值]；文件不是合法 JSON 时返回 $false 且不写
function Edit-JsonKey($path, $op, $keyPath, $value) {
  $obj = New-Object PSObject
  if (Test-Path $path) {
    try { $obj = (Read-Text $path).Text | ConvertFrom-Json } catch { return $false }
    if ($null -eq $obj) { $obj = New-Object PSObject }
  }
  $keys = $keyPath.Split('.')
  $cur = $obj
  for ($i = 0; $i -lt $keys.Count - 1; $i++) {
    $k = $keys[$i]
    $child = $cur.PSObject.Properties[$k]
    if ($null -eq $child -or $child.Value -isnot [System.Management.Automation.PSCustomObject]) {
      if ($op -eq 'unset') { return $true }
      $cur | Add-Member -NotePropertyName $k -NotePropertyValue (New-Object PSObject) -Force
    }
    $cur = $cur.$k
  }
  $last = $keys[-1]
  if ($op -eq 'set') { $cur | Add-Member -NotePropertyName $last -NotePropertyValue $value -Force }
  else {
    $cur.PSObject.Properties.Remove($last)
    if ($keys.Count -gt 1 -and @($cur.PSObject.Properties).Count -eq 0) { $obj.PSObject.Properties.Remove($keys[0]) }
  }
  $json = ConvertTo-Json -InputObject $obj -Depth 20
  $d = Split-Path -Parent $path
  if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
  [IO.File]::WriteAllText($path, $json + "`n", $Utf8NoBom)
  return $true
}

# ---- 取文件 / 校验 ----
function Get-Asset($name, $dest) {
  if ($env:HONEYCOMB_ASSETS) { Copy-Item -LiteralPath (Join-Path $env:HONEYCOMB_ASSETS $name) -Destination $dest -Force }
  else { Invoke-WebRequest -UseBasicParsing "$Repo/releases/download/$Tag/$name" -OutFile $dest }
}

# 下载 <文件名> 并按 <校验和文件名> 里的 SHA256 核对，通过才落到 <dest>；失败打一行原因返回 $false
function Get-Verified($name, $sumsName, $dest) {
  $t = Join-Path ([IO.Path]::GetTempPath()) ('hc-' + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $t | Out-Null
  try {
    try { Get-Asset $name (Join-Path $t 'f'); Get-Asset $sumsName (Join-Path $t 'sums') }
    catch { Write-Host "[失败] 下载不到 $name（或它的校验文件）：检查网络，或这个版本的 Release 里没有它"; return $false }
    $want = ''
    foreach ($l in [IO.File]::ReadAllLines((Join-Path $t 'sums'))) {
      $p = $l.Trim() -split '\s+', 2
      if ($p.Count -eq 2 -and $p[1].TrimStart('*') -eq $name) { $want = $p[0].ToLower(); break }
    }
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $t 'f')).Hash.ToLower()
    if (-not $want -or $got -ne $want) { Write-Host "[失败] $name 的 SHA256 对不上，已丢弃，没有安装"; return $false }
    $dd = Split-Path -Parent $dest
    if ($dd -and -not (Test-Path $dd)) { New-Item -ItemType Directory -Force -Path $dd | Out-Null }
    Move-Item -LiteralPath (Join-Path $t 'f') -Destination $dest -Force
    return $true
  } finally { Remove-Item -Recurse -Force -LiteralPath $t -ErrorAction SilentlyContinue }
}

function Stop-UnderDir($dir) {
  Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { try { $_.Kill(); $_.WaitForExit(3000) | Out-Null } catch { } }
}

function Write-StartupScript($vbsPath, $exe, $arg, $window) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $vbsPath) | Out-Null
  $cmdline = '""' + $exe + '""' + $arg
  [IO.File]::WriteAllText($vbsPath, 'CreateObject("WScript.Shell").Run ' + '"' + $cmdline + '", ' + $window + ", False`r`n", [Text.Encoding]::ASCII)
}

# ---- 令牌与地址（一次问完，后面各步共用；绝不打印令牌） ----
function Get-Creds {
  $tok = $env:HONEYCOMB_DEVICE_TOKEN
  if (-not $tok -and [Environment]::UserInteractive) {
    try {
      $sec = Read-Host '设备令牌（不回显）' -AsSecureString
      $tok = (New-Object System.Net.NetworkCredential('', $sec)).Password
    } catch { $tok = '' }
  }
  $srv = $env:HONEYCOMB_SERVER
  if ($tok -and -not $srv -and [Environment]::UserInteractive) { try { $srv = Read-Host '服务器地址 [http://127.0.0.1:8800]' } catch { $srv = '' } }
  if (-not $srv) { $srv = 'http://127.0.0.1:8800' }
  $script:Tok = $tok; $script:Srv = $srv
}

# ---- 2. ai-detector ----
function Install-Detector {
  if ($Uninstall) {
    if (Test-Path $Bin) { $null = Invoke-Quiet $Bin @('disable') }
    Stop-UnderDir $Bin
    Remove-Item -Force -LiteralPath $Bin, $DetectorVbs -ErrorAction SilentlyContinue
    if ((Test-Path $DetectorCfg)) { $null = Edit-JsonKey $DetectorCfg 'unset' 'deviceToken' }
    Write-Host "ai-detector：已停止、取消自启、删除程序和配置里的设备令牌（采集数据留在 $DetectorDir）"
    return
  }
  if (-not [Environment]::Is64BitOperatingSystem) { Write-Host '[跳过] ai-detector：只有 64 位 Windows 的程序'; return }
  if (Test-ReleaseUnset) { Write-Host '[跳过] ai-detector：这份脚本没有版本号（不是从 Release 下载的），请用 Release 页面里的 honeycomb-connect.ps1'; return }
  New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
  Stop-UnderDir $Bin
  $new = Join-Path $AppDir 'ai-detector.exe.new'
  if (-not (Get-Verified "ai-detector-$Tag-windows-amd64.exe" "ai-detector-$Tag-SHA256SUMS.txt" $new)) { return }
  Move-Item -LiteralPath $new -Destination $Bin -Force
  $null = Invoke-Quiet $Bin @('init')
  if ($script:Tok) {
    if ((Edit-JsonKey $DetectorCfg 'set' 'cockpitUrl' $script:Srv) -and (Edit-JsonKey $DetectorCfg 'set' 'deviceToken' $script:Tok)) { $null = Invoke-Quiet $Bin @('enable') }
    else { Write-Host "[跳过] $DetectorCfg 不是合法 JSON，没改" }
  } else { Add-Todo 'ai-detector 还没填服务器和设备令牌：设好 HONEYCOMB_DEVICE_TOKEN 再跑一次本脚本' }
  Write-StartupScript $DetectorVbs $Bin ' run' 0
  if (-not $env:HONEYCOMB_NO_START) { Start-Process -FilePath $Bin -ArgumentList 'run' -WindowStyle Hidden }
  Write-Host "ai-detector：已安装到 $Bin，已写配置、注册开机自启并启动"
  Add-Done "ai-detector（采集窗口活动，开机自启）：$Bin"
}

# ---- 3. ActivityWatch ----
function Test-ActivityWatch {
  if ($Uninstall -or $NoDetector) { return }
  $running = $false
  try { $null = Invoke-WebRequest -UseBasicParsing 'http://localhost:5600/api/0/info' -TimeoutSec 2; $running = $true } catch { }
  $installed = (Test-Path (Join-Path $LocalApp 'Programs\ActivityWatch')) -or ($env:ProgramFiles -and (Test-Path (Join-Path $env:ProgramFiles 'ActivityWatch'))) -or [bool](Get-Command aw-qt -ErrorAction SilentlyContinue)
  if ($running) { Write-Host 'ActivityWatch：已在运行（localhost:5600）' }
  elseif ($installed) { Write-Host 'ActivityWatch：已安装但没在运行：请打开它（ai-detector 靠它读窗口）'; Add-Todo '打开 ActivityWatch' }
  elseif ((Get-Command winget -ErrorAction SilentlyContinue) -and (Test-Interactive) -and ((Read-Host '没找到 ActivityWatch，用 winget 装一个吗（winget install ActivityWatch.ActivityWatch）[y/N]') -match '^(y|yes)$')) {
    $rc = Invoke-Quiet 'winget' @('install', 'ActivityWatch.ActivityWatch', '--accept-source-agreements', '--accept-package-agreements')
    if ($rc -eq 0) { Write-Host 'ActivityWatch：已用 winget 装好，请打开一次'; Add-Todo '打开 ActivityWatch' }
    else { Write-Host '[失败] winget 没装成：到 https://activitywatch.net/downloads/ 手工下载'; Add-Todo '装 ActivityWatch：https://activitywatch.net/downloads/' }
  } else {
    Write-Host 'ActivityWatch：没找到。请到 https://activitywatch.net/downloads/ 下载安装（有 winget 时也可 winget install ActivityWatch.ActivityWatch）'
    Add-Todo '装 ActivityWatch：https://activitywatch.net/downloads/'
  }
  Write-Host '浏览器扩展（要在浏览器里手工装，脚本替不了）：'
  Write-Host '  Chrome：https://chromewebstore.google.com/detail/activitywatch-web-watcher/nglaklhklhcoonedhgnpgddginnjdadi'
  Write-Host '  Firefox：https://addons.mozilla.org/firefox/addon/aw-watcher-web/'
  Add-Todo '在浏览器里装 aw-watcher-web 扩展（链接在上面）'
}

# ---- 4. Claude Code 钩子 ----
function Find-Python {
  foreach ($c in @('python', 'python3')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $o = & $cmd.Source -c 'import sys;print(sys.executable)' 2>$null } catch { $o = $null } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -eq 0 -and $o) { return ([string]($o | Select-Object -First 1)).Trim() }   # 应用商店的假 python 会退出非 0
  }
  return ''
}

function Merge-ClaudeHooks($py, $op) {
  $f = Join-Path ([IO.Path]::GetTempPath()) ('hc-hooks-' + [Guid]::NewGuid().ToString('N') + '.py')
  try {
    [IO.File]::WriteAllText($f, $PyHooks, $Utf8NoBom)
    return ((Invoke-Quiet $py @($f, $ClaudeSettings, $op, ($HookDir.Replace('\', '/') + '/claude_hook.py'), $py.Replace('\', '/'))) -eq 0)
  } finally { Remove-Item -Force -LiteralPath $f -ErrorAction SilentlyContinue }
}

function Install-ClaudeHooks {
  if ($Uninstall) {
    $py = Find-Python
    if ((Test-Path $ClaudeSettings) -and $py) {
      if (Merge-ClaudeHooks $py 'unset') { Write-Host "Claude Code 钩子：已从 $ClaudeSettings 摘掉" } else { Write-Host "[跳过] $ClaudeSettings 不是合法 JSON，没动" }
    } elseif (Test-Path $ClaudeSettings) { Write-Host "[跳过] Claude Code 钩子：没找到 Python，摘不了 $ClaudeSettings 里的条目（命令里带 $HookDir 的那些），请手工删" }
    Remove-Item -Recurse -Force -LiteralPath $HookDir -ErrorAction SilentlyContinue
    if (Test-Path $HooksCfg) { $null = (Edit-JsonKey $HooksCfg 'unset' 'url') -and (Edit-JsonKey $HooksCfg 'unset' 'token') }
    return
  }
  $py = Find-Python
  if (-not $py) {
    Write-Host '[跳过] Claude Code 钩子：没找到 Python 3（钩子是 python 脚本）。装好 Python 3（https://www.python.org/downloads/）后再跑一次本脚本'
    Add-Todo '装 Python 3 后重跑本脚本（给 Claude Code 装钩子）'; return
  }
  if (-not (Test-Path $ClaudeDir)) { Write-Host "[跳过] Claude Code 钩子：没发现 Claude Code（没有 $ClaudeDir）。装好并跑过一次后再跑本脚本"; return }
  if (Test-ReleaseUnset) { Write-Host '[跳过] Claude Code 钩子：这份脚本没有版本号，请用 Release 页面里的 honeycomb-connect.ps1'; return }
  if (-not $script:Tok) { Write-Host '[跳过] Claude Code 钩子：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 再跑）'; return }
  New-Item -ItemType Directory -Force -Path $HookDir | Out-Null
  foreach ($f in @('claude_hook.py', 'cockpit_client.py')) {
    if (-not (Get-Verified "agent-hooks-$f" 'agent-hooks-SHA256SUMS.txt' (Join-Path $HookDir "$f.new"))) { return }
    Move-Item -LiteralPath (Join-Path $HookDir "$f.new") -Destination (Join-Path $HookDir $f) -Force
  }
  if (-not ((Edit-JsonKey $HooksCfg 'set' 'url' $script:Srv) -and (Edit-JsonKey $HooksCfg 'set' 'token' $script:Tok))) { Write-Host "[跳过] $HooksCfg 不是合法 JSON，没改"; return }
  if (Merge-ClaudeHooks $py 'set') {
    Write-Host "Claude Code 钩子：已装好，合并进 $ClaudeSettings（别的钩子没动；新开的 Claude Code 会话生效）"
    Add-Done "Claude Code 钩子（把会话时间报给 HoneyComb）：$HookDir"
  } else { Write-Host "[跳过] $ClaudeSettings 不是合法 JSON，没改" }
}

# ---- 5. 桌宠 ----
function Find-PetExe {
  if (-not (Test-Path $PetAppDir)) { return $null }
  $exes = @(Get-ChildItem -LiteralPath $PetAppDir -Recurse -Filter '*.exe' -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(uninstall|elevate|crashpad)' })
  if (-not $exes) { return $null }
  $pref = @($exes | Where-Object { $_.Name -match 'honeycomb' })
  if (-not $pref) { $pref = $exes }
  return ($pref | Sort-Object { $_.FullName.Split([IO.Path]::DirectorySeparatorChar).Count } | Select-Object -First 1).FullName
}

function Install-Pet {
  if ($Uninstall) {
    Stop-UnderDir $PetAppDir
    Remove-Item -Recurse -Force -LiteralPath $PetAppDir -ErrorAction SilentlyContinue
    Remove-Item -Force -LiteralPath $PetVbs -ErrorAction SilentlyContinue
    Write-Host "桌宠：已停止、取消自启、删除程序（设置和素材留在 $PetDir）"
    return $false
  }
  if (Test-ReleaseUnset) { Write-Host '[跳过] 桌宠：这份脚本没有版本号，请用 Release 页面里的 honeycomb-connect.ps1'; return $false }
  if (-not [Environment]::Is64BitOperatingSystem) { Write-Host '[跳过] 桌宠：只有 64 位 Windows 的程序'; return $false }
  $zip = Join-Path ([IO.Path]::GetTempPath()) ('hc-pet-' + [Guid]::NewGuid().ToString('N') + '.zip')
  $x = Join-Path ([IO.Path]::GetTempPath()) ('hc-petx-' + [Guid]::NewGuid().ToString('N'))
  try {
    if (-not (Get-Verified "honeycomb-pet-$Tag-win-x64.zip" "honeycomb-pet-$Tag-SHA256SUMS.txt" $zip)) { return $false }
    Expand-Archive -LiteralPath $zip -DestinationPath $x -Force
    Stop-UnderDir $PetAppDir
    Remove-Item -Recurse -Force -LiteralPath $PetAppDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Move-Item -LiteralPath $x -Destination $PetAppDir
  } finally {
    Remove-Item -Force -LiteralPath $zip -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -LiteralPath $x -ErrorAction SilentlyContinue
  }
  $exe = Find-PetExe
  if (-not $exe) { Write-Host '[失败] 桌宠压缩包里没有 .exe'; return $false }
  Write-Host "桌宠：已装到 $PetAppDir"
  return $true
}

function Start-Pet {
  $exe = Find-PetExe
  if (-not $exe) { return }
  Write-StartupScript $PetVbs $exe '' 1
  if (-not $env:HONEYCOMB_NO_START) { Start-Process -FilePath $exe; $script:PetStarted = $true }
  Write-Host '桌宠：已注册开机自启并启动'
  Add-Done "桌宠（开机自启）：$PetAppDir"
}

# ---- 6. 令牌与地址写进配置 ----
function Set-PetToken {
  if (-not (Test-Path $DetectorCfg)) { Write-Host "[跳过] pet.token：没有 $DetectorCfg，先装 ai-detector（或跑一次 ai-detector init）"; return }
  if ($Uninstall) {
    if (Edit-JsonKey $DetectorCfg 'unset' 'pet.token') { Write-Host 'pet.token：已删除' } else { Write-Host "[跳过] $DetectorCfg 不是合法 JSON" }
    return
  }
  $tokFile = Join-Path $PetDir 'pet-token'
  if ($script:PetStarted) { for ($i = 0; $i -lt 10 -and -not (Test-Path $tokFile); $i++) { Start-Sleep -Seconds 1 } }
  $t = if (Test-Path $tokFile) { ([IO.File]::ReadAllText($tokFile)).Trim() } else { '' }
  if (-not $t) { Write-Host '[跳过] pet.token：桌宠还没生成令牌，先启动一次桌宠再跑本脚本'; return }
  if (Edit-JsonKey $DetectorCfg 'set' 'pet.token' $t) { Write-Host "pet.token：已写入 $DetectorCfg" } else { Write-Host "[跳过] $DetectorCfg 不是合法 JSON，没改" }
}

function Set-PetConfig {
  if ($Uninstall) {
    if (-not (Test-Path $PetCfg)) { return }
    if ((Edit-JsonKey $PetCfg 'unset' 'server') -and (Edit-JsonKey $PetCfg 'unset' 'deviceToken')) { Write-Host '桌宠 config.json：已删除 server、deviceToken' }
    else { Write-Host "[跳过] $PetCfg 不是合法 JSON" }
    return
  }
  if (-not $script:Tok) { Write-Host '[跳过] 桌宠 config.json：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 或在终端里跑）'; return }
  New-Item -ItemType Directory -Force -Path $PetDir | Out-Null
  if ((Edit-JsonKey $PetCfg 'set' 'server' $script:Srv) -and (Edit-JsonKey $PetCfg 'set' 'deviceToken' $script:Tok)) { Write-Host "桌宠 config.json：已写入 $PetCfg（server、deviceToken）" }
  else { Write-Host "[跳过] $PetCfg 不是合法 JSON，没改" }
}

# ---- 主流程 ----
if (-not $Uninstall) { Get-Creds }
if (-not $NoShell) { Step '终端标题' { Set-TitleBlocks } }
if (-not $NoDetector) { Step 'ai-detector' { Install-Detector } }
Step 'ActivityWatch' { Test-ActivityWatch }
if (-not $NoHooks) { Step 'Claude Code 钩子' { Install-ClaudeHooks } }
if (-not $NoPet) {
  Step '桌宠' {
    $ok = Install-Pet
    Set-PetConfig
    if ($ok -and -not $Uninstall) { Start-Pet }
  }
  Step 'pet.token' { Set-PetToken }
}

if ($Uninstall) {
  if ((Test-Path $AppDir) -and -not (Get-ChildItem -LiteralPath $AppDir -Force)) { Remove-Item -Force -LiteralPath $AppDir }
  Write-Host '已卸载：上面列出的都撤回了。（ActivityWatch、浏览器扩展是你自己装的，没动）'
} else {
  Write-Host ''
  Write-Host '已装好：'
  if ($script:Done.Count) { $script:Done | ForEach-Object { Write-Host $_ } } else { Write-Host '  （没有新装的东西）' }
  if ($script:Todo.Count) { Write-Host '还要你手工做：'; $script:Todo | ForEach-Object { Write-Host $_ } }
}
