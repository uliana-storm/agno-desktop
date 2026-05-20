# Agno Desktop Project Structure

This document describes the major files and folder structure of the Agno Desktop multi-agent research system.

## Project Overview

A two-agent research system built on the Agno framework, featuring:
- **Jarvis**: Intake coordinator (FileTools, handoff, scheduling, Slack delivery)
- **Tony**: Research specialist (web search, news feeds, CoinGecko, Python, file I/O, scheduling)
- **AgentOS** (port 7777): API server with native cron scheduler — executes scheduled agent runs
- **Slack bot** (Socket Mode): Live conversation dispatch to Jarvis or Tony
- Shared knowledge base with project memory
- Persistent per-agent SQLite memory + shared scheduler database (`agno.db`)

---

## Root Directory Files

| File | Purpose |
|------|---------|
| `.env` | Environment variables (Slack tokens, API keys) |
| `agno.db` | Shared SQLite DB for AgentOS scheduler and `SchedulerTools` (schedules, run history) |

**Python extras:** `pip install reportlab` for Tony PDF generation via `FileGenerationTools`.

---

## `/server/` — AgentOS & Shared Runtime Config

| File | Purpose |
|------|---------|
| `agent_os.py` | AgentOS instance with `scheduler=True`. Registers Jarvis and Tony via `AgentFactory`, serves on port **7777**. |
| `paths.py` | Canonical directory constants (`memory/`, `output/`, `projects/`) and legacy migration helpers |
| `scheduler_db.py` | `get_scheduler_db()` — shared `SqliteDb` pointing at root `agno.db` |
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
| `marketing/affiliates/` | Affiliate outreach, criteria, commission rates |
| `compliance/` | Department regulatory KB (GDPR, risk management) — distinct from `core/compliance.md` |
| `compliance/gdpr/` | Data retention policies, consent management |
| `content/` | Content creation, SEO, article templates |
| `content/seo/` | SEO templates, keyword research guides |

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
| `llm_logger.py` | `LoggedOpenAILike` — audit logging wrapper for agent LLM calls |

### `/agents/jarvis/` — Intake Coordinator

| File | Purpose |
|------|---------|
| `agent.py` | Jarvis agent factory. Model: port 8082 (Qwopus-GLM-18B). Memory: `memory/jarvis_memory.db`. Session: `jarvis-{user_id}`. |
| `prompts.md` | Jarvis system prompt (intake, KB, handoff, scheduling) |

**Jarvis tools:**
- `FileTools` — read-only `knowledge/`, read-only `projects/` (index + listing)
- `handoff_to_tony` — packages brief and writes `projects/{name}/handoff.json`
- `post_to_slack` — delivers scheduled-run results to Slack
- `SchedulerTools` — create/list/manage cron schedules → `/agents/jarvis/runs`

**Jarvis constraints:**
- No web search, news feeds, or Python execution
- Read access: `knowledge/` and `projects/index.md` (not workfiles)
- Write access: None (handoff tool writes project files only)
- History runs: 5

### `/agents/tony/` — Research Specialist

| File | Purpose |
|------|---------|
| `agent.py` | Tony agent factory. Model: port 8081 (Qwopus 35B). Memory: `memory/tony_memory.db`. Session: `tony-{user_id}-{thread_ts}`. |
| `prompts.md` | Tony system prompt (execution, workfile, scheduling) |

