# Agno Desktop Project Structure

This document describes the major files and folder structure of the Agno Desktop multi-agent research system.

## Project Overview

A two-agent research system built on the Agno framework, featuring:
- **Jarvis**: Intake coordinator (FileTools, handoff, scheduling, Slack delivery)
- **Tony**: Research specialist (web search, NewsFeed with compression, CoinGecko with compression, Python, file I/O, SlackFetch, SlackSearch, scheduling)
- **AgentOS** (port 7777): API server with native cron scheduler — executes scheduled agent runs
- **Slack bot** (Socket Mode): Live conversation dispatch to Jarvis or Tony
- **Qwopus 4B Extractor** (port 8083): Stateless extraction model used to compress raw text (such as articles and APIs) before entering main model context
- Shared knowledge base with project memory
- Persistent per-agent SQLite memory + shared scheduler database (`memory/agno.db`)

---

## Root Directory Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (Slack tokens, API keys, extractor endpoints) |
| `.gitignore` | Ignored patterns (such as databases and local caches) |
| `notes.md` | Local configuration and extractor integration notes |
| `PROJECT_STRUCTURE.md` | This project structure documentation file |

---

## `/server/` — AgentOS & Shared Runtime Config

| File | Purpose |
|------|---------|
| `agent_os.py` | AgentOS instance with `scheduler=True`. Registers Jarvis and Tony via `AgentFactory`, serves on port **7777**. |
| `paths.py` | Canonical directory constants (`memory/`, `output/`, `projects/`) and legacy migration helpers |
| `scheduler_db.py` | `get_scheduler_db()` — shared `SqliteDb` pointing at `memory/agno.db` |
| `agent_payload.py` | Merge `factory_input` into scheduler run payloads |
| `slack_schedule_executor.py` | In-process Slack streaming for scheduled agent runs |
| `scheduler_lifespan.py` | Patched AgentOS scheduler lifespan using `SlackStreamingScheduleExecutor` |

**AgentOS endpoints (examples):**
- `POST /agents/jarvis/runs` — Jarvis run (live or scheduled)
- `POST /agents/tony/runs` — Tony run (live or scheduled)
- `GET/POST /schedules` — Schedule CRUD (also via SchedulerTools)

---

## `/knowledge/` — Knowledge Base

Hierarchical knowledge base organized by department.

| Directory/File | Purpose |
|----------------|---------|
| `index.md` | Knowledge base index with department links |
| `core/` | **Global company guidance** — goals, compliance output rules, brand voice (not department KB) |
| `core/goals.md` | Company goals, purpose, values, always-remember rules |
| `core/compliance.md` | Output compliance rules, restricted words, disclaimers |
| `core/voice.md` | Voice and tone guidelines, brand expression |
| `marketing/` | Marketing strategy, affiliate programs, advertising, CRM |
| `marketing/index.md` | Marketing department index |
| `marketing/affiliates/` | Affiliate outreach, criteria, commission rates |
| `marketing/affiliates/criteria.md` | Partner affiliate screening criteria |
| `marketing/affiliates/outreach.md` | Outreach email templates and pipelines |
| `marketing/affiliates/rates.md` | Affiliate referral commission rates |
| `compliance/` | Department regulatory KB (GDPR, risk management) — distinct from `core/compliance.md` |
| `compliance/index.md` | Compliance department index |
| `compliance/slack-scan-criteria.md` | Data scanning compliance criteria |
| `content/` | Content creation, SEO, article templates |
| `content/index.md` | Content department index |
| `content/seo/templates.md` | SEO templates and keyword research guides |

---

## `/projects/` — Project Registry

Active project tracking and workfiles.

| File | Purpose |
|------|---------|
| `index.md` | Project registry with status, department, keywords, summaries |
| `{project-name}/` | Per-project `handoff.json`, `workfile.md`, assets |

---

## `/agents/` — Two-Agent Architecture

| File | Purpose |
|------|---------|
| `guidance.py` | `load_guidance_files()` — loads `knowledge/core/*.md` into Tony context |
| `task_context.py` | `melbourne_datetime_context()`, `task_context()`, `looks_like_scheduling_request()`, etc. — builds additional context snippets (clock, Slack location, EOD instructions) for agent runs |
| `llm_logger.py` | `LoggedOpenAILike` — audit logging wrapper for agent LLM calls |

### `/agents/jarvis/` — Intake Coordinator

| File | Purpose |
|------|---------|
| `agent.py` | Jarvis agent factory. Model: port 8082 (Qwopus-GLM-18B). Memory: `memory/jarvis_memory.db`. Session: `jarvis-{user_id}`. |
| `prompts.md` | Jarvis system prompt (intake, KB, handoff, scheduling) |

