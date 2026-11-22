"""Tailor: local web app. Run with ./app/run.sh, then open http://127.0.0.1:8000"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from pypdf import PdfReader

from jobfetch import FetchError, fetch_job_text
from ratelimit import RateLimiter
from tailor import TailorError, tailor

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
load_dotenv(APP_DIR.parent / ".env")  # .env lives at the repo root

app = FastAPI(title="Tailor")

# The AI route costs money per call: limit each visitor, and cap the whole site per day.
limiter = RateLimiter(
    per_visitor=int(os.environ.get("RATE_LIMIT_PER_HOUR", "5")),
    window_s=3600,
    global_per_day=int(os.environ.get("DAILY_RUN_CAP", "100")),
)


def client_ip(request: Request) -> str:
    # Behind Render's proxy the visitor's IP is the first X-Forwarded-For entry.
    # A visitor can fake this header; the site-wide daily cap still bounds the spend.
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def too_many(ip: str, message: str) -> JSONResponse:
    return JSONResponse({"error": message, "rate_limited": True}, status_code=429,
                        headers={"Retry-After": str(limiter.retry_after(ip))})


class TailorRequest(BaseModel):
    url: str = ""
    job_text: str = ""  # filled in by the paste fallback when fetching fails


def load_base(kind: str) -> str:
    """Your base resume or cover letter, as text.

    Drop a PDF into app/data/ with "resume" or "cover" in its filename and it's used
    automatically. Otherwise falls back to the markdown file (base_resume.md / base_cover_letter.md).
    """
    pdfs = sorted(p for p in DATA_DIR.glob("*.pdf") if kind in p.name.lower())
    if pdfs:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(pdfs[0]).pages).strip()
        if not text:
            raise FileNotFoundError(f"{pdfs[0].name} has no extractable text (scanned image?). Export it as a text PDF.")
        return text
    md = DATA_DIR / ("base_resume.md" if kind == "resume" else "base_cover_letter.md")
    if not md.exists():
        raise FileNotFoundError(f"No {kind} found. Put a PDF with '{kind}' in its name in app/data/.")
    return md.read_text()


@app.get("/")
def index():
    # Fill in PostHog settings from .env. With no key set, the page skips analytics.
    html = (APP_DIR / "static" / "index.html").read_text()
    html = html.replace("__POSTHOG_KEY__", os.environ.get("POSTHOG_KEY", ""))
    html = html.replace("__POSTHOG_HOST__", os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"))
    return HTMLResponse(html)


@app.post("/api/tailor")
def tailor_endpoint(req: TailorRequest, request: Request):
    ip = client_ip(request)
    if not limiter.allow_visitor(ip):
        return too_many(ip, f"You've hit Tailor's limit of {limiter.per_visitor} runs per hour. Try again later.")
    job_text = req.job_text.strip()[:20000]  # pasted text gets the same cap as fetched pages
    if not job_text:
        try:
            job_text = fetch_job_text(req.url.strip())
        except FetchError as e:
            # needs_paste tells the UI to show the textarea fallback
            return JSONResponse({"error": str(e), "needs_paste": True}, status_code=422)

    try:
        base_resume = load_base("resume")
        base_cover = load_base("cover")
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not limiter.allow_ai_run():
        return too_many(ip, "Tailor has reached its daily limit. Please try again tomorrow.")
    try:
        result = tailor(job_text, base_resume, base_cover)
    except TailorError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return result
