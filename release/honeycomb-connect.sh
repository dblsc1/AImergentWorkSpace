#!/bin/sh
# HoneyComb 一键连接脚本（macOS / Linux）。在你自己的电脑上一条命令装好客户端这一侧的全部东西，可重复跑。
#
#   sh honeycomb-connect.sh                      （交互输入服务器地址与设备令牌，令牌不回显）
#   read -rs HONEYCOMB_DEVICE_TOKEN; export HONEYCOMB_DEVICE_TOKEN; sh honeycomb-connect.sh   （不要把令牌写在命令前，会进 shell 历史）
#   sh honeycomb-connect.sh --uninstall          （把下面做的全部撤回）
#
# 做这些事（不要 sudo / 管理员；每步打一行中文结果）：
#   1. 终端标题（当前文件夹）写进 ~/.bashrc / ~/.zshrc / fish 的 conf.d（标记两行之间；再跑 = 替换这一段）
#   2. ai-detector：从同版本 Release 下载、校验 SHA256、装到用户目录、写配置、注册开机自启、立刻启动
#   3. ActivityWatch：检测；缺就按系统给安装办法（macOS 有 brew 时问你要不要装）；打印浏览器扩展链接
#   4. Claude Code 钩子：脚本放到用户目录，合并进 ~/.claude/settings.json（不动别的钩子；要 python3）
#   5. 桌宠（macOS）：下载、校验、装进 ~/Applications、写 config.json、注册开机自启、启动
#   6. 桌宠令牌写进 ai-detector 配置的 pet.token
# 选项：--no-detector --no-hooks --no-pet --no-shell 跳过对应部分（对 --uninstall 同样生效）。
# 环境变量：HONEYCOMB_SERVER（缺省 http://127.0.0.1:8800）、HONEYCOMB_DEVICE_TOKEN（不设就交互输入，不回显）、
#   HONEYCOMB_ASSETS（目录：从这里取文件，不联网下载；测试 / 离线用）、
#   AI_DETECTOR_HOME（同 ai-detector）、HONEYCOMB_PET_HOME（覆盖桌宠数据目录，测试用）、
#   HONEYCOMB_OS / HONEYCOMB_ARCH / HONEYCOMB_NO_START（测试用：冒充系统 / 不真启动）
# 安装位置：macOS ~/Library/Application Support/HoneyComb/ 与 ~/Applications/；Linux ~/.local/share/honeycomb/。
set -eu

REPO=https://github.com/dblsc1/AImergentWorkSpace
TAG=__TAG__
START='# >>> honeycomb terminal title >>>'
END='# <<< honeycomb terminal title <<<'

UNINSTALL=0; NO_DETECTOR=0; NO_HOOKS=0; NO_PET=0; NO_SHELL=0
for a in "$@"; do
  case "$a" in
    --uninstall) UNINSTALL=1 ;;
    --no-detector) NO_DETECTOR=1 ;;
    --no-hooks) NO_HOOKS=1 ;;
    --no-pet) NO_PET=1 ;;
    --no-shell) NO_SHELL=1 ;;
    *) echo "用法：sh honeycomb-connect.sh [--uninstall] [--no-detector] [--no-hooks] [--no-pet] [--no-shell]" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
