# HoneyComb

An event-sourced kernel for time and task management. Zones → projects → tasks;
the timing log is append-only, and every read endpoint is a projection.

**Not** another to-do list. It records **where your time actually went**, not
where you planned it to go.

中文版：[README.zh-CN.md](README.zh-CN.md)

---

## Just want to use it

Only Docker is needed (Docker Desktop on Windows / macOS). No source, no Python.

```sh
# Linux / macOS
curl -fsSL https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.sh | sh
```

```powershell
# Windows (PowerShell)
irm https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.ps1 -OutFile honeycomb-install.ps1
powershell -ExecutionPolicy Bypass -File .\honeycomb-install.ps1
```

It installs into `./honeycomb`, prints a login password once (also kept in
`honeycomb/.env`) and opens on <http://127.0.0.1:8800/>. Running the same script
from a newer release upgrades in place and keeps your data. Images come from
`ghcr.io/dblsc1/honeycomb-*`; see `release/`.

The rest of this README is the source route: clone, read, change, run.

## Thirty seconds to running

```sh
cd deploy
cp .env.example .env
# Edit .env and set a real HONEYCOMB_PASSWORD — there is no default,
# and the stack refuses to start without one.
docker compose up -d
```

Then open <http://127.0.0.1:8800/>.

Needs Docker and Docker Compose. **Nothing else to install** — no `npm install`,
no `pip install`.

### A fresh install is an empty database

Logging in takes you to the task hive (`/hive/`), but a new database is empty.
Fill it with made-up demo data:

```sh
read -rsp 'HoneyComb password: ' HONEYCOMB_PASSWORD && export HONEYCOMB_PASSWORD
python3 seed/seed_demo.py
python3 seed/seed_demo.py --big     # bigger set: 10 zones / 40 projects
```

4 zones, 9 projects, 18 tasks, plus 14 days of backfilled timing sessions so the
statistics and the timing archive have something in them. Standard library only,
no dependencies. **Safe to run twice** — existing names are skipped and repeated
backfills are deduplicated server-side (verified: three runs, the event count
stays at 43).

`read -rsp` prompts without echoing and, unlike an inline assignment, keeps the
password out of your shell history. The script reads it from the environment
rather than a command-line flag, so it never shows up in `ps` output either.

### It binds to loopback only, on purpose

`.env.example` ships `HONEYCOMB_BIND=127.0.0.1:8800`. To expose it on a LAN or
the internet:

- change `HONEYCOMB_BIND`
- **put TLS in front of it** (Caddy, nginx, Traefik — your call)
- don't expose port 80 directly

The login gate is single-password, and the cookie carries `Secure` by default.
Opening it at `127.0.0.1` / `localhost` is fine: browsers treat the local machine
as a secure origin. **Over plain HTTP on a LAN IP, browsers will not store it**:
put TLS in front, and only for LAN debugging set `AUTH_COOKIE_SECURE=false`.
Never set that on a public host.

One more thing before you expose the port: a single-user install accepts agent
status reports that carry **no credentials** (they can only add lane entries and read
nothing). Set `AUTH_ANONYMOUS_REPORT=false` in `.env` when anyone else can reach the
port; see "Tokens for desktop programs and AI agents" below.

### Upgrading

After `git pull`, run `docker compose up -d` as usual. nexus-core is rebuilt from
the new code (cached, so it is quick). Data lives in the volume and is not touched.

---

## How it fits together

```
modules/      The real code for each module. Every module is usable on its own
              and depends on no other module.
contracts/    Contracts. The only coupling point between modules.
deploy/       The assembly layer. **Zero copies of code** — just compose +
              nginx wiring modules into one site.
install.sh    Reads contracts, resolves dependencies, generates compose + routes.
```

### Modules depend on contract IDs, never on each other

This is the foundation of the whole structure. `nexus-core` does not know
whether a frontend exists. A frontend does not know whether the login gate is a
30-line stub or a full account system with its own database. They only know
contract IDs and status codes.

That is what makes "which modules do I need" computable:

