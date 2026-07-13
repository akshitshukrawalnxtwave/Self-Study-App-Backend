# Self-Study App Backend — Complete Project Guide

This document describes how the backend works end-to-end: architecture, storage, development vs production, where every kind of data is saved, and how the frontend loads lessons.

For the HTTP API contract alone, see [API.md](./API.md).

---

## Table of contents

1. [What this project does](#1-what-this-project-does)
2. [High-level architecture](#2-high-level-architecture)
3. [Repository layout](#3-repository-layout)
4. [Two kinds of data](#4-two-kinds-of-data)
5. [Workspace file layout](#5-workspace-file-layout)
6. [Storage backends](#6-storage-backends)
7. [Development environment](#7-development-environment)
8. [Production environment](#8-production-environment)
9. [Database models](#9-database-models)
10. [HTTP routes](#10-http-routes)
11. [End-to-end flows](#11-end-to-end-flows)
12. [Agent service](#12-agent-service)
13. [How lessons, CSS, and JS reach the browser](#13-how-lessons-css-and-js-reach-the-browser)
14. [Environment variables reference](#14-environment-variables-reference)
15. [Commands and operations](#15-commands-and-operations)
16. [Security (current and planned)](#16-security-current-and-planned)
17. [Roadmap milestones](#17-roadmap-milestones)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. What this project does

The backend powers a **self-study teaching app**:

- **Left panel:** chat with an AI teacher (Claude Agent SDK + `teach` skill).
- **Right panel:** interactive HTML lessons in an iframe.

Each learning topic gets an isolated **workspace**. The agent writes lesson files, mission docs, and learning records into that workspace. The API returns **URLs** to lesson HTML (not inline HTML). The browser loads HTML, CSS, and JS as separate file requests.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         React frontend                                   │
│  Chat panel (left)              Lesson iframe (right)                     │
│       │                                │                                 │
│       │ POST /api/.../chat/            │ GET /workspaces/{id}/lessons/…  │
│       │ GET  /api/.../lessons/         │ GET /workspaces/{id}/assets/…   │
└───────┼────────────────────────────────┼─────────────────────────────────┘
        │                                │
        ▼                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Django backend (this repo)                          │
│                                                                          │
│  views.py          agent.py           response_mapper.py                 │
│  (HTTP handlers)   (Claude SDK)       (file diff → artifacts, URLs)      │
│       │                  │                      │                        │
│       └──────────────────┼──────────────────────┘                        │
│                          ▼                                               │
│                   get_storage()                                          │
│              ┌───────────┴───────────┐                                   │
│              ▼                       ▼                                   │
│     LocalWorkspaceStorage    S3WorkspaceStorage                          │
└──────────────┬──────────────────────────┬───────────────────────────────┘
               │                          │
               ▼                          ▼
     workspaces_data/{id}/        s3://{bucket}/{prefix}/{id}/
     (local disk)                  (AWS S3)

┌─────────────────────────────────────────────────────────────────────────┐
│  SQLite (db.sqlite3) — workspaces, chat sessions, messages only        │
│  (lesson HTML/CSS/JS are NOT stored in the database)                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Design principles

| Principle | Detail |
|-----------|--------|
| **Files ≠ DB** | Lesson content lives in workspace storage (disk or S3). SQLite only stores metadata and chat. |
| **URL, not inline HTML** | Chat responses return `panel.html_url`. Browser loads files separately. |
| **Storage abstraction** | All file I/O goes through `WorkspaceStorage` (`read`, `write`, `list`, `exists`, `snapshot`). |
| **Agent needs a real filesystem** | Even with S3, the Claude SDK runs with `cwd` on local disk; S3 is synced before/after turns. |

---

## 3. Repository layout

```
Self-Study-App-Backend/
├── config/                    # Django project settings
│   ├── settings.py            # Env vars, storage, agent config
│   └── urls.py                # Routes: /admin, /api, /workspaces
├── workspaces/                # Main Django app
│   ├── models.py              # Workspace, ChatSession, Message
│   ├── views.py               # API + file serving handlers
│   ├── api_urls.py            # /api/workspaces/...
│   ├── file_urls.py           # /workspaces/{id}/...
│   ├── utils.py               # JSON helpers, turn_to_dict
│   ├── storage/               # Storage abstraction
│   │   ├── base.py            # WorkspaceStorage ABC
│   │   ├── local.py           # Local disk backend
│   │   ├── s3.py              # AWS S3 backend
│   │   └── __init__.py        # get_storage(), S3 sync helpers
│   ├── services/
│   │   ├── agent.py           # AgentService, SDK integration
│   │   ├── response_mapper.py # Artifacts + panel.html_url
│   │   └── seeding.py         # Seed assets, sample lesson HTML
│   ├── seed_assets/           # Template lesson.css, quiz.js
│   ├── management/commands/
│   │   └── migrate_workspaces_to_s3.py
│   └── migrations/
├── .claude/skills/teach/      # Teach skill for Claude Agent SDK
├── docs/
│   ├── API.md                 # HTTP API contract
│   └── PROJECT_GUIDE.md       # This file
├── workspaces_data/           # Local workspace files (gitignored)
├── db.sqlite3                 # SQLite database (gitignored)
├── .env                       # Secrets (gitignored; see .env.example)
├── .env.example
├── manage.py
└── requirements.txt
```

---

## 4. Two kinds of data

### A. Database (SQLite in dev; Postgres recommended in prod)

| Stored in DB | Examples |
|--------------|----------|
| Workspace metadata | `id`, `title`, `topic_slug`, `created_at`, `last_panel_html_url` |
| Chat sessions | `id`, `workspace_id`, `sdk_session_id`, `is_active` |
| Messages | `role`, `content`, `turn_id`, timestamps |

**Not in DB:** lesson HTML, CSS, JS, `MISSION.md`, or any workspace file content.

### B. Workspace files (local disk or S3)

| Stored as files | Examples |
|-----------------|----------|
| Lessons | `lessons/0001-getting-started.html` |
| Shared assets | `assets/lesson.css`, `assets/quiz.js` |
| Agent-written docs | `MISSION.md`, `RESOURCES.md`, `learning-records/*.md` |
| Reference material | `reference/*.html` |

The database holds a workspace **UUID**. That UUID is the key for both DB rows and the file storage path/prefix.

---

## 5. Workspace file layout

Every workspace uses the same directory structure (on disk or in S3):

```
{workspace_id}/
├── MISSION.md                 # Learning mission (agent-written)
├── RESOURCES.md               # Optional resources (agent-written)
├── NOTES.md                   # Optional notes
├── lessons/
│   ├── 0001-getting-started.html
│   ├── 0002-pressure-depth.html
│   └── remembering-things.html
├── reference/
│   └── series-and-dataframe.html
├── learning-records/
│   └── 0001-topic-started.md
└── assets/
    ├── lesson.css             # Shared stylesheet (seeded at workspace creation)
    └── quiz.js                # Shared quiz interactivity (seeded)
```

### Where files are physically saved

| Environment | `STORAGE_BACKEND` | Physical location |
|-------------|-------------------|-------------------|
| **Development (default)** | `local` | `{PROJECT_ROOT}/workspaces_data/{workspace_id}/...` |
| **Production (S3)** | `s3` | `s3://{AWS_S3_BUCKET_NAME}/{AWS_S3_KEY_PREFIX}/{workspace_id}/...` |

Example local path:

```
workspaces_data/a1b2c3d4-e5f6-7890-abcd-ef1234567890/lessons/0001-getting-started.html
```

Example S3 key:

```
s3://my-bucket/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890/lessons/0001-getting-started.html
```

### Public URL mapping (same in dev and prod today)

```
File path:  lessons/0001-getting-started.html
Public URL: /workspaces/{workspace_id}/lessons/0001-getting-started.html
```

No separate mapping table. The URL is derived from `workspace_id` + relative path.

---

## 6. Storage backends

All file operations use the `WorkspaceStorage` interface:

```python
class WorkspaceStorage:
    def read(workspace_id, path) -> str
    def read_bytes(workspace_id, path) -> bytes
    def write(workspace_id, path, content: str) -> None
    def list(workspace_id, prefix) -> list[str]
    def exists(workspace_id, path) -> bool
    def ensure_workspace(workspace_id) -> None
    def snapshot(workspace_id) -> dict[str, float]  # path → mtime
```

`get_storage()` in `workspaces/storage/__init__.py` picks the backend from `STORAGE_BACKEND`.

### Local storage (`STORAGE_BACKEND=local`)

- **Implementation:** `LocalWorkspaceStorage` in `workspaces/storage/local.py`
- **Root:** `settings.WORKSPACES_ROOT` → defaults to `BASE_DIR / 'workspaces_data'`
- **Read/write:** direct filesystem operations with path-traversal protection
- **Used by:** API views, agent (SDK `cwd`), file serving

### S3 storage (`STORAGE_BACKEND=s3`)

- **Implementation:** `S3WorkspaceStorage` in `workspaces/storage/s3.py`
- **Bucket:** `AWS_S3_BUCKET_NAME`
- **Key prefix:** `AWS_S3_KEY_PREFIX` (default `workspaces`)
- **Full key:** `{prefix}/{workspace_id}/{relative_path}`
- **Auth:** `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, or IAM role on AWS

### S3 + agent: local cache mirror

The Claude Agent SDK requires a **real filesystem** as `cwd`. When `STORAGE_BACKEND=s3`:

```
Before agent turn:
  sync_s3_to_local(workspace_id)
  → downloads all S3 objects to workspaces_data/{id}/

Agent runs:
  cwd = workspaces_data/{id}/
  agent writes/edits files locally

After agent turn (real SDK only, not fixture mode):
  sync_local_to_s3(workspace_id)
  → uploads all local files back to S3
```

So in S3 mode, `workspaces_data/` acts as a **cache** for the agent, while S3 remains the source of truth for API reads/writes via `get_storage()`.

| Operation | S3 mode — who reads/writes |
|-----------|----------------------------|
| `GET /api/.../lessons/` | `S3WorkspaceStorage.list()` → S3 |
| `serve_workspace_file` | `S3WorkspaceStorage.read_bytes()` → S3 |
| `seed_workspace_assets` | `S3WorkspaceStorage.write()` → S3 |
| Agent SDK `cwd` | Local `workspaces_data/{id}/` |
| After agent turn | `sync_local_to_s3()` → S3 |

---

## 7. Development environment

### What is used in dev (typical)

| Component | Dev default |
|-----------|-------------|
| Storage | **Local disk** (`STORAGE_BACKEND=local`) |
| Database | **SQLite** (`db.sqlite3`) |
| Agent | **Fixture mode** (`AGENT_FIXTURE_MODE=true`) — no real Claude API calls |
| File serving | **Django proxy** (`GET /workspaces/...`) |
| Django server | `python manage.py runserver` on port 8000 |
| Frontend | Vite dev server proxies `/api` and `/workspaces` to Django |

### Dev setup steps

```bash
# 1. Virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Environment
cp .env.example .env
# Edit .env — AGENT_FIXTURE_MODE=true for local dev without API key

# 4. Database
python manage.py migrate

# 5. Run server
python manage.py runserver
```

### Dev `.env` (minimal)

```env
STORAGE_BACKEND=local
AGENT_FIXTURE_MODE=true
AGENT_TIMEOUT_SECONDS=300
AGENT_PERMISSION_MODE=bypassPermissions
AGENT_MAX_TURNS=25
```

### What happens when you create a workspace in dev

1. `POST /api/workspaces/` creates a `Workspace` row in SQLite.
2. `seed_workspace_assets(workspace_id)` runs:
   - Creates `workspaces_data/{id}/lessons/`, `assets/`, etc.
   - Copies `workspaces/seed_assets/lesson.css` → `assets/lesson.css`
   - Copies `workspaces/seed_assets/quiz.js` → `assets/quiz.js`
3. A `ChatSession` is created.

### What happens on chat in dev (fixture mode)

1. User message saved to DB.
2. `_fixture_turn()` simulates the agent:
   - Turn 1: welcome / mission interview text, no lesson.
   - Turn 2+: writes `lessons/0001-getting-started.html` and `MISSION.md` to local disk.
3. `response_mapper` diffs file snapshot before/after → builds `artifacts` and `panel.html_url`.
4. Assistant message saved to DB.
5. JSON response returned with `panel.html_url`.

### Frontend proxy (development)

```ts
// vite.config.ts
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/workspaces': 'http://localhost:8000',
  },
}
```

---

## 8. Production environment

### What is used in prod (recommended)

| Component | Production |
|-----------|------------|
| Storage | **S3** (`STORAGE_BACKEND=s3`) |
| Database | **PostgreSQL** (not implemented in repo yet; replace SQLite) |
| Agent | **Real SDK** (`AGENT_FIXTURE_MODE=false`, `AGENT_PROVIDER=anthropic` or `bedrock`) |
| Django | Gunicorn/uWSGI behind reverse proxy |
| File serving | **Option A:** Django proxy (server must stay up) |
| | **Option B:** Presigned S3 URLs (ephemeral chat workers) |
| Secrets | IAM role or env vars; never commit `.env` |

### Prod `.env` (S3 + real agent)

**Option A — Anthropic API**

```env
STORAGE_BACKEND=s3
AWS_S3_BUCKET_NAME=your-bucket-name
AWS_S3_REGION=ap-south-1
AWS_S3_KEY_PREFIX=workspaces
AWS_ACCESS_KEY_ID=...          # or use IAM role
AWS_SECRET_ACCESS_KEY=...

AGENT_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
AGENT_FIXTURE_MODE=false
AGENT_TIMEOUT_SECONDS=300
AGENT_PERMISSION_MODE=bypassPermissions
AGENT_MAX_TURNS=25
AGENT_MODEL=sonnet
```

**Option B — Amazon Bedrock (no Anthropic API key)**

```env
STORAGE_BACKEND=s3
AWS_S3_BUCKET_NAME=your-bucket-name
AWS_S3_REGION=us-east-1
AWS_S3_KEY_PREFIX=workspaces
AWS_ACCESS_KEY_ID=...          # or use IAM role on EC2/ECS
AWS_SECRET_ACCESS_KEY=...

AGENT_PROVIDER=bedrock
AWS_REGION=us-east-1           # Bedrock region (can differ from S3)
AGENT_FIXTURE_MODE=false
AGENT_TIMEOUT_SECONDS=300
AGENT_PERMISSION_MODE=bypassPermissions
AGENT_MAX_TURNS=25
AGENT_MODEL=sonnet
# Optional: pin Bedrock model IDs
# ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-4-6
```

Bedrock IAM policy must include `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`. Enable Claude models in the Bedrock console for your region.

### S3 bucket setup

1. Create a **private** bucket (block all public access).
2. IAM policy with `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`, `s3:HeadObject` on the bucket.
3. Optional: CORS if the browser loads directly from S3 via presigned URLs.

### Migrating existing local workspaces to S3

```bash
# Preview
python manage.py migrate_workspaces_to_s3 --dry-run

# Upload all
python manage.py migrate_workspaces_to_s3

# Single workspace
python manage.py migrate_workspaces_to_s3 --workspace-id <uuid>
```

Requires `STORAGE_BACKEND=s3` and valid AWS credentials.

### Production file serving — two options

#### Option A: Django proxy (current code)

```
Browser → GET /workspaces/{id}/lessons/foo.html
       → Django serve_workspace_file
       → S3WorkspaceStorage.read_bytes()
       → returns HTML
```

- URLs are stable and never expire.
- Django (or an always-on file service) must be running for iframe loads.

#### Option B: Presigned S3 URLs (recommended for ephemeral workers)

```
Chat worker → uploads files to S3 → returns presigned html_url → shuts down
Browser     → GET presigned S3 URL directly → loads HTML/CSS/JS from S3
```

- Chat workers can be ephemeral.
- **Lesson list must return stable `path` values**, not long-lived presigned URLs.
- On lesson tab click: call a **sign endpoint** to get a fresh presigned URL, then set `iframe.src`.

> Presigned URL generation is **not implemented yet** in the codebase. The architecture doc describes the intended production pattern.

### Ephemeral chat worker flow (target production)

```
1. POST /chat/  →  worker starts
2. sync_s3_to_local (if needed)
3. Agent runs, writes files locally
4. sync_local_to_s3
5. Return { panel: { html_url: "<presigned or stable url>" } }
6. Worker exits
7. Browser loads lesson from S3 (no chat worker needed)
```

---

## 9. Database models

### Workspace

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID (PK) | Workspace identity; also used in file paths |
| `user_id` | UUID, nullable | Reserved for M5 multi-user auth |
| `title` | string | Display name ("Learn Python") |
| `topic_slug` | slug, unique | Dedup key ("learn-python") |
| `created_at` | datetime | Creation time |
| `last_panel_html_url` | string | Last lesson URL shown in panel |

### ChatSession

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID (PK) | Session identity |
| `workspace` | FK → Workspace | Parent workspace |
| `last_active_at` | datetime | Updated on each message |
| `is_active` | bool | One active session per workspace (M2) |
| `sdk_session_id` | string | Claude Agent SDK resume ID |

### Message

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID (PK) | Message identity |
| `session` | FK → ChatSession | Parent session |
| `role` | `user` \| `assistant` | Speaker |
| `message_type` | string | Always `text` for now |
| `content` | text | Message body |
| `turn_id` | UUID, nullable | Links assistant reply to a chat turn |
| `created_at` | datetime | Timestamp |

---

## 10. HTTP routes

### JSON API (`/api/...`)

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| `GET` | `/api/workspaces/` | `list_workspaces` | List all workspaces |
| `POST` | `/api/workspaces/` | `workspaces_collection` | Create workspace (or return existing by slug) |
| `GET` | `/api/workspaces/{id}/lessons/` | `list_lessons` | List lesson HTML files with URLs |
| `GET` | `/api/workspaces/{id}/messages/` | `list_messages` | Chat history for active session |
| `POST` | `/api/workspaces/{id}/chat/` | `chat` | Send message, run agent, return turn |

### Workspace files (not JSON — raw file bytes)

| Method | Path | Handler | Purpose |
|--------|------|---------|---------|
| `GET` | `/workspaces/{workspace_id}/{file_path}` | `serve_workspace_file` | Serve HTML, CSS, JS, MD |

Defined in `workspaces/file_urls.py`. `file_path` can include slashes (e.g. `lessons/0001.html`).

---

## 11. End-to-end flows

### Flow 1: App startup

```
GET /api/workspaces/
  → SQLite: list Workspace rows
  → JSON: [{ id, title, topic_slug, created_at }]
```

### Flow 2: Select workspace

```
GET /api/workspaces/{id}/lessons/
  → storage.list(id, "lessons")
  → filter *.html
  → JSON: [{ url, path, title }, ...]
```

Frontend stores lesson URLs for the tab bar.

### Flow 3: Send chat message

```
POST /api/workspaces/{id}/chat/  { content: "..." }
  1. Save user Message to SQLite
  2. agent_service.run_turn()
       a. [S3] sync_s3_to_local
       b. snapshot files (before)
       c. Run fixture or Claude SDK
       d. [S3] sync_local_to_s3
       e. snapshot files (after)
       f. map_turn → artifacts + panel.html_url
  3. Save assistant Message to SQLite
  4. Update workspace.last_panel_html_url
  5. JSON: { turn_id, messages, artifacts, panel: { html_url } }
```

**Important:** `panel.html_url` is a URL string only. No HTML/CSS/JS in the JSON body.

### Flow 4: Display lesson in iframe (new lesson from chat)

```
Frontend: iframe.src = panel.html_url
  e.g. /workspaces/{id}/lessons/remembering-things.html

GET /workspaces/{id}/lessons/remembering-things.html
  → serve_workspace_file
  → storage.read_bytes()
  → 200 text/html

Browser parses HTML, sees:
  <link href="../assets/lesson.css">
  <script src="../assets/quiz.js">

GET /workspaces/{id}/assets/lesson.css   → 200 text/css
GET /workspaces/{id}/assets/quiz.js      → 200 application/javascript
```

CSS and JS are **not** in the chat response. The browser fetches them because the HTML references them.

### Flow 5: Click lesson tab (sidebar)

```
No /api/ call on click.
Frontend sets iframe.src to URL already from lesson list.
Browser requests GET /workspaces/{id}/lessons/{filename}.html (+ assets).
CSS/JS may be served from browser cache if already loaded.
```

---

## 12. Agent service

Location: `workspaces/services/agent.py`

### Modes

| Mode | Env | Behaviour |
|------|-----|-----------|
| **Fixture** | `AGENT_FIXTURE_MODE=true` | Scripted responses; writes sample lesson locally. No API key needed. |
| **Real SDK (Anthropic)** | `AGENT_FIXTURE_MODE=false`, `AGENT_PROVIDER=anthropic` | Claude Agent SDK via Anthropic API. Requires `ANTHROPIC_API_KEY`. |
| **Real SDK (Bedrock)** | `AGENT_FIXTURE_MODE=false`, `AGENT_PROVIDER=bedrock` | Claude Agent SDK via Amazon Bedrock. Uses AWS credentials; no API key. |

### Real SDK configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `cwd` | `workspaces_data/{workspace_id}/` | Agent working directory |
| `skills` | `["teach"]` | Loads `.claude/skills/teach/SKILL.md` |
| `allowed_tools` | Read, Write, Edit, Glob, Grep, Bash | File tools |
| `permission_mode` | `bypassPermissions` | No interactive prompts (required for headless server) |
| `max_turns` | 25 | SDK turn limit |
| `model` | `sonnet` | Model alias (`AGENT_MODEL`) |
| `env` | — | When `AGENT_PROVIDER=bedrock`, passes `CLAUDE_CODE_USE_BEDROCK=1` and AWS region/credentials to the SDK subprocess |
| `resume` | `session.sdk_session_id` | Multi-turn continuity |

### Prompt format

```
/teach {user_message}
```

### Response mapping

`response_mapper.py` compares file snapshots before and after the agent turn:

- New/changed files → `artifacts[]` with `type`, `path`, `action`, `url`
- Latest new lesson → `panel.html_url`
- If no new lesson this turn → keeps `previous_panel_html_url`

---

## 13. How lessons, CSS, and JS reach the browser

This is the complete chain — nothing is embedded in chat JSON.

### Step 1: Seed assets (workspace creation)

```
workspaces/seed_assets/lesson.css  ──copy──►  {storage}/assets/lesson.css
workspaces/seed_assets/quiz.js     ──copy──►  {storage}/assets/quiz.js
```

### Step 2: Agent writes lesson HTML

Lesson HTML contains **relative references**, not inline CSS/JS:

```html
<link rel="stylesheet" href="../assets/lesson.css">
<script src="../assets/quiz.js"></script>
```

### Step 3: Chat API returns URL only

```json
{
  "panel": {
    "html_url": "/workspaces/{id}/lessons/remembering-things.html"
  }
}
```

### Step 4: Browser loads files (3 requests minimum)

| # | Request | Response | Served from |
|---|---------|----------|-------------|
| 1 | `GET .../lessons/remembering-things.html` | HTML | storage |
| 2 | `GET .../assets/lesson.css` | CSS | storage |
| 3 | `GET .../assets/quiz.js` | JS | storage |

In **local dev**, storage = `workspaces_data/`.
In **S3 mode**, storage = S3 (via Django proxy today).

### Shared assets across lessons

`lesson.css` and `quiz.js` are **one copy per workspace**, shared by all lessons. When the user switches lesson tabs:

- HTML is fetched again (~5 KB)
- CSS/JS often come from **browser cache** (0 KB)

---

## 14. Environment variables reference

| Variable | Default | Used when | Purpose |
|----------|---------|-----------|---------|
| `STORAGE_BACKEND` | `local` | Always | `local` or `s3` |
| `WORKSPACES_ROOT` | `{BASE_DIR}/workspaces_data` | Code default | Local file root (not env in .env.example but in settings) |
| `AWS_S3_BUCKET_NAME` | `""` | `s3` | S3 bucket name |
| `AWS_S3_REGION` | `us-east-1` (settings) / `ap-south-1` (.env.example) | `s3` | AWS region |
| `AWS_S3_KEY_PREFIX` | `workspaces` | `s3` | Key prefix before workspace ID |
| `AWS_ACCESS_KEY_ID` | `""` | `s3` / `bedrock` | AWS credentials (optional if IAM role) |
| `AWS_SECRET_ACCESS_KEY` | `""` | `s3` / `bedrock` | AWS credentials |
| `AGENT_PROVIDER` | `anthropic` | Real agent | `anthropic` or `bedrock` |
| `AGENT_MODEL` | `sonnet` | Real agent | Model alias for SDK (`sonnet`, `opus`, `haiku`) |
| `AWS_REGION` | falls back to `AWS_S3_REGION` | `bedrock` | Bedrock region for agent inference |
| `ANTHROPIC_API_KEY` | — | `anthropic` agent | Claude API key (not used when `AGENT_PROVIDER=bedrock`) |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | — | `bedrock` | Optional Bedrock model ID override |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | — | `bedrock` | Optional Bedrock model ID override |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | — | `bedrock` | Optional Bedrock model ID override |
| `AGENT_FIXTURE_MODE` | `true` | Agent | `true` = mock agent |
| `AGENT_TIMEOUT_SECONDS` | `300` | Real agent | Max seconds per turn |
| `AGENT_PERMISSION_MODE` | `bypassPermissions` | Real agent | SDK permission mode |
| `AGENT_MAX_TURNS` | `25` | Real agent | SDK max turns |

---

## 15. Commands and operations

| Command | Purpose |
|---------|---------|
| `python manage.py migrate` | Apply DB migrations |
| `python manage.py runserver` | Start dev server |
| `python manage.py test workspaces` | Run tests (includes S3 tests with moto) |
| `python manage.py migrate_workspaces_to_s3` | Upload local `workspaces_data/` to S3 |
| `python manage.py migrate_workspaces_to_s3 --dry-run` | Preview upload |
| `python manage.py migrate_workspaces_to_s3 --workspace-id <uuid>` | Upload one workspace |

---

## 16. Security (current and planned)

### Current state (M2)

| Area | Status |
|------|--------|
| API authentication | **None** — all endpoints are public |
| File route auth | **None** — anyone with workspace UUID + path can fetch files |
| Path traversal | **Blocked** — `..` rejected in `serve_workspace_file` |
| S3 bucket | Must be **private** in production |
| CSRF | Exempt on `POST` chat and workspace create |

### Planned (M5)

- User model + authentication
- Workspace ownership via `user_id`
- Authorization on all routes
- Presigned URLs with short TTL + sign-on-demand for lesson loads

---

## 17. Roadmap milestones

| Milestone | Storage | Agent | Auth |
|-----------|---------|-------|------|
| **M2** (current) | Local disk | Fixture or SDK | None |
| **M3** | Local | Teach skill enabled | None |
| **M4** | S3 | Same | None |
| **M5** | S3 | Same | Multi-user |

See `docs/Self Study Roadmap N(1).md` for full product roadmap.

---

## 18. Troubleshooting

### Lessons not showing in iframe

- Check `panel.html_url` is not `null` in chat response.
- Verify file exists: `workspaces_data/{id}/lessons/...` (local) or S3 console.
- Check browser Network tab for 404 on HTML, CSS, or JS.
- Frontend and backend must be **same origin** for `X-Frame-Options: SAMEORIGIN` (or use proxy).

### Agent errors in dev

- `AGENT_FIXTURE_MODE=true` avoids needing Claude SDK/API key.
- If `AGENT_FIXTURE_MODE=false` and `AGENT_PROVIDER=anthropic`: set `ANTHROPIC_API_KEY` and install `claude-agent-sdk`.
- If `AGENT_FIXTURE_MODE=false` and `AGENT_PROVIDER=bedrock`: set `AWS_REGION`, enable Claude models in Bedrock, and ensure IAM has `bedrock:InvokeModel`.

### S3 errors

- Verify `STORAGE_BACKEND=s3` and `AWS_S3_BUCKET_NAME` set.
- Check IAM permissions.
- Run `migrate_workspaces_to_s3 --dry-run` to test connectivity.

### CSS/JS not loading in lesson

- Lesson HTML must use relative paths: `../assets/lesson.css` (not absolute `/assets/...`).
- Verify `assets/lesson.css` and `assets/quiz.js` exist in workspace (seeded on create).
- Check Network tab for 404 on asset URLs.

---

## Quick reference: dev vs prod

| | Development | Production |
|--|-------------|------------|
| **Storage** | Local `workspaces_data/` | S3 private bucket |
| **Database** | SQLite `db.sqlite3` | PostgreSQL (recommended) |
| **Agent** | Fixture mode (default) | Real Claude SDK |
| **Lesson URLs** | `/workspaces/{id}/...` (stable) | Stable proxy or presigned S3 |
| **File serving** | Django `serve_workspace_file` | Django proxy or direct S3 |
| **Chat response** | URL only, never inline HTML | Same |
| **CSS/JS delivery** | Browser fetches via HTML `<link>`/`<script>` | Same |
| **Secrets** | `.env` locally | IAM role / secrets manager |

---

## Related docs

- [API.md](./API.md) — HTTP request/response shapes
- [Self Study Roadmap N(1).md](./Self%20Study%20Roadmap%20N(1).md) — product milestones
- [.env.example](../.env.example) — environment template