DONE=''; TODO=''
done_add() { DONE="$DONE
  - $*"; }
todo_add() { TODO="$TODO
  - $*"; }

OS_NAME=${HONEYCOMB_OS:-$(uname -s)}
ARCH_NAME=${HONEYCOMB_ARCH:-$(uname -m)}
if [ "$OS_NAME" = Darwin ]; then
  CFG_BASE="$HOME/Library/Application Support"
  APP_DIR="$HOME/Library/Application Support/HoneyComb"
else
  CFG_BASE="${XDG_CONFIG_HOME:-$HOME/.config}"
  APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/honeycomb"
fi
DETECTOR_DIR="${AI_DETECTOR_HOME:-$CFG_BASE/honeycomb}"
PET_DIR="${HONEYCOMB_PET_HOME:-$CFG_BASE/honeycomb-desktop-pet}"
DETECTOR_CFG="$DETECTOR_DIR/ai-detector.json"
PET_CFG="$PET_DIR/config.json"
HOOKS_CFG="${HONEYCOMB_AGENT_HOOKS_HOME:+$HONEYCOMB_AGENT_HOOKS_HOME/config}"
HOOKS_CFG="${HOOKS_CFG:-$CFG_BASE/honeycomb}/agent-hooks.json"
CLAUDE_SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
BIN="$APP_DIR/ai-detector"
HOOK_DIR="$APP_DIR/agent-hooks"
PET_APP="$HOME/Applications/HoneyComb Pet.app"
LA_DIR="$HOME/Library/LaunchAgents"
UID_N=$(id -u)

# ---- JSON：只改一个键，保序。node 或 python3，找得到哪个用哪个 ----
# 值是字符串；HC_TYPE=bool 时按 true / false 写
JS_JSON='
const fs=require("fs");const [f,op,p]=process.argv.slice(1);const k=p.split(".");
const o=JSON.parse(fs.existsSync(f)?fs.readFileSync(f,"utf8").replace(/^﻿/,""):"{}");
let c=o;
for(const x of k.slice(0,-1)){
  if(op==="set"){if(typeof c[x]!=="object"||c[x]===null)c[x]={};}
  else if(typeof c[x]!=="object"||c[x]===null)process.exit(0);
  c=c[x];
}
const l=k[k.length-1];
if(op==="set")c[l]=process.env.HC_TYPE==="bool"?process.env.HC_VAL==="true":process.env.HC_VAL;else delete c[l];
if(op==="unset"&&k.length>1&&Object.keys(c).length===0)delete o[k[0]];
fs.writeFileSync(f,JSON.stringify(o,null,2)+"\n",{mode:0o600});fs.chmodSync(f,0o600);
'
PY_JSON='
import json,os,sys
f,op,p=sys.argv[1:4];k=p.split(".")
o=json.load(open(f,encoding="utf-8-sig")) if os.path.exists(f) else {}
c=o
for x in k[:-1]:
    if op=="set":
        if not isinstance(c.get(x),dict): c[x]={}
    elif not isinstance(c.get(x),dict): sys.exit(0)
    c=c[x]
if op=="set": c[k[-1]]=(os.environ["HC_VAL"]=="true") if os.environ.get("HC_TYPE")=="bool" else os.environ["HC_VAL"]
else: c.pop(k[-1],None)
if op=="unset" and len(k)>1 and not c: o.pop(k[0],None)
open(f,"w",encoding="utf-8").write(json.dumps(o,indent=2,ensure_ascii=False)+"\n")
os.chmod(f,0o600)
'
# 合并 / 摘除 Claude Code 的 settings.json 里本脚本装的钩子（只认命令里带我们装的那个路径的条目）
PY_HOOKS='
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
'
if command -v node >/dev/null 2>&1; then JSON_TOOL=node
elif command -v python3 >/dev/null 2>&1; then JSON_TOOL=python3
else JSON_TOOL=''; fi

# json_edit set|unset <文件> <键路径，点分>；值走环境变量 HC_VAL。配置文件不是合法 JSON 时不写、返回非 0
json_edit() {
  if [ "$JSON_TOOL" = node ]; then node -e "$JS_JSON" "$2" "$1" "$3"
  else python3 -c "$PY_JSON" "$2" "$1" "$3"; fi
}

# ---- 1. 终端标题 ----
bash_block() {
  cat <<'B'
# >>> honeycomb terminal title >>>
__hc_title() { __d=$PWD; case $__d in "$HOME"|"$HOME"/*) __d="~${__d#"$HOME"}";; esac; __d=${__d//[[:cntrl:]]/}; printf '\033]0;%s\007' "$__d"; }
case "${PROMPT_COMMAND:-}" in *__hc_title*) ;; *) PROMPT_COMMAND="${PROMPT_COMMAND%;}${PROMPT_COMMAND:+;}__hc_title";; esac
# <<< honeycomb terminal title <<<
B
}
zsh_block() {
  cat <<'B'
# >>> honeycomb terminal title >>>
__hc_title() { local d=$PWD; case $d in $HOME|$HOME/*) d="~${d#$HOME}";; esac; d=${d//[[:cntrl:]]/}; printf '\033]0;%s\007' "$d"; }
(( ${precmd_functions[(I)__hc_title]} )) || precmd_functions+=(__hc_title)
# <<< honeycomb terminal title <<<
B
}
fish_block() {
  cat <<'B'
# >>> honeycomb terminal title >>>
function fish_title
    string replace -r "^$HOME" "~" -- $PWD | string replace -ra '[[:cntrl:]]' ''
end
# <<< honeycomb terminal title <<<
B
}

# 把文件里的标记段换成 $2 文件的内容（$2 为空 = 删掉）；标记外原样；没有标记段就追加在末尾
replace_block() {
  if grep -qxF "$START" "$1" 2>/dev/null && ! grep -qxF "$END" "$1"; then
    say "[跳过] $1 里有开始标记没有结束标记，不敢动，请手工处理"; return 0
  fi
  if [ -n "$2" ] && [ -s "$1" ] && [ -n "$(tail -c1 "$1")" ]; then echo >> "$1"; fi
  tmp=$(mktemp)
  awk -v s="$START" -v e="$END" -v bf="$2" '
    function emit(  l) { while ((getline l < bf) > 0) print l; close(bf) }
    $0 == s { if (!done) { if (bf != "") emit(); done = 1 } skip = 1; next }
    skip { if ($0 == e) skip = 0; next }
    { print }
    END { if (!done && bf != "") emit() }' "$1" > "$tmp"
  cat "$tmp" > "$1"; rm -f "$tmp"
}

title_step() {
  FISH_RC="${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/honeycomb-title.fish"
  files=''
  [ -f "$HOME/.bashrc" ] && files="bash"
  [ -f "$HOME/.zshrc" ] && files="$files zsh"
  { [ -d "${XDG_CONFIG_HOME:-$HOME/.config}/fish" ] || [ -f "$FISH_RC" ]; } && files="$files fish"
  if [ -z "$files" ] && [ "$UNINSTALL" = 0 ]; then
    case "$(basename "${SHELL:-}")" in
      bash) files=bash ;;
      zsh) files=zsh ;;
      *) say "[跳过] 终端标题：没有 ~/.bashrc、~/.zshrc 或 fish 配置，当前 shell 也不是 bash / zsh"; return 0 ;;
    esac
  fi
  bf=$(mktemp)
  for sh_name in $files; do
    case "$sh_name" in bash) rc="$HOME/.bashrc" ;; zsh) rc="$HOME/.zshrc" ;; fish) rc="$FISH_RC" ;; esac
    if [ "$UNINSTALL" = 1 ]; then
      if [ "$sh_name" = fish ]; then
        [ -f "$rc" ] && grep -qxF "$START" "$rc" && rm -f "$rc"
      else replace_block "$rc" ''; fi
      say "终端标题：已从 $rc 删除"
    else
      [ "$sh_name" = fish ] && mkdir -p "$(dirname "$rc")"
      [ -f "$rc" ] || : > "$rc"
      "${sh_name}_block" > "$bf"
      replace_block "$rc" "$bf"; say "终端标题：已写入 $rc"
    fi
  done
  rm -f "$bf"
  [ "$UNINSTALL" = 1 ] || done_add "终端标题显示当前文件夹（新开的终端窗口生效）"
}

# ---- 取文件 / 校验 ----
have_tty() { stty -g </dev/tty >/dev/null 2>&1; }

# release_unset：版本号没被替换（直接从仓库跑的脚本）且没指定本地文件目录
release_unset() { case "$TAG" in __T*) [ -z "${HONEYCOMB_ASSETS:-}" ] ;; *) return 1 ;; esac; }

# fetch <Release 里的文件名> <落到哪里>
fetch() {
  if [ -n "${HONEYCOMB_ASSETS:-}" ]; then cp "$HONEYCOMB_ASSETS/$1" "$2" 2>/dev/null
  elif command -v curl >/dev/null 2>&1; then curl -fsSL --retry 2 -o "$2" "$REPO/releases/download/$TAG/$1"
  else return 1; fi
}

sha_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

# verify_sum <文件> <SHA256SUMS 文件> <Release 里的文件名>
verify_sum() {
  want=$(awk -v n="$3" '{ f = $2; sub(/^\*/, "", f); if (f == n) { print $1; exit } }' "$2")
  [ -n "$want" ] && [ "$(sha_of "$1")" = "$want" ]
}

# get_verified <文件名> <校验和文件名> <落到哪里>；失败打一行原因并返回非 0
get_verified() {
  gv_t=$(mktemp -d)
  if ! fetch "$1" "$gv_t/f" || ! fetch "$2" "$gv_t/sums"; then
    say "[失败] 下载不到 $1（或它的校验文件）：检查网络，或这个版本的 Release 里没有它"; rm -rf "$gv_t"; return 1
  fi
  if ! verify_sum "$gv_t/f" "$gv_t/sums" "$1"; then
    say "[失败] $1 的 SHA256 对不上，已丢弃，没有安装"; rm -rf "$gv_t"; return 1
  fi
  mv "$gv_t/f" "$3"; rm -rf "$gv_t"
}

# ---- 令牌与地址（一次问完，后面各步共用；绝不打印令牌） ----
collect_creds() {
  TOK=${HONEYCOMB_DEVICE_TOKEN:-}
  if [ -z "$TOK" ] && have_tty; then
    printf '设备令牌（不回显）：' >/dev/tty; trap 'stty echo </dev/tty; exit 130' INT TERM HUP
    stty -echo </dev/tty; IFS= read -r TOK </dev/tty || true; stty echo </dev/tty; trap - INT TERM HUP; echo >/dev/tty
  fi
  SRV=${HONEYCOMB_SERVER:-}
  if [ -n "$TOK" ] && [ -z "$SRV" ] && have_tty; then printf '服务器地址 [http://127.0.0.1:8800]：' >/dev/tty; IFS= read -r SRV </dev/tty || true; fi
  SRV=${SRV:-http://127.0.0.1:8800}
}

# ---- 2. ai-detector ----
detector_asset() {
  case "$OS_NAME" in Darwin) o=darwin ;; Linux) o=linux ;; *) return 1 ;; esac
  case "$ARCH_NAME" in x86_64|amd64) a=amd64 ;; arm64|aarch64) a=arm64 ;; *) return 1 ;; esac
  case "$o-$a" in linux-amd64|darwin-arm64|darwin-amd64) DET_OS=$o; DET_ARCH=$a ;; *) return 1 ;; esac
}

