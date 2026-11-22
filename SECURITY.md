# Security audit: Tailor (Build 3)

- **Live app:** https://jb9592-somethingcool.onrender.com
- **Lane:** Beginner
- **App type:** **AI app, no database, no sign-in.** Visitors upload or paste their resume, Tailor calls one paid AI model (Claude Sonnet 5.5 via OpenRouter), and the result goes back to the browser. Nothing is stored on the server.
- **Audit date:** Nov 22, 2026, against the live site and commit `6181dc8`.
- **How to rerun:** `scripts/security_check.sh https://jb9592-somethingcool.onrender.com`

## Results at a glance

| Check | Result |
|---|---|
| Secret keys in repo, git history, live page | ✅ None found |
| Rate limit, 30 calls in a row to the AI route | ✅ Calls 5–30 returned **429** |
| Dependency audit (`pip-audit`) | ✅ 121 known vulnerabilities found → upgraded → **0** |
| Agent security review | 6 issues found and fixed, 1 found and left open (see below) |
| File storage | ✅ None. Uploaded PDFs are read in memory and discarded |
| Database / backups | Not applicable: no database |

## 1. Access-control test (AI app, no database)

**Key search.** The script searches for `sb_secret_`, `service_role`, `sk-or-`, `sk-ant-`, `sk-proj-` and `sk_live_`, for the real shapes of those keys, and for our own OpenRouter key (read from `.env`, never printed). It searched every tracked file, the full git history, and the live page source:

```
1a. Repo: lines mentioning a key prefix (expected: placeholders and docs only)
.env.example:5:OPENROUTER_API_KEY=sk-or-v1-replace-with-your-key
app/tailor.py:49:    if not api_key or api_key.startswith("sk-or-v1-replace"):
1b. Repo files + all git history: anything shaped like a REAL key
   real-key-shaped matches: 0 | our own OpenRouter key found: 0 time(s)
1c. Live site: page source checked for key prefixes
   none found
   (PostHog phc_ key present: 1 line(s). That key is public by design.)
```

The two prefix hits are the placeholder in `.env.example` and the code that detects that placeholder. Neither is a key.

**Rate limit, 30 calls in a row** to `POST /api/tailor` on the live site:

```
400 400 400 400 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429 429
-> 26 of 30 calls returned 429 (rate limited)
sample 429 body: {"error":"You've hit Tailor's limit of 5 runs per hour. Try again later.","rate_limited":true}
```

The limit is 5 per visitor per hour. One request had already been used by a real test run a minute earlier, so 4 got through. The 400s are "add your resume first": the test sends no resume on purpose, so it never reaches the paid model and costs $0.

## 2. Secret keys stay on the server

- `OPENROUTER_API_KEY` lives only in `.env` locally (gitignored) and in Render's environment variables. The browser never sees it: every AI call goes through the server.
- **Fixed:** when OpenRouter returned an error, the server passed OpenRouter's raw error text to the visitor. One real 402 error contained a link with an ID for our account's key. Visitors now get a plain message, and the details go to the server log only (`_public_error` in `app/tailor.py`, with a test).
- The PostHog `phc_` key is in the page on purpose. It can only send analytics events.

## 3. File storage

There's none. Uploaded PDFs go to `/api/pdf-text`, are read in memory (max 2 MB, first 10 pages), and only the extracted text goes back to the browser. Nothing is written to disk.

The owner's real resume PDFs in `app/data/` are gitignored, so the live server only has the fake "Alex Placeholder" sample. Verified live: `/api/defaults` returns `source: sample`.

## 4. Protecting production from the agent

- AGENTS.md has an **Ask me first** list covering data deletion, keys and permissions, anything that costs money, pushing to `main` (that deploys), and publishing anything personal.
- There's no database, so there's nothing to back up. If one is added later, the backup step is on the Ask-me-first list.
- Tests can't spend money: `app/tests/conftest.py` hides the API key from every test. That was added after one test accidentally made a real paid call (about $0.05).

## 5. Spending caps and rate limits

