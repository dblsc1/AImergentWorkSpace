# HoneyComb

An event-sourced kernel for time and task management. Zones → projects → tasks;
the timing log is append-only, and every read endpoint is a projection.

**Not** another to-do list. It records **where your time actually went**, not
where you planned it to go.

中文版：[README.zh-CN.md](README.zh-CN.md)

---

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

Logging in gives you `{"zones":[],"projects":[]}` and nothing to look at. Fill it
with made-up demo data:

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

The login gate is single-password, and the cookie carries `Secure` by default,
which means **browsers will not store it over plain HTTP**. For local HTTP
debugging set `AUTH_COOKIE_SECURE=false` explicitly. Never set that on a public
host.

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
| `modules/nexus-core` | The event-sourced kernel (FastAPI + MongoDB). Provides 11 contracts: timing, task CRUD, the event write entry point, and read projections for tree / ring / gantt / export. |
| `contracts/yq-event.v1` | The event envelope spec. **The core contract of the whole system** — every write is an event posted into this envelope. |
| `contracts/auth.gate.v1` | The login gate contract, a stub implementation (standard library only, zero dependencies), and a minimal login page. |

**Not wired up yet**: both frontend modules — the task hive (`/hive/`) and the
timer ring (`/ring/`) — are in `modules/`, but neither is served. The assembly
layer keeps their locations commented out until each gets a `module.yaml`.

---

## The login gate is a door, not an account system

The stub implementation of `contracts/auth.gate.v1` is a **single shared
password**. It deliberately has **no** registration, no multi-user support, no
password recovery, no permission tiers, and no third-party login.

The reason for drawing the line there: an open-source release should not ship a
real account system bolted on. If you need multi-user, replace that one
implementation — as long as it still satisfies the same contract's four
endpoints and three invariants, **the assembly layer needs no changes at all**.

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