```sh
./install.sh list              # modules, and what each provides / consumes
./install.sh plan nexus-core   # show the resolution, write nothing
./install.sh add  nexus-core   # resolve and generate compose/nginx
./install.sh doctor            # check the installed set still matches the files
```

A missing dependency is resolved in this order: **a module provides it** → **a
spec-only document exists** (the event envelope format, for instance) → **a stub
implementation exists**. If none of the three is present it **fails hard and
lists the missing IDs**.

It never skips silently. An install with a dangling dependency is worse than one
that fails, because it breaks in strange ways at runtime while you believe it
succeeded.

### Two compose files, different jobs

| | Maintained by | When to use |
|---|---|---|
| `deploy/docker-compose.yml` | hand-written | The default assembly. **Runs out of the box, zero dependencies.** |
| `deploy/generated/` | produced by `install.sh add` | When changing the module set |

`install.sh doctor` compares the service sets of the two and warns on drift.

`list` / `plan` / `doctor` have no third-party dependencies; only `add` needs
`pyyaml` (it has to parse the nested structure of module manifests). **The
default assembly does not use the installer at all**, so "clone it and run it"
never depends on a `pip install`.

---

## What is in this release

| | |
|---|---|
| `modules/nexus-core` | The event-sourced kernel (FastAPI + MongoDB). Provides 13 contracts: timing, task CRUD, the event write entry point and archive read, and read projections for tree / ring / gantt / export. |
| `modules/hive` | The task hive (`/hive/`), the main screen. A static frontend; all data goes through `/api/core/`. |
| `modules/ring` | The timer ring (`/ring/`): contribution ring plus start / stop / cancel / backfill. A static frontend. |
| `modules/assistant` | The AI assistant page (`/assistant/`), for looking back: chat with the assistant, confirm detected activity, and edit the activity detector's privacy / away settings. A static frontend. |
| `modules/nginx-docker` | The gateway's shared parts: the navbar, design tokens, favicons, and the gate and inject snippets. The gateway injects the navbar into every frontend, with one tab per installed frontend. |
| `contracts/yq-event.v1` | The event envelope spec. **The core contract of the whole system** — every write is an event posted into this envelope. |
| `contracts/auth.gate.v1` | The login gate contract, a stub implementation (standard library only, zero dependencies), and a minimal login page. |
| `contracts/gateway.v1` | The gateway's public surface: swap the auth service, swap the login page, add your own routes, read the current tenant — without editing any file in this repo. |

Both frontends sit behind the login gate: open `http://127.0.0.1:8800/`, log
in, and you land on the task hive. The frontend directories are mounted into
nginx read-only, so an edit under `modules/<name>/code/frontend/` shows up on
the next browser refresh.

---

## The login gate is a door, not an account system

The stub implementation of `contracts/auth.gate.v1` does two things: a
**single shared password** (everyone shares one set of data), and simple
**username + password accounts** (each account gets its own data). It
deliberately has **no** self-registration, no password recovery, no permission
tiers, and no third-party login.

### Several accounts, each with its own data

For a household or a small team on a LAN. In `deploy/`:

```sh
# .env: leave HONEYCOMB_PASSWORD empty, set NEXUS_TENANT_STRICT=1
docker compose run --rm auth python /app/auth_stub.py adduser alice   # asks for the password twice
docker compose run --rm auth python /app/auth_stub.py adduser bob
docker compose up -d
```

The login page then asks for an account. `passwd <name>` changes a password,
`deluser <name>` removes an account (its data stays), `users` lists them;
changing or deleting logs that account out everywhere within two seconds.
Switching from the shared password and want to keep your existing data?
`adduser <name> --id u_local` hands that data to the account.
`NEXUS_TENANT_STRICT=1` makes the backend refuse any request that arrives
without an account instead of quietly dropping it into the shared data.

### Tokens for desktop programs and AI agents: read / write / report-only

A sync program or an agent hook has no browser cookie. Give it a token and send
`Authorization: Bearer <token>`. Tokens **open the API only, never pages**, and
last a year by default. **Tokens are never minted automatically**: you issue
one, you decide which agent gets it and how much it may do.

