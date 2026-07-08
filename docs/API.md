# Teach App — API Contract

This document defines all HTTP APIs between the **React frontend** and the **Django backend**, plus static workspace file serving. It aligns with the product roadmap (M2+) and the current frontend implementation in `src/api/` and `src/types/api.ts`.

---

## Overview

The app uses two kinds of HTTP traffic:

| Kind | Base path | Content-Type | Purpose |
|------|-----------|--------------|---------|
| **JSON API** | `/api/...` | `application/json` | Workspaces, chat turns, lesson lists |
| **Workspace files** | `/workspaces/{id}/...` | `text/html`, `text/css`, `application/javascript`, etc. | Lesson HTML and assets loaded by the iframe |

**Important:** The chat API returns a **URL** to the lesson (`panel.html_url`), not the HTML body. The browser loads the HTML via a separate request to the workspace static route so relative asset paths (`../assets/lesson.css`, `quiz.js`) work correctly.

---

## Conventions

### Base URLs

| Environment | JSON API | Workspace files |
|-------------|----------|-----------------|
| Development | `http://localhost:8000/api` (proxied via Vite as `/api`) | `http://localhost:8000/workspaces/...` |
| Frontend env var | `VITE_API_URL` (default: `/api`) | Same origin `/workspaces/...` |

### Path rules

- All JSON API routes are prefixed with `/api/`.
- Workspace file URLs follow: `/workspaces/{workspace_id}/{relative_path}`.
- `relative_path` is the path inside the workspace directory (e.g. `lessons/0001-hydrostatics.html`).
- Paths must not contain `..` (traversal protection on the backend).

### Workspace directory layout (on disk)

```
workspaces/{workspace_id}/
  MISSION.md
  RESOURCES.md
  NOTES.md
  lessons/
    0001-hydrostatics.html
    0002-pressure-depth.html
  reference/
    series-and-dataframe.html
  learning-records/
    0001-topic-started.md
  assets/
    lesson.css
    quiz.js
```

### URL mapping (file path → public URL)

```
File:  workspaces/abc-uuid/lessons/0001-hydrostatics.html
URL:   /workspaces/abc-uuid/lessons/0001-hydrostatics.html
```

The backend builds this URL when the agent writes a file and returns it in the chat turn response.

---

## Shared types

These match `src/types/api.ts`.

### Message

```json
{
  "role": "user | assistant",
  "type": "text",
  "content": "string"
}
```

### Artifact

```json
{
  "type": "lesson | mission | reference",
  "path": "lessons/0001-hydrostatics.html",
  "url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html",
  "action": "created | updated"
}
```

- `url` is required for `lesson` and `reference` artifacts when they are renderable in the panel.
- `url` may be omitted for hidden files (`MISSION.md`, `learning-records/`, etc.).

### Turn (chat response)

```json
{
  "turn_id": "uuid",
  "messages": [ /* Message[] — assistant messages for this turn */ ],
  "artifacts": [ /* Artifact[] — files created/updated this turn */ ],
  "panel": {
    "html_url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html"
  }
}
```

- `panel.html_url` may be `null` when the turn is chat-only (e.g. mission interview with no lesson yet).
- `messages` contains **assistant** messages only for this turn (the frontend adds the user message locally before the request).

### Workspace

```json
{
  "id": "abc-uuid",
  "title": "Fluid Mechanics",
  "topic_slug": "fluid-mechanics",
  "created_at": "2026-01-15T10:00:00Z"
}
```

---

## M2 APIs (required for frontend integration)

These are the APIs the current React app calls when `VITE_USE_MOCK_API=false`.

---

### 1. List workspaces

Load the workspace/skill switcher on app startup.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/workspaces/` |
| **Auth** | None (M5: required) |
| **Frontend** | `fetchWorkspaces()` in `src/api/workspaces.ts` |

#### Request

No body.

```http
GET /api/workspaces/ HTTP/1.1
Accept: application/json
```

#### Response `200 OK`

```json
[
  {
    "id": "demo",
    "title": "Fluid Mechanics",
    "topic_slug": "fluid-mechanics",
    "created_at": "2026-01-15T10:00:00Z"
  },
  {
    "id": "pandas-demo",
    "title": "Pandas for Hackathons",
    "topic_slug": "pandas-for-hackathons",
    "created_at": "2026-02-01T14:30:00Z"
  }
]
```

#### Backend behaviour

- Returns all workspaces for the current user (M5: filter by `user_id`).
- Ordered by `created_at` descending (recommended).

---

### 2. Create workspace

Triggered by the **New skill** modal.

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/workspaces/` |
| **Auth** | None (M5: required) |
| **Frontend** | `createWorkspace()` in `src/api/workspaces.ts` |