**Jarvis tools:**
- `FileTools` — read-only `knowledge/`, read-only `projects/` (index + listing)
- `handoff_to_tony` — packages brief and writes `projects/{name}/handoff.json`
- `SchedulerTools` — list/manage cron schedules (via `BlockedCreateSchedulerTools`)
- `schedule_reminder_in_minutes` — one-shot reminders

**Jarvis constraints:**
- No web search, news feeds, or Python execution
- Read access: `knowledge/` and `projects/index.md` (not workfiles)
- Write access: None (handoff tool writes project files only)
- History runs: 5

### `/agents/tony/` — Research Specialist

| File | Purpose |
|------|---------|
| `agent.py` | Tony agent factory. Model: port 8081 (Qwopus 35B). Memory: `memory/tony_memory.db`. Session: `tony-{user_id}-{thread_ts}`. |
| `prompts.md` | Tony system prompt (execution checklist, checkpointing rules, and tool calling discipline) |

**Tony tools:**
- `BraveSearchToolkit` — web search via Brave API
- `NewsFeedToolkit` — RSS feeds for crypto/fintech news (uses Qwopus 4B compression)
- `CoinGeckoToolkit` — crypto prices and market data (uses Qwopus 4B compression)
- `SandboxPythonTools` — restricted Python execution sandbox
- `FileTools` — scoped `read_file`, `save_file` on project workfiles
- `SlackFetchToolkit` — fetch chronological history of Slack channels as digests
- `SlackSearchToolkit` — intent-based native Slack search using user tokens
- `upload_deliverable` — uploads deliverables from disk to Slack
- `SchedulerTools` — list/manage cron schedules (via `BlockedCreateSchedulerTools`)

**Tony constraints:**
- Read access: `knowledge/`, `projects/`, `output/`
- Write access: `output/` and project workfiles/assets
- History runs: 3

---

## `/bot/` — Slack Bot & Router

| File | Purpose |
|------|---------|
| `slack_bot.py` | Main Slack bot (Socket Mode). Routes DMs and @mentions to Jarvis or Tony. |
| `agent_runner.py` | Shared streaming runner, file discovery, upload helpers |
| `slack_notify.py` | `post_to_slack_channel(channel, text, thread_ts)` — Slack Web API helper |
| `router.py` | Pre-routing: `keyword_match()`, `get_active_project()`, `route()`. Thread → project mapping in `memory/active_threads.json`. |
| `tool_debug.py` | Formats and summarizes tool results for clean, concise Slack debug posts |
| `debug.py` | Checks if debug logging is enabled (`AGENT_DEBUG` environment variable) |

---

## `/tools/` — Custom Agno Tools

| File | Purpose |
|------|---------|
| `brave_search_tool.py` | **BraveSearchToolkit** — web search via Brave API (Tony) |
| `feed_fetch_tool.py` | **NewsFeedToolkit** — RSS feeds for news, full text compressed/extracted via Qwopus 4B before entering Tony context (Tony) |
| `coingecko_tool.py` | **CoinGeckoToolkit** — crypto market data, routes Pro API and raw gets through Qwopus 4B extractor if needed (Tony) |
| `handoff_tool.py` | `handoff_to_tony` — Jarvis-only project brief handoff |
| `html_generator.py` | `generate_html_report()`, `generate_html_from_markdown()` — HTML report generation with professional styling, saves to `output/reports/` |
| `tony_file_toolkits.py` | Scoped `read_file` / `save_file` / `list_files` / `search_files` with explicit `scope` validation and path-traversal safety |
| `scheduler_tools_config.py` | `BlockedCreateSchedulerTools` — blocks raw `create_schedule`; directs users to reminders and project schedule wrappers |
| `schedule_reminder_tool.py` | One-shot Jarvis reminders (`schedule_reminder_in_minutes`) |
| `extractor_client.py` | Stateless extraction client for Qwopus 4B on port 8083; compresses raw text before it hits main model context |
| `slack_fetch_tools.py` | **SlackFetchToolkit** — fetches channel history and returns compressed plain-text digests |
| `slack_helpers.py` | Shared pure-utility helpers (resolvers, clients, formatters) for Slack tools |
| `slack_search_tools.py` | **SlackSearchToolkit** — intent-based native search queries translated into Slack search modifiers |
| `upload_deliverable_tool.py` | `upload_deliverable` — uploads files from disk to Slack, avoiding massive base64 JSON payload transfers |

### Path Resolution

