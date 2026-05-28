# Agno Desktop Project Structure

This document describes the major files and folder structure of the Agno Desktop multi-agent research system.

## Project Overview

A two-agent research system built on the Agno framework, featuring:
- **Jarvis**: Intake coordinator (scoped read-only files, handoff, Slack fetch/post, scheduling, reminders)
- **Tony**: Research specialist (web search, NewsFeed, CoinGecko, Python sandbox, scoped file I/O, file generation, Slack fetch/search, project schedules)
- **AgentOS** (port 7777): API server with native cron scheduler — executes scheduled agent runs
- **Slack bot** (Socket Mode): Live conversation dispatch to Jarvis or Tony
- **Qwopus 4B Extractor** (port 8083): Stateless extraction model used to compress raw text (articles, API payloads) before main model context
- Shared knowledge base with project memory
- Persistent per-agent SQLite memory + shared scheduler database (`memory/agno.db`)

---

## Root Directory Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (Slack tokens, API keys, extractor endpoints) |
| `.gitignore` | Ignored patterns (databases, local caches, `memory/`) |
| `PROJECT_STRUCTURE.md` | This project structure documentation file |

---

## `/server/` — AgentOS & Shared Runtime Config

| File | Purpose |
|------|---------|
| `agent_os.py` | AgentOS instance with `scheduler=True`. Registers Jarvis and Tony via `AgentFactory`, serves on port **7777**. |
| `paths.py` | Canonical directory constants (`memory/`, `output/`, `projects/`, `knowledge/`), `PROJECTS_INDEX_PATH`, `similarity_sentinel_path(thread_ts)`, and deliverable scan helpers (excludes `similarity_check_*.json`, `active_threads.json`, `.db`) |
| `scheduler_db.py` | `get_scheduler_db()` — shared `SqliteDb` pointing at `memory/agno.db` |
| `agent_payload.py` | Merge `factory_input` into scheduler run payloads |
| `slack_schedule_executor.py` | In-process Slack streaming for scheduled agent runs |
| `scheduler_lifespan.py` | Patched AgentOS scheduler lifespan using `SlackStreamingScheduleExecutor` |
| `trimmed_sqlite_db.py` | `TrimmedSqliteDb` — truncates tool results to 800 chars before session persist (Jarvis) |
| `selective_compression.py` | `SelectiveCompressionManager` — Tony tool-result compression (token trigger @ 40k, min 2k chars per result) |

**AgentOS endpoints (examples):**
- `POST /agents/jarvis/runs` — Jarvis run (live or scheduled)
- `POST /agents/tony/runs` — Tony run (live or scheduled)
- `GET/POST /schedules` — Schedule CRUD (also via SchedulerTools)

---

## `/knowledge/` — Knowledge Base

Hierarchical knowledge base organized by department. Agents read files reactively via `read_file(scope="knowledge", ...)` — nothing is pre-loaded at factory time.

| Directory/File | Purpose |
|----------------|---------|
| `index.md` | Knowledge base index with department links |
| `core/` | **Global company guidance** — goals, compliance output rules, brand voice (not department KB) |
| `core/goals.md` | Company goals, purpose, values, always-remember rules |
| `core/compliance.md` | Output compliance rules, restricted words, disclaimers |
| `core/voice.md` | Voice and tone guidelines, brand expression |
| `marketing/index.md` | Marketing department index |
| `compliance/index.md` | Compliance department index (distinct from `core/compliance.md`) |
| `compliance/slack-scan-criteria.md` | Data scanning compliance criteria |
| `research/index.md` | Research department index (market research workflow) |
| `research/market_research_workflow/` | Step prompts, coin list, workflow index |

`agents/guidance.py` defines `load_guidance_files()` for **token audit scripts only** — not injected into live agent runs.

---

## `/projects/` — Project Registry

Active project tracking and workfiles.

| File | Purpose |
|------|---------|
| `index.md` | Project registry — Jarvis/Tony read; Tony appends on new projects |
| `{project-name}/handoff.json` | Jarvis handoff brief (written by `handoff_to_tony` after similarity gate passes) |
| `{project-name}/workfile.md` | Tony project state (meta, brief, research-log, draft, history) |
| `{project-name}/assets/` | Optional project assets |

### `projects/index.md` format

