# HoneyComb

事件溯源的时间与任务内核。分区 → 项目 → 任务，计时流水只进不改，所有读端都是投影。

**不是** 又一个待办清单。它记的是**你实际把时间花在哪了**，而不是你计划花在哪。

English: [README.md](README.md)

---

## 三十秒跑起来

```sh
cd deploy
cp .env.example .env
# 编辑 .env，给 HONEYCOMB_PASSWORD 设一个真正的口令 —— 没有默认值，不设起不来
docker compose up -d
```

然后开 <http://127.0.0.1:8800/>。

需要 Docker 和 Docker Compose，**其余什么都不用装**。没有 npm install，没有 pip install。

### 新装是空库，先灌演示数据

登录后进入任务蜂巢（`/hive/`），但新库是空的。灌一批**编造的**演示数据：

```sh
read -rsp '口令: ' HONEYCOMB_PASSWORD && export HONEYCOMB_PASSWORD
python3 seed/seed_demo.py
python3 seed/seed_demo.py --big     # 大盘：10 分区 / 40 项目
```

4 分区、9 项目、18 任务，再补 14 天的历史计时段，这样统计面板和计时档案一打开
就有东西。纯标准库，零依赖。**可以重复跑** —— 同名的跳过，重复的补登由服务端
按 dedupe 去重（实测跑三遍，events 总数恒为 43）。

`read -rsp` 不回显、也不进 shell 历史（写成 `HONEYCOMB_PASSWORD=xxx python3 ...`
那种一行式**会**进历史）。脚本从环境变量读而不收命令行参数，所以也不出现在
`ps` 输出里。

### 默认只绑回环，这是有意的

`.env.example` 里 `HONEYCOMB_BIND=127.0.0.1:8800`。要放到局域网或公网：

- 改 `HONEYCOMB_BIND`
- **在前面加一层 TLS**（Caddy / nginx / Traefik 随你）
- 别把 80 直接暴露出去

登录门是单口令的，cookie 默认带 `Secure`。本机用 `127.0.0.1` / `localhost` 打开不受影响——浏览器把本机当安全来源。**用局域网 IP 走 HTTP 时浏览器不会保存它**：前面加 TLS；只在内网调试时才设 `AUTH_COOKIE_SECURE=false`，别在公网上设这个。

### 升级

`git pull` 之后照旧 `docker compose up -d`，nexus-core 会按新代码重新构建（有缓存，很快）；数据在卷里，不动。

---

## 它是怎么搭起来的

```
modules/      各模块的真代码。每个模块**独立可用**，不依赖别的模块
contracts/    契约。模块之间唯一的耦合点
deploy/       组装层。**零代码副本**，只用 compose + nginx 把模块拼成一个站点
install.sh    读契约解析依赖，生成 compose 与路由
```

### 模块只依赖契约 id，不依赖别的模块

这是整套结构的地基。`nexus-core` 不知道有没有前端，前端不知道登录门背后是一个 30 行的占位脚本还是一整套带数据库的账号系统。它们只认契约 id 和状态码。

于是"装哪几个模块"可以自动算出来：

```sh
./install.sh list              # 有哪些模块、各自 provides / consumes 什么
./install.sh plan nexus-core   # 依赖怎么解，不写文件
./install.sh add  nexus-core   # 解依赖并生成 compose/nginx
./install.sh doctor            # 核对已装清单与当前文件是否还对得上
```

缺的依赖按顺序找三个去处：**有模块提供它** → **有纯规范文档**（比如事件信封格式）→ **有占位实现**。三个都没有就**硬失败并列出缺了哪些 id**。

不会静默跳过。一个依赖悬空的安装比装不上更糟，因为它会在运行时以奇怪的方式坏掉，而你以为它装好了。

### 两份 compose，分工不同

| | 谁维护 | 什么时候用 |
|---|---|---|
| `deploy/docker-compose.yml` | 手写 | 默认组装，**零依赖开箱跑** |
| `deploy/generated/` | `install.sh add` 产出 | 换模块组合时 |

`install.sh doctor` 会比对两者的服务集合，漂移了就告警。

`list` / `plan` / `doctor` 零第三方依赖；只有 `add` 需要 `pyyaml`（要解析模块清单的嵌套结构）。**默认组装根本不用安装器**，这样"拉下来就能跑"不依赖任何 `pip install`。

---

## 这个版本里有什么