| Tool Type | Scope/Directory | Path Format | Example |
|-----------|----------------|-------------|---------|
| Scoped File Tools | `projects/` | Relative to `projects/` | `save_file(scope="projects", path="my-project/workfile.md")` |
| Scoped File Tools | `knowledge/` | Relative to `knowledge/` | `read_file(scope="knowledge", path="core/voice.md")` |
| HTML Generator | `output/reports/` | Filename with extension | `generate_html_report(file_name="analysis.html")` |
| SandboxPythonTools | `output/` | Relative to `output/` | `open('reports/data.txt', 'w')` |
| Upload Deliverable | Any scope | Full relative path | `upload_deliverable(scope="output", path="reports/file.html")` |

---

## `/memory/` — Agent Memory & Router State (Gitignored)

| File | Purpose |
|------|---------|
| `jarvis_memory.db` | Jarvis conversation memory |
| `tony_memory.db` | Tony conversation memory |
| `agno.db` | Shared SQLite database for the AgentOS scheduler (run schedules, history) |
| `active_threads.json` | Slack thread → project name (router persistence) |

---

## `/output/` — Generated Deliverables

| Directory/File | Purpose |
|----------------|---------|
| `reports/` | Generated HTML/PDF/CSV research reports |
| `project_server/app.py` | Master FastAPI app for Tony-deployed web apps — port **8090** |
| `project_server/projects/` | Dynamic sub-apps auto-mounted by project server |

**Path rules:** Tools scoped to `output/` use relative paths (`reports/foo.html`, not `output/reports/foo.html`). Project workfiles live in `projects/`, never `output/projects/`.

---

## Slack Streaming & Interactive Debug

Both the Slack bot and AgentOS scheduled runs (when `slack_channel` + `thread_ts` are in the payload) stream assistant text via `chat_postMessage` + `chat_update`. A separate `_(Ns)_` timing message is posted afterward. Tool start/complete events are posted for debugging. The bot auto-uploads new deliverables from `output/reports/`, `output/project_server/`, and `projects/`.

---

## `/scripts/` — Utility Scripts

| File | Purpose |
|------|---------|
| `token_audit.py` | Diagnoses prompt token sizes for Jarvis and Tony configurations |

---

## Key Architecture Decisions

### Two-Agent Design

| Aspect | Jarvis (Intake) | Tony (Research) |
|--------|-----------------|-----------------|
| **Role** | First contact, KB answers, project intake, reminders | Research, writing, analysis, file creation |
| **Model** | Port 8082 (Qwopus-GLM-18B) | Port 8081 (Qwopus 35B) |
| **Tools** | Scoped files (read-only), handoff, `schedule_reminder_in_minutes`, scheduler list/manage | Search, NewsFeed (with Qwopus 4B), CoinGecko (with Qwopus 4B), Python sandbox, Scoped files, SlackFetch, SlackSearch, upload_deliverable |
| **Writes to** | Nothing (handoff tool writes project dirs) | `output/`, `projects/` workfiles |
| **Memory DB** | `memory/jarvis_memory.db` | `memory/tony_memory.db` |
| **Session ID** | `jarvis-{slack_user_id}` | `tony-{slack_user_id}-{thread_ts}` |
| **History Runs** | 5 | 3 |

### Runtime Processes

| Process | Port | Purpose |
|---------|------|---------|
| **AgentOS** | 7777 | Agent API + cron scheduler poller |
| **Slack bot** | — | Socket Mode; calls agents in-process |
| **Tony project server** | 8090 | Serves deployed project apps |
| **Local LLMs** | 8081, 8082, 8083 | Model endpoints for Tony (8081), Jarvis (8082), and Qwopus 4B Extractor (8083) |

Live Slack traffic uses `agent.run()` directly in the bot. Scheduled runs are persisted to `memory/agno.db` and executed by AgentOS calling `/agents/{jarvis|tony}/runs`.

### Scheduler (Agno Native)

- **No APScheduler** — uses Agno `SchedulerTools` + AgentOS `scheduler=True`
- Cron timezone default: `Australia/Melbourne`
- Scheduled payloads for run endpoints require `message`; use `factory_input` JSON for `project`, `slack_channel`, `thread_ts`
- Install extras: `uv pip install "agno[scheduler]"`

### Routing Logic

`bot/router.py`:
1. Active project thread → Tony (`continue`)
2. Keyword match in `projects/index.md` → Jarvis (`project_select`)
3. Default → Jarvis (`casual` / `kb_query`)

### Tool Stack