launchd_load() { # <plist>
  command -v launchctl >/dev/null 2>&1 || return 1
  launchctl bootout "gui/$UID_N" "$1" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$UID_N" "$1"
}
launchd_unload() { command -v launchctl >/dev/null 2>&1 && launchctl bootout "gui/$UID_N" "$1" >/dev/null 2>&1 || true; }
kill_by() { command -v pkill >/dev/null 2>&1 && pkill -f "$1" >/dev/null 2>&1 || true; }

detector_step() {
  plist="$LA_DIR/com.honeycomb.ai-detector.plist"
  if [ "$UNINSTALL" = 1 ]; then
    [ "$OS_NAME" = Darwin ] && launchd_unload "$plist"
    if [ -x "$BIN" ]; then
      "$BIN" autostart uninstall >/dev/null 2>&1 || true
      "$BIN" disable >/dev/null 2>&1 || true
    fi
    kill_by "$BIN run"
    rm -f "$BIN" "$plist"
    [ -f "$DETECTOR_CFG" ] && [ -n "$JSON_TOOL" ] && json_edit unset "$DETECTOR_CFG" deviceToken >/dev/null 2>&1 || true
    say "ai-detector：已停止、取消自启、删除程序和配置里的设备令牌（采集数据留在 $DETECTOR_DIR）"
    return 0
  fi
  if ! detector_asset; then say "[跳过] ai-detector：这个系统（$OS_NAME $ARCH_NAME）没有现成的程序"; return 0; fi
  if release_unset; then say "[跳过] ai-detector：这份脚本没有版本号（不是从 Release 下载的），请用 Release 页面里的 honeycomb-connect.sh"; return 0; fi
  name="ai-detector-$TAG-$DET_OS-$DET_ARCH"
  mkdir -p "$APP_DIR"
  new="$APP_DIR/ai-detector.new"
  get_verified "$name" "ai-detector-$TAG-SHA256SUMS.txt" "$new" || return 0
  chmod 755 "$new"; mv -f "$new" "$BIN"
  [ "$OS_NAME" = Darwin ] && command -v xattr >/dev/null 2>&1 && xattr -d com.apple.quarantine "$BIN" >/dev/null 2>&1 || true
  "$BIN" init >/dev/null 2>&1 || true
  if [ -n "${TOK:-}" ] && [ -n "$JSON_TOOL" ]; then
    if HC_VAL=$SRV json_edit set "$DETECTOR_CFG" cockpitUrl && HC_VAL=$TOK json_edit set "$DETECTOR_CFG" deviceToken; then
      "$BIN" enable >/dev/null 2>&1 || true
    else say "[跳过] $DETECTOR_CFG 不是合法 JSON，没改"; fi
  else todo_add "ai-detector 还没填服务器和设备令牌：设好 HONEYCOMB_DEVICE_TOKEN 再跑一次本脚本"; fi
  "$BIN" autostart install >/dev/null 2>&1 || say "[跳过] 开机自启没写成：手工运行 $BIN autostart install"
  if [ -z "${HONEYCOMB_NO_START:-}" ]; then
    if [ "$OS_NAME" = Darwin ] && [ -f "$plist" ] && launchd_load "$plist" >/dev/null 2>&1; then :
    else nohup "$BIN" run >/dev/null 2>&1 & fi
  fi
  say "ai-detector：已安装到 $BIN，已写配置、注册开机自启并启动"
  done_add "ai-detector（采集窗口活动，开机自启）：$BIN"
}