| | |
|---|---|
| `modules/nexus-core` | 事件溯源内核（FastAPI + MongoDB）。提供 13 个契约：计时、任务 CRUD、事件写入口与档案读端、以及树/圆环/甘特/导出等读端投影 |
| `modules/hive` | 任务蜂巢（`/hive/`），主界面。纯静态前端，数据全走 `/api/core/` |
| `modules/ring` | 计时台（`/ring/`）：贡献圆环 + 开始/停止/取消/补登。纯静态前端 |
| `contracts/yq-event.v1` | 事件信封规范。**整个系统的核心契约** —— 所有写操作都是往这个信封里投事件 |
| `contracts/auth.gate.v1` | 登录门契约 + 占位实现（纯标准库，零依赖）+ 最小登录页 |

两个前端都在登录门后面：打开 `http://127.0.0.1:8800/`，登录后落到任务蜂巢。前端目录是只读挂载进 nginx 的，改 `modules/<名>/code/frontend/` 里的文件，浏览器刷新就生效。

---

## 登录门是一道门，不是账号系统

`contracts/auth.gate.v1` 的占位实现是**单一共享口令**。它刻意**没有**：注册、多用户、找回密码、权限分级、第三方登录。

为什么这么划：开源版不该捆绑任何真实账号系统。需要多用户的人，换掉那个实现就行 —— 只要还满足同一份契约的四个端点和三条不变量，**组装层一行都不用改**。

想自己实现，读 `contracts/auth.gate.v1/contract.md` 的「换实现要满足什么」一节。

---

## 数据在哪

Mongo 的 named volume `honeycomb_mongo_data`。

```sh
docker compose down      # 不删数据
docker compose down -v   # 连数据一起删
```

`modules/nexus-core/code/backend/scripts/` 下有备份/恢复/清理孤儿事件的脚本。备份**不进业务仓** —— 备份进业务仓会让每次备份后工作树永远是脏的。

---

## 想改点什么

**契约先行。** 有外部消费方的行为变更，先改 `contract.md` 再改代码，否则消费方按文档写的东西会在某次部署后悄悄坏掉。

**破坏性变更发新版本号**，不要就地改 v1。`auth.gate.v1` 就是 `auth.gate.v1`，改状态码或改不变量都得叫 `v2`，新旧并行。

**关键路径缺文件必须报错，不许静默跳过。** 可选件缺失要打印"已跳过"。唯一不许的是静默跳过然后报成功 —— 崩溃会让人停下，撒谎让人以为装好了。

---

## 贡献

见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。用 **DCO**（`git commit -s`），
不用 CLA —— 你的贡献以 AGPL-3.0 进来也永远是 AGPL-3.0，不会被以闭源条款转卖。

## 许可

**AGPL-3.0**，全文见 [LICENSE](LICENSE)。

自行部署、自己用、组织内部用 —— 完全自由，和 GPL 没区别。
**若你修改了代码并通过网络向他人提供服务**，需要向该服务的使用者提供你
修改后的完整源码（[AGPL-3.0 §13](LICENSE)）。这是 AGPL 区别于 GPL 的唯一
实质条款，也是选它的原因。自己一个人用、或组织内部不对外提供服务，不触发。

商标不在授权范围内 —— 这是 AGPL 本身的性质，不需要额外声明：
代码许可和商标许可是两件事，拿到前者不等于拿到后者。

> `LICENSE` 里是 AGPL-3.0 的**官方英文文本**。FSF **不授权任何翻译作为
> 有法律效力的版本**，所以**不要添加翻译版的 `LICENSE`** —— 非官方译文
> 拿来读可以，但不能替换英文原文，也不能并排放着让人以为两份同等有效。
> 这条写在中文版里，因为会动手加中文 LICENSE 的正是读中文的人。

### 关于 v0.1 的 MIT

**v0.1（2026-09-14）以 MIT 发布过，那份授权对当时已取得副本的人不可撤销。**
换许可只对之后的版本生效，收不回已发出去的。v0.1 的快照保留在 `v0.1`
分支上，仍按 MIT 条款。

写在这里是因为这件事会被反复问，而答案是确定的。

### 依赖的许可

运行期依赖不随本仓库分发，各自遵循自身许可：
FastAPI (MIT)、Uvicorn (BSD-3-Clause)、Pydantic (MIT)、PyMongo (Apache-2.0)、
pytest (MIT)、HTTPX (BSD-3-Clause)。

本仓库**不内嵌任何第三方源代码**。`contracts/auth.gate.v1/stub/web/`
下的前端是手写的，零依赖零框架。

**MongoDB 的 SSPL 值得单独看一眼。** 它不是 OSI 认可的开源许可，约束的是
"把 MongoDB 作为服务提供给第三方"这种用法。本项目只连接一个 MongoDB 实例，
不分发它也不转售它，所以不受约束。但如果你要把 HoneyComb 打包成 SaaS 卖，
自己去读 SSPL —— 那是你和 MongoDB 之间的事，与本项目的 AGPL 无关。
