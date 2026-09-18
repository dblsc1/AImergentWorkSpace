# 贡献

English: [CONTRIBUTING.md](CONTRIBUTING.md)

## 签署要求：DCO

本项目用 **DCO（Developer Certificate of Origin）**，不用 CLA。

区别只有一句：DCO 只让你**证明来源**，不让你**授予改许可的权利**。
你的贡献以 AGPL-3.0 进来，也永远是 AGPL-3.0 —— 项目方不能把它以闭源
条款卖出去。这是对贡献者的保证，不是形式。

在每个提交上加一行：

```
Signed-off-by: 你的名字 <你的邮箱>
```

`git commit -s` 会自动加。已经提交了忘记加，`git commit --amend -s` 补。
**未签署的提交不会被合并**，这条不商量 —— 因为一个仓里不能一半签一半没签。

### DCO 1.1 全文

签署 `Signed-off-by` 即表示你认可下面的 (a) 到 (d) 各条。

以下为 <https://developercertificate.org/> 的逐字原文，**含前言**。
那份文件自己写着「不许修改」，而**截短也算修改** —— 所以别把这个代码块
缩短，连版权头也别删。

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

注意 (d)：签名里的姓名与邮箱会**永久留在提交历史里**，且无法事后抹掉。
不想公开真名就用你在用的化名和一个你愿意公开的邮箱 —— 但必须是能联系到
你的、长期有效的。

---

## 改代码之前先读契约

这个项目的架构约束不是风格偏好，是它能被拆开用的前提：

1. **模块只依赖契约 ID，不互相依赖。**
   `modules/<名字>/module_docs/contract.md` 是它对外行为的**唯一事实源**。
   实现和契约不一致时，按契约算 bug。

2. **破坏性变更另开版本号，不原地改。**
   `yq-event.v1` 要改到不兼容，就新建 `yq-event.v2` 并行跑一段，
   不要编辑 v1。已经有人按 v1 写了消费方。

3. **`events` 集合只追加。** 所有读端点都是投影。
   任何"修正历史"的想法都应该变成"追加一条更正事件"。
   删除只有一条受闸门保护的通道（`scripts/delete-event.sh`，
   带 dry-run、二次确认、强制备份），别绕过它。

改了契约的 PR 请在描述里说明：改了哪一条、为什么不能向后兼容。

## 测试

```bash
cd modules/nexus-core/code/backend
python -m venv .venv                       # 必须在这个路径，见下
.venv/bin/pip install -r requirements.txt
NEXUS_MONGO_URI=mongodb://127.0.0.1:27017 \
NEXUS_DB_NAME=nexus_core_test \
NEXUS_TZ=Asia/Shanghai \
  .venv/bin/python -m pytest -q
```

三件会让你困惑的事，先说清楚：

- **venv 必须落在 `backend/.venv`。** `scripts/delete-event.sh` 和
  `prune-orphan-events.sh` 里 `PY="$BACKEND/.venv/bin/python"` 是硬写的，
  缺了就退出而不是退回系统 python —— 后者会因为缺 pymongo 失败得更难查。
  装到别处的后果是 13 个测试红，全是删除/清理的安全闸。
- **`NEXUS_DB_NAME` 必须以 `_test` 结尾。** `conftest.py` 有硬拒绝：
  库名不像测试库就不跑。测试会清表，这道拒绝防的是清掉真库。
- **两个测试要 `docker exec` 一个具名 mongo 容器**（`--apply` 路径前置一次
  真备份）。本地没有那个容器时它们会正确地中止，CI 里用
  `--deselect` 跳过这两个，不要按文件跳 —— 同文件里还有 39 个不需要
  docker 的测试，包括"dry-run 不删""确认错了中止""备份失败中止"。

关键配置**没有默认值**，缺了立刻失败。那是被测行为本身，别为了方便加默认值。

## 提 PR

- 一个 PR 做一件事。改契约、改实现、改文档分开提。
- 带上能失败的测试。改的是行为就加断言；**反向验证过的断言**才算数 ——
  把实现改坏，看它是否真的报红。
- 提交信息写**为什么**，不写改了什么（改了什么 diff 里有）。
