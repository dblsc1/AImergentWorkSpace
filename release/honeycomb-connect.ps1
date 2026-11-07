# HoneyComb 连接脚本（Windows）。不下载、不安装任何程序本体。
#
#   $env:HONEYCOMB_SERVER = 'http://127.0.0.1:8800'; $env:HONEYCOMB_DEVICE_TOKEN = '...'
#   powershell -ExecutionPolicy Bypass -File .\honeycomb-connect.ps1
#   powershell -ExecutionPolicy Bypass -File .\honeycomb-connect.ps1 -Uninstall
#
# 本文件带 UTF-8 BOM：Windows PowerShell 5.1 读无 BOM 的文件会按系统代码页（GBK）解，
# 中文全变乱码。别用编辑器"顺手"去掉它。
#
# 与 honeycomb-connect.sh 做同样的事（可重复跑）：
#   1. 终端标题写进 $PROFILE（标记两行之间；再跑 = 替换这一段）
#   2. 桌宠令牌（%APPDATA%\honeycomb-desktop-pet\pet-token）写进 ai-detector 配置的 pet.token
#   3. 服务器地址与设备令牌写进桌宠 config.json（键 server、deviceToken）
# 环境变量：HONEYCOMB_SERVER（缺省 http://127.0.0.1:8800）、HONEYCOMB_DEVICE_TOKEN（不设就交互输入，不回显）、
#   AI_DETECTOR_HOME（同 ai-detector）、HONEYCOMB_PET_HOME（覆盖桌宠数据目录，测试用）、
#   HONEYCOMB_PROFILE（覆盖 $PROFILE 路径，测试用）
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'

$Start = '# >>> honeycomb terminal title >>>'
$End = '# <<< honeycomb terminal title <<<'

$Appdata = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }
$DetectorDir = if ($env:AI_DETECTOR_HOME) { $env:AI_DETECTOR_HOME } else { Join-Path $Appdata 'honeycomb' }
$PetDir = if ($env:HONEYCOMB_PET_HOME) { $env:HONEYCOMB_PET_HOME } else { Join-Path $Appdata 'honeycomb-desktop-pet' }
$DetectorCfg = Join-Path $DetectorDir 'ai-detector.json'
$PetCfg = Join-Path $PetDir 'config.json'
$ProfilePath = if ($env:HONEYCOMB_PROFILE) { $env:HONEYCOMB_PROFILE } else { $PROFILE }

$Block = @'
# >>> honeycomb terminal title >>>
if (-not (Test-Path Function:\__hc_orig_prompt)) { Copy-Item Function:\prompt Function:\__hc_orig_prompt }
function global:prompt {
  $p = $PWD.Path
  if ($p -eq $HOME -or $p.StartsWith($HOME + [IO.Path]::DirectorySeparatorChar)) { $p = '~' + $p.Substring($HOME.Length) }
  $Host.UI.RawUI.WindowTitle = $p
  & __hc_orig_prompt
}
# <<< honeycomb terminal title <<<
'@

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 读文本，顺带告诉调用方原文件有没有 BOM（写回时保持）
function Read-Text($path) {
  if (-not (Test-Path $path)) { return @{ Text = ''; Bom = $false } }
  $bytes = [IO.File]::ReadAllBytes($path)
  $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
  return @{ Text = [IO.File]::ReadAllText($path); Bom = $bom }
}

# ---- 1. 终端标题 ----
function Set-TitleBlock {
  $dir = Split-Path -Parent $ProfilePath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $r = Read-Text $ProfilePath
  $lines = @(); if ($r.Text -ne '') { $lines = $r.Text -split "`r?`n"; if ($lines[-1] -eq '') { $lines = $lines[0..($lines.Count - 2)] } }
  $hasStart = $lines -contains $Start; $hasEnd = $lines -contains $End
  if ($hasStart -and -not $hasEnd) { Write-Host "[跳过] $ProfilePath 里有开始标记没有结束标记，不敢动，请手工处理"; return }
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
  [IO.File]::WriteAllText($ProfilePath, $text, (New-Object System.Text.UTF8Encoding($r.Bom)))
  if ($Uninstall) { Write-Host "终端标题：已从 $ProfilePath 删除" } else { Write-Host "终端标题：已写入 $ProfilePath" }
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
  [IO.File]::WriteAllText($path, $json + "`n", $Utf8NoBom)
  return $true
}

# ---- 2. 桌宠令牌 -> ai-detector pet.token ----
function Set-PetToken {
  if (-not (Test-Path $DetectorCfg)) { Write-Host "[跳过] pet.token：没有 $DetectorCfg，先跑一次 ai-detector init"; return }
  if ($Uninstall) {
    if (Edit-JsonKey $DetectorCfg 'unset' 'pet.token') { Write-Host 'pet.token：已删除' } else { Write-Host "[跳过] $DetectorCfg 不是合法 JSON" }
    return
  }
  $tokFile = Join-Path $PetDir 'pet-token'
  $t = if (Test-Path $tokFile) { ([IO.File]::ReadAllText($tokFile)).Trim() } else { '' }
  if (-not $t) { Write-Host '[跳过] pet.token：桌宠还没生成令牌，先启动一次桌宠再跑本脚本'; return }
  if (Edit-JsonKey $DetectorCfg 'set' 'pet.token' $t) { Write-Host "pet.token：已写入 $DetectorCfg" } else { Write-Host "[跳过] $DetectorCfg 不是合法 JSON，没改" }
}

# ---- 3. 服务器地址与设备令牌 -> 桌宠 config.json ----
function Set-PetConfig {
  if ($Uninstall) {
    if (-not (Test-Path $PetCfg)) { return }
    if ((Edit-JsonKey $PetCfg 'unset' 'server') -and (Edit-JsonKey $PetCfg 'unset' 'deviceToken')) { Write-Host '桌宠 config.json：已删除 server、deviceToken' }
    else { Write-Host "[跳过] $PetCfg 不是合法 JSON" }
    return
  }
  $tok = $env:HONEYCOMB_DEVICE_TOKEN
  if (-not $tok -and [Environment]::UserInteractive) {
    try {
      $sec = Read-Host '设备令牌（不回显）' -AsSecureString
      $tok = (New-Object System.Net.NetworkCredential('', $sec)).Password
    } catch { $tok = '' }
  }
  if (-not $tok) { Write-Host '[跳过] 桌宠 config.json：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 或在终端里跑）'; return }
  $srv = $env:HONEYCOMB_SERVER
  if (-not $srv -and [Environment]::UserInteractive) { try { $srv = Read-Host '服务器地址 [http://127.0.0.1:8800]' } catch { $srv = '' } }
  if (-not $srv) { $srv = 'http://127.0.0.1:8800' }
  New-Item -ItemType Directory -Force -Path $PetDir | Out-Null
  if ((Edit-JsonKey $PetCfg 'set' 'server' $srv) -and (Edit-JsonKey $PetCfg 'set' 'deviceToken' $tok)) { Write-Host "桌宠 config.json：已写入 $PetCfg（server、deviceToken）" }
  else { Write-Host "[跳过] $PetCfg 不是合法 JSON，没改" }
}

Set-TitleBlock
Set-PetToken
Set-PetConfig