One block per project (field names match what `bot/router.py` parses):

```markdown
## {project-name}
keywords: comma, separated, phrases
status: active | stalled | complete
summary: One-line goal
workfile: projects/{project-name}/workfile.md
```

- **Router** uses `keywords` for pre-message `keyword_match` (hyphens normalized to spaces).
- **Handoff tool** uses `similar_projects(name, goal)` to block duplicate names before writing `handoff.json`.
- **Tony** appends new blocks via `append_file` per project index schema in `agents/tony/prompts.md`.

---

## `/agents/` — Two-Agent Architecture

| File | Purpose |
|------|---------|
| `guidance.py` | `load_guidance_files()` — loads `knowledge/core/*.md` for `scripts/token_audit.py` only |
| `task_context.py` | `melbourne_datetime_context()`, `scheduling_datetime_context()`, `slack_location_context()`, `jarvis_slack_instructions()`, `tony_slack_instructions()`, `looks_like_scheduling_request()` |
| `llm_logger.py` | `LoggedOpenAILike` — audit logging wrapper for agent LLM calls |

### `/agents/jarvis/` — Intake Coordinator

| File | Purpose |
|------|---------|
| `agent.py` | Jarvis agent factory. Model: port **8082**. Memory: `memory/jarvis_memory.db` via `TrimmedSqliteDb`. |
| `prompts.md` | Jarvis system prompt (intake, KB, handoff, scheduling, Slack formatting) |

**Jarvis tools:**
- `ScopedFileToolkit` (read-only) — `read_file` / `list_files` / `search_files` on `knowledge/`, `projects/`, `output/`
- `handoff_to_tony` — packages brief; writes `handoff.json` or similarity sentinel (see Handoff flow below). Requires `slack_channel` and `thread_ts` from ## Slack location.
- `schedule_reminder_in_minutes` — one-shot reminders
- `BlockedCreateSchedulerTools` — list/get/enable/disable/delete schedules (`create_schedule` blocked)
- `SlackFetchToolkit` — `fetch_digest` plain-text channel digests
- `post_to_slack_channel` — cross-channel/thread posts (broadcast summaries to e.g. `#dev`)

**Jarvis constraints:**
- No web search, news feeds, or Python execution
- Prompts direct KB reads via `read_file(scope="knowledge", ...)` and project routing via `projects/index.md`
- Before new project names: read `projects/index.md`; on `SIMILAR_PROJECTS` return, bot asks user to choose — do not claim handoff
- Similarity gate: overlapping names blocked until user picks existing project or confirms new (bot-owned disambiguation)
- Write access: none directly (handoff tool writes project dirs)
- `num_history_runs=5`, `tool_call_limit=15`
- Compression: `CompressionManager(compress_tool_results_limit=20)` (count-based)
- Model: `thinking` disabled via `extra_body`
- Melbourne datetime injected manually for scheduled runs and scheduling requests (not `add_datetime_to_context`)

**Jarvis session IDs** (`bot/slack_bot.py`):
- DM: `jarvis-{user_id}`
- Channel thread: `jarvis-{user_id}-{thread_ts}`

### `/agents/tony/` — Research Specialist

| File | Purpose |
|------|---------|
| `agent.py` | Tony agent factory. Model: port **8081**. Memory: `memory/tony_memory.db` via plain `SqliteDb`. |
| `prompts.md` | Tony system prompt (execution checklist, checkpointing, Slack formatting, tool discipline) |

**Tony tools:**
- `ScopedFileToolkit` — read/write on `projects/`; read on `knowledge/` and `output/`
- `BraveSearchToolkit` — web search via Brave API
- `NewsFeedToolkit` — RSS + full-text fetch, compressed via Qwopus 4B extractor
- `CoinGeckoToolkit` — crypto market data (extractor on raw API paths)
- `SandboxPythonTools` — restricted Python in `output/`
- `FileGenerationTools` — generate PDF/CSV/JSON/TXT in `output/reports/`
- `generate_html_report` / `generate_html_from_markdown` — HTML reports in `output/reports/`
- `SlackFetchToolkit` — `fetch_digest` channel digests
- `SlackSearchToolkit` — workspace search via user token
- `upload_deliverable` — upload files from disk to Slack
- `create_project_schedule` — recurring project cron jobs (wrapper around scheduler)
- `BlockedCreateSchedulerTools` — list/manage schedules (`create_schedule` blocked)

