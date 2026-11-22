"""Tailor: local web app. Run with ./app/run.sh, then open http://127.0.0.1:8000"""

import base64
import binascii
import io
import os
import re
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
    resume_text: str = ""  # the visitor's resume (pasted, from a PDF, or the sample)
    cover_text: str = ""  # optional


class PdfRequest(BaseModel):
    data_b64: str


# Size limits: keep requests (and AI cost) bounded. A long real resume is ~4-5k chars.
MAX_RESUME_CHARS = 15_000
MAX_COVER_CHARS = 10_000
MAX_PDF_BYTES = 2_000_000
MAX_PDF_PAGES = 10
NO_COVER = "(No cover letter provided. Write a short one using only facts from the base resume.)"

# PDF-to-text is free (no AI) but still work for the server: separate, looser limit.
pdf_limiter = RateLimiter(per_visitor=30, window_s=3600, global_per_day=1_000_000)


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


@app.get("/api/defaults")
def defaults():
    """Starter resume + cover letter. Locally: your own PDFs/markdown in app/data/.
    On the live server only the fake sample exists (PDFs are gitignored, never deployed)."""
    clean = lambda t: re.sub(r"<!--.*?-->\s*", "", t, flags=re.S).strip()
    try:
        return {"source": "saved" if any(DATA_DIR.glob("*.pdf")) else "sample",
                "resume": clean(load_base("resume")), "cover": clean(load_base("cover"))}
    except FileNotFoundError:
        return JSONResponse({"error": "No sample resume on this server."}, status_code=404)


@app.post("/api/pdf-text")
def pdf_text(req: PdfRequest, request: Request):
    """Uploaded PDF -> plain text. Nothing is saved; the text goes back to the browser."""
    ip = client_ip(request)
    if not pdf_limiter.allow_visitor(ip):
        return JSONResponse({"error": "Too many uploads. Try again later.", "rate_limited": True}, status_code=429)
    if len(req.data_b64) > MAX_PDF_BYTES * 4 // 3 + 16:  # check size before decoding
        return JSONResponse({"error": "That PDF is over 2 MB."}, status_code=413)
    try:
        data = base64.b64decode(req.data_b64, validate=True)
    except (binascii.Error, ValueError):
        return JSONResponse({"error": "Couldn't read that file."}, status_code=400)
    if not data.startswith(b"%PDF"):
        return JSONResponse({"error": "That file isn't a PDF."}, status_code=400)
    try:
        pages = PdfReader(io.BytesIO(data)).pages[:MAX_PDF_PAGES]
        text = "\n".join(p.extract_text() or "" for p in pages).strip()
    except Exception:  # malformed or encrypted PDFs; pypdf raises many types
        return JSONResponse({"error": "Couldn't read that PDF. Try exporting it again, or paste the text."}, status_code=400)
    if not text:
        return JSONResponse({"error": "That PDF has no text in it (maybe a scan). Paste the text instead."}, status_code=422)
    return {"text": text}


@app.post("/api/tailor")
def tailor_endpoint(req: TailorRequest, request: Request):
    ip = client_ip(request)
    if not limiter.allow_visitor(ip):
        return too_many(ip, f"You've hit Tailor's limit of {limiter.per_visitor} runs per hour. Try again later.")
    resume, cover = req.resume_text.strip(), req.cover_text.strip()
    if not resume:
        return JSONResponse({"error": "Add your resume first: upload a PDF, paste it, or use the sample."}, status_code=400)
    if len(resume) > MAX_RESUME_CHARS or len(cover) > MAX_COVER_CHARS:
        return JSONResponse({"error": f"That's too long (limit: {MAX_RESUME_CHARS:,} characters for the resume, "
                                      f"{MAX_COVER_CHARS:,} for the cover letter)."}, status_code=413)
    job_text = req.job_text.strip()[:20000]  # pasted text gets the same cap as fetched pages
    if not job_text:
        try:
            job_text = fetch_job_text(req.url.strip())
        except FetchError as e:
            # needs_paste tells the UI to show the textarea fallback
            return JSONResponse({"error": str(e), "needs_paste": True}, status_code=422)

    if not limiter.allow_ai_run():
        return too_many(ip, "Tailor has reached its daily limit. Please try again tomorrow.")
    try:
        result = tailor(job_text, resume, cover or NO_COVER)
    except TailorError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return result