| Scope | What it can do | Give it to |
|---|---|---|
| `report` | Only report an agent run's start / phase / heartbeat / stop. **Reads nothing**, writes nothing else | A coding agent's hooks (`tools/agent-hooks`) |
| `read` | Report, plus read `/api/core/` and the read-only MCP tools. Changes nothing | An assistant that only needs to know what is going on; your own MCP client |
| `write` | Everything a device token could do before: also upload activity and events, let the AI draft suggestions | The desktop detector (`modules/ai-detector`), a management agent you fully trust |

Things only a person may do (changing detector settings and rules, confirming
suggestions, reassigning time, ...) stay closed to every token.

- On the web: log in, open the **AI助理** page, section **Agent 令牌**: pick a
  scope, add a note, generate. The token is **shown once**, with ready-to-paste
  configuration; the list lets you revoke a single token.
- API: `POST /api/auth/tokens` (`Content-Type: application/json`, body
  `{"scope":"report","name":"laptop"}`, both optional, default `write`);
  `GET /api/auth/tokens` lists them (never the token itself);
  `POST /api/auth/tokens/revoke` with `{"tokenId":"..."}` revokes one, with an
  empty body every token of your own account.
- From the CLI (in `deploy/`):
  `docker compose exec auth python /app/auth_stub.py token alice --scope report --name laptop`,
  `tokens alice`, `revoke-token <id>`, `revoke alice`; no name = the
  shared-password identity.

**A request with no token can only report.** A single-user install (shared
password only) accepts status reports that carry no credentials at all, so the
hooks work without a token; runs reported that way are flagged unverified (`unverified: true` in
`views/lanes`) and always land in the inbox. The cost, stated plainly: **while this
is on, anyone who can reach the port can add entries to your lanes.** They can
read nothing and change nothing that exists, but they can add (rate-limited at
the gateway, at most 20 live at a time). With the default loopback binding that
is harmless; **if you expose the port to a LAN or the internet, set
`AUTH_ANONYMOUS_REPORT=false` in `.env`** and hand out `report` tokens instead.
A request that presents a bad or expired token is always rejected, never
treated as anonymous. With accounts enabled (multi-user) there is no anonymous
reporting.

`AUTH_SECRET` must be set in `.env` (the release installer already writes one;
for a hand-built `deploy/` add a random string yourself) — without a fixed key a
token would die on restart, so none are issued. Changing or deleting an account,
or changing or removing the shared password, kills the matching tokens; revocation takes
effect within two seconds. Tokens issued by v0.3 (starting with `hct1`) keep
working as `write` and do not appear in the list.

The reason for drawing the line there: an open-source release should not ship a
real account system bolted on. If you need more, replace that one
implementation — as long as it still satisfies the same contract's endpoints
and three invariants, **the assembly layer needs no changes at all**.
How to plug it in (`AUTH_UPSTREAM`, your own login page, switching the stub off)
is in `contracts/gateway.v1/contract.md`.

### The AI assistant ("Ask the assistant" on the timer page)

The timer page has a chat panel for questions like "which project took most of my
time this week?". The assistant reads your data only through the read-only MCP
tools — it writes nothing, runs no commands, has no web access. Put your model key
in `.env` as `AGENT_API_KEY` (default model `deepseek/deepseek-flash`) and run
`docker compose up -d`; without a key the panel tells you to add one and
everything else works as before. Other models, a local Ollama or any
OpenAI-compatible server on your network: see the AI assistant section of
`deploy/.env.example`. The interface is `contracts/agent.chat.v1`; the default
implementation (opencode) lives in `modules/agent/`. To see exactly what each
turn sends to the model and what comes back, set `AGENT_DEBUG=1` in `.env` and
restart: every answer gets a collapsed "调试" (debug) section. Those records are
your data (full prompts and tool results) — only turn it on while debugging on
your own machine, and set it back to `0` afterwards. With "allow the AI to manage
the in-progress task" switched on, the assistant also looks at windows your rules
cannot place and writes a rule for that one window in the background (the timer
page shows "自动 · … (AI 认的)" with a one-click "不对" to undo; at most 12 an
hour). Set `AGENT_AUTOTRACK=0` in `.env` to turn that off.

### Serving it under a sub-path

