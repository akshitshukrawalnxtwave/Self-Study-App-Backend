# Agent ↔ S3 Storage Architecture

How the Claude Agent SDK works against workspace files that live in S3, why the
agent does **not** talk to S3 directly, where the "too many S3 requests" problem
actually comes from, and how to fix it.

This complements [PROJECT_GUIDE.md](./PROJECT_GUIDE.md) (§6 Storage backends,
§12 Agent service) and [API.md](./API.md).

---

## Table of contents

1. [The core constraint](#1-the-core-constraint)
2. ["Agent works directly with S3" — why that's the wrong goal](#2-agent-works-directly-with-s3--why-thats-the-wrong-goal)
3. [The pattern we actually use: hydrate → run → sync back](#3-the-pattern-we-actually-use-hydrate--run--sync-back)
4. [Where the requests come from (the real cost model)](#4-where-the-requests-come-from-the-real-cost-model)
5. [Why "many files per folder" is NOT the problem](#5-why-many-files-per-folder-is-not-the-problem)
6. [Will S3 handle the load? Rate limits vs. cost vs. latency](#6-will-s3-handle-the-load-rate-limits-vs-cost-vs-latency)
7. [Solution 1 — Incremental sync (delta, not full)](#7-solution-1--incremental-sync-delta-not-full)
8. [Solution 2 — Warm cache + session affinity](#8-solution-2--warm-cache--session-affinity)
9. [Solution 3 — Derive snapshots locally, parallelize transfers](#9-solution-3--derive-snapshots-locally-parallelize-transfers)
10. [The concurrency question (10–12 agents)](#10-the-concurrency-question-1012-agents)
11. [Target-state summary](#11-target-state-summary)

---

## 1. The core constraint

The teaching loop is driven by the **Claude Agent SDK**. The agent's whole model
of the world is a **POSIX filesystem rooted at a working directory (`cwd`)**. The
`teach` skill assumes this everywhere:

- `MISSION.md`, `RESOURCES.md`, `NOTES.md` at the workspace root
- `./lessons/*.html`, `./reference/*.html`, `./learning-records/*.md`, `./assets/*`
- Lessons link to each other and to assets with **relative paths**
  (`../assets/lesson.css`), which only resolve inside a real directory tree.

The tools the agent is given are filesystem tools:

```python
# workspaces/services/agent.py  (_build_sdk_options)
"allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
"cwd": workspace_path,
```

`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash` all operate on a local path. They
do not speak the S3 API. **S3 is an object store, not a filesystem** — no
directories, no `mtime`-based partial writes, no `Bash` against it.

So the question isn't "how do I make the agent talk to S3." It's "how do I give
the agent a filesystem while S3 remains the durable source of truth."

---

## 2. "Agent works directly with S3" — why that's the wrong goal

There are three ways to bridge a filesystem agent to S3. Only one is right for us.

| Option | How | Verdict |
|--------|-----|---------|
| **A. Agent calls S3 per operation** (custom tools that GET/PUT on every read/write) | Replace `Read`/`Write` with S3-backed tools | ❌ Every file op becomes a network round-trip. `Glob`/`Grep`/`Bash` can't work. Relative-path lessons break. Latency explodes. |
| **B. Mount S3 as a filesystem** (`s3fs-fuse`, AWS `mountpoint-s3`) | FUSE mount, agent sees a "directory" | ⚠️ Transparent, but every `open()`/`readdir()` is still an S3 call under the hood. Poor for many small files + frequent writes; write semantics are limited. Just hides the request amplification. |
| **C. Local mirror, sync at boundaries** ✅ | Download workspace to local disk, run agent locally, upload changes | ✅ Agent gets a real, fast filesystem. S3 requests happen **only** at turn boundaries, not per file op. **This is what the code does today.** |

**Conclusion:** "the agent works directly with S3" is not the target and would be
actively harmful (Options A/B turn every `Read` into a billable, latency-adding
network call). The target is **Option C**: the agent works directly with a *local
filesystem that is a mirror of S3*, and the backend owns the sync.

The rest of this doc is about making Option C efficient.

---

## 3. The pattern we actually use: hydrate → run → sync back

This already exists in `workspaces/services/agent.py::run_turn`:

```python
storage = get_storage()
workspace_id = str(workspace.id)

# 1. HYDRATE: pull S3 -> local so the SDK has a real cwd
if is_s3_backend():
    ensure_local_workspace_dirs(workspace_id)
    sync_s3_to_local(workspace_id)

before = storage.snapshot(workspace_id)      # file set + mtimes, before the turn

# 2. RUN: agent reads/writes LOCAL files only (zero S3 traffic here)
result = self._run_with_timeout(workspace, session, user_content)

# 3. SYNC BACK: push local -> S3 so writes are durable
if is_s3_backend() and not settings.AGENT_FIXTURE_MODE:
    uploaded = sync_local_to_s3(workspace_id)

after = storage.snapshot(workspace_id)       # diff before/after -> lesson artifacts
```

The two-storage-backend design (`STORAGE_BACKEND=local` in dev, `s3` in prod)
lives in `workspaces/storage/` behind the `WorkspaceStorage` interface
(`base.py`, `local.py`, `s3.py`). This is a good foundation. The problem is
purely in **how much** the sync moves.

---

## 4. Where the requests come from (the real cost model)

Look at the current sync functions in `workspaces/storage/__init__.py`:

```python
def sync_s3_to_local(workspace_id, ...):
    for rel_path in storage.list(workspace_id, ""):     # 1 LIST (paginated)
        target.write_bytes(storage.read_bytes(...))     # 1 GET *per file*

def sync_local_to_s3(workspace_id, ...):
    for path in root.rglob("*"):                        # every local file
        storage.write_bytes(workspace_id, rel, ...)     # 1 PUT *per file* (even unchanged!)
```

And `snapshot()` for S3 is a full `list_objects_v2` paginate (a LIST).

**Per-turn S3 request count for a workspace with N files:**

| Step | S3 requests |
|------|-------------|
| `sync_s3_to_local` — list | 1 LIST |
| `sync_s3_to_local` — download | **N GET** |
| `before = snapshot()` | 1 LIST |
| agent turn (reads/writes local) | **0** |
| `sync_local_to_s3` — upload everything | **N PUT** |
| `after = snapshot()` | 1 LIST |
| **Total** | **≈ 3 LIST + N GET + N PUT** |

For a mature workspace — a few lessons, a `reference/` folder, `assets/`,
learning records — N is easily **20–40 files**. That's **~60–80 S3 requests every
single chat turn**, and the overwhelming majority are redundant:

- We re-download files that haven't changed since last turn.
- We re-upload files the agent never touched.

**This is the "too many requests" problem.** It is not caused by the agent, and
not caused by having many files in `lessons/` / `reference/` / `material/`. It is
caused by **full sync on both edges of every turn.**

---

## 5. Why "many files per folder" is NOT the problem

Your worry — *"if there are many files in the lecture / reference / material
folders, and each turn the backend makes so many calls…"* — is aimed at the wrong
layer. Two separate things are happening:

1. **The agent reading files during a turn.** When the agent does `Read
   reference/thermodynamics.html` or `Glob reference/*.html`, it reads from
   **local disk**. Those operations cost **zero S3 requests**, no matter how many
   files are in the folder. The agent can open 100 reference files in a turn and
   S3 sees nothing.

2. **The backend syncing the folder to/from S3.** *This* is where many files =
   many requests — but only because the current sync copies **all** of them every
   turn instead of just the delta.

So the fix is not "reduce the number of files the agent can see" (that would hurt
teaching quality). The fix is **make the sync move only what changed**, and
**keep the local mirror warm between turns** so most turns download nothing.

---

## 6. Will S3 handle the load? Rate limits vs. cost vs. latency

Three different concerns, often conflated:

**Rate limits — almost certainly a non-issue.** S3 scales to **5,500 GET/HEAD and
3,500 PUT/COPY/POST/DELETE requests per second _per prefix_**. Each workspace is
its own prefix (`workspaces/{workspace_id}/…`), so one user's turn (~60 requests
spread over a couple seconds) is nowhere near the ceiling. You would need
thousands of turns/second against a *single* workspace prefix to get throttled
(`503 SlowDown`). That won't happen — different users are different prefixes.

**Cost — real but small, and worth trimming.** S3 request pricing is roughly
\$0.0004 / 1,000 GET and \$0.005 / 1,000 PUT. 80 requests/turn × millions of turns
adds up, and it's ~90% waste. Incremental sync removes most of it.

**Latency — the one that actually hurts UX.** Each GET/PUT is a round-trip
(~10–30 ms in-region). N of them run **serially** in the current code, so a
40-file workspace adds **1–2 seconds of pure S3 wall-clock per turn**, on top of
agent thinking time. This is the most user-visible symptom and the best reason to
fix sync.

> **Bottom line:** S3 *can* handle it; it won't fall over. But the current access
> pattern is slow and wasteful. The goal is fewer, smarter requests — for latency
> and cost, not because S3 will break.

---

## 7. Solution 1 — Incremental sync (delta, not full)

Move only what changed. Two halves:

### Upload only what the agent changed

We already compute the diff — we just don't use it for the upload. `run_turn`
takes `before` and `after` snapshots (path → mtime). The changed set is
`after`-minus-`before` plus mtime-changed. Upload exactly those:

```python
# Replace the blanket sync_local_to_s3() with a targeted push.
def sync_changed_to_s3(workspace_id, before, after, local_root):
    changed = [p for p in after if p not in before or after[p] != before[p]]
    deleted = [p for p in before if p not in after]
    for rel in changed:
        storage.write_bytes(workspace_id, rel, (local_root / rel).read_bytes())
    for rel in deleted:
        storage.delete(workspace_id, rel)          # s3.py already has delete()
    return changed, deleted
```

A typical turn changes **1–3 files** (one new lesson, maybe a learning record).
That turns **N PUT → ~2 PUT**.

### Download only what's missing or stale

Don't re-pull the whole workspace if the local mirror is already warm. Compare S3
`LastModified` against local `mtime` and fetch only newer/missing files:

```python
def sync_s3_to_local_incremental(workspace_id, local_root):
    remote = storage.snapshot(workspace_id)        # {rel: s3_last_modified} (1 LIST)
    for rel, remote_mtime in remote.items():
        target = local_root / rel
        if target.exists() and target.stat().st_mtime >= remote_mtime:
            continue                               # already current -> skip GET
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(storage.read_bytes(workspace_id, rel))
```

On a warm mirror (see §8), most turns skip **every** GET. Cold start still pays
N GET once, which is fine.

**Result per warm turn:** ~1 LIST + 0 GET + ~2 PUT ≈ **3 requests instead of 80.**

---

## 8. Solution 2 — Warm cache + session affinity

Incremental sync only helps if the local mirror **survives between turns**. Two
requirements:

1. **Persistent local storage for `WORKSPACES_ROOT`.** Don't put it on an
   ephemeral container layer that's wiped per request. Use a mounted volume
   (EBS/EFS/persistent disk) or a long-lived worker with local SSD. Then the
   files the agent wrote last turn are still there this turn.

2. **Session affinity (sticky routing).** Route all turns for a given workspace
   to the **same worker** that already has the warm mirror. Otherwise worker B
   has to re-hydrate everything worker A already had. Options:
   - Route by `workspace_id` hash to a worker (consistent hashing), or
   - Pin a workspace to a worker for the lifetime of a `ChatSession`.

With both in place, the steady state is: **first turn hydrates, every later turn
downloads ~nothing and uploads only the 1–3 files it changed.**

If you can't get persistent local disk (e.g. fully stateless Lambda-style
workers), the fallback is **mountpoint-s3 with the local cache enabled**
(`--cache /path` + metadata TTL). It gives read caching so repeated reads of the
same reference file don't re-fetch. It's a weaker version of the mirror pattern —
prefer persistent-volume + affinity when you can.

---

## 9. Solution 3 — Derive snapshots locally, parallelize transfers

Two smaller wins on top of §7–8:

**Snapshot from local disk, not S3.** After hydration, the local mirror *is* the
truth for the turn. Take `before`/`after` with `LocalWorkspaceStorage.snapshot()`
against `WORKSPACES_ROOT` instead of calling S3's paginated LIST twice. Saves
2 LISTs/turn and is faster. (S3 stays the durable store; we just don't need it to
compute an in-turn diff.)

**Parallelize the transfers that remain.** When a cold start genuinely needs N
GETs, run them concurrently instead of the current serial loop:

```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=16) as ex:
    list(ex.map(_download_one, missing_paths))
```

boto3 clients are thread-safe for this. 40 serial GETs (~1.5 s) become ~4 batches
(~150 ms). Same for the (now-small) upload set.

---

## 10. The concurrency question (10–12 agents)

Your note — *"only 10–12 agents working at a time, or a better approach"* — is a
**separate bottleneck from S3**, and it's important not to solve the wrong one.

Each turn (`agent.py::_run_with_timeout`) spawns a Claude Agent SDK subprocess
under a `ThreadPoolExecutor(max_workers=1)` with a 300 s timeout. The limit on how
many run at once is **CPU / memory / subprocess count on the worker** — *not* S3.
S3 is happy with far more concurrency than your compute is.

Recommended shape:

- **Don't run agents inline in the web request.** Put turns on a **task queue**
  (Celery / RQ / Django-Q). The web request creates the turn and returns; the
  worker runs the agent and the frontend polls or streams the result. This is
  also what lets you enforce a global concurrency cap cleanly.
- **Concurrency = a worker-pool setting, not a hardcoded 10–12.** Size the pool to
  `~CPU cores` per worker box, then **autoscale the number of workers** on queue
  depth. The "10–12" becomes "N per worker × M workers," and M scales with load.
- **Combine with session affinity (§8):** route a workspace's queued turns to the
  worker holding its warm mirror.
- **Backpressure, not failure:** when all slots are busy, turns wait in the queue
  (show "thinking…") instead of erroring. The 300 s timeout still bounds runaway
  turns.

So: keep a per-worker concurrency limit (that's healthy), but make the *fleet*
scale horizontally behind a queue rather than capping the whole system at a fixed
small number.

---

## 11. Target-state summary

| Concern | Today | Target |
|---------|-------|--------|
| Agent ↔ storage | Local mirror, synced per turn ✅ | Same, keep it |
| Download per warm turn | N GET (all files) | ~0 GET (delta only) |
| Upload per turn | N PUT (all files) | ~1–3 PUT (changed only) + deletes |
| Snapshots | 2 S3 LISTs | Local disk (0 S3) |
| Local mirror lifetime | Assumed re-hydrated | Persistent volume + session affinity |
| Cold-start transfers | Serial | Parallel (ThreadPool) |
| Agent concurrency | Inline, ~10–12 cap | Task queue + per-worker cap + autoscaling |
| S3 rate limits | Not a risk (per-prefix, per-workspace) | Still not a risk |

**One-line answer to "won't so many calls be a problem?"**
The agent reading many files is free (local disk). The cost is the backend
*syncing* every file every turn. Switch to **incremental sync + a warm,
worker-pinned local mirror**, and a normal turn drops from ~80 S3 requests to a
handful — while the agent keeps its full, fast filesystem view of every lesson,
reference, and material file.

---

## Implementation checklist

- [ ] Add `delete()` to the `WorkspaceStorage` ABC (S3 has it; Local doesn't yet).
- [ ] Replace `sync_local_to_s3` with a `before`/`after`-diff upload (§7).
- [ ] Replace `sync_s3_to_local` with an mtime/`LastModified` incremental download (§7).
- [ ] Take `before`/`after` snapshots from the local mirror when on S3 (§9).
- [ ] Parallelize cold-start GET/PUT with a thread pool (§9).
- [ ] Mount a persistent volume for `WORKSPACES_ROOT`; add session affinity (§8).
- [ ] Move `run_turn` onto a task queue with a per-worker concurrency cap + autoscaling (§10).
