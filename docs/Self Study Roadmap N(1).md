# **Teach App — Product & Engineering Roadmap**

A web app that replicates the teach skill workflow: an AI teacher that reads/writes a **learning workspace** while the user chats on one side and views HTML lessons on the other.

## **Vision**

| Surface | Role |
| ----- | ----- |
| **Chat (left)** | Conversation with the teacher — clarifying questions, follow-ups, quiz feedback in prose |
| **Lesson pane (right)** | Renders HTML lessons, reference sheets, and linked assets from the workspace |

The agent is not “chat OR HTML.” A single turn can update multiple workspace files and return both chat text and a lesson URL.

## **How the Teach Skill Maps to the App**

| Teach concept | Workspace path | User sees | Agent uses for |
| ----- | ----- | ----- | ----- |
| Mission | MISSION.md | Usually hidden (established via chat) | Grounding every lesson |
| Resources | RESOURCES.md | Hidden | Citations, trusted sources |
| Notes | NOTES.md | Hidden | User preferences |
| Lessons | lessons/000N-\<slug\>.html | **Right panel** | Primary teaching unit |
| Reference | reference/\*.html | Right panel (linked from lessons) | Quick lookup / cheat sheets |
| Learning records | learning-records/000N-\<slug\>.md | Hidden | Zone of proximal development |
| Assets | assets/lesson.css, quiz.js, … | Loaded by lessons | Shared styling & widgets |
| Skill spec | .claude/skills/teach/SKILL.md | Hidden | Skill instructions (loaded by SDK) |

**Rule from teach:** one mission per workspace. A “skill” or “topic” \= one workspace directory.

## **Workspace Directory Layout**

Each learning topic gets an isolated workspace on disk (local through M3, S3 from M4):

workspaces/{workspace\_id}/  
  MISSION.md  
  RESOURCES.md  
  NOTES.md  
  lessons/  
    0001-series-and-dataframe.html  
    0002-load-filter-summarise.html  
  reference/  
    series-and-dataframe.html  
    hackathon-cheatsheet.html  
  learning-records/  
    0001-pandas-learning-started.md  
  assets/  
    lesson.css  
    quiz.js

Lessons link to assets with relative paths (../assets/lesson.css). The frontend must serve the **whole workspace**, not isolated HTML strings, or styles and quizzes break.

## **Milestones**

### **Milestone 1 — React UI**

**Goal:** Split layout — chat on one side, HTML lesson pane on the other.  
**Deliverables:**

* \[ \] Two-panel layout (chat \+ lesson viewer)  
* \[ \] Lesson viewer via iframe or sandboxed HTML renderer  
* \[ \] Asset-aware URLs: /workspaces/{id}/assets/lesson.css, /workspaces/{id}/lessons/0001-….html  
* \[ \] Workspace switcher (list topics/skills)  
* \[ \] Lesson history sidebar (list lessons/\*.html, reopen past lessons)  
* \[ \] “New skill” flow (UI only; backend in M2)  
* \[ \] Loading and empty states (chat-only turns with no lesson yet)

**Notes:**

* First sessions are often mission interviews — no HTML. UI must handle chat-only turns gracefully.  
* Quizzes live **inside lesson HTML** via assets/quiz.js, not in chat by default.

### **Milestone 2 — Django API \+ Claude Agent SDK (platform)**

**Goal:** Backend owns workspaces, chat, and the Claude Agent SDK integration layer. Connect the frontend to the agent API contract. Local filesystem storage. The teach skill is not enabled yet — the SDK runs with a thin app prompt so you can validate the full request/response path end-to-end.

**Deliverables:**

* \[ \] WorkspaceStorage abstraction (local filesystem)  
* \[ \] Workspace CRUD — creating a skill creates a directory \+ seeds assets/  
* \[ \] ChatSession model (separate from workspace)  
* \[ \] POST /workspaces/{id}/chat — accept user message, return turn  
* \[ \] Static file serving for workspace paths  
* \[ \] **Agent service** — Django module that calls Claude Agent SDK query() per turn  
* \[ \] **SDK baseline config** — cwd, setting\_sources, allowed\_tools (teach skill omitted for now)  
* \[ \] **Response mapper** — SDK stream/events → messages \+ artifacts \+ panel.html\_url  
* \[ \] **Fixture mode** (optional) — deterministic responses through the same AgentService code path for frontend-only work  
* \[ \] Topic dedup: same topic → reopen existing workspace, don’t create duplicate