**Tony constraints:**
- Read: `knowledge/`, `projects/`, `output/` | Write: `projects/` workfiles/assets, `output/reports/` via generation tools
- `num_history_runs=3`, `max_tool_calls_from_history=1`, `tool_call_limit=50`
- Compression: `SelectiveCompressionManager(compress_token_limit=40_000, min_chars=2_000)` — no count trigger; small tool results skip LLM compression
- `send_media_to_model=False` — llama-server on `:8081` rejects OpenAI `type:file` content blocks
- `add_datetime_to_context=True` (Australia/Melbourne)
- Workfile may be injected at factory time via `workfile_path`
- Registers new projects in `projects/index.md` (project index schema in prompts — `##`, `keywords:`, `status:`, `summary:`, `workfile:`)

**Tony session ID:** `tony-{user_id}-{thread_ts}`

---

## `/bot/` — Slack Bot & Router

| File | Purpose |
|------|---------|
| `slack_bot.py` | Main Slack bot (Socket Mode). Routes DMs and @mentions; pending similarity disambiguation; unified `_dispatch_tony` for handoff and continue. |
| `agent_runner.py` | Shared streaming runner; detects `HANDOFF_READY` / `SIMILAR_PROJECTS`; file discovery and upload helpers |
| `slack_notify.py` | `post_to_slack_channel(channel, text, thread_ts)` — Slack Web API helper |
| `router.py` | Pre-routing: `keyword_match()`, `similar_projects()`, `get_active_project()`, `route()`. Thread → project mapping in `memory/active_threads.json`. |
| `jarvis_ack_phrases.py` | Rotating Jarvis ack messages on new Jarvis turns |
| `tool_debug.py` | Formats tool results for debug output |
| `debug.py` | `agent_debug_enabled()` — checks `AGENT_DEBUG=1` |

**Slack input:** text-only (`event.text`). File attachments are not downloaded or passed to agents.

### Handoff & similarity flow (`slack_bot.py` + `handoff_tool.py`)

1. Jarvis calls `handoff_to_tony` with `thread_ts` / `slack_channel`.
2. Tool runs `similar_projects(proposed_name, goal)` against `projects/index.md`.
3. **No overlap** → write `projects/{name}/handoff.json` → return `HANDOFF_READY:{name}` → bot dispatches Tony via `_dispatch_tony(mode="handoff")`.
4. **Overlap** → write `memory/similarity_check_{thread_ts}.json` (brief + matches) → return `SIMILAR_PROJECTS` → bot posts disambiguation, stores in-memory pending (1h TTL).
5. User replies with existing project name or *new project* → bot writes `handoff.json` and dispatches Tony without re-calling Jarvis.
6. Casual replies while pending are ignored (pending kept); cleared on project close, stop, or TTL expiry.

Pre-message routing (`route()`): keyword hits in user text → Jarvis `project_select` with matched list in context (separate from handoff similarity gate).

---

## `/tools/` — Custom Agno Tools

| File | Purpose |
|------|---------|
| `brave_search_tool.py` | **BraveSearchToolkit** — web search (Tony) |
| `feed_fetch_tool.py` | **NewsFeedToolkit** — RSS + `fetch_and_extract` via Qwopus 4B (Tony) |
| `coingecko_tool.py` | **CoinGeckoToolkit** — crypto data; raw API via extractor when needed (Tony) |
| `create_project_schedule_tool.py` | `create_project_schedule` — Tony recurring schedules |
| `handoff_tool.py` | `handoff_to_tony` — Jarvis handoff with `similar_projects` gate; sentinel on conflict |
| `html_generator.py` | `generate_html_report()`, `generate_html_from_markdown()` |
| `tony_file_toolkits.py` | Scoped file tools with path validation and size caps |
| `scheduler_tools_config.py` | `BlockedCreateSchedulerTools` — blocks raw `create_schedule` |
| `schedule_reminder_tool.py` | `schedule_reminder_in_minutes` — Jarvis one-shot reminders |
| `extractor_client.py` | Qwopus 4B client on port 8083 |
| `slack_fetch_tools.py` | **SlackFetchToolkit** — `fetch_digest` only |
| `slack_post_tool.py` | `post_to_slack_channel` — Jarvis cross-channel post |
| `slack_helpers.py` | Shared Slack clients, channel/user resolvers, formatters |
| `slack_search_tools.py` | **SlackSearchToolkit** — workspace search (user token) |
| `upload_deliverable_tool.py` | `upload_deliverable` — disk → Slack |
| `python_sandbox.py` | **SandboxPythonTools** — restricted Python (Tony) |