**Tony tools:**
- `BraveSearchToolkit`, `NewsFeedToolkit`, `CoinGeckoToolkit`
- `PythonTools` (sandbox), `FileTools`, `FileGenerationTools`
- `SlackTools` (Agno) — read channels/threads, send messages, upload files ([docs](https://docs.agno.com/examples/tools/slack-tools))
- `post_eod_report` — Block Kit EOD reports
- `post_to_slack`, `SchedulerTools`

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

---

## `/tools/` — Custom Agno Tools

| File | Purpose |
|------|---------|
| `brave_search_tool.py` | **BraveSearchToolkit** — web search via Brave API (Tony) |
| `feed_fetch_tool.py` | **NewsFeedToolkit** — RSS feeds for crypto/fintech news (Tony) |
| `coingecko_tool.py` | **CoinGeckoToolkit** — crypto prices and market data (Tony) |
| `handoff_tool.py` | `handoff_to_tony` — Jarvis-only project brief handoff |
| `slack_notify_tool.py` | `post_to_slack` — agent-callable wrapper around `bot.slack_notify` |
| `slack_tools_config.py` | Agno `SlackTools` presets for Jarvis (read-only) and Tony (read+post) |
| `slack_blocks_tool.py` | `post_eod_report` — Block Kit EOD digest posting |
| `slack_report_blocks.py` | Block Kit builder for EOD reports |
| `slack_read_tool.py` | `get_messages_since_today`, `search_slack_messages` (complements Agno SlackTools) |
| `workfile_config.py` | EOD project workfile config JSON read/write + channel auto-enroll |
| `tony_file_toolkits.py` | Scoped `read_file` / `save_file` / `list_files` / `search_files` with `scope` arg |
| `create_project_schedule_tool.py` | Deterministic recurring cron + payload for Tony |
| `scheduler_tools_config.py` | `BlockedCreateSchedulerTools` — `create_schedule` disabled on both agents |
| `schedule_reminder_tool.py` | One-shot Jarvis reminders (`schedule_reminder_in_minutes`) |

---

## `/memory/` — Agent Memory & Router State

| File | Purpose |
|------|---------|
| `jarvis_memory.db` | Jarvis conversation memory |
| `tony_memory.db` | Tony conversation memory |
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

## Slack streaming

Both the Slack bot and AgentOS scheduled runs (when `slack_channel` + `thread_ts` are in the payload) stream assistant text via `chat_postMessage` + `chat_update`. A separate `_(Ns)_` timing message is posted afterward. Tool start/complete events are posted for debugging. The bot auto-uploads new deliverables from `output/reports/`, `output/project_server/`, and `projects/`.

---

## `/scripts/` — Utility Scripts

| File | Purpose |
|------|---------|
| `compare_models.py` | Run same task across local models (qwopus, qwopus-glm, gemma, qwen) |
| `weekly_report.py` | Stub for weekly Tony status report to `output/reports/` |
| `token_audit.py` | Diagnose prompt token sizes for Jarvis/Tony configurations |
| `verify_agent_os.py` | Step 0 check — AgentOS health, `/schedules` API, jarvis/tony registration |
| `start_live_test.sh` | Start AgentOS + Slack bot + web server; health checks; `tail -f` combined logs on the terminal |
| `stop_live_test.sh` | Stop processes started by `start_live_test.sh` |

---

## `/tests/` — Testing

| File | Purpose |
|------|---------|
| `run_agent.py` | Single-run agent test with custom prompts |
| `test_coingecko_tool.py` | CoinGecko toolkit tests |

---

## `/agent-ui/` — Web UI (Next.js)

Separate Next.js project (`package.json`, `tsconfig.json`, `components.json`, `.next/` build output).

---

## Key Architecture Decisions

### Two-Agent Design

| Aspect | Jarvis (Intake) | Tony (Research) |
|--------|-----------------|-----------------|
| **Role** | First contact, KB answers, project intake, reminders | Research, writing, analysis, file creation |
| **Model** | Port 8082 (Qwopus-GLM-18B) | Port 8081 (Qwopus 35B) |
| **Tools** | Scoped files (read-only), handoff, `schedule_reminder_in_minutes`, scheduler list/manage, Slack | Search, news, CoinGecko, Python, scoped files (incl. save on `projects`), `create_project_schedule`, scheduler list/manage, Slack |
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
| **Local LLMs** | 8081, 8082 | Model endpoints for Tony and Jarvis |

Live Slack traffic uses `agent.run()` directly in the bot. Scheduled runs are persisted to `agno.db` and executed by AgentOS calling `/agents/{jarvis|tony}/runs`.

### Scheduler (Agno Native)

- **No APScheduler** — uses Agno `SchedulerTools` + AgentOS `scheduler=True`
- Agents call `create_schedule`, `list_schedules`, `delete_schedule`, etc. in natural language
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
| create_project_schedule | `tools/create_project_schedule_tool.py` | Tony |
| post_to_slack | `tools/slack_notify_tool.py` | Both |
| list_schedules, enable/disable, … | `tools/scheduler_tools_config.py` | Both (`create_schedule` blocked) |
| Brave Search | `tools/brave_search_tool.py` | Tony |
| NewsFeed | `tools/feed_fetch_tool.py` | Tony |
| CoinGecko | `tools/coingecko_tool.py` | Tony |
| Python | `agno.tools.python` | Tony |

### Model Configuration

| Name | Port | Model ID | Agent |
|------|------|----------|-------|
| **qwopus** | 8081 | Qwopus3.6-35B-A3B-v1-Q4_K_M.gguf | Tony |
| **qwopus-glm** | 8082 | Qwopus-GLM-18B-Healed-Q5_K_M.gguf | Jarvis |
| **gemma** | 8083 | gemma-4-26B-A4B-it-UD-Q5_K_XL.gguf | — (compare script) |
| **qwen** | 8084 | Qwen3.5-27B-UD-Q4_K_XL.gguf | Jarvis (alt) |

### Output Format

- **Slack**: Plain text, `*bold*`, `•` bullets — no markdown headers
- **Reports**: HTML with inline CSS in `output/reports/`

---

## Environment Variables

Required in root `.env`:

```bash
SLACK_BOT_TOKEN=xoxb-...          # Slack bot OAuth token
SLACK_APP_TOKEN=xapp-...          # Slack app-level token (Socket Mode)
BRAVE_API_KEY=...                 # Brave Search API key
COINGECKO_API_KEY=...             # CoinGecko API key (Tony)
```

**Slack bot scopes** (EOD + thread reads): `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`, `chat:write`, `reactions:read`. **Event:** `member_joined_channel` for EOD channel auto-enroll.

Optional:

```bash
SLACK_USER_TOKEN=xoxp-...            # User token for search_slack_messages workspace search (search:read scope)
AGENT_OS_HOST=127.0.0.1           # AgentOS bind host (default 127.0.0.1)
AGENT_OS_PORT=7777                # AgentOS port (default 7777)
AGENT_DEBUG=1                     # Verbose stream/tool debug logs in slack_bot
```

**Projects:** `projects/index.md` is a registry template only until Tony creates projects (Tony appends `## project-name` after each new workfile). Schedules persist in local `agno.db` (not in git).

---

## Usage Patterns

### First live test (all three processes)

```bash
./scripts/start_live_test.sh    # start + verify + stream logs (Ctrl+C leaves servers running)
./scripts/stop_live_test.sh     # stop when done
```

Or start individually:

```bash
.venv/bin/python server/agent_os.py
.venv/bin/python bot/slack_bot.py
uvicorn output.project_server.app:app --host 0.0.0.0 --port 8090
.venv/bin/python scripts/verify_agent_os.py
```

### Model comparison

```bash
.venv/bin/python scripts/compare_models.py
```

### Quick agent test

```bash
.venv/bin/python tests/run_agent.py --model qwopus
```

---

## File Count Summary

| Category | Count | Description |
|----------|-------|-------------|
| Core | 4 | `goals.md`, `compliance.md`, `voice.md`, `scheduler_db.py` |
| AgentOS | 1 | `server/agent_os.py` |
| Knowledge Base | 9+ | `knowledge/**/*.md` |
| Agent Factories | 4 | `agents/jarvis/`, `agents/tony/` (`agent.py`, `prompts.md`) |
| Bot | 3 | `slack_bot.py`, `slack_notify.py`, `router.py` |
| Custom Tools | 5 | brave, feed, coingecko, handoff, slack_notify |
| Scripts | 5 | compare_models, weekly_report, verify_agent_os, start/stop_live_test |
| Tests | 2 | run_agent, test_coingecko_tool |
| Config | 1 | `.env` |
| Databases | 3 | `agno.db`, jarvis_memory.db, tony_memory.db |
| Servers | 2 | AgentOS :7777, project server :8090 |
| Generated | N | `output/reports/*.html`, dynamic `output/project_server/projects/` |
