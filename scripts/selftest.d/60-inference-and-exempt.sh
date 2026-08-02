# 断言 56–61 · 角色推断 / 行级豁免 / 审核覆盖 / 密钥判据
# 由 scripts/selftest.sh 按文件名顺序 source；共享 repo/S/P/F/N 与 pass/fail/na 计数。
# 本文件不可单独执行（没有那些变量），入口永远是 scripts/selftest.sh。

# 56 ── 角色自动推断是否覆盖 J3 之后的全部 canonical 留痕路径（F3）
#      **这一条是为了不把 #50 的坑重挖一遍而存在的**：#50 的沙箱只喂
#      codeagent/programmer/docs/worklog/x.md —— 五条里唯一还能命中旧正则的那条，
#      于是断言恒绿而现实全是 unknown。这里逐条喂，缺一条就判不合格。
printf '56. 角色推断是否覆盖 J3 canonical 留痕路径（逐条喂）\n'
if [ ! -f "$S/lib/paths.sh" ] || ! grep -q 'role_from_trace_path' "$S/lib/paths.sh" 2>/dev/null; then
  F "lib/paths.sh 里没有 role_from_trace_path —— 角色推断没有公共件，各处各写一套（F3）"
else
  # 逐条喂：<路径>|<期望角色>|<仓型>（m=模块仓 / f=框架仓；仓型影响 code/ 的判读）
  _cases56=(
    "agents/consulter/docs/worklog/x.md|consulter|f"          # ref-fixture
    "agents/cfo/docs/worklog/x.md|cfo|f"                      # ref-fixture
    "module_docs/worklog/x.md|arbiter|m"                      # ref-fixture
    "code/backend/worklog/x.md|programmer|m"                  # ref-fixture
    "code/backend/orders/report.json|programmer|m"            # ref-fixture
    "review/reviewreport/report.json|programmer_reviewer|m"   # ref-fixture
    "review/reviewcode/tests/t.sh|programmer_reviewer|m"      # ref-fixture
    "codeagent/programmer/docs/worklog/x.md|programmer|m"     # ref-fixture
    "codeagent/programmer/01/docs/comm.jsonl|programmer|m"    # ref-fixture
    "codeagent/module_reviewer/docs/report.json|module_reviewer|m"  # ref-fixture
  )
  _sbx56=$(mktemp -d); mkdir -p "$_sbx56/m/codeagent" "$_sbx56/m/module_docs" "$_sbx56/f"
  _miss56=""
  for _case in "${_cases56[@]}"; do
    _p56=${_case%%|*}; _rest56=${_case#*|}; _want56=${_rest56%%|*}; _kind56=${_rest56#*|}
    _got56=$( cd "$_sbx56/$_kind56" && . "$S/lib/paths.sh" && \
              printf '%s\n' "$_p56" | role_from_staged )
    [ "$_got56" = "$_want56" ] || _miss56="$_miss56
       $_p56 → 期望 $_want56，实得「${_got56:-<空>}」"
  done
  # 反面两条：框架根的 code/ 装的是模块仓不是代码侧；同层两个角色必须判歧义
  _fwc=$( cd "$_sbx56/f" && . "$S/lib/paths.sh" && printf 'code/gantt/x.md\n' | role_from_staged )   # ref-fixture
  [ -z "$_fwc" ] || _miss56="$_miss56
       框架仓的 code/gantt/x.md 被推成「$_fwc」—— 框架根的 code/ 装的是模块仓"   # ref-fixture
  _amb56=$( cd "$_sbx56/f" && . "$S/lib/paths.sh" && \
            printf 'agents/cfo/docs/a.md\nagents/consulter/docs/b.md\n' | role_from_staged )   # ref-fixture
  [ -z "$_amb56" ] || _miss56="$_miss56
       同层出现 cfo 与 consulter 两个角色却给了「$_amb56」—— 歧义必须返回空，不许挑第一个"
  rm -rf "$_sbx56"
  if [ -n "$_miss56" ]; then
    F "角色推断没覆盖全部 canonical 路径（F3）：$_miss56"
  else
    P "十条 canonical 留痕路径逐条命中；框架仓 code/ 不误判；同层歧义返回空不静默挑第一个"
  fi
fi

# 57 ── ref-fixture 行级标记：既要能豁免测试输入，又**不许把这道门变哑**
#      成因：F3 的断言必须把 J3 canonical 路径写成字面量喂进角色推断，
#      而 check-references 把它们全判成悬空引用。逐条登记进 doc-path-exempt.txt
#      只修实例——下一个写断言的人照样撞，且登记表会长成一张混着
#      「真的还没建」与「永远不会建」的名单，再也分不开。
#      行级标记修的是类；但**任何豁免机制都必须验它没顺手把门关掉**。
printf '57. ref-fixture 标记豁免测试输入，但不许把引用完整性变哑\n'
if [ ! -x "$S/gates/check-references.sh" ]; then
  N "本仓没有 gates/check-references.sh，不适用"
else
  _sbx57=$(mktemp -d); git -C "$_sbx57" init -q
  mkdir -p "$_sbx57/scripts/gates"
  cp "$S/gates/check-references.sh" "$_sbx57/scripts/gates/"
  chmod +x "$_sbx57/scripts/gates/check-references.sh"
  : > "$_sbx57/scripts/gates/doc-path-exempt.txt"
  # 一行带标记（应放行）、一行不带（应照红）。两条路径都真不存在。
  {
    printf '#!/usr/bin/env bash\n'
    printf 'fixture="scripts/never-exists-fixture.sh"   # ref-fixture\n'
    printf 'realref="scripts/never-exists-dangling.sh"\n'   # ref-fixture
  } > "$_sbx57/scripts/probe.sh"
  git -C "$_sbx57" add -A >/dev/null 2>&1
  _o57=$( cd "$_sbx57" && bash scripts/gates/check-references.sh 2>&1 )
  rm -rf "$_sbx57"
  if grep -q 'never-exists-fixture' <<<"$_o57"; then
    F "带 ref-fixture 标记的测试输入仍被判成悬空引用 —— 类没修掉，下一个写断言的人还得逐条登记"
  elif ! grep -q 'never-exists-dangling' <<<"$_o57"; then
    F "没带标记的真悬空引用也没被抓 —— 豁免机制把整道门变哑了（比误报危险得多）"
  else
    _md57=""
    # 同一个约定必须在 **.md 一侧**也成立：判例库里「讲某个路径类缺陷」的条目会
    # 逐字引用那些路径，于是讲缺陷的文档自己成了该缺陷的新实例（「字面量类」第四次复发）。
    # 两个检查器共用一个词，不要各造一套 —— 否则下一个人得记两套规矩。
    if [ -x "$S/checks/_common/60-doc-paths-exist.sh" ]; then
      _sbx57b=$(mktemp -d); git -C "$_sbx57b" init -q
      git -C "$_sbx57b" config user.email t@e.com; git -C "$_sbx57b" config user.name t
      mkdir -p "$_sbx57b/scripts/gates" "$_sbx57b/scripts/checks/_common" "$_sbx57b/scripts/lib"
      cp "$S/checks/_common/60-doc-paths-exist.sh" "$_sbx57b/scripts/checks/_common/"
      cp "$S/lib/paths.sh" "$_sbx57b/scripts/lib/"
      chmod +x "$_sbx57b/scripts/checks/_common/"*.sh
      : > "$_sbx57b/scripts/gates/doc-path-exempt.txt"
      printf '讲缺陷时逐字引用 `scripts/never-md-fixture.sh` 只是例子  <!-- ref-fixture -->\n真引用 `scripts/never-md-dangling.sh` 应该照红\n' \
        > "$_sbx57b/note.md"
      git -C "$_sbx57b" add -A >/dev/null 2>&1
      _o57b=$( cd "$_sbx57b" && bash scripts/checks/_common/60-doc-paths-exist.sh 2>&1 )
      rm -rf "$_sbx57b"
      grep -q 'never-md-fixture' <<<"$_o57b" && _md57="checks/60 不认 ref-fixture 标记（两个检查器约定不一致，人得记两套规矩）"
      grep -q 'never-md-dangling' <<<"$_o57b" || _md57="checks/60 连没带标记的真死链也不抓了 —— 标记把 .md 那道门也变哑了"
    fi
    if [ -n "$_md57" ]; then
      F "$_md57"
    else
      P "同一个 ref-fixture 约定在 .sh（check-references）与 .md（checks/60）两侧都成立；两侧都验过没把门变哑"
    fi
  fi
fi

# 58 ── 写区粒度棘轮：放宽必须被看见，但**不许硬拦**（F2）
#      五天 diary 的形状：05 拦下越区提交 → 持有者放宽自己的签 → 覆盖面越来越大，
#      每次膨胀前 90 秒内都紧跟一条 failed:write-lease。棘轮没有反向齿。
#      三件一起验：① 放宽要检出并说清哪一条 ② 不放宽不许误报
#      ③ **必须只警告不拦**（判断题硬拦会逼人用逃生口，逃生口用滥闸门全废）
#      ④ 警告必须带签史，且**永远含第一条** —— 只看最近几次看不出单调性
printf '58. 写区放宽是否被看见（且只警告不硬拦、带签史）\n'
if [ ! -f "$S/mission_start.sh" ] || ! grep -q 'lease_widening' "$S/lib/lease.sh" 2>/dev/null; then
  F "lib/lease.sh 没有 lease_widening —— 写区棘轮无人看见（F2）"
else
  _sbx58=$(mktemp -d)
  _mk_lease_sandbox "$_sbx58" someone_else 'unrelated-area/'
  _ld58="$_sbx58/.git/aimergent-leases"
  # 自己的旧签：docs/a/ —— 本轮申领 docs/ 是把它放宽
  printf 'docs/a/\n' > "$_ld58/consulter.lease"
  printf 'granted_at=%s\nttl=7200\ntask=上一轮\n' "$(date -u +%s)" > "$_ld58/consulter.meta"
  # 造六条签史，验「中间略、但第一条永远在」
  mkdir -p "$_sbx58/logs"
  for _i in 1 2 3 4 5 6; do
    printf '{"ts":"2026-07-0%sT01:02:03Z","event":"lease_grant","note":"consulter: docs/a/","rc":0}\n' "$_i"
  done > "$_sbx58/logs/diary.jsonl"
  _rc58=0
  ( cd "$_sbx58" && bash scripts/mission_start.sh consulter task.md docs/ ) \
    >"$_sbx58/wide.txt" 2>&1 || _rc58=$?
  _w58=$(cat "$_sbx58/wide.txt")
  # 收窄/不变的一侧：申领 docs/a/b/（落在旧签里）不该报放宽
  printf 'docs/a/\n' > "$_ld58/consulter.lease"
  printf 'granted_at=%s\nttl=7200\ntask=上一轮\n' "$(date -u +%s)" > "$_ld58/consulter.meta"
  ( cd "$_sbx58" && bash scripts/mission_start.sh consulter task.md docs/a/b/ ) \
    >"$_sbx58/narrow.txt" 2>&1
  _n58=$(cat "$_sbx58/narrow.txt")
  rm -rf "$_sbx58"
  if ! grep -q '放宽：docs/a/ → docs/' <<<"$_w58"; then
    F "写区从 docs/a/ 放宽到 docs/ 没被检出 —— 棘轮转了没人看见（F2）"
  elif [ "$_rc58" -ne 0 ]; then
    F "放宽被**硬拦**了（rc=$_rc58）—— 判断题不做硬闸门，硬拦会逼人改用逃生口"
  elif ! grep -q '签史' <<<"$_w58" || ! grep -q '6 次发签' <<<"$_w58"; then
    F "警告没带签史 —— 只说「你的签有点宽」没有信息量，要说「从几片涨到几片、从未收窄」"
  elif ! grep -q '2026-07-01' <<<"$_w58"; then
    F "签史略掉了第一条 —— 棘轮比的是起点和现在，只看最近几次看不出单调性"
  elif grep -q '放宽：' <<<"$_n58"; then
    F "申领的写区落在旧签里（收窄）却报了放宽 —— 误报会训练人忽略这条警告"
  else
    P "放宽被检出并指名道姓、只警告不拦（rc=$_rc58）、签史含第一条、收窄不误报"
  fi
fi

# 59 ── 合并门的审核覆盖断言：审得越窄不许越容易过（F5）
#      原来 verify_report **只读 .review_target.head，从不读 .base**。
#      前向那头有守（白名单卡 reviewed..candidate），后向那头完全没守：
#      分支上 C1..C3，reviewer 只审最后一段，前面几个 commit 一次没被审就合进去。
#      这里造**真实 commit 链**喂 lib/review.sh 里的**那一份** verify_report，
#      不是抄一份等价逻辑去测（判例库「被测对象是哪一份」）。
printf '59. 合并门是否断言审核覆盖了被合并的工作\n'
if [ ! -f "$S/lib/review.sh" ]; then
  F "没有 lib/review.sh —— 审核绑定判据还留在 merge-to-integration.sh 里，沙箱够不着（那正是 F5 躺了四天的原因）"
elif grep -q '^verify_report() {' "$S/merge-to-integration.sh" 2>/dev/null; then
  F "merge-to-integration.sh 里还有第二份 verify_report 定义 —— 测的那份和跑的那份会漂移"
else
  _sbx59=$(mktemp -d); git -C "$_sbx59" init -q
  git -C "$_sbx59" config user.email t@example.com; git -C "$_sbx59" config user.name selftest
  mkdir -p "$_sbx59/agents/consulter/docs/findings" "$_sbx59/agents/consulter/docs/worklog" "$_sbx59/scripts"
  _rp59=agents/consulter/docs/findings/report.json
  _wl59=agents/consulter/docs/worklog/w.md   # ref-fixture
  echo base > "$_sbx59/README.md"
  git -C "$_sbx59" add -A >/dev/null; git -C "$_sbx59" commit -q --no-verify -m M
  _M59=$(git -C "$_sbx59" rev-parse HEAD)                 # 集成基线 = 分叉点
  echo work > "$_sbx59/scripts/thing.sh"                  # C1：一段真实工作
  git -C "$_sbx59" add -A >/dev/null; git -C "$_sbx59" commit -q --no-verify -m C1
  echo more >> "$_sbx59/README.md"
  git -C "$_sbx59" add -A >/dev/null; git -C "$_sbx59" commit -q --no-verify -m C2
  _C259=$(git -C "$_sbx59" rev-parse HEAD)                # 审核 head 固定在这里
  _try59() {   # _try59 <review_target.base> → verify_report 的退出码
    echo w > "$_sbx59/$_wl59"
    cat > "$_sbx59/$_rp59" <<JSON
{"schema_version":2,"module":"m","role":"consulter","task":"t","tier":"simple",
 "status":"approved","summary":"s",
 "git":{"protocol":"embedded-self-v2","branch":"feat/x","base":"$_M59","head":"SELF",
        "diff_mode":"contains","changed_files":["$_rp59","$_wl59"]},
 "review_target":{"branch":"feat/x","base":"$1","head":"$_C259",
                  "diff_mode":"exact","changed_files":[]}}
JSON
    git -C "$_sbx59" add -A >/dev/null
    git -C "$_sbx59" commit -q --no-verify -m C3 >/dev/null 2>&1 ||
      git -C "$_sbx59" commit -q --no-verify --amend -m C3 >/dev/null 2>&1
    ( repo=$_sbx59; candidate=$(git -C "$_sbx59" rev-parse HEAD); branch=feat/x; main_sha=$_M59
      . "$S/lib/review.sh"; verify_report "$_rp59" consulter >/dev/null 2>&1; echo $? )
  }
  _wide59=$(_try59 "$_M59")     # 从分叉点起审 → 应放行
  _narrow59=$(_try59 "$_C259")  # 只审最后一段（C1 没人看过）→ 应拒绝
  rm -rf "$_sbx59"
  if [ "$_wide59" != 0 ]; then
    F "从分叉点起审的报告被合并门拒了（rc=$_wide59）—— 判据过严会逼人用逃生口"
  elif [ "$_narrow59" = 0 ]; then
    F "审核区间不覆盖分支起点也照样放行 —— 审得越窄越容易过（F5），前面的 commit 一次没被审就合进去"
  else
    P "审核区间从分叉点起→放行；起点落在分叉点之后（前面的 commit 没人看过）→拒绝（rc=$_narrow59）"
  fi
fi

# 60 ── consulter 非审查轮次：显式 null 放行，省略仍判否（F6）+ 门禁输出无 locale 噪音（F7）
#      F6 病根：schema 把 consulter 与两个 reviewer 同等对待，缺 review_target 即判否，
#      而 consulter 多数轮次根本不产生审查区间 —— 于是只能回填。commit ee5806a 的标题
#      就是证据：「恢复 review_target 最近审查区间——推送门 schema 要求」。
#      **一个为满足门禁而回填的字段不再承载信息**，还会被合并门当成真审核凭据。
#      修法不是「可以不写」，是「必须显式声明 null」：省略是疏忽，null 是决定。
printf '60. consulter 非审查轮次可显式声明；省略仍判否；输出无 locale 噪音\n'
if [ ! -x "$S/gates/check-report-schema.sh" ]; then
  N "本仓没有 gates/check-report-schema.sh，不适用"
else
  _sbx60=$(mktemp -d); git -C "$_sbx60" init -q -b main
  git -C "$_sbx60" config user.email t@example.com; git -C "$_sbx60" config user.name selftest
  mkdir -p "$_sbx60/agents/consulter/docs/findings" "$_sbx60/agents/consulter/docs/worklog" \
           "$_sbx60/scripts/gates"
  cp "$S/gates/check-report-schema.sh" "$_sbx60/scripts/gates/"; chmod +x "$_sbx60/scripts/gates/"*.sh
  echo base > "$_sbx60/README.md"
  git -C "$_sbx60" add -A >/dev/null; git -C "$_sbx60" commit -q --no-verify -m base
  _M60=$(git -C "$_sbx60" rev-parse HEAD)
  git -C "$_sbx60" checkout -q -b feat/x
  _rp60=agents/consulter/docs/findings/report.json
  # F7 的 fixture：**点文件 + 大写文件名**。上一轮我把成因判成「非 ASCII」，
  # 实测证伪——中文路径同序，真正触发 locale/字节序分歧的是
  # 「前导标点被忽略」（.gitignore vs code/x）与「不分大小写」（README.md vs gates/）。
  # 第一版断言用中文名，红测时**没能变红**，就是这个错误诊断的直接后果。
  _wl60='agents/consulter/docs/worklog/工作日志.md'
  _dot60='.gitignore'; _up60='scripts/README.md'
  _run60() {   # _run60 <review_target 的 JSON 片段> → 退出码（stderr 落 err.txt）
    echo w > "$_sbx60/$_wl60"; echo ig > "$_sbx60/$_dot60"; echo up > "$_sbx60/$_up60"
    cat > "$_sbx60/$_rp60" <<JSON
{"schema_version":2,"module":"m","role":"consulter","task":"t","tier":"simple",
 "status":"approved","summary":"s",
 "git":{"protocol":"embedded-self-v2","branch":"feat/x","base":"$_M60","head":"SELF",
        "diff_mode":"contains","changed_files":["$_rp60","$_wl60","$_dot60","$_up60"]},
 "contract":{"touched":false,"which":[],"consumes":[]},
 "cross_module_impact":[],"escalation":null$1}
JSON
    git -C "$_sbx60" add -A >/dev/null
    git -C "$_sbx60" commit -q --no-verify -m r >/dev/null 2>&1 ||
      git -C "$_sbx60" commit -q --no-verify --amend -m r >/dev/null 2>&1
    ( cd "$_sbx60" && AIMERGENT_REPORT_BASE=$_M60 bash scripts/gates/check-report-schema.sh \
        >/dev/null 2>"$_sbx60/err.txt"; printf '%s' $? )
  }
  _null60=$(_run60 ',"review_target":null')          # 显式声明非审查轮次 → 应放行
  _err_null=$(cat "$_sbx60/err.txt")
  _omit60=$(_run60 '')                                # 整个键省略 → 仍应判否
  _err_omit=$(cat "$_sbx60/err.txt")
  # 真审查轮次：review_target 是对象，且 changed_files 必须与 diff 完全相等（含非 ASCII 路径）
  _H60=$(git -C "$_sbx60" rev-parse HEAD)
  _real60=$(_run60 ",\"review_target\":{\"branch\":\"feat/x\",\"base\":\"$_M60\",\"head\":\"$_H60\",\"diff_mode\":\"exact\",\"changed_files\":[\"$_rp60\",\"$_wl60\",\"$_dot60\",\"$_up60\"]}")
  _err_real=$(cat "$_sbx60/err.txt")
  rm -rf "$_sbx60"
  if [ "$_null60" != 0 ]; then
    F "consulter 显式写 review_target 为 null 仍被判否（rc=$_null60）—— 非审查轮次只能继续回填假区间（F6）"
  elif [ "$_omit60" = 0 ]; then
    F "整个 review_target 键省略也放行 —— 省略与显式声明分不开了，疏忽会被当成决定（F6）"
  elif ! grep -q 'review_target' <<<"$_err_omit"; then
    F "省略时判否了，但没说清缺的是 review_target —— 错误信息把人引向错的地方"
  elif grep -q 'not in sorted order' <<<"$_err_null$_err_omit$_err_real"; then
    F "门禁 stderr 仍有 comm 排序噪音（F7）—— 绿灯里混红色输出会训练人忽略门禁"
  else
    P "显式 null 放行、省略判否且说明原因、真审查轮次（含点文件/大写名/中文名）全绿且 stderr 干净"
  fi
fi

# 61 ── 铁律 2 有没有机械执行者（2026-08-02 P0：口令明文进了三个模块仓）
#      此前唯一相关的 gate 是 gitleaks，而它 ① 配置是空壳零规则
#      ② 被 RUN_GITLEAKS_LOCAL=1 挡着 ③ 本机没装二进制 —— 三重失效，
#      铁律 2 写了一个多月，一次都没执行过。
#      实弹：三种不同形状的明文凭据各喂一次（shell export / JSON / 裸 token），
#      再喂七种合法写法确认不误伤。**判据本身不许含真实口令**，所以样本用假值。
printf '61. 明文凭据是否有机械执行者（铁律 2）\n'
_c18="$S/checks/_common/18-secret-literal.sh"
if [ ! -f "$_c18" ]; then
  F "没有 checks/_common/18-secret-literal.sh —— 铁律 2 零执行者（gitleaks 那条是空壳+被开关挡着+本机无二进制）"
else
  _sbx=$(mktemp -d); git -C "$_sbx" init -q
  mkdir -p "$_sbx/scripts/checks/_common" "$_sbx/scripts/lib"
  cp "$_c18" "$_sbx/scripts/checks/_common/"; chmod +x "$_sbx/scripts/checks/_common/18-secret-literal.sh"
  cp "$S/lib/paths.sh" "$_sbx/scripts/lib/" 2>/dev/null
  # 三种坏形状（全是假值）
  printf 'export APP_TEST_PASSWORD=notarealpw\n'  > "$_sbx/bad1.md"
  printf '{"password":"notarealpw"}\n'            > "$_sbx/bad2.json"
  printf 'GITHUB_TOKEN=notarealtoken\n'           > "$_sbx/bad3.sh"
  # 七种合法写法
  {
    printf '口令只从 APP_TEST_PASSWORD 读，缺了就响亮跳过。\n'
    printf 'export APP_TEST_PASSWORD=$MY_PW\n'
    printf 'APP_TEST_PASSWORD=${MY_PW}\n'
    printf 'APP_TEST_PASSWORD=<你的口令>\n'
    printf 'APP_TEST_PASSWORD=changeme\n'
    printf 'pw = os.environ["APP_TEST_PASSWORD"]\n'
    printf 'tok = os.getenv("GITHUB_TOKEN")\n'
  } > "$_sbx/good.md"
  git -C "$_sbx" add -A >/dev/null 2>&1
  _out=$( cd "$_sbx" && bash scripts/checks/_common/18-secret-literal.sh 2>&1 )
  _rc_bad=$( cd "$_sbx" && bash scripts/checks/_common/18-secret-literal.sh >/dev/null 2>&1; echo $? )
  ( cd "$_sbx" && git rm -q --cached bad1.md bad2.json bad3.sh >/dev/null 2>&1; rm -f bad1.md bad2.json bad3.sh )
  _rc_good=$( cd "$_sbx" && bash scripts/checks/_common/18-secret-literal.sh >/dev/null 2>&1; echo $? )
  rm -rf "$_sbx"
  _named=0
  for _b in bad1.md bad2.json bad3.sh; do case "$_out" in *"$_b"*) _named=$((_named+1)) ;; esac; done
  if [ "$_rc_bad" -ne 0 ] && [ "$_rc_good" -eq 0 ] && [ "$_named" -eq 3 ]; then
    P "三种形状的明文凭据各自被点名（shell/JSON/裸 token），七种合法写法零误伤"
  else
    F "密钥判据失灵：坏样本 exit=$_rc_bad（应非0，点名 $_named/3）、合法写法 exit=$_rc_good（应0）"
  fi