# ---- 3. ActivityWatch ----
ask_yn() { have_tty || return 1; printf '%s [y/N]：' "$1" >/dev/tty; IFS= read -r ans </dev/tty || return 1; case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac; }

aw_step() {
  [ "$UNINSTALL" = 0 ] && [ "$NO_DETECTOR" = 0 ] || return 0
  if command -v curl >/dev/null 2>&1 && curl -fsS -m 2 http://localhost:5600/api/0/info >/dev/null 2>&1; then
    say "ActivityWatch：已在运行（localhost:5600）"
  elif [ -d /Applications/ActivityWatch.app ] || [ -d "$HOME/Applications/ActivityWatch.app" ] || command -v aw-qt >/dev/null 2>&1; then
    say "ActivityWatch：已安装但没在运行：请打开它（ai-detector 靠它读窗口）"
    todo_add "打开 ActivityWatch"
  elif [ "$OS_NAME" = Darwin ] && command -v brew >/dev/null 2>&1 && ask_yn "没找到 ActivityWatch，用 brew 装一个吗（brew install --cask activitywatch）"; then
    if brew install --cask activitywatch; then say "ActivityWatch：已用 brew 装好，请打开一次"; todo_add "打开 ActivityWatch"
    else say "[失败] brew 没装成：到 https://activitywatch.net/downloads/ 手工下载"; todo_add "装 ActivityWatch：https://activitywatch.net/downloads/"; fi
  else
    say "ActivityWatch：没找到。请到 https://activitywatch.net/downloads/ 下载安装（macOS 也可 brew install --cask activitywatch）"
    todo_add "装 ActivityWatch：https://activitywatch.net/downloads/"
  fi
  say "浏览器扩展（要在浏览器里手工装，脚本替不了）："
  say "  Chrome：https://chromewebstore.google.com/detail/activitywatch-web-watcher/nglaklhklhcoonedhgnpgddginnjdadi"
  say "  Firefox：https://addons.mozilla.org/firefox/addon/aw-watcher-web/"
  todo_add "在浏览器里装 aw-watcher-web 扩展（链接在上面）"
  [ "$OS_NAME" = Darwin ] && todo_add "macOS：系统设置 → 隐私与安全性 → 辅助功能，允许 ActivityWatch（否则读不到窗口标题）"
  return 0
}