**API response shape (recommended):**

{  
  "turn\_id": "uuid",  
  "messages": \[  
    {  
      "role": "assistant",  
      "type": "text",  
      "content": "Why do you want to learn this?"  
    }  
  \],  
  "artifacts": \[  
    {  
      "type": "lesson",  
      "path": "lessons/0001-hydrostatics.html",  
      "url": "/workspaces/abc/lessons/0001-hydrostatics.html",  
      "action": "created"  
    },  
    {  
      "type": "mission",  
      "path": "MISSION.md",  
      "action": "updated"  
    }  
  \],  
  "panel": {  
    "html\_url": "/workspaces/abc/lessons/0001-hydrostatics.html"  
  }  
}

**Response types (simplified view for frontend):**

| Type | Purpose |
| ----- | ----- |
| in\_chat | Clarifying questions, explanations, quiz discussion |
| html\_response | Lesson or reference HTML ready in the right panel |

Under the hood, prefer messages \+ artifacts \+ optional panel.html\_url over a strict binary.

**M2 agent behaviour (teach skill not yet enabled):**

1. SDK session runs against workspace cwd with platform tools only  
2. Thin app prompt handles basic chat; file writes go through WorkspaceStorage  
3. Response mapper produces the same turn shape the frontend will use in M3  
4. Optional fixture mode returns canned in\_chat / html\_response data through AgentService for UI development

### **Milestone 3 — Teach Skill (Claude Agent SDK \+ Skills)**

**Goal:** Enable the **teach skill** on the Claude Agent SDK agent service built in M2. The teacher conducts mission interviews, writes lessons, and updates learning records. Local filesystem storage only.

#### **Two directories (critical)**

| Directory | Path | Contents |
| ----- | ----- | ----- |
| **Skill package** (static, one per app) | .claude/skills/teach/ | SKILL.md, MISSION-FORMAT.md, LEARNING-RECORD-FORMAT.md, RESOURCES-FORMAT.md, GLOSSARY-FORMAT.md |
| **Workspace** (per topic, agent cwd) | workspaces/{workspace\_id}/ | MISSION.md, lessons/, reference/, learning-records/, assets/ |

#### **Deliverables**

* \[ \] **Teach skill installed** at .claude/skills/teach/ (copy from this repo’s .agents/skills/teach/)  
* \[ \] **Enable teach skill** on the existing AgentService — add skills=\["teach"\] to SDK config from M2  
* \[ \] **SDK configuration (full):**  
  ClaudeAgentOptions(  
      cwd=workspace\_path,                    \# workspaces/{id}/  
      setting\_sources=\["project"\],             \# discover skills from filesystem  
      skills=\["teach"\],                        \# only the teach skill  
      allowed\_tools=\["Skill", "Read", "Write", "Edit", "Glob", "Grep", "Bash"\],  
  )

* \[ \] **Thin app prompt** (optional, \~5 lines) — product wiring only: “You are the teaching backend. User sees chat \+ lesson pane.”   
* \[ \] **Invoke teach** — prefix user messages with /teach or rely on skill description \+ user intent; recommend explicit /teach for reliability  
* \[ \] **Artifact detection** — extend M2 response mapper to detect workspace file writes this turn:  
* messages\[\] — assistant text → in\_chat  
* detect new/updated files under workspace → artifacts\[\]  
* latest lessons/\*.html written this turn → panel.html\_url  
* \[ \] **Workspace seeding** — new workspace gets assets/lesson.css \+ quiz.js templates  
* \[ \] **Streaming** — SSE or WebSocket from Django → React while agent runs (partial text \+ “writing lesson…” state)  
* \[ \] **Long-running** — timeout, cancel, error handling (CursorAgentError vs run failure)  
* \[ \] **Optional: WebSearch MCP** — if teach skill needs external resources for RESOURCES.md (teach: don’t rely on parametric knowledge alone)

#### **Files the agent should produce (enforced by skill, observed by app)**

* MISSION.md, RESOURCES.md, NOTES.md  
* lessons/000N-\<slug\>.html  
* reference/\*.html  
* learning-records/000N-\<slug\>.md

The app watches for filesystem changes (or parses tool-use events) after each turn to populate artifacts and panel.html\_url.

### **Milestone 4 — Cloud Storage (S3)**

