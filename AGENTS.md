# AGENTS.md — Coding Agent Operating Manual

> Give this file to your coding agent and check that it is using these instructions. Keep the stack, commands, and ground rules current.

## 1. Project Overview
- **What this repo builds:** Tailor — paste a job posting URL, get your existing resume and cover letter tailored to that job. Local web app in `/app`; Product Page in `/docs`.
- **Primary user & core workflow:** Tech MBA students at NYU Stern applying to PM roles. Job URL in → posting fetched and understood → base resume + cover letter rewritten (nothing invented) → review changes → download. Full PRD in `docs/index.html`.
- **Tech stack:** Python 3 / FastAPI / plain HTML+JS page / httpx + BeautifulSoup for fetching / OpenRouter API (default model `anthropic/claude-sonnet-5.5`)

## 2. Project Structure
```
app/                      Tailor web app (runs locally)
  main.py                 FastAPI server: serves the page, POST /api/tailor, loads base resume/cover letter
  jobfetch.py             Fetches a job URL → plain text (JSON-LD JobPosting first, then visible text; skips LinkedIn)
  tailor.py               One OpenRouter call + prompt rules; new_numbers() and word_diff() checks run in code
  static/index.html       The whole UI (plain HTML/JS) + PostHog events
  data/                   Base resume + cover letter. *.pdf with "resume"/"cover" in the name wins over the .md
                          placeholders. PDFs are gitignored (personal info stays off GitHub).
  tests/test_tailor.py    Deterministic tests (no network, no API key)
  requirements.txt        Python dependencies
  run.sh                  One-command launcher (creates app/.venv on first run)
  ratelimit.py            Per-visitor + site-wide daily limits on the paid AI route
docs/                     Public Product Page / living PRD (GitHub Pages serves main → /docs)
  index.html              Pitch, PRD, build log, PostHog snippet
  assets/                 Screenshots
.env                      Local secrets (gitignored). Copy from .env.example.
render.yaml               Render deploy config (see Shipping)
scripts/security_check.sh Key search + 30-call rate-limit test against any URL
SECURITY.md               Build 3 security audit results
```

## 3. Setup & Commands
- **First-time setup:**
  1. `cp .env.example .env`, then set `OPENROUTER_API_KEY` (secret) and `POSTHOG_KEY` (public `phc_` key; leave empty to turn analytics off).
  2. Put your resume and cover letter PDFs in `app/data/` (filenames must contain "resume" and "cover").
- **Install dependencies:** handled by `./app/run.sh` on first run (creates `app/.venv`, installs `app/requirements.txt`)
- **Run locally:** `./app/run.sh` → http://127.0.0.1:8000
- **Run deterministic tests:** `app/.venv/bin/pytest app/tests`
- **Run model evals:** not yet (Build 4)
- **Cost logging:** each run prints `[tailor] run cost: {...}` (tokens + USD) in the server terminal.
- **Testing PostHog with a headless browser (Playwright):** PostHog drops events from automated browsers as bots. In the test, call `posthog.set_config({opt_out_useragent_filter: true})`. Don't put that in app code.

## 4. Non-Negotiable Engineering Rules
1. **Plan before coding:** Before touching multiple files, propose a brief plan and wait for approval.
2. **Small batches:** Make small, focused changes that can be reviewed and explained by a human in a single Pull Request.
3. **Explainability:** Avoid unnecessary abstractions or dependencies. Help the human builder understand the main components, what changed, why it matters, what could break, and how it was checked.
4. **Verify changes:** Run relevant checks. For a reproducible logic bug, use a failing test to demonstrate the fix when practical. Record manual checks and known limitations; follow the selected Build 4 evaluation track.
5. **Zero secrets in code:** Never hardcode API keys, tokens, or credentials. Always read from environment variables (`.env`) and update `.env.example` when new variables are needed.
6. **Stop when looping:** If a fix fails twice in a row, stop, summarize what failed, and ask the human before trying a third approach.

### Rules added after real agent mistakes
7. **Stage files by name, never by folder.** (Nov 15: the agent told the human to run `git add docs` while a screenshot of the human's real resume sat untracked in `docs/assets/`; it would have been published to the public repo.) Use `git add <file> <file>`, then check `git status` for anything personal before committing.
8. **Tests must never reach a paid API.** (Nov 22: a new test hit the real OpenRouter route because `main.py` loads the key from `.env`; it cost ~$0.05.) `app/tests/conftest.py` hides `OPENROUTER_API_KEY` for every test. Keep it that way, and mock anything that would call the model.
9. **Size limits on real data, not placeholders.** (Nov 15: the agent set `max_tokens` to fit the short placeholder resume; the human's real resume overflowed it and broke the app.) Check any length limit against a realistic, full-length input.

## 5. Shipping (deploy to Render)
Tailor runs on Render's free tier from `render.yaml`. Pushing to `main` redeploys automatically.

**One-time setup (human only; needs the Render account):**
1. render.com → sign in with GitHub → **New → Blueprint** → pick this repo → Render reads `render.yaml`.
2. When asked, enter `OPENROUTER_API_KEY` (secret) and `POSTHOG_KEY` (`phc_…`). Never put these in git.
3. **Apply.** The first build takes a few minutes. The URL looks like `https://tailor-xxxx.onrender.com`.

**Every deploy (agent may do steps 1–3; step 4 needs the human's OK):**
1. `app/.venv/bin/pytest app/tests`: all tests must pass.
2. `app/.venv/bin/pip-audit -r app/requirements.txt`: no known vulnerabilities.
3. `scripts/security_check.sh http://127.0.0.1:8000` against a local run: no real keys found; calls 6–30 return 429.
4. Commit by file name (rule 7), then `git push`. Render rebuilds; watch the deploy in the Render dashboard.
5. Verify the live site: open it on a phone, run the core workflow with **Use the sample**, check PostHog Activity shows `tailor_succeeded`, then run `scripts/security_check.sh https://<live-url>`.

**Rollback:** Render dashboard → the service → **Events/Deploys** → pick the last good deploy → **Rollback**. Or `git revert <bad-commit>` and push.

## 6. Ask me first
The agent stops and asks the human before any of these:
- **Deleting or overwriting data:** files outside the repo, user documents (e.g. `app/data/*.pdf`), git history (`reset --hard`, force-push, rebase of pushed commits).
- **Keys, secrets and permissions:** creating, rotating, printing or moving any key; editing `.env`; Render environment variables; repo or org settings; GitHub Pages settings.
- **Anything that costs money:** real AI runs beyond a single verification run, raising `RATE_LIMIT_PER_HOUR` or `DAILY_RUN_CAP`, paid hosting plans, OpenRouter credits or limits.
- **Deploying:** `git push` to `main` (it deploys to production).
- **Publishing anything personal:** screenshots or files containing the human's resume, email or job search.