fi

# 62 ── C1（单文件行数）有没有机械执行者
#      C1 是人类 2026-07-29 的裁决（台账 D12），三档写得死死的，
#      但从落地到 2026-08-02 一直是散文。**不是"没有 gate"，比那更糟：
#      gate 一直在，而且一直打印 ✅「无超限文件」** —— 两个独立原因让它全瞎：
#        ① tracked 用 `[ -d code ]` 判仓型，框架仓的 code/ 也存在，
#           于是只扫 code/_template 下 26 个文件（scripts/ agents/ 全不在视野）
#        ② 扩展名白名单只认 py/js/ts/html/css/vue/svelte —— .sh .md .json 都不看
#      任一原因单独存在就足够让 1129 行的 selftest.sh 隐身。恒定答案 = 没有这道门。
#
#      **沙箱必须建出 code/ 目录**：那是原 bug 的触发条件。少了它，
#      这条断言会在 bug 存在时照样绿 —— 又一台验不存在机制的烟雾报警器。
printf '62. 单文件行数三档（C1）是否有机械执行者\n'
_rg="$S/gates/run-gates.sh"
if [ ! -f "$_rg" ]; then
  F "没有 gates/run-gates.sh"
elif ! command -v jq >/dev/null 2>&1; then
  N "本机无 jq，无法核 501–1000 那一档的 report.json 记账"