### `fetch_digest` (SlackFetchToolkit)

Single tool surface for channel history. Parameters:
- `channels` — comma-separated names or IDs (bot must be invited)
- `hours` — lookback window (default 24); ignored when `date` is set
- `date` — `YYYY-MM-DD` calendar day in Australia/Melbourne
- `format` — `stream` (default) or `blocks`

Thread replies included when `reply_count >= min_reply_count`. User IDs resolved to display names inside the tool.

### Path Resolution

| Tool Type | Scope/Directory | Path Format | Example |
|-----------|----------------|-------------|---------|
| Scoped File Tools | `projects/` | Relative to `projects/` | `save_file(scope="projects", path="my-project/workfile.md")` |
| Scoped File Tools | `knowledge/` | Relative to `knowledge/` | `read_file(scope="knowledge", path="core/voice.md")` |
| FileGenerationTools | `output/reports/` | Filename with extension | `generate_pdf(filename="analysis.pdf")` |
| HTML Generator | `output/reports/` | Filename with extension | `generate_html_report(file_name="analysis.html")` |
| SandboxPythonTools | `output/` | Relative to `output/` | `open('reports/data.txt', 'w')` |
| Upload Deliverable | `projects` or `output` | Relative path within scope | `upload_deliverable(scope="output", path="reports/file.html")` |

---

## `/memory/` — Agent Memory & Router State (Gitignored)

| File | Purpose |
|------|---------|
| `jarvis_memory.db` | Jarvis conversation memory |
| `tony_memory.db` | Tony conversation memory |
| `agno.db` | Shared SQLite database for the AgentOS scheduler |
| `active_threads.json` | Slack thread → project name (router persistence) |
| `similarity_check_{thread_ts}.json` | Ephemeral handoff sentinel during similarity disambiguation (deleted after bot reads it) |

In-memory `_pending_handoffs` in `slack_bot.py` is not persisted to disk.

---

## `/output/` — Generated Deliverables

| Directory/File | Purpose |
|----------------|---------|
| `reports/` | Generated HTML/PDF/CSV/JSON/TXT research reports |
| `project_server/app.py` | Master FastAPI app for Tony-deployed web apps — port **8090** |
| `project_server/projects/` | Dynamic sub-apps auto-mounted by project server |

**Path rules:** Tools scoped to `output/` use relative paths (`reports/foo.html`, not `output/reports/foo.html`). Project workfiles live in `projects/`, never `output/projects/`.

---

## Slack Streaming & Debug

Both the Slack bot and AgentOS scheduled runs (when `slack_channel` + `thread_ts` are in the payload) stream assistant text via `chat_postMessage` + `chat_update`. A separate `_(Ns)_` timing message is posted afterward.

- **Tool-start Slack posts:** only when `AGENT_DEBUG=1` (preview of tool name + args)
- **Auto-upload:** new deliverables under `output/reports/`, `output/project_server/`, and `projects/`

Bot must be **invited** to a channel to read history or post — channel ID alone is not sufficient.

---

## `/scripts/` — Utility Scripts

| File | Purpose |
|------|---------|
| `token_audit.py` | Estimates prompt token sizes for Jarvis and Tony configurations |
| `repro_fetch_digest.py` | Reproduce/debug `fetch_digest` behavior (`AGENT_DEBUG=1`) |
| `delete_bot_messages.py` | Bulk-delete bot messages from a channel by pattern |

---

## Key Architecture Decisions

### Two-Agent Design

