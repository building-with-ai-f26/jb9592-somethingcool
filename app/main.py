"""Tailor: local web app. Run with ./app/run.sh, then open http://127.0.0.1:8000"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from jobfetch import FetchError, fetch_job_text
from tailor import TailorError, tailor

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
load_dotenv(APP_DIR.parent / ".env")  # .env lives at the repo root

app = FastAPI(title="Tailor")


class TailorRequest(BaseModel):
    url: str = ""
    job_text: str = ""  # filled in by the paste fallback when fetching fails


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

    base_resume = (DATA_DIR / "base_resume.md").read_text()
    base_cover = (DATA_DIR / "base_cover_letter.md").read_text()
    try:
        result = tailor(job_text, base_resume, base_cover)
    except TailorError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return result