#### Request

```http
POST /api/workspaces/ HTTP/1.1
Content-Type: application/json
```

```json
{
  "title": "Fluid mechanics",
  "topic_slug": "fluid-mechanics"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Display name shown in the UI |
| `topic_slug` | string | yes | Normalized slug for dedup, e.g. `"fluid mechanics"` → `fluid-mechanics` |

#### Response `201 Created`

```json
{
  "id": "abc-uuid",
  "title": "Fluid mechanics",
  "topic_slug": "fluid-mechanics",
  "created_at": "2026-07-06T12:00:00Z"
}
```

#### Backend behaviour

1. Check topic dedup: if `topic_slug` already exists, return existing workspace (`200`) or `409` with existing workspace — team choice.
2. Create workspace directory: `workspaces/{id}/`.
3. Seed `assets/lesson.css` and `assets/quiz.js` templates.
4. Create initial `ChatSession` (M2 data model).
5. Return the new `Workspace`.

#### Error responses

| Status | When |
|--------|------|
| `400` | Missing or invalid `title` / `topic_slug` |
| `409` | Duplicate topic (optional — may return existing instead) |

---

### 3. List lessons

Populate the lesson history sidebar when a workspace is selected.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/workspaces/{workspace_id}/lessons/` |
| **Auth** | None (M5: required) |
| **Frontend** | `fetchWorkspaceLessons()` in `src/api/workspaces.ts` |

#### Request

```http
GET /api/workspaces/abc-uuid/lessons/ HTTP/1.1
Accept: application/json
```

#### Response `200 OK`

```json
[
  {
    "url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html",
    "path": "lessons/0001-hydrostatics.html",
    "title": "Hydrostatics"
  },
  {
    "url": "/workspaces/abc-uuid/lessons/0002-pressure-depth.html",
    "path": "lessons/0002-pressure-depth.html",
    "title": "Pressure and Depth"
  }
]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | yes | Public URL for iframe `src` (frontend maps to string[]) |
| `path` | string | no | Workspace-relative path |
| `title` | string | no | Display label for sidebar (frontend can derive from filename if omitted) |

#### Backend behaviour

- List all `*.html` files under `workspaces/{id}/lessons/`.
- Return sorted by filename (ascending) so `0001` comes before `0002`.

#### Error responses

| Status | When |
|--------|------|
| `404` | Workspace not found |

---

### 4. Send chat message

Main teaching interaction. Runs the agent for one turn.

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/workspaces/{workspace_id}/chat/` |
| **Auth** | None (M5: required) |
| **Frontend** | `sendChatMessage()` in `src/api/chat.ts` |

#### Request

```http
POST /api/workspaces/abc-uuid/chat/ HTTP/1.1
Content-Type: application/json
```