else
  _sb62=$(mktemp -d); git -C "$_sb62" init -q
  mkdir -p "$_sb62/scripts/gates" "$_sb62/scripts/lib" "$_sb62/code" "$_sb62/agents/x/docs"
  cp "$_rg" "$_sb62/scripts/gates/"; cp "$S/lib/paths.sh" "$_sb62/scripts/lib/"
  printf 'x\n' > "$_sb62/code/keep.txt"          # ← 触发旧 bug 的条件：code/ 存在
  printf '{"schema_version":2}\n' > "$_sb62/agents/x/docs/report.json"
  _seg62() { ( cd "$_sb62" && bash scripts/gates/run-gates.sh 2>&1 ) | sed -n '/单文件行数/,/gitleaks/p'; }
  # 下面 scripts/big.sh、scripts/mid.sh 是喂给判据的合成输入，按定义不在本仓里存在。   # ref-fixture
  _big62() { { printf '#!/bin/sh\n'; [ -n "${1:-}" ] && printf '# %s\n' "$1"; _i=0; while [ $_i -lt "$2" ]; do printf ':\n'; _i=$((_i+1)); done; } > "$_sb62/scripts/big.sh"; git -C "$_sb62" add -A >/dev/null 2>&1; }   # ref-fixture

  _big62 '' 1200; _o62a=$(_seg62)                                   # >1000 硬拦
  _big62 '存量导入：下次重构拆掉' 1200; _o62b=$(_seg62)              # D2 只警告
  rm -f "$_sb62/scripts/big.sh"   # ref-fixture
  { printf '#!/bin/sh\n'; _i=0; while [ $_i -lt 700 ]; do printf ':\n'; _i=$((_i+1)); done; } > "$_sb62/scripts/mid.sh"   # ref-fixture
  git -C "$_sb62" add -A >/dev/null 2>&1; _o62c=$(_seg62)           # 501–1000 未记账
  # ⚠️ 续行 `\` 后面不许再跟注释：反斜杠会把注释前那个空格转义成字面空格，
  #    `#` 起的注释再吃掉换行，于是下一行 `> file` 变成一条**只有重定向**的命令，
  #    把 report.json 截成空文件 —— 而 `bash -n` 照样报 OK。所以写成一行。
  _rj62="$_sb62/agents/x/docs/report.json"
  printf '{"schema_version":2,"oversize_files":[{"path":"scripts/mid.sh","lines":701,"why":"t","plan":"t"}]}\n' > "$_rj62"   # ref-fixture
  git -C "$_sb62" add -A >/dev/null 2>&1; _o62d=$(_seg62)           # 记上那一笔 → 放行
  rm -rf "$_sb62"

  _v62=""
  case "$_o62a" in *"❌"*"scripts/big.sh"*) ;; *) _v62="$_v62 [>1000未硬拦]" ;; esac   # ref-fixture
  case "$_o62b" in *"❌"*) _v62="$_v62 [D2标记后仍硬拦]" ;; esac
  case "$_o62b" in *"⚠️"*"scripts/big.sh"*) ;; *) _v62="$_v62 [D2标记未打印警告]" ;; esac   # ref-fixture
  case "$_o62c" in *"❌"*"scripts/mid.sh"*) ;; *) _v62="$_v62 [501-1000未记账却放行]" ;; esac   # ref-fixture
  case "$_o62d" in *"❌"*"scripts/mid.sh"*) _v62="$_v62 [记了账仍拦]" ;; esac   # ref-fixture
  # ⚠️ 这条子断言早先写成 `*"扫描 "*` —— 恒真：sed 区间的**末行**是
  #    「── gate: gitleaks 密钥扫描 ──」，自带「扫描 」。删掉打印行照样绿。
  #    反向验证当场逮住（判例库：反向验证是唯一可信的证明，看代码不算）。
  case "$_o62a" in *"个文本文件（跳过"*) ;; *) _v62="$_v62 [不打印被验对象]" ;; esac
  if [ -z "$_v62" ]; then
    P "三档齐全：>1000 硬拦、D2 顶部标记只警告、501–1000 记不到那一笔就红、记到就放行，且打印被验对象"
  else
    F "C1 判据失灵：$_v62"
  fi
fi
