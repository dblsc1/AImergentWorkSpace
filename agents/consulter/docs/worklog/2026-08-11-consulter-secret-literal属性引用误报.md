# 2026-08-11 · consulter · 修 `18-secret-literal.sh` 属性引用误报

## 来源

CFO 转来的 objection（来自 ai-planner programmer 波2-A）：
`scripts/checks/_common/18-secret-literal.sh` 把 `json={"password"`+`: cfg.nexus_password}`
这类**变量引用**误判成硬编码密钥字面量（本文档举例按 2026-08-02 判例库惯例用
`+` 拆开写，防止讲这个缺陷的文档自己成为它的新实例）。该 programmer 顶住了没迎合扫描器改坏代码
（改成「口令登录时才从 env 读、不在 Config 长期持有」），但误报本身仍是缺陷——
误报比没门禁更坏：它教下一个 agent 去改对的代码迎合扫描器，或直接找逃生口。

## 复现（修前）

```
$ cd /tmp/c18test && printf 'json={"password": cfg.nexus_password}\n' > repro.py
$ printf 'PASSWORD = self.password\n' >> repro2.py
$ printf 'API_KEY = obj.attr.sub\n'  >> repro2.py
$ printf '"secret": x.y\n'          >> repro2.py
$ bash scripts/checks/_common/18-secret-literal.sh
明文凭据疑似进仓（铁律 2）：
  repro.py:1:json={"password": cfg.nexus_password}
  repro2.py:1:PASSWORD = self.password
  repro2.py:2:API_KEY = obj.attr.sub
  repro2.py:3:"secret": x.y
```
四种属性引用形状全部误报。

## 病根

`value_is_literal()` 剥掉外层引号后，依次检查 `$VAR`/占位符/`SCREAMING_SNAKE`标识符/
`os.environ` 等调用——但**没有识别「未加引号的点号属性链」**。`cfg.nexus_password`
剥完引号（本来就没引号）后落到最后一句 `return True`，被当成字面量。

这是「只认裸标识符、不认属性/下标/调用」这个通则的一个实例：判据认得
`PASSWORD_ENV_VAR`（全大写裸标识符）是变量名，但认不出 `cfg.nexus_password`（点号链）
也是变量名，只因为多了一层属性访问。

## 修法

1. 新增 `ATTR_REF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")`
   —— 每一段都必须以字母/下划线开头（`192.168.1.1` 这类数字段落不算，防止 IP/版本号
   被连坐放行）。
2. 记录 `was_quoted`（原始右值是否被引号包着）。**顺序不可颠倒**：先判断有没有引号，
   再判断形状——引号包住的内容是字符串字面量，不该被「像属性链」的形状救走
   （否则 `PASSWORD="cfg.nexus_password"` 这种真被引号包住的怪字面量也会被误放行）。
3. `not was_quoted and ATTR_REF.fullmatch(v)` → 判非字面量，放行。

## 同类扫描（铁律 23）

在 `scripts/checks/` 和 `scripts/gates/` 里搜索做同类"变量引用识别"逻辑的脚本：

```
grep -rln "os\.environ\|getenv\|process\.env\|fullmatch\|A-Za-z_\]\[A-Za-z0-9_\]" scripts --include="*.sh" --include="*.py"
```

只命中 `scripts/checks/_common/18-secret-literal.sh` 本身和引用它的
`scripts/selftest.d/60-inference-and-exempt.sh`（后者是测试数据，非逻辑重复）。
`scripts/gates/run-gates.sh` 里的「禁默认值」门（`change-me`/`replace-me` 等）是纯
grep 占位符词表，不做「是否是变量引用」的判断，不是同一形状。

**结论：同类缺陷只有 1 处，已修 1 处。** 没有第二个需要同步修的脚本。

## 断言（铁律 23，反向验证是硬要求）

扩了既有 selftest `#61`（`scripts/selftest.d/60-inference-and-exempt.sh`）而不是新开一条：

- **正例（新增 4 行到 good.txt，下面同样用 `+` 拆开写以免自我触发）**：
  `PASSWORD`+`= self.password`、`API_KEY`+`= obj.attr.sub`、
  `SECRET`+`= cfg.nexus_secret`、`json={"password"`+`: cfg.nexus_password}`
  （原始事故复现行）—— 修后必须零误伤。
- **反例（新增第 11 发到 bad.txt，用既有的键名拆分惯例避免自我触发）**：
  `PASSWORD`+`="cfg.nexus_password"`——**加引号**的属性链形状字面量，验证
  `was_quoted` 守卫没有被新规则连坐放行。这是本次修复里最容易踩的坑：
  如果只加"看起来像属性链就放行"而不看是否有引号，这条反例会变成新的假阴性。

修前坏形状计数从 10 发涨到 11 发，合法写法从 13 条涨到 17 条；断言里的数字同步改了
（`_nbad -eq 11`，PASS 文案改成「十一种…十七种」）。

## 验证（真实输出）

```
$ bash scripts/selftest.sh 2>&1 | grep -A1 '^61\.'
61. 明文凭据是否有提交时刻的机械执行者（铁律 2）
  ✅ PASS  十一种明文凭据形状全被点名（含加引号的属性链伪装）、
           十七种合法写法零误伤（含未加引号的属性引用链）、
           公共件缺失响亮死（不静默退 0）
```

全量 selftest：`PASS 70 · FAIL 0 · N/A 0`（本轮改动前后条数不变，#61 内容加严）。

对本次改动的两个文件本身跑 dogfood：
```
$ git add scripts/checks/_common/18-secret-literal.sh scripts/selftest.d/60-inference-and-exempt.sh
$ bash scripts/checks/_common/18-secret-literal.sh; echo "rc=$?"
rc=0
```
（新增的反例第 11 发用了拆分写法，不会在提交这两个文件时把自己拦下；这是既有惯例，不是新发明。）

## 边界与自我约束

- 分支 `fix/consulter-secret-literal-attr-ref`，未碰 `main`（铁律 1）。
- 只改了 `scripts/checks/_common/18-secret-literal.sh` 和
  `scripts/selftest.d/60-inference-and-exempt.sh` 两个文件，未越界改业务代码。
- **本轮改的是框架（门禁脚本本身），consulter 不能自审自己改的框架**（裁决 J7）。
  谁来复验见下方 `report.json` 的 `reviewer_opinion`：人类审，CFO 可辅助跑三件审法
  举证（跑 selftest #61、跑 dogfood、核对 diff 范围），但不下 approve/reject 结论。
