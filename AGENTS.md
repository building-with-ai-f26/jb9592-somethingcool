# AGENTS.md — Coding Agent Operating Manual

> Give this file to your coding agent and check that it is using these instructions. Keep the stack, commands, and ground rules current.

## 1. Project Overview
- **What this repo builds:** Tailor — paste a job posting URL, get your existing resume and cover letter tailored to that job. Local web app in `/app`; Product Page in `/docs`.
- **Primary user & core workflow:** [See the living PRD in `docs/index.html`]
- **Tech stack:** Python 3 / FastAPI / plain HTML+JS page / httpx + BeautifulSoup for fetching / OpenRouter API (default model `anthropic/claude-sonnet-5.5`)

## 2. Commands
- **Install dependencies:** handled by `./app/run.sh` on first run (creates `app/.venv`, installs `app/requirements.txt`)
- **Run locally:** `cp .env.example .env` (add key), then `./app/run.sh` → http://127.0.0.1:8000
- **Run deterministic tests:** `app/.venv/bin/pytest app/tests`
- **Run model evals:** not yet (Build 4)

## 3. Non-Negotiable Engineering Rules
1. **Plan before coding:** Before touching multiple files, propose a brief plan and wait for approval.
2. **Small batches:** Make small, focused changes that can be reviewed and explained by a human in a single Pull Request.
3. **Explainability:** Avoid unnecessary abstractions or dependencies. Help the human builder understand the main components, what changed, why it matters, what could break, and how it was checked.
4. **Verify changes:** Run relevant checks. For a reproducible logic bug, use a failing test to demonstrate the fix when practical. Record manual checks and known limitations; follow the selected Build 4 evaluation track.
5. **Zero secrets in code:** Never hardcode API keys, tokens, or credentials. Always read from environment variables (`.env`) and update `.env.example` when new variables are needed.
6. **Stop when looping:** If a fix fails twice in a row, stop, summarize what failed, and ask the human before trying a third approach.