# ---- 4. Claude Code 钩子 ----
hooks_step() {
  if [ "$UNINSTALL" = 1 ]; then
    if [ -f "$CLAUDE_SETTINGS" ] && command -v python3 >/dev/null 2>&1; then
      python3 -c "$PY_HOOKS" "$CLAUDE_SETTINGS" unset "$HOOK_DIR/claude_hook.py" python3 && say "Claude Code 钩子：已从 $CLAUDE_SETTINGS 摘掉" || say "[跳过] $CLAUDE_SETTINGS 不是合法 JSON，没动"
    fi
    rm -rf "$HOOK_DIR"
    if [ -f "$HOOKS_CFG" ] && [ -n "$JSON_TOOL" ]; then json_edit unset "$HOOKS_CFG" url >/dev/null 2>&1 && json_edit unset "$HOOKS_CFG" token >/dev/null 2>&1 || true; fi
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    say "[跳过] Claude Code 钩子：没找到 python3（钩子是 python 脚本）。装好 Python 3 后再跑一次本脚本"
    todo_add "装 Python 3 后重跑本脚本（给 Claude Code 装钩子）"; return 0
  fi
  if [ ! -d "$(dirname "$CLAUDE_SETTINGS")" ]; then say "[跳过] Claude Code 钩子：没发现 Claude Code（没有 $(dirname "$CLAUDE_SETTINGS")）。装好并跑过一次后再跑本脚本"; return 0; fi
  if release_unset; then say "[跳过] Claude Code 钩子：这份脚本没有版本号，请用 Release 页面里的 honeycomb-connect.sh"; return 0; fi
  if [ -z "${TOK:-}" ] || [ -z "$JSON_TOOL" ]; then say "[跳过] Claude Code 钩子：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 再跑）"; return 0; fi
  mkdir -p "$HOOK_DIR"
  for f in claude_hook.py cockpit_client.py; do
    get_verified "agent-hooks-$f" "agent-hooks-SHA256SUMS.txt" "$HOOK_DIR/$f.new" || return 0
    mv -f "$HOOK_DIR/$f.new" "$HOOK_DIR/$f"
  done
  mkdir -p "$(dirname "$HOOKS_CFG")"
  HC_VAL=$SRV json_edit set "$HOOKS_CFG" url && HC_VAL=$TOK json_edit set "$HOOKS_CFG" token || { say "[跳过] $HOOKS_CFG 不是合法 JSON，没改"; return 0; }
  if python3 -c "$PY_HOOKS" "$CLAUDE_SETTINGS" set "$HOOK_DIR/claude_hook.py" python3; then
    say "Claude Code 钩子：已装好，合并进 $CLAUDE_SETTINGS（别的钩子没动；新开的 Claude Code 会话生效）"
    done_add "Claude Code 钩子（把会话时间报给 HoneyComb）：$HOOK_DIR"
  else say "[跳过] $CLAUDE_SETTINGS 不是合法 JSON，没改"; fi
}