| Aspect | Jarvis (Intake) | Tony (Research) |
|--------|-----------------|-----------------|
| **Role** | First contact, KB answers, project intake, reminders, Slack broadcast | Research, writing, analysis, file creation, project schedules |
| **Model** | Port 8082 | Port 8081 (Qwopus3.6-35B-A3B-v1-Q4_K_M.gguf typical) |
| **Memory DB** | `jarvis_memory.db` (`TrimmedSqliteDb`) | `tony_memory.db` (`SqliteDb`) |
| **Session ID** | `jarvis-{user_id}` (DM) or `jarvis-{user_id}-{thread_ts}` | `tony-{user_id}-{thread_ts}` |
| **History runs** | 5 | 3 |
| **Tool compression** | Count @ 20 tool results | Selective @ 40k tokens, min 2k chars/result |
| **Writes to** | Nothing directly (handoff writes project dirs) | `projects/`, `output/reports/` |

### Runtime Processes

| Process | Port | Purpose |
|---------|------|---------|
| **AgentOS** | 7777 | Agent API + cron scheduler poller |
| **Slack bot** | — | Socket Mode; calls agents in-process |
| **Tony project server** | 8090 | Serves deployed project apps |
| **Local LLMs** | 8081, 8082, 8083 | Tony, Jarvis, Qwopus 4B extractor |

Live Slack traffic uses `agent.run()` directly in the bot. Scheduled runs persist to `memory/agno.db` and execute via AgentOS `/agents/{jarvis|tony}/runs`.

### Scheduler (Agno Native)

- **No APScheduler** — Agno `SchedulerTools` + AgentOS `scheduler=True`
- Cron timezone default: `Australia/Melbourne`
- Raw `create_schedule` blocked — use `schedule_reminder_in_minutes` (Jarvis) or `create_project_schedule` (Tony)
- Scheduled payloads require `message`; use `factory_input` JSON for `project`, `slack_channel`, `thread_ts`
- Install extras: `uv pip install "agno[scheduler]"`

### Routing Logic

`bot/router.py` — `route(message_text, thread_ts)` priority:

1. **Project close** (`close project`, etc.) → Jarvis, deregister thread, clear pending
2. **Jarvis escape** (`hey jarvis`, `new topic`, …) → Jarvis, keep thread registered
3. **Active project thread** (`memory/active_threads.json`) → Tony (`continue`)
4. **Keyword match** in `projects/index.md` → Jarvis (`project_select`)
5. **Default** → Jarvis (`casual`)

**Matching helpers:**
- `keyword_match(text)` — normalized substring match on index `keywords:` (e.g. `daily-summary` matches "dev daily summary").
- `similar_projects(proposed_name, goal)` — shared name tokens + goal overlap; used at handoff time, not on every message.

**Tony dispatch** (`slack_bot._dispatch_tony`):
- `mode="handoff"` — after Jarvis handoff or pending resolution; registers thread; uses `build_tony_handoff_prompt`.
- `mode="continue"` — active project thread; user message as prompt; no re-register.

### Tool Stack

| Tool | Source | Agent |
|------|--------|-------|
| read_file / list_files / search_files | `tools/tony_file_toolkits.py` | Both (Tony: save on `projects` only) |
| save_file / append_file / save_file_base64 | `tools/tony_file_toolkits.py` | Tony (`projects` only) |
| handoff_to_tony | `tools/handoff_tool.py` | Jarvis |
| schedule_reminder_in_minutes | `tools/schedule_reminder_tool.py` | Jarvis |
| create_project_schedule | `tools/create_project_schedule_tool.py` | Tony |
| post_to_slack_channel | `tools/slack_post_tool.py` | Jarvis |
| list_schedules, enable/disable, … | `tools/scheduler_tools_config.py` | Both (`create_schedule` blocked) |
| fetch_digest | `tools/slack_fetch_tools.py` | Jarvis + Tony |
| SlackSearch | `tools/slack_search_tools.py` | Tony |
| Brave Search | `tools/brave_search_tool.py` | Tony |
| NewsFeed | `tools/feed_fetch_tool.py` | Tony (Qwopus 4B extractor) |
| CoinGecko | `tools/coingecko_tool.py` | Tony (Qwopus 4B extractor) |
| Python sandbox | `tools/python_sandbox.py` | Tony |
| FileGenerationTools | Agno + `agent.py` wiring | Tony |
| HTML generators | `tools/html_generator.py` | Tony |
| upload_deliverable | `tools/upload_deliverable_tool.py` | Tony |

### Model Configuration

