#!/bin/sh
# HoneyComb 连接脚本（Linux / macOS）。不下载、不安装任何程序本体。
#
#   sh honeycomb-connect.sh                      （交互输入服务器地址与设备令牌，令牌不回显）
#   read -rs HONEYCOMB_DEVICE_TOKEN; export HONEYCOMB_DEVICE_TOKEN; sh honeycomb-connect.sh   （不要把令牌写在命令前，会进 shell 历史）
#   sh honeycomb-connect.sh --uninstall
#
# 做三件事（可重复跑）：
#   1. 终端标题写进 ~/.bashrc / ~/.zshrc（标记两行之间；再跑 = 替换这一段）
#   2. 桌宠令牌（桌宠数据目录 pet-token）写进 ai-detector 配置的 pet.token
#   3. 服务器地址与设备令牌写进桌宠 config.json（键 server、deviceToken）
# 环境变量：HONEYCOMB_SERVER（缺省 http://127.0.0.1:8800）、HONEYCOMB_DEVICE_TOKEN（不设就交互输入，不回显）、
#   AI_DETECTOR_HOME（同 ai-detector）、HONEYCOMB_PET_HOME（覆盖桌宠数据目录，测试用）
# --uninstall：删掉标记段，并删掉上面第 2、3 步写进去的键。
set -eu

START='# >>> honeycomb terminal title >>>'
END='# <<< honeycomb terminal title <<<'

case "${1:-}" in
  '') UNINSTALL=0 ;;
  --uninstall) UNINSTALL=1 ;;
  *) echo "用法：sh honeycomb-connect.sh [--uninstall]" >&2; exit 2 ;;
esac

say() { printf '%s\n' "$*"; }

if [ "$(uname -s)" = Darwin ]; then
  CFG_BASE="$HOME/Library/Application Support"
else
  CFG_BASE="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
DETECTOR_DIR="${AI_DETECTOR_HOME:-$CFG_BASE/honeycomb}"
PET_DIR="${HONEYCOMB_PET_HOME:-$CFG_BASE/honeycomb-desktop-pet}"
DETECTOR_CFG="$DETECTOR_DIR/ai-detector.json"
PET_CFG="$PET_DIR/config.json"

# ---- JSON：只改一个键，保序。node 或 python3，找得到哪个用哪个 ----
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
if(op==="set")c[l]=process.env.HC_VAL;else delete c[l];
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
if op=="set": c[k[-1]]=os.environ["HC_VAL"]
else: c.pop(k[-1],None)
if op=="unset" and len(k)>1 and not c: o.pop(k[0],None)
open(f,"w",encoding="utf-8").write(json.dumps(o,indent=2,ensure_ascii=False)+"\n")
os.chmod(f,0o600)
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
  files=''
  [ -f "$HOME/.bashrc" ] && files="bash"
  [ -f "$HOME/.zshrc" ] && files="$files zsh"
  if [ -z "$files" ] && [ "$UNINSTALL" = 0 ]; then
    case "$(basename "${SHELL:-}")" in
      bash) files=bash ;;
      zsh) files=zsh ;;
      *) say "[跳过] 终端标题：没有 ~/.bashrc 或 ~/.zshrc，当前 shell 也不是 bash / zsh"; return 0 ;;
    esac
  fi
  bf=$(mktemp)
  for sh_name in $files; do
    if [ "$sh_name" = bash ]; then rc="$HOME/.bashrc"; else rc="$HOME/.zshrc"; fi
    if [ "$UNINSTALL" = 1 ]; then
      replace_block "$rc" ''; say "终端标题：已从 $rc 删除"
    else
      [ -f "$rc" ] || : > "$rc"
      "${sh_name}_block" > "$bf"
      replace_block "$rc" "$bf"; say "终端标题：已写入 $rc"
    fi
  done
  rm -f "$bf"
}

# ---- 2、3. 令牌与地址 ----
have_tty() { stty -g </dev/tty >/dev/null 2>&1; }

pet_token_step() {
  if [ -z "$JSON_TOOL" ]; then say "[跳过] 没找到 node 或 python3，改不了 JSON：请手工把桌宠令牌写进 $DETECTOR_CFG 的 pet.token"; return 0; fi
  [ -f "$DETECTOR_CFG" ] || { say "[跳过] pet.token：没有 $DETECTOR_CFG，先跑一次 ai-detector init"; return 0; }
  if [ "$UNINSTALL" = 1 ]; then
    json_edit unset "$DETECTOR_CFG" pet.token && say "pet.token：已删除" || say "[跳过] $DETECTOR_CFG 不是合法 JSON"
    return 0
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
  tok=${HONEYCOMB_DEVICE_TOKEN:-}
  if [ -z "$tok" ] && have_tty; then
    printf '设备令牌（不回显）：' >/dev/tty; trap 'stty echo </dev/tty; exit 130' INT TERM HUP
    stty -echo </dev/tty; IFS= read -r tok </dev/tty || true; stty echo </dev/tty; trap - INT TERM HUP; echo >/dev/tty
  fi
  [ -n "$tok" ] || { say "[跳过] 桌宠 config.json：没有设备令牌（设 HONEYCOMB_DEVICE_TOKEN 或在终端里跑）"; return 0; }
  srv=${HONEYCOMB_SERVER:-}
  if [ -z "$srv" ] && have_tty; then printf '服务器地址 [http://127.0.0.1:8800]：' >/dev/tty; IFS= read -r srv </dev/tty || true; fi
  srv=${srv:-http://127.0.0.1:8800}
  (umask 077; mkdir -p "$PET_DIR")
  if (umask 077; HC_VAL=$srv json_edit set "$PET_CFG" server && HC_VAL=$tok json_edit set "$PET_CFG" deviceToken); then
    say "桌宠 config.json：已写入 $PET_CFG（server、deviceToken）"
  else say "[跳过] $PET_CFG 不是合法 JSON，没改"; fi
}

title_step
pet_token_step
pet_config_step