| Tool | Source | Agent |
|------|--------|-------|
| read_file / save_file / list_files / search_files | `tools/tony_file_toolkits.py` | Both (Tony: save on `projects` only) |
| handoff_to_tony | `tools/handoff_tool.py` | Jarvis |
| schedule_reminder_in_minutes | `tools/schedule_reminder_tool.py` | Jarvis |
| list_schedules, enable/disable, … | `tools/scheduler_tools_config.py` | Both (`create_schedule` blocked) |
| Brave Search | `tools/brave_search_tool.py` | Tony |
| NewsFeed | `tools/feed_fetch_tool.py` | Tony (uses Qwopus 4B extractor) |
| CoinGecko | `tools/coingecko_tool.py` | Tony (uses Qwopus 4B extractor) |
| Python sandbox | `tools/python_sandbox.py` | Tony |
| SlackFetch | `tools/slack_fetch_tools.py` | Tony |
| SlackSearch | `tools/slack_search_tools.py` | Tony |
| Upload Deliverable | `tools/upload_deliverable_tool.py` | Tony |
| HTML Generator | `tools/html_generator.py` | Tony |

### Model Configuration

| Name | Port | Model ID | Agent |
|------|------|----------|-------|
| **qwopus** | 8081 | Qwopus3.6-35B-A3B-v1-Q4_K_M.gguf | Tony (default) |
| **gemma** | 8082 | gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf | Jarvis |
| **qwopus-4b** | 8083 | Qwopus3.5-4B-v3-MTP-Q5_K_M.gguf | Extractor (used by NewsFeed and CoinGecko) |

### Output Format

- **Slack**: Plain text, `*bold*`, `•` bullets — no markdown headers
- **Reports**: HTML with inline CSS in `output/reports/`

---

## Security Features

| Feature | Implementation | File |
|---------|---------------|------|
| Sandbox module blocking | `os`, `socket`, `urllib`, `http`, `ftplib`, etc. blocked | `tools/python_sandbox.py` |
| Execution timeout | 30-second limit via `signal.alarm` | `tools/python_sandbox.py` |
| Path traversal protection | Symlink detection + scope validation | `tools/tony_file_toolkits.py` |
| File size limits | 10MB cap on base64 file operations | `tools/tony_file_toolkits.py` |
| Thread state file locking | `fcntl.flock` for atomic writes | `bot/router.py` |

---

## Environment Variables

Required in root `.env`:

```bash
SLACK_BOT_TOKEN=xoxb-...          # Slack bot OAuth token
SLACK_APP_TOKEN=xapp-...          # Slack app-level token (Socket Mode)
BRAVE_API_KEY=...                 # Brave Search API key
COINGECKO_API_KEY=...             # CoinGecko API key (Tony)
MODEL_EXTRACTOR_URL=http://localhost:8083/v1/chat/completions  # Qwopus 4B Extractor endpoint
MODEL_EXTRACTOR_ID=Qwopus3.5-4B-v3-MTP-Q5_K_M.gguf   # Qwopus 4B model ID matching llama.cpp --alias
```

**Slack bot scopes** (EOD + thread reads): `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`, `chat:write`, `reactions:read`. **Event:** `member_joined_channel` for EOD channel auto-enroll.

Optional:

```bash
SLACK_USER_TOKEN=xoxp-...         # User token for search_slack_messages workspace search (search:read scope)
AGENT_OS_HOST=127.0.0.1           # AgentOS bind host (default 127.0.0.1)
AGENT_OS_PORT=7777                # AgentOS port (default 7777)
AGENT_DEBUG=1                     # Verbose stream/tool debug logs in slack_bot
```

**Projects:** `projects/index.md` is a registry template only until Tony creates projects (Tony appends `## project-name` after each new workfile). Schedules persist in local `memory/agno.db` (not in git). All databases (scheduler + agent memories) live in `memory/` directory.

---

## Usage Patterns

### Start Runtime Processes Individually

```bash
.venv/bin/python server/agent_os.py
.venv/bin/python bot/slack_bot.py
uvicorn output.project_server.app:app --host 0.0.0.0 --port 8090
```

---

## File Count Summary

| Category | Count | Description |
|----------|-------|-------------|
| Core / Config | 3 | `.gitignore`, `notes.md`, `PROJECT_STRUCTURE.md` |
| AgentOS / Server | 6 | `/server/` directory Python files |
| Knowledge Base | 12 | `/knowledge/` Markdown files |
| Agents | 7 | `/agents/` (`guidance.py`, `task_context.py`, `llm_logger.py`, jarvis, tony agent/prompts) |
| Bot | 6 | `/bot/` directory Python files |
| Custom Tools | 15 | `/tools/` directory Python files (Brave, Feed, CoinGecko, Handoff, HTML, etc.) |
| Projects Registry | 1 | `/projects/index.md` |
| Output/Server | 2 | `output/project_server/` app + project subfolders |
| Scripts | 1 | `scripts/token_audit.py` |
| **Total** | **53** | Total tracked project assets |