```json
{
  "content": "I want to learn fluid mechanics for my university exams."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | yes | User message text |

Optional M5 fields (not used by current frontend):

```json
{
  "content": "...",
  "session_id": "session-uuid"
}
```

#### Response `200 OK`

**Chat-only turn** (mission interview, no lesson yet):

```json
{
  "turn_id": "turn-uuid-1",
  "messages": [
    {
      "role": "assistant",
      "type": "text",
      "content": "Welcome! Why do you want to learn this topic, and what do you hope to achieve?"
    }
  ],
  "artifacts": [],
  "panel": {
    "html_url": null
  }
}
```

**Turn with new lesson**:

```json
{
  "turn_id": "turn-uuid-2",
  "messages": [
    {
      "role": "assistant",
      "type": "text",
      "content": "I've created your first lesson on hydrostatic pressure. Read it on the right and ask me anything."
    }
  ],
  "artifacts": [
    {
      "type": "lesson",
      "path": "lessons/0001-hydrostatics.html",
      "url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html",
      "action": "created"
    },
    {
      "type": "mission",
      "path": "MISSION.md",
      "action": "updated"
    }
  ],
  "panel": {
    "html_url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html"
  }
}
```

**Follow-up turn** (chat only, keep current lesson visible):

```json
{
  "turn_id": "turn-uuid-3",
  "messages": [
    {
      "role": "assistant",
      "type": "text",
      "content": "Pressure increases with depth because the fluid column above grows heavier. The formula is p = ρgh."
    }
  ],
  "artifacts": [],
  "panel": {
    "html_url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html"
  }
}
```

#### Response field usage (frontend)

| Field | UI effect |
|-------|-----------|
| `messages` | Appended to chat panel (left) |
| `panel.html_url` | Sets iframe `src` on right panel; `null` shows empty state |
| `artifacts` | Available for future UI (badges, notifications); not rendered yet |

#### Backend behaviour

1. Load workspace and active `ChatSession`.
2. Persist user `Message`.
3. Run Claude Agent SDK with `cwd = workspaces/{id}/`.
4. Agent may write files (`lessons/`, `MISSION.md`, etc.).
5. Response mapper detects new/updated files this turn.
6. Build `panel.html_url` from latest lesson written this turn (or keep previous lesson URL).
7. Persist assistant `Message`(s).
8. Return `Turn`.

#### Error responses

| Status | When |
|--------|------|
| `400` | Empty or missing `content` |
| `404` | Workspace not found |
| `504` | Agent timeout |
| `500` | Agent or internal error |

```json
{
  "error": "Agent timed out after 120s",
  "code": "AGENT_TIMEOUT"
}
```

---

### 5. Serve workspace file (static)

Loads lesson HTML and assets in the iframe. **Not a JSON API.**

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/workspaces/{workspace_id}/{file_path}` |
| **Auth** | None (M5: may require auth cookie) |
| **Frontend** | Browser iframe navigation — `LessonViewer` sets `src={htmlUrl}` |

#### Request examples

```http
GET /workspaces/abc-uuid/lessons/0001-hydrostatics.html HTTP/1.1
```

```http
GET /workspaces/abc-uuid/assets/lesson.css HTTP/1.1
```

```http
GET /workspaces/abc-uuid/assets/quiz.js HTTP/1.1
```

#### Response `200 OK`

Returns the raw file content with the appropriate `Content-Type`:

| Extension | Content-Type |
|-----------|--------------|
| `.html` | `text/html; charset=utf-8` |
| `.css` | `text/css; charset=utf-8` |
| `.js` | `application/javascript; charset=utf-8` |
| `.md` | `text/plain; charset=utf-8` |

#### Backend behaviour

1. Resolve `workspaces/{workspace_id}/{file_path}` on disk (or S3 in M4).
2. Reject paths containing `..` or absolute paths.
3. Return file bytes.

#### Error responses

| Status | When |
|--------|------|
| `404` | File or workspace not found |
| `403` | Path traversal attempt |

---

## How file → URL mapping works

The backend does **not** need a separate mapping table for M2. The URL is derived from the workspace ID and the file path the agent writes.

```
Agent writes:     lessons/0001-hydrostatics.html
On disk:          workspaces/abc-uuid/lessons/0001-hydrostatics.html
Public URL:       /workspaces/abc-uuid/lessons/0001-hydrostatics.html
In Turn response: panel.html_url = "/workspaces/abc-uuid/lessons/0001-hydrostatics.html"
```

Detection options for the response mapper:

1. **Tool-use events** (preferred): SDK reports `Write lessons/0001-hydrostatics.html`.
2. **Filesystem diff**: compare `lessons/` listing before and after the agent turn.
3. **DB mirror** (optional): `WorkspaceFile` table stores paths for history/search.

---

## End-to-end flow

```
1. GET  /api/workspaces/                          → populate skill dropdown
2. GET  /api/workspaces/{id}/lessons/             → populate lesson history
3. POST /api/workspaces/{id}/chat/  { content }   → assistant reply + panel.html_url
4. GET  /workspaces/{id}/lessons/0001-....html     → iframe loads lesson HTML
5. GET  /workspaces/{id}/assets/lesson.css        → loaded by HTML <link>
6. GET  /workspaces/{id}/assets/quiz.js           → loaded by HTML <script>
```

Selecting an old lesson from the sidebar changes the iframe `src` locally — **no API call**.

---

## M3 additions (same API shape)

M3 enables the teach skill on the existing agent service. **No breaking changes** to the API contract above.

Additional backend behaviour:

- Agent writes `MISSION.md`, `RESOURCES.md`, `lessons/`, `reference/`, `learning-records/`.
- Response mapper detects more artifact types.
- Optional: **SSE/WebSocket streaming** for partial text while the agent runs (new endpoint, see below).

---

## M3 streaming (planned, optional)

Not implemented in the frontend yet.

| | |
|---|---|
| **Method** | `GET` or `POST` |
| **Path** | `/api/workspaces/{workspace_id}/chat/stream/` |

#### SSE event examples

```
event: message_delta
data: {"content": "Let me explain"}

event: artifact
data: {"type": "lesson", "path": "lessons/0001-hydrostatics.html", "action": "created"}

event: turn_complete
data: {"turn_id": "...", "panel": {"html_url": "/workspaces/abc-uuid/lessons/0001-hydrostatics.html"}}
```

---

## M5 APIs (planned, not in frontend yet)

### List chat sessions

```http
GET /api/workspaces/{workspace_id}/sessions/
```

```json
[
  {
    "id": "session-uuid",
    "workspace_id": "abc-uuid",
    "last_active_at": "2026-07-06T12:00:00Z"
  }
]
```

### Get chat history

```http
GET /api/workspaces/{workspace_id}/sessions/{session_id}/messages/
```

```json
[
  {
    "id": "msg-uuid",
    "role": "user",
    "type": "text",
    "content": "I want to learn fluid mechanics",
    "created_at": "2026-07-06T11:00:00Z"
  },
  {
    "id": "msg-uuid-2",
    "role": "assistant",
    "type": "text",
    "content": "Why do you want to learn this?",
    "created_at": "2026-07-06T11:00:05Z"
  }
]
```

### Create chat session (same topic, new conversation)

```http
POST /api/workspaces/{workspace_id}/sessions/
```

```json
{
  "id": "new-session-uuid",
  "workspace_id": "abc-uuid",
  "last_active_at": "2026-07-06T12:00:00Z"
}
```

---

## Error response format (recommended)

All JSON API errors should use a consistent shape:

```json
{
  "error": "Human-readable message",
  "code": "MACHINE_READABLE_CODE"
}
```

| HTTP Status | Typical `code` |
|-------------|----------------|
| `400` | `VALIDATION_ERROR` |
| `404` | `NOT_FOUND` |
| `409` | `DUPLICATE_TOPIC` |
| `500` | `INTERNAL_ERROR` |
| `504` | `AGENT_TIMEOUT` |

---

## Frontend integration reference

| User action | API called | Frontend file |
|-------------|------------|---------------|
| App loads | `GET /api/workspaces/` | `src/hooks/useWorkspaces.ts` |
| New skill | `POST /api/workspaces/` | `src/hooks/useWorkspaces.ts` |
| Switch workspace | `GET /api/workspaces/{id}/lessons/` | `src/hooks/useLessonPanel.ts` |
| Send message | `POST /api/workspaces/{id}/chat/` | `src/hooks/useChat.ts` |
| Lesson renders | `GET /workspaces/{id}/lessons/...` | `src/components/lessons/LessonViewer.tsx` |
| Pick old lesson | *(none — local state)* | `src/hooks/useLessonPanel.ts` |

### Enable real backend

```env
# .env
VITE_USE_MOCK_API=false
VITE_API_URL=/api
```

```ts
// vite.config.ts (development proxy)
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/workspaces': 'http://localhost:8000',
  },
}
```

---

## M4 S3 note

When storage moves to S3, the **URL shape can stay the same** if Django proxies file requests:

```
GET /workspaces/{id}/lessons/foo.html
  → Django reads from S3
  → returns HTML to browser
```

Alternatively, `panel.html_url` may become a presigned S3 URL. The frontend only needs a valid iframe `src` — the URL format can change as long as the lesson and assets load correctly.

---

## Quick reference

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `GET` | `/api/workspaces/` | — | `Workspace[]` |
| `POST` | `/api/workspaces/` | `{ title, topic_slug }` | `Workspace` |
| `GET` | `/api/workspaces/{id}/lessons/` | — | `{ url, path?, title? }[]` |
| `POST` | `/api/workspaces/{id}/chat/` | `{ content }` | `Turn` |
| `GET` | `/workspaces/{id}/{file_path}` | — | Raw file (HTML/CSS/JS) |