| Layer | Limit |
|---|---|
| Per visitor (by IP) | 5 requests/hour to the AI route (`RATE_LIMIT_PER_HOUR`); 30/hour to the PDF reader |
| Whole site | 100 AI runs/day (`DAILY_RUN_CAP`), about $5/day at about $0.05/run |
| Request size | 4 MB per request (checked while the upload streams in), resume 15k characters, cover letter 10k, job text 20k, PDF 2 MB / 10 pages |
| OpenRouter account | Credit limit on the API key: **[confirm: $__ set on openrouter.ai/settings/keys]** |

## 6. Dependency audit

`pip-audit -r app/requirements.txt` found **121 known vulnerabilities in 4 packages**:

| Package | Was | Now | Known vulnerabilities |
|---|---|---|---|
| pypdf | 5.4.0 | 7.1.1 | 103 (important: pypdf now parses PDFs uploaded by strangers) |
| starlette (via fastapi) | 0.46.2 | 1.10.0 | 14 |
| python-dotenv | 1.1.0 | 1.2.4 | 2 |
| pytest | 8.3.5 | 9.2.0 | 2 |

After upgrading: `No known vulnerabilities found`. All 25 tests pass, and a real resume PDF still extracts.

## 7. Agent security review: attacks tried

| # | Attack | Before | Fix | After |
|---|---|---|---|---|
| 1 | **Server-side request forgery (SSRF):** use the job-URL box to make the server fetch `http://169.254.169.254/` (cloud metadata), `localhost`, private IPs, `file://`, or a public page that redirects to a private IP | The server fetched any URL and followed redirects | Only http(s) to public IP addresses, checked again on every redirect; downloads capped at 3 MB | Refused up front. 8 tests cover it |
| 2 | **Huge request body:** POST hundreds of MB to exhaust the 512 MB server | The whole body was read into memory before any size check | Middleware refuses anything over 4 MB as it streams in, even without a Content-Length header | 413. Tested with and without Content-Length |
| 3 | **Cost abuse with huge inputs:** paste a giant "resume" or job description to burn tokens | No limit on pasted text | Length caps (above), checked before any AI call | 413 / truncated. Tested: the AI is never called |
| 4 | **30 calls in a row** to the paid route | No limit | 5/hour per visitor, 100/day site-wide | 429 from call 6 (live result above) |
| 5 | **Rate-limit bypass with a fake `X-Forwarded-For` header,** a different fake address on each request | n/a | **Not fixed:** documented below | ⚠️ **Bypass works.** 10 of 10 spoofed requests passed (live, Nov 22) |
| 6 | **Read the owner's account details from errors** | OpenRouter's raw error text was shown to visitors | Generic messages | Fixed (section 2) |
| 7 | **Script injection through AI output:** a job posting that makes the model output HTML or script | Checked: every model output is escaped or put in text boxes | None needed | Safe |

Also fixed in an earlier build (Nov 15): PostHog session replay recorded the tailored results, which quote the user's resume. The whole results area is now masked in recordings.

## 8. Remaining issues (known, not fixed)

1. **The per-visitor rate limit can be bypassed** by sending a different fake `X-Forwarded-For` on each request (attack 5). Render keeps the header a client sends and adds the real address after it, and Tailor reads the first entry. **Bound:** the 100-runs/day site cap still holds (about $5/day), and so does the OpenRouter key's credit limit. A fix would read the address Render appends instead; we chose to document it for now.
2. **Limits reset when the server restarts.** They're kept in memory, and Render's free tier sleeps after about 15 minutes idle and restarts on the next visit. That resets the hourly counts and the daily count. The OpenRouter credit limit is the backstop that survives restarts.
3. **DNS rebinding:** the SSRF check looks up the address and then connects in a separate step, so a malicious DNS server could answer differently the second time. Low risk here: the server only reads public job pages, and there's no internal network on Render's free tier.
4. **Prompt injection:** a job posting can contain instructions aimed at the model. The model has no tools and the output only goes to the visitor who requested it, so the main risk is a strange result for that visitor.
5. **Third-party data handling:** the visitor's resume is sent to OpenRouter and the model provider to generate the result. The page says so ("Your resume is sent to the AI model… Tailor doesn't save it").
6. **PDF parsing costs server CPU.** Capped at 2 MB, 10 pages and 30 uploads per hour per visitor, but subject to the same header bypass as #1.
7. **No extra security headers** (Content-Security-Policy, etc.). There are no logins or cookies to steal, so the impact is low.