**Goal:** Move workspace files to S3. Agent service and teach skill configuration stay the same.

**Deliverables:**

* \[ \] S3WorkspaceStorage implementing same interface as M2  
* \[ \] Config switch: STORAGE\_BACKEND=local|s3  
* \[ \] Presigned URLs or proxy for lesson/asset serving  
* \[ \] Migration path from local workspaces (if any)

**Storage interface (defined in M2, S3 implementation in M4):**

class WorkspaceStorage:  
    def read(self, workspace\_id: str, path: str) \-\> str: ...  
    def write(self, workspace\_id: str, path: str, content: str) \-\> None: ...  
    def list(self, workspace\_id: str, prefix: str) \-\> list\[str\]: ...  
    def exists(self, workspace\_id: str, path: str) \-\> bool: ...

### **Milestone 5 — Multi-user & Multi-session**

**Goal:** Auth, ownership, multiple users and chat sessions.

**Deliverables:**

* \[ \] User model \+ authentication  
* \[ \] Workspace ownership (user\_id on workspaces — add column in M2 even if unused)  
* \[ \] Multiple ChatSessions per workspace (same topic, different conversations over time)  
* \[ \] Session list per user/workspace  
* \[ \] Topic matching: new chat \+ existing topic → open latest session on that workspace  
* \[ \] Authorization checks on all workspace/file routes

**Entity relationships:**

User  
  ⊥── Workspace (one per topic/skill)  
        ⊢── WorkspaceFile (MISSION, lessons, etc.)  
        ⊥── ChatSession (many)  
              ⊥── Message (many)

* **New skill** → new Workspace \+ directory \+ first ChatSession  
* **Same topic, new chat** → new ChatSession on existing Workspace (do not duplicate workspace)  
* **Agent** → reads/writes WorkspaceFile paths; frontend uses panel.html\_url when a lesson is ready

## **Data Model (Django)**

| Model | Key fields |
| ----- | ----- |
| User | id, email, … |
| Workspace | id, user\_id, topic\_slug, title, created\_at |
| ChatSession | id, workspace\_id, last\_active\_at |
| Message | id, session\_id, role, content, created\_at |
| WorkspaceFile | workspace\_id, path, content or s3\_key (optional mirror; or rely on storage only) |

**Topic slug:** normalized from user input, e.g. "fluid mechanics" → fluid-mechanics. Used for dedup when starting a new chat.

## **Frontend Flows**

### **New skill**

1. User clicks “New skill”  
2. Enters topic (or chats “I want to learn X”)  
3. Backend creates Workspace \+ directory \+ ChatSession  
4. Agent interviews for mission (in\_chat only until MISSION.md exists)  
5. First lesson appears in right panel when ready

### **Existing topic, new chat**

1. User starts chat with topic that already has a workspace  
2. Backend finds workspace by topic\_slug  
3. Opens existing workspace \+ latest or new ChatSession  
4. Agent reads existing MISSION.md, learning-records, lesson history

### **Lesson pane**

* Default: latest lesson or empty state  
* User can pick from lesson history  
* iframe sandbox as needed; allow scripts for quiz.js

## **Security & Quality**

| Concern | Mitigation |
| ----- | ----- |
| Agent-generated HTML | Sandboxed iframe; sanitize if serving untrusted content |
| Long generation times | Loading UI, timeouts, cancel button |
| File path traversal | Validate paths stay inside workspace root |
| Missing resources | Agent should populate RESOURCES.md via search for new topics |

## **Suggested Build Order (summary)**

| Milestone | Focus | Critical addition |
| ----- | ----- | ----- |
| M1 | React split UI | Asset-aware iframe, workspace list, lesson history |
| M2 | Django \+ Claude Agent SDK platform | Workspaces, AgentService, response mapper, fixture mode |
| M3 | Teach skill on Agent SDK | Register skill, artifact detection, streaming |
| M4 | S3 | S3WorkspaceStorage backend |
| M5 | Auth \+ multi-user | Ownership, sessions per user |

## **Reference: Existing Teach Workspace**

This repo (learning-french) is a working example of the teach pattern:

* MISSION.md — pandas for hackathons  
* lessons/0001-series-and-dataframe.html, 0002-load-filter-summarise.html  
* reference/, learning-records/, assets/  
* Skill definition: .agents/skills/teach/SKILL.md

Use these files as templates when seeding new workspaces and designing agent output.

