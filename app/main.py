"""Tailor: local web app. Run with ./app/run.sh, then open http://127.0.0.1:8000"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pypdf import PdfReader

from jobfetch import FetchError, fetch_job_text
from tailor import TailorError, tailor

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
load_dotenv(APP_DIR.parent / ".env")  # .env lives at the repo root

app = FastAPI(title="Tailor")


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
    return FileResponse(APP_DIR / "static" / "index.html")


@app.post("/api/tailor")
def tailor_endpoint(req: TailorRequest):
    job_text = req.job_text.strip()
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
    try:
        result = tailor(job_text, base_resume, base_cover)
    except TailorError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return result