Behind another reverse proxy at `https://example.com/Cockpit/`? Set
`HONEYCOMB_BASE_PATH=/Cockpit/` in `.env` and have the outer proxy pass
`/Cockpit/` through unchanged. Pages, API, redirects and the login cookie all
follow the prefix. See `contracts/gateway.v1/contract.md`, section 7.

### Adding your own routes or frontend

Put a directory outside the repo with `*.conf.template` files (nginx location
blocks) and point `HONEYCOMB_EXTRA_ROUTES_DIR` at it in `.env`. One line,
`include /etc/nginx/honeycomb/gate.inc;`, puts a route behind the same login
gate; add `inject.inc` and the page gets the shared navbar. See
`contracts/gateway.v1/contract.md`.

To write your own, read the "what a replacement must satisfy" section of
`contracts/auth.gate.v1/contract.md`.

---

## Where the data lives

A Mongo named volume, `honeycomb_mongo_data`.

```sh
docker compose down      # keeps data
docker compose down -v   # deletes data too
```

`modules/nexus-core/code/backend/scripts/` holds backup, restore, and
orphan-event cleanup scripts. Backups **do not go into the code repository** —
committing them makes the working tree permanently dirty after every backup.

---

## If you want to change something

**Contract first.** For any behavior change with external consumers, edit
`contract.md` before the code. Otherwise whatever a consumer wrote against the
documentation breaks quietly on some later deploy.

**Breaking changes get a new version number.** Do not edit v1 in place.
`auth.gate.v1` is `auth.gate.v1`; changing a status code or an invariant means
shipping `v2` alongside it.

**A missing file on a critical path must fail loudly, never skip silently.** A
missing optional part should print "skipped". The one thing that is never
acceptable is skipping silently and then reporting success — a crash makes
someone stop, a lie makes them believe it worked.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). This project uses **DCO**
(`git commit -s`), not a CLA — your contribution comes in under AGPL-3.0 and
stays AGPL-3.0. It will not be relicensed and sold under closed-source terms.

## License

**AGPL-3.0**, full text in [LICENSE](LICENSE).

Self-hosting, personal use, and internal use inside an organization are
completely free — no different from the GPL. **If you modify this code and
provide it as a service over a network to others**, you must offer those users
the complete corresponding source of your modified version
([AGPL-3.0 §13](LICENSE)). That clause is the only substantive difference from
the GPL, and it is the reason for choosing it. Using it yourself, or internally
without offering a service to others, does not trigger it.

Trademarks are not covered — which is simply how the AGPL works and needs no
extra declaration: a code license and a trademark license are two different
things, and having the first is not having the second.

> The AGPL-3.0 text in `LICENSE` is the official English version. The FSF does
> not authorize translations as legally valid, so **do not add a translated
> `LICENSE`** — an unofficial translation may be useful to read, but it must not
> replace or sit beside the English text as if it were equally binding.

### About v0.1 and MIT

**v0.1 (2026-09-14) was released under the MIT license, and that grant is
irrevocable for anyone who obtained a copy at the time.** Relicensing only
applies to later versions; it cannot pull back what has already been
distributed. The v0.1 snapshot stays on the `v0.1` branch under MIT terms.

This is written down because the question comes up repeatedly and the answer is
settled.

### Licenses of dependencies

Runtime dependencies are not distributed with this repository and carry their
own licenses: FastAPI (MIT), Uvicorn (BSD-3-Clause), Pydantic (MIT), PyMongo
(Apache-2.0), pytest (MIT), HTTPX (BSD-3-Clause).

This repository **vendors no third-party source code**. The frontend under
`contracts/auth.gate.v1/stub/web/` is hand-written, with zero dependencies and
no framework.

**MongoDB's SSPL deserves a separate look.** It is not an OSI-approved open
source license, and what it constrains is *offering MongoDB itself as a service
to third parties*. This project merely connects to a MongoDB instance; it
neither distributes nor resells it, so the constraint does not apply. But if you
intend to package HoneyComb as a SaaS product, go read the SSPL yourself — that
is between you and MongoDB, and has nothing to do with this project's AGPL.
