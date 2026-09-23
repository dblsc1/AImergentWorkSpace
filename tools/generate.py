#!/usr/bin/env python3
"""从 module.yaml / stub.yaml 生成 compose 与 nginx 配置。

只被 install.py 的 add 命令延迟导入 —— list/plan/doctor 不该为 pyyaml 付账。

## 设计约束

**生成物是给人读的。** 不是中间产物，用户会打开它、改它、拿它排障。
所以生成的 YAML 带中文注释，说明每段是从哪个清单来的、为什么长这样。
一个没法读的生成物等于一个黑盒，排障时会逼人去读生成器。

## 两种清单

    modules/<名>/module.yaml        模块怎么跑
    contracts/<id>/stub/stub.yaml   占位实现怎么跑

后者缺失时**硬失败**并说明要写什么字段，不猜。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml


class BadManifest(Exception):
    pass


def _load(p: Path) -> dict:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BadManifest(f"{p} 不是合法 YAML：{e}") from e
    if not isinstance(data, dict):
        raise BadManifest(f"{p} 顶层必须是映射")
    return data


def _req(d: dict, key: str, where: Path):
    if key not in d:
        raise BadManifest(f"{where} 缺字段 `{key}`")
    return d[key]


def _statics(manifest: dict, base: str, where: Path) -> list[dict]:
    """清单里的 `static:` 列表 → 挂载与 location 需要的全部信息。

    每项：prefix（URL 前缀，/ 开头 / 结尾）、root（相对清单所在目录）、
    index（缺省 index.html）、gated、home（`/` 跳到这里，全站至多一个）、
    nav（顶栏页签的文字；缺省不出页签）、timer（顶栏计时芯片点过去的页面）。
    """
    out = []
    for st in manifest.get("static") or []:
        prefix = str(_req(st, "prefix", where))
        if not (prefix.startswith("/") and prefix.endswith("/")) or prefix == "/":
            raise BadManifest(f"{where} 的 static.prefix 必须形如 /名字/，收到 {prefix!r}")
        out.append({
            "prefix": prefix,
            "src": f"{base}/{_req(st, 'root', where)}",
            "dir": "/usr/share/nginx/html" + prefix.rstrip("/"),
            "index": st.get("index", "index.html"),
            "gated": bool(st.get("gated")),
            "home": bool(st.get("home")),
            "nav": st.get("nav"),
            "timer": bool(st.get("timer")),
        })
    return out


def emit(root: Path, plan: dict, out: Path) -> tuple[list[Path], dict]:
    """返回 (生成的文件, 额外元数据)。

    元数据里的 tabs / requires 要读 module.yaml，所以只在这条已经依赖 pyyaml 的
    路径上算。零依赖的 plan/list 不该为它们付账。

    tabs 是给前端用的：用户只勾了 ring，顶栏就不该出现点了跳 404 的死页签。
    所以「装了什么」这件事必须有一份机器可读的事实，就是 installed.json。
    """
    services: dict[str, dict] = {}
    req_set: set[str] = set()
    tabs: list[dict] = []
    routes: list[dict] = []
    statics: list[dict] = []     # 静态目录：挂进 web，由 nginx 直接 serve
    needs_mongo = False
    sources: list[str] = []

    # ── 模块 ───────────────────────────────────────────────────
    for name in plan["modules"]:
        mf = root / "modules" / name / "module.yaml"
        if not mf.is_file():
            raise BadManifest(
                f"模块 {name} 没有 module.yaml —— 它能被当依赖引用，但不能直接装。\n"
                f"要装它得先写 {mf.relative_to(root)}（照 modules/nexus-core/module.yaml 的形状）"
            )
        m = _load(mf)
        sources.append(str(mf.relative_to(root)))
        statics += _statics(m, f"modules/{name}", mf)
        if m.get("kind") == "static":
            # 纯前端：没有进程可起，只有一个目录要挂。顶栏据此出页签。
            tabs.append({"module": name, "label": str(m.get("summary", name)).split("。")[0]})
            continue
        svc = _req(m, "service", mf)
        sname = m.get("name", name)

        entry: dict = {}
        if "build" in svc:
            # 构建上下文相对 module.yaml 所在目录；compose 文件在 deploy/generated/，
            # 所以要往上退两级再进 modules/。**不复制代码**是这套结构的要点。
            entry["build"] = {"context": f"../../modules/{name}/{svc['build']}"}
            # 每次 up 都按源码构建：机器上已有同名镜像（旧版、另一套部署）时，
            # 不加它 compose 会不声不响地拿那个镜像跑。
            entry["pull_policy"] = "build"
        if "image" in svc:
            entry["image"] = svc["image"]
        if svc.get("env"):
            entry["environment"] = dict(svc["env"])
        hc = svc.get("healthcheck")
        port = svc.get("port")
        if hc and port:
            entry["healthcheck"] = {
                "test": ["CMD", "python", "-c",
                         "import urllib.request,sys; sys.exit(0 if urllib.request."
                         f"urlopen('http://127.0.0.1:{port}{hc}').status==200 else 1)"],
                "interval": "10s", "timeout": "5s", "retries": 5, "start_period": "15s",
            }
        entry["restart"] = "unless-stopped"
        entry["networks"] = ["honeycomb-net"]

        reqs = svc.get("requires") or []
        req_set.update(reqs)
        if "mongo" in reqs:
            needs_mongo = True
            entry["depends_on"] = {"mongo": {"condition": "service_healthy"}}

        services[sname] = entry
        for r in m.get("routes") or []:
            routes.append({**r, "service": sname, "port": port})

    module_services = set(services)

    # ── 登录门：声明了 gated 就必须真有门 ─────────────────────
    #
    # 2026-09-17 实测的 fail-open bug：nexus-core 的 module.yaml 声明
    # `gated: true`，但因为没有任何模块 consumes auth.gate.v1，生成的 nginx
    # 里一条 auth_request 都没有 —— /api/core/ 直接裸奔，而且**没有任何报错**。
    #
    # 修法是**往安全那一侧失败**：有 gated 路由就自动把门的占位件装上并响亮告知；
    # 连提供方都找不到就硬失败。绝不允许"声明了要门、生成出来没门、还一声不响"。
    stub_ids = list(plan["stubs"])
    needs_gate = any(r.get("gated") for r in routes + statics)
    auto_gate: str | None = None
    if needs_gate and not any(c.startswith("auth.gate") for c in stub_ids):
        found = sorted(
            d.name for d in (root / "contracts").glob("auth.gate.*")
            if (d / "stub" / "stub.yaml").is_file()
        )
        if not found:
            raise BadManifest(
                "有路由声明了 gated: true，但找不到任何 auth.gate.* 的提供方。\n"
                "  → 要么在 contracts/auth.gate.v1/stub/ 放一个占位实现（带 stub.yaml），\n"
                "     要么把那些路由的 gated 改成 false。\n"
                "**不会给你生成一个没有门的配置** —— 声明了要保护却没保护，"
                "比直接不保护更危险，因为你以为它保护了。"
            )
        auto_gate = found[0]
        stub_ids.append(auto_gate)

    # ── 占位实现 ───────────────────────────────────────────────
    named_volumes: list[str] = []
    for cid in stub_ids:
        sf = root / "contracts" / cid / "stub" / "stub.yaml"
        if not sf.is_file():
            raise BadManifest(
                f"契约 {cid} 有 stub/ 但没有 stub.yaml，不知道怎么把它跑起来。\n"
                f"写 {sf.relative_to(root)}，字段：\n"
                "    service: {name, image, command, port, healthcheck, env}\n"
                "    routes:  [{prefix, upstream, gated}]\n"
                "（照 contracts/auth.gate.v1/stub/stub.yaml）"
            )
        s = _load(sf)
        sources.append(str(sf.relative_to(root)))
        statics += _statics(s, f"contracts/{cid}/stub", sf)
        svc = _req(s, "service", sf)
        sname = svc.get("name") or cid.split(".")[0]
        port = svc.get("port")
        entry = {
            "image": _req(svc, "image", sf),
            "restart": "unless-stopped",
            "networks": ["honeycomb-net"],
            # 占位件是**源码挂载只读**跑的，不 build 镜像：
            # 它的全部意义是"改一行就生效、不需要构建步骤"。
            "volumes": [f"../../contracts/{cid}/stub:/app:ro"],
        }
        # 占位件自己的数据（如账号文件）放 named volume，名字进顶层 volumes 声明。
        for v in svc.get("volumes") or []:
            entry["volumes"].append(v)
            named_volumes.append(v.split(":", 1)[0])
        if "command" in svc:
            entry["command"] = svc["command"]
        if svc.get("env"):
            entry["environment"] = dict(svc["env"])
        if svc.get("healthcheck") and port:
            entry["healthcheck"] = {
                "test": ["CMD", "python", "-c",
                         "import urllib.request,sys; sys.exit(0 if urllib.request."
                         f"urlopen('http://127.0.0.1:{port}{svc['healthcheck']}').status==200 else 1)"],
                "interval": "10s", "timeout": "5s", "retries": 5, "start_period": "10s",
            }
        services[sname] = entry
        for r in s.get("routes") or []:
            routes.append({**r, "service": sname, "port": port})

    stub_names = set(services) - module_services

    # ── 基础设施 ───────────────────────────────────────────────
    if needs_mongo:
        services["mongo"] = {
            "image": "mongo:7",
            "restart": "unless-stopped",
            "networks": ["honeycomb-net"],
            "volumes": ["honeycomb_mongo_data:/data/db"],
            "healthcheck": {
                "test": ["CMD", "mongosh", "--quiet", "--eval",
                         "db.adminCommand('ping').ok"],
                "interval": "10s", "timeout": "5s", "retries": 5, "start_period": "20s",
            },
        }

    gate = needs_gate          # 到这里提供方一定存在（上面要么装上了要么已硬失败）

    def _mount(st: dict) -> str:
        # 登录页可被部署方整个换掉（contracts/gateway.v1 的 HONEYCOMB_LOGIN_DIR）。
        src = f"../../{st['src']}"
        if st["prefix"] == "/login/":
            src = "${HONEYCOMB_LOGIN_DIR:-" + src + "}"
        return f"{src}:{st['dir']}:ro"

    services["web"] = {
        "image": "nginx:alpine",
        "restart": "unless-stopped",
        "networks": ["honeycomb-net"],
        # 只有 web 映射宿主端口。默认绑回环 —— 要暴露得自己显式改，
        # 而不是装完就已经在公网上了。
        "ports": ["${HONEYCOMB_BIND:-127.0.0.1:8800}:80"],
        # 网关对外冻结的接口见 contracts/gateway.v1/contract.md。
        "environment": {
            "AUTH_UPSTREAM": "${AUTH_UPSTREAM:-auth:8010}",
            "HONEYCOMB_BASE_PATH": "/",       # gateway.v1 预留，本版固定
            "NGINX_ENVSUBST_FILTER": "^(AUTH_UPSTREAM|HONEYCOMB_)",
        },
        # 静态目录只读挂载，不复制代码：改前端去模块目录改，刷新即生效。
        "volumes": [
            "./nginx/templates/default.conf.template:/etc/nginx/templates/default.conf.template:ro",
            "${HONEYCOMB_EXTRA_ROUTES_DIR:-../nginx/extra}:/etc/nginx/templates/extra:ro",
            "../../modules/nginx-docker/nginx:/etc/nginx/honeycomb:ro",
            "../../modules/nginx-docker/static:/usr/share/nginx/html/__cockpit:ro",
        ] + [_mount(st) for st in statics],
        # 占位件的依赖是可选的：部署方用 override 把它关掉（profiles），
        # 换成自己的认证服务时网关照常起（contracts/gateway.v1）。
        "depends_on": {
            s: {"condition": "service_healthy", **({"required": False} if s in stub_names else {})}
            for s, v in services.items() if "healthcheck" in v
        },
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "nginx" / "templates").mkdir(parents=True, exist_ok=True)

    header = (
        "# ⚠ 本文件由 install.sh 生成，改了会被下次 add 覆盖。\n"
        "# 要长期改就改上游清单：\n"
        + "".join(f"#   {s}\n" for s in sources)
        + "#\n# 起：docker compose up -d      停：docker compose down\n"
        "# 数据在 named volume honeycomb_mongo_data；down -v 会**连数据一起删**。\n\n"
    )
    compose = {
        # 项目名留 env 覆盖点。写死会堵掉 compose 原生的项目名覆盖，而同一台机器上
        # 如果已经跑着另一套同名 honeycomb，`up` **不报错**就把它的容器换掉了。
        "name": "${HONEYCOMB_PROJECT:-honeycomb}",
        "networks": {"honeycomb-net": {"driver": "bridge"}},
        "services": services,
    }
    if needs_mongo:
        named_volumes.insert(0, "honeycomb_mongo_data")
    if named_volumes:
        compose["volumes"] = {v: None for v in named_volumes}

    cf = out / "docker-compose.yml"
    cf.write_text(
        header + yaml.safe_dump(compose, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    nf = out / "nginx" / "templates" / "default.conf.template"
    nf.write_text(_nginx(routes, statics, gate, sources), encoding="utf-8")
    meta = {"requires": sorted(req_set), "tabs": tabs, "stubs": stub_ids}
    if auto_gate:
        meta["auto_included"] = [auto_gate]
    return [cf, nf], meta


def _nav_json(statics: list[dict], home: str | None) -> str:
    """顶栏页签配置（注入页面的 window.HONEYCOMB_NAV）：装了什么前端就有什么页签。"""
    tabs = [{"href": st["prefix"], "label": str(st["nav"])} for st in statics if st.get("nav")]
    timer = next((st["prefix"] for st in statics if st.get("timer")), None)
    nav = json.dumps({"home": home or "/", "timer": timer, "tabs": tabs},
                     ensure_ascii=False, separators=(",", ":"))
    if "'" in nav or "$" in nav:
        raise BadManifest(f"顶栏页签文字里不能有 ' 或 $：{nav}")
    return nav


def _nginx(routes: list[dict], statics: list[dict], gate: bool, sources: list[str]) -> str:
    homes = [st["prefix"] for st in statics if st["home"]]
    if len(homes) > 1:
        raise BadManifest(f"多个静态目录都声明了 home: true：{homes}——`/` 只能跳一个地方")
    L: list[str] = [
        "# ⚠ 本文件由 install.sh 生成，改了会被下次 add 覆盖。",
        "# 要长期改就改上游清单：",
        *(f"#   {s}" for s in sources),
        "#",
        "# envsubst 模板：nginx 镜像启动时渲染成 conf.d/default.conf，只替换",
        "# AUTH_UPSTREAM 与 HONEYCOMB_*。对外冻结接口见 contracts/gateway.v1/contract.md。",
        "server {",
        "    listen 80;",
        "    server_name _;",
        "    charset utf-8;",
        "",
        "    # 跳转一律用相对 Location。默认 absolute_redirect on 时 nginx 会拿 $host",
        "    # 拼绝对 URL，而 $host **不带端口** —— 于是 http://IP:8800/ 的 302 跳到了",
        "    # http://IP/...，打在 80 上，浏览器看到的是「自动跳转然后 502」。真机踩过。",
        "    absolute_redirect off;",
        "",
        '    set $honeycomb_base "${HONEYCOMB_BASE_PATH}";',
        f"    set $honeycomb_nav '{_nav_json(statics, homes[0] if homes else None)}';",
        "",
        "    location = /healthz { return 200 \"ok\\n\"; add_header Content-Type text/plain; }",
        "",
        "    # 顶栏、设计 tokens、站点图标（modules/nginx-docker/static）。不含用户数据，不设门。",
        "    location /__cockpit/ {",
        "        alias /usr/share/nginx/html/__cockpit/;",
        "        add_header Cache-Control \"no-cache\";",
        "    }",
        "    location = /favicon.ico { alias /usr/share/nginx/html/__cockpit/favicon.ico; }",
        "    location = /favicon.svg { alias /usr/share/nginx/html/__cockpit/favicon.svg; }",
    ]
    # 根路径：有主界面跳主界面；没有但有门就跳登录页（唯一确实存在的页面）；都没有就不管。
    if homes:
        L += ["", f"    location = / {{ return 302 {homes[0]}; }}"]
    elif gate:
        L += ["", "    location = / { return 302 /login/; }"]
    if gate:
        L += [
            "",
            "    # 登录门（gateway.v1 冻结 /__auth_verify 与 @to_login 两个名字）。",
            "    # auth_request 只看状态码，body 不转；客户端自带的租户头不转给认证服务。",
            "    location = /__auth_verify {",
            "        internal;",
            "        proxy_pass http://${AUTH_UPSTREAM}/api/auth/verify;",
            "        proxy_pass_request_body off;",
            "        proxy_set_header Content-Length \"\";",
            "        proxy_set_header X-Nexus-Tenant \"\";",
            "        proxy_set_header X-Original-URI $request_uri;",
            "    }",
            "    location @to_login { return 302 /login/; }",
            "",
            "    # 认证服务的整个 /api/auth/ 前缀。不设门——替换进来的认证服务，",
            "    # 这个前缀下每个非登录端点都得自己鉴权，网关不替它挡。",
            "    location /api/auth/ {",
            "        proxy_pass http://${AUTH_UPSTREAM};",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "        proxy_set_header X-Nexus-Tenant \"\";",
            "    }",
        ]
    for st in statics:
        L += ["", f"    location {st['prefix']} {{"]
        if gate and st["gated"]:
            L += [
                "        include /etc/nginx/honeycomb/gate.inc;",
                "        include /etc/nginx/honeycomb/inject.inc;",
            ]
        L += [
            f"        alias {st['dir']}/;",
            f"        index {st['index']};",
            f"        try_files $uri $uri/ {st['prefix']}{st['index']};" if st["prefix"] != "/login/"
            else "        try_files $uri $uri/ =404;",
            "    }",
        ]
    for i, r in enumerate(routes):
        if r.get("prefix") in ("/api/auth/",):
            continue          # 门自己那条上面已经写了
        # degraded：轮询型端点（顶栏计时芯片）。未登录或后端挂了都回这份 JSON，
        # 不跳登录页——给一个每 10s 一次的 XHR 回 302 毫无意义。
        degraded = r.get("degraded")
        if degraded is not None and "'" in str(degraded):
            raise BadManifest(f"路由 {r['prefix']} 的 degraded 里不能有单引号：{degraded!r}")
        L += ["", f"    location {'= ' if r.get('exact') else ''}{r['prefix']} {{"]
        gated = gate and r.get("gated")
        if gated and degraded is None:
            L += ["        include /etc/nginx/honeycomb/gate.inc;"]
        elif gated:
            L += [
                "        auth_request /__auth_verify;",
                "        auth_request_set $honeycomb_tenant $upstream_http_x_nexus_tenant;",
                f"        error_page 401 = @degraded_{i};",
                "        proxy_set_header X-Nexus-Tenant $honeycomb_tenant;",
            ]
        else:
            L += ["        proxy_set_header X-Nexus-Tenant \"\";"]
        L += [
            f"        proxy_pass http://{r['service']}:{r['port']}{r.get('upstream', r['prefix'])};",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        ]
        if degraded is None:
            L += ["        proxy_read_timeout 30s;", "    }"]
        else:
            L += [
                "        proxy_connect_timeout 2s;",
                "        proxy_read_timeout 3s;",
                "        proxy_intercept_errors on;",
                f"        error_page 500 502 503 504 = @degraded_{i};",
                "    }",
                f"    location @degraded_{i} {{",
                "        default_type application/json;",
                f"        return 200 '{degraded}';",
                "    }",
            ]
    L += [
        "",
        "    # 部署方的额外路由（HONEYCOMB_EXTRA_ROUTES_DIR 里的 *.conf.template）。",
        "    include /etc/nginx/conf.d/extra/*.conf;",
        "}",
        "",
    ]
    return "\n".join(L)