# ---- 5. 桌宠 ----
pet_zip_name() {
  [ "$OS_NAME" = Darwin ] || return 1
  case "$ARCH_NAME" in arm64|aarch64) PET_ARCH=arm64 ;; x86_64|amd64) PET_ARCH=x64 ;; *) return 1 ;; esac
  PET_ZIP="honeycomb-pet-$TAG-mac-$PET_ARCH.zip"
}

pet_install_step() {
  plist="$LA_DIR/com.honeycomb.desktop-pet.plist"
  if [ "$UNINSTALL" = 1 ]; then
    launchd_unload "$plist"
    kill_by "$PET_APP/Contents/MacOS"
    rm -rf "$PET_APP"; rm -f "$plist"
    say "桌宠：已停止、取消自启、删除程序（设置和素材留在 $PET_DIR）"
    return 0
  fi
  pet_zip_name || { say "[跳过] 桌宠：这个系统（$OS_NAME $ARCH_NAME）没有安装包（目前只有 Windows 与 macOS）"; return 1; }
  release_unset && { say "[跳过] 桌宠：这份脚本没有版本号，请用 Release 页面里的 honeycomb-connect.sh"; return 1; }
  command -v unzip >/dev/null 2>&1 || { say "[跳过] 桌宠：没找到 unzip"; return 1; }
  zt=$(mktemp -d)
  get_verified "$PET_ZIP" "honeycomb-pet-$TAG-SHA256SUMS.txt" "$zt/pet.zip" || { rm -rf "$zt"; return 1; }
  mkdir "$zt/x"; unzip -q "$zt/pet.zip" -d "$zt/x" || { say "[失败] 桌宠压缩包解不开"; rm -rf "$zt"; return 1; }
  app=''
  for c in "$zt"/x/*.app "$zt"/x/*/*.app; do [ -d "$c" ] && { app=$c; break; }; done
  [ -n "$app" ] || { say "[失败] 桌宠压缩包里没有 .app"; rm -rf "$zt"; return 1; }
  mkdir -p "$HOME/Applications"
  kill_by "$PET_APP/Contents/MacOS"
  rm -rf "$PET_APP"; mv "$app" "$PET_APP"; rm -rf "$zt"
  command -v xattr >/dev/null 2>&1 && xattr -cr "$PET_APP" >/dev/null 2>&1 || true
  say "桌宠：已装到 $PET_APP"
  return 0
}