Model GGUF filenames are configured in your local llama.cpp server launch — the codebase only sets OpenAI-compatible `base_url` ports.

| Port | Typical model | Role |
|------|---------------|------|
| **8081** | Qwopus3.6-35B-A3B-v1-Q4_K_M.gguf | Tony |
| **8082** | Local intake model (llama.cpp `--alias local-model`) | Jarvis |
| **8083** | Qwopus3.5-4B-v3-MTP-Q5_K_M.gguf | Extractor (NewsFeed, CoinGecko) |

### Output Format

- **Slack**: `*bold*` (single asterisks), `•` bullets, `<url|text>` links — no `**`, `#` headings, backticks, numbered lists, or markdown tables (see agent `prompts.md`)
- **Reports**: HTML/PDF/CSV/JSON/TXT in `output/reports/`

---

## Security & Context Limits

| Feature | Limit | File |
|---------|-------|------|
| Sandbox module blocking | `os`, `socket`, `urllib`, etc. blocked | `tools/python_sandbox.py` |
| Sandbox timeout | 30s via `signal.alarm` | `tools/python_sandbox.py` |
| Sandbox output cap | 5,000 chars stdout/stderr | `tools/python_sandbox.py` |
| Path traversal protection | Symlink detection + scope validation | `tools/tony_file_toolkits.py` |
| `read_file` cap | 12,000 chars per call | `tools/tony_file_toolkits.py` |
| `save_file` / `append_file` cap | 2,000 chars inline per call | `tools/tony_file_toolkits.py` |
| Base64 save cap | 10 MB decoded | `tools/tony_file_toolkits.py` |
| Jarvis session tool trim | 800 chars on persist | `server/trimmed_sqlite_db.py` |
| Thread state locking | `fcntl.flock` | `bot/router.py` |
| Pending handoff TTL | 1 hour in-memory | `bot/slack_bot.py` |
| Similarity sentinel | Ephemeral JSON under `memory/` | `tools/handoff_tool.py`, `server/paths.py` |

---

## Environment Variables

Required in root `.env`:

```bash
SLACK_BOT_TOKEN=xoxb-...          # Slack bot OAuth token
SLACK_APP_TOKEN=xapp-...          # Slack app-level token (Socket Mode)
BRAVE_API_KEY=...                 # Brave Search API key (Tony)
COINGECKO_API_KEY=...             # CoinGecko API key (Tony)
SLACK_USER_TOKEN=xoxp-...         # User token for SlackSearchToolkit (search:read)
MODEL_EXTRACTOR_URL=http://localhost:8083/v1/chat/completions
MODEL_EXTRACTOR_ID=Qwopus3.5-4B-v3-MTP-Q5_K_M.gguf
```

**Slack bot scopes:** `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`, `chat:write`, `reactions:read`.

Optional:

```bash
AGENT_OS_HOST=127.0.0.1           # AgentOS bind host (default 127.0.0.1)
AGENT_OS_PORT=7777                # AgentOS port (default 7777)
AGENT_DEBUG=1                     # Verbose logs + tool-start posts in Slack
```

**Projects:** `projects/index.md` is the shared registry — Jarvis reads for routing and similarity checks; Tony appends entries when creating projects. Handoff similarity is enforced in `tools/handoff_tool.py`, not by the LLM alone. Schedules persist in `memory/agno.db` (not in git). All databases and router state live under `memory/`.

---

## Usage Patterns

### Start Runtime Processes Individually

```bash
.venv/bin/python server/agent_os.py
.venv/bin/python bot/slack_bot.py
uvicorn output.project_server.app:app --host 0.0.0.0 --port 8090
```

---

## File Count Summary (approximate)

| Category | Count | Description |
|----------|-------|-------------|
| Core / Config | 2 | `.gitignore`, `PROJECT_STRUCTURE.md` |
| Server | 8 | `/server/` Python files |
| Knowledge Base | 10+ | `/knowledge/` Markdown files (incl. research workflow) |
| Agents | 7 | `guidance.py`, `task_context.py`, `llm_logger.py`, jarvis + tony |
| Bot | 7 | `/bot/` Python files |
| Custom Tools | 16 | `/tools/` Python files |
| Scripts | 3 | `/scripts/` utility scripts |
| Projects | 1+ | `projects/index.md` + per-project dirs |
