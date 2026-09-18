# Contributing

中文版：[CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)

## Sign-off requirement: DCO

This project uses the **Developer Certificate of Origin (DCO)**, not a CLA.

The difference in one sentence: a DCO only lets you **certify provenance**; it
does not make you **grant relicensing rights**. Your contribution comes in under
AGPL-3.0 and stays AGPL-3.0 forever — the maintainers cannot sell it under
closed-source terms. That is a guarantee to you, not a formality.

Add one line to every commit:

```
Signed-off-by: Your Name <your@email>
```

`git commit -s` adds it for you. If you forgot, `git commit --amend -s` fixes
the last one. **Commits without a sign-off will not be merged** — and this one
is not negotiable, because a repository cannot be half signed-off.

### The DCO 1.1 text

Adding `Signed-off-by` means you certify clauses (a) through (d) below.

Reproduced verbatim from <https://developercertificate.org/>, **including the
preamble**. The document itself says changing it is not allowed, and trimming it
counts as changing it — so do not shorten this block, not even the copyright
header.

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

Note clause (d): the name and email in your sign-off stay in the commit history
**permanently** and cannot be scrubbed afterwards. If you do not want your legal
name public, use the pseudonym you already work under and an email address you
are willing to publish — but it has to be one that actually reaches you and will
keep working.

---

## Read the contract before changing code

The architectural constraints here are not style preferences. They are what make
the pieces separable:

1. **Modules depend on contract IDs, never on each other.**
   `modules/<name>/module_docs/contract.md` is the **single source of truth** for
   that module's external behavior. When the implementation and the contract
   disagree, the contract wins and the implementation has a bug.

2. **Breaking changes get a new version number. Never edit in place.**
   To change `yq-event.v1` incompatibly, create `yq-event.v2` and run both for a
   while. Do not edit v1 — someone has already written a consumer against it.

3. **The `events` collection is append-only.** Every read endpoint is a
   projection. Any impulse to "correct history" should become "append a
   correction event" instead.
   Deletion has exactly one gated path (`scripts/delete-event.sh`, with dry-run,
   a second confirmation, and a forced backup). Don't route around it.

If your PR changes a contract, say so in the description: which clause changed,
and why it cannot be backward compatible.

## Tests

```bash
cd modules/nexus-core/code/backend
python -m venv .venv                       # must be this exact path, see below
.venv/bin/pip install -r requirements.txt
NEXUS_MONGO_URI=mongodb://127.0.0.1:27017 \
NEXUS_DB_NAME=nexus_core_test \
NEXUS_TZ=Asia/Shanghai \
  .venv/bin/python -m pytest -q
```

Three things that will otherwise confuse you:

- **The venv must live at `backend/.venv`.** `scripts/delete-event.sh` and
  `prune-orphan-events.sh` hard-code `PY="$BACKEND/.venv/bin/python"` and exit
  if it is missing, rather than falling back to the system python — the fallback
  would fail later and more obscurely, on a missing `pymongo`. Installing
  elsewhere turns 13 tests red, all of them the safety gates around deletion and
  cleanup.
- **`NEXUS_DB_NAME` must end in `_test`.** `conftest.py` refuses to run when the
  database name does not look like a test database. The tests truncate
  collections; that refusal is what stands between them and a real database.
- **Two tests need `docker exec` against a named mongo container** (the
  `--apply` path takes a real backup first). Without that container they abort
  correctly. In CI, skip exactly those two with `--deselect` — do **not** skip
  them by file: the same files hold 39 more tests that need no Docker, including
  "dry-run deletes nothing", "a wrong confirmation aborts", and "a failed backup
  aborts".

Critical configuration has **no defaults** and fails immediately when absent.
That is the behavior under test — please don't add defaults for convenience.

## Opening a PR

- One PR, one thing. Contract changes, implementation changes, and doc changes
  go separately.
- Bring a test that can fail. If you changed behavior, add an assertion — and an
  assertion only counts once you have **verified it in reverse**: break the
  implementation and confirm it actually goes red.
- Write commit messages about **why**, not what. The what is in the diff.

## A note on language

The code comments and `module_docs/` are currently in Chinese; the project
started as a personal tool. English contributions are welcome and no one will
ask you to write Chinese. If you are translating existing comments, do it in a
PR of its own — mixing a translation pass into a behavior change makes both
impossible to review.