pet_launch_step() {
  plist="$LA_DIR/com.honeycomb.desktop-pet.plist"
  exe_name=$(ls "$PET_APP/Contents/MacOS" 2>/dev/null | head -1)
  [ -n "$exe_name" ] || return 0
  mkdir -p "$LA_DIR"
  cat > "$plist" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.honeycomb.desktop-pet</string>
  <key>ProgramArguments</key><array><string>$PET_APP/Contents/MacOS/$exe_name</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
P
  if [ -z "${HONEYCOMB_NO_START:-}" ]; then launchd_load "$plist" >/dev/null 2>&1 || (nohup "$PET_APP/Contents/MacOS/$exe_name" >/dev/null 2>&1 &); fi
  say "桌宠：已注册开机自启并启动"
  done_add "桌宠（开机自启）：$PET_APP"
}

# ---- 6. 令牌与地址写进配置 ----
pet_token_step() {
  if [ -z "$JSON_TOOL" ]; then say "[跳过] 没找到 node 或 python3，改不了 JSON：请手工把桌宠令牌写进 $DETECTOR_CFG 的 pet.token"; return 0; fi
  [ -f "$DETECTOR_CFG" ] || { say "[跳过] pet.token：没有 $DETECTOR_CFG，先装 ai-detector（或跑一次 ai-detector init）"; return 0; }
  if [ "$UNINSTALL" = 1 ]; then
    json_edit unset "$DETECTOR_CFG" pet.token && say "pet.token：已删除" || say "[跳过] $DETECTOR_CFG 不是合法 JSON"
    return 0
  fi
  if [ -n "${PET_STARTED:-}" ] && [ ! -s "$PET_DIR/pet-token" ]; then
    n=0; while [ $n -lt 10 ] && [ ! -s "$PET_DIR/pet-token" ]; do sleep 1; n=$((n+1)); done
  fi
  t=$(tr -d '\r\n' < "$PET_DIR/pet-token" 2>/dev/null || true)
  [ -n "$t" ] || { say "[跳过] pet.token：桌宠还没生成令牌，先启动一次桌宠再跑本脚本"; return 0; }
  if HC_VAL=$t json_edit set "$DETECTOR_CFG" pet.token; then say "pet.token：已写入 $DETECTOR_CFG"
  else say "[跳过] $DETECTOR_CFG 不是合法 JSON，没改"; fi
}

pet_config_step() {
  if [ -z "$JSON_TOOL" ]; then say "[跳过] 没找到 node 或 python3，改不了 JSON：请手工改 $PET_CFG 的 server 与 deviceToken"; return 0; fi
  if [ "$UNINSTALL" = 1 ]; then
    [ -f "$PET_CFG" ] || return 0
    { json_edit unset "$PET_CFG" server && json_edit unset "$PET_CFG" deviceToken; } && say "桌宠 config.json：已删除 server、deviceToken" || say "[跳过] $PET_CFG 不是合法 JSON"
    return 0
  fi
  [ -n "${TOK:-}" ] || { say "[跳过] 桌宠 config.json：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 或在终端里跑）"; return 0; }
  (umask 077; mkdir -p "$PET_DIR")
  if (umask 077; HC_VAL=$SRV json_edit set "$PET_CFG" server && HC_VAL=$TOK json_edit set "$PET_CFG" deviceToken); then
    say "桌宠 config.json：已写入 $PET_CFG（server、deviceToken）"
  else say "[跳过] $PET_CFG 不是合法 JSON，没改"; fi
}

# ---- 主流程 ----
[ "$UNINSTALL" = 1 ] || collect_creds
[ "$NO_SHELL" = 1 ] || title_step
[ "$NO_DETECTOR" = 1 ] || detector_step
aw_step
[ "$NO_HOOKS" = 1 ] || hooks_step
PET_STARTED=''
if [ "$NO_PET" = 0 ]; then
  if [ "$UNINSTALL" = 1 ]; then pet_install_step; pet_config_step
  else
    if pet_install_step; then pet_config_step; pet_launch_step; [ -z "${HONEYCOMB_NO_START:-}" ] && PET_STARTED=1
    else pet_config_step; fi
  fi
fi
[ "$NO_PET" = 1 ] || pet_token_step

if [ "$UNINSTALL" = 1 ]; then
  rmdir "$APP_DIR" 2>/dev/null || true
  say "已卸载：上面列出的都撤回了。（ActivityWatch、浏览器扩展是你自己装的，没动）"
else
  say ""
  say "已装好："
  [ -n "$DONE" ] && printf '%s\n' "${DONE#
}" || say "  （没有新装的东西）"
  if [ -n "$TODO" ]; then say "还要你手工做："; printf '%s\n' "${TODO#
}"; fi
fi
