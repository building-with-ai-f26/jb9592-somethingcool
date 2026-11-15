"""One OpenRouter call: job text + base resume + base cover letter -> tailored versions.

Also runs a cheap deterministic check afterwards (`new_numbers`) that flags any
number in the tailored resume that wasn't in the base resume, which is a quick
way to catch an invented metric.
"""

import difflib
import json
import os
import re

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
# A full-length real resume needs >3.5k output tokens (Nov 15: hit the old 3,500 cap).
# You pay only for tokens used; the cap just has to fit the longest answer.
MAX_TOKENS = 8000

SYSTEM_PROMPT = """You tailor a candidate's EXISTING resume and cover letter to one specific job posting.

HARD RULES (never break these):
- Never add skills, tools, titles, metrics, numbers, degrees, or employers that are not already in the base resume.
- Never change dates, company names, job titles, or numbers from the base resume.
- You MAY: reorder bullets and sections, rephrase bullets to use the posting's vocabulary where it honestly
  describes the same work, tighten wording, cut less relevant bullets, and choose which existing
  experiences the cover letter emphasizes.
- If the posting asks for something the candidate doesn't have, do NOT fake it. Mention it in `gaps`.
- Keep the candidate's voice. No corporate filler ("synergy", "passionate about leveraging").
- Keep the resume about the same length as the base resume. Keep the markdown structure.

Return ONLY a JSON object with exactly these keys:
{
  "job": {"role": str, "company": str, "must_have_skills": [str], "keywords": [str]},
  "tailored_resume": str (markdown),
  "tailored_cover_letter": str (markdown),
  "changes": [{"what": str, "why": str}]   (3-8 items, short, concrete),
  "gaps": [str]   (must-haves the base resume does not support; may be empty)
}"""


class TailorError(Exception):
    pass


def tailor(job_text: str, base_resume: str, base_cover_letter: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key or api_key.startswith("sk-or-v1-replace"):
        raise TailorError("OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key.")
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

    user_msg = (
        f"<job_posting>\n{job_text}\n</job_posting>\n\n"
        f"<base_resume>\n{base_resume}\n</base_resume>\n\n"
        f"<base_cover_letter>\n{base_cover_letter}\n</base_cover_letter>"
    )
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {"type": "json_object"},
                # Without a cap OpenRouter reserves the model's max output (65k tokens)
                # against your credit balance and can reject the call with HTTP 402.
                "max_tokens": MAX_TOKENS,
                "usage": {"include": True},  # ask OpenRouter to return the $ cost
            },
            timeout=180,
        )
    except httpx.HTTPError as e:
        raise TailorError(f"OpenRouter request failed ({type(e).__name__}).") from e
    if resp.status_code != 200:
        raise TailorError(f"OpenRouter returned HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    usage = body.get("usage", {})
    usage_summary = {
        "model": body.get("model", model),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost_usd": usage.get("cost"),
    }
    print(f"[tailor] run cost: {json.dumps(usage_summary)}", flush=True)

    choice = body["choices"][0]
    _check_finished(choice)
    result = _parse_json(choice["message"]["content"])
    result["usage"] = usage_summary
    result["new_numbers"] = new_numbers(base_resume, result.get("tailored_resume", ""))
    result["resume_diff"] = word_diff(base_resume, result.get("tailored_resume", ""))
    return result


def _check_finished(choice: dict) -> None:
    """A cut-off answer is half a JSON object; say so instead of 'invalid JSON'."""
    if choice.get("finish_reason") == "length":
        raise TailorError(
            f"The answer was cut off at the {MAX_TOKENS}-token limit. "
            "Your resume may be longer than expected; raise MAX_TOKENS in app/tailor.py."
        )


def _parse_json(content: str) -> dict:
    # Some models wrap JSON in ```json fences even when asked not to.
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise TailorError("The model didn't return valid JSON. Try again.") from e
    for key in ("tailored_resume", "tailored_cover_letter", "changes"):
        if key not in data:
            raise TailorError(f"The model's answer was missing '{key}'. Try again.")
    return data


def word_diff(base: str, tailored: str) -> list[list[str]]:
    """Word-level diff of base vs tailored resume, computed in code (not by the model).

    Returns [op, text] pairs where op is "same", "add", or "del", so the UI can
    highlight exactly which words Tailor added or removed.
    """
    a = re.findall(r"\S+|\s+", base)
    b = re.findall(r"\S+|\s+", tailored)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(["same", "".join(b[j1:j2])])
            continue
        if i2 > i1:
            out.append(["del", "".join(a[i1:i2])])
        if j2 > j1:
            out.append(["add", "".join(b[j1:j2])])
    return out


_NUM = re.compile(r"\d[\d,.]*\d|\d")


def new_numbers(base: str, tailored: str) -> list[str]:
    """Numbers that appear in the tailored resume but nowhere in the base resume."""
    norm = lambda s: s.replace(",", "").rstrip(".")
    base_nums = {norm(n) for n in _NUM.findall(base)}
    return sorted({n for n in _NUM.findall(tailored) if norm(n) not in base_nums})
