"""Deterministic tests (no network, no API key). Run: app/.venv/bin/pytest app/tests"""

import json

import pytest

from jobfetch import FetchError, extract_text, fetch_job_text
from tailor import _parse_json, new_numbers, word_diff


def test_json_ld_job_posting_is_preferred_for_js_rendered_pages():
    # Ashby-style page: almost no visible text, full description in JSON-LD.
    ld = {"@type": "JobPosting", "title": "Product Manager", "hiringOrganization": {"name": "Notion"},
          "description": "<p>" + "Own the roadmap for integrations. " * 30 + "</p>"}
    html = f'<html><body><div id="root"></div><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'
    text = extract_text(html)
    assert "Company: Notion" in text
    assert "Own the roadmap" in text


def test_visible_text_strips_scripts_and_nav():
    html = "<html><nav>Menu</nav><script>var x=1;</script><main><h1>PM role</h1><p>Do product.</p></main></html>"
    text = extract_text(html)
    assert "PM role" in text and "var x" not in text and "Menu" not in text


def test_linkedin_goes_straight_to_paste_fallback():
    with pytest.raises(FetchError, match="LinkedIn"):
        fetch_job_text("https://www.linkedin.com/jobs/view/123")


def test_new_numbers_flags_invented_metrics_only():
    base = "Shipped MVP to 200 beta customers; sized a $40M opportunity; cut prep from 6 hours to 1."
    tailored = "Shipped an MVP to 200 customers, sized a $40M market, and grew revenue 35%."
    assert new_numbers(base, tailored) == ["35"]


def test_parse_json_handles_code_fences():
    raw = '```json\n{"tailored_resume": "r", "tailored_cover_letter": "c", "changes": []}\n```'
    assert _parse_json(raw)["tailored_resume"] == "r"


def test_word_diff_catches_added_claim_that_number_check_misses():
    # Real slip from the Nov 8 Figma run: no number changed, but a claim was added.
    base = "- Defined success metrics and built a Looker dashboard; beta users paid invoices 18% faster"
    tailored = "- Defined success metrics and built a Looker dashboard to track adoption; beta users paid invoices 18% faster"
    assert new_numbers(base, tailored) == []  # the old check sees nothing
    added = "".join(t for op, t in word_diff(base, tailored) if op == "add")
    assert "track adoption" in added


def test_word_diff_rebuilds_tailored_text_exactly():
    base = "Worked with 4 engineers.\n\nSkills: SQL, Python"
    tailored = "Partnered with 4 engineers.\n\nSkills: Python, SQL"
    diff = word_diff(base, tailored)
    assert "".join(t for op, t in diff if op != "del") == tailored
    assert "".join(t for op, t in diff if op != "add") == base


def test_index_injects_posthog_key_from_env(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv("POSTHOG_KEY", "phc_test123")
    html = TestClient(main.app).get("/").text
    assert 'const POSTHOG_KEY = "phc_test123"' in html
    assert "__POSTHOG_HOST__" not in html


def test_index_without_posthog_key_leaves_analytics_off(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    monkeypatch.delenv("POSTHOG_KEY", raising=False)
    html = TestClient(main.app).get("/").text
    assert 'const POSTHOG_KEY = ""' in html  # POSTHOG_ON evaluates to false


def test_cut_off_answer_gets_a_clear_error_not_invalid_json():
    # Real bug, Nov 15: a full-length resume hit the 3,500-token cap, the JSON was
    # cut off mid-string, and the user saw a misleading "didn't return valid JSON".
    from tailor import MAX_TOKENS, TailorError, _check_finished

    assert MAX_TOKENS >= 8000
    with pytest.raises(TailorError, match="cut off"):
        _check_finished({"finish_reason": "length", "message": {"content": '{"tailored_resume": "# Jas'}})
    _check_finished({"finish_reason": "stop", "message": {"content": "{}"}})  # no error


# ---------------------------------------------------------------- Build 3 security: SSRF
# On a public server, the job-URL field must not be usable to reach private/internal addresses.
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/",                      # the server itself
    "http://localhost/",                           # resolves to loopback
    "http://169.254.169.254/latest/meta-data/",    # cloud metadata endpoint
    "http://10.0.0.5/admin",                       # private network
    "http://[::1]/",                               # IPv6 loopback
    "file:///etc/passwd",                          # non-web scheme
])
def test_fetch_refuses_private_and_non_web_urls(url):
    # Must be refused up front ("private"/"Only http"), not just fail because nothing answered.
    with pytest.raises(FetchError, match="private|Only http"):
        fetch_job_text(url)


def test_fetch_refuses_redirect_into_private_network(monkeypatch):
    import httpx
    import jobfetch

    monkeypatch.setattr(jobfetch, "_resolve", lambda host: {"93.184.216.34"} if host == "jobs.example.com" else {"10.0.0.1"})
    transport = httpx.MockTransport(lambda req: httpx.Response(302, headers={"location": "http://internal.example/secret"}))
    with pytest.raises(FetchError, match="private"):
        fetch_job_text("https://jobs.example.com/job/1", transport=transport)


def test_upstream_error_details_are_not_shown_to_visitors():
    # OpenRouter's 402 body includes a link with an ID for the account's key. Visitors must not see it.
    from tailor import _public_error

    msg = _public_error(402, '{"error":{"message":"visit https://openrouter.ai/workspaces/default/keys/3a61217cc651"}}')
    assert "openrouter.ai/workspaces" not in msg and "3a61217cc651" not in msg


# ---------------------------------------------------------------- Build 3 security: rate limits
def test_thirty_calls_in_a_row_get_rate_limited(monkeypatch):
    # The assignment's test: call the AI route 30 times; the limit must kick in with 429.
    from fastapi.testclient import TestClient
    import main
    from ratelimit import RateLimiter

    monkeypatch.setattr(main, "limiter", RateLimiter(per_visitor=5, window_s=3600, global_per_day=100))
    client = TestClient(main.app)
    codes = [client.post("/api/tailor", json={"url": ""}).status_code for _ in range(30)]
    assert 429 not in codes[:5]
    assert codes[5:] == [429] * 25
    r = client.post("/api/tailor", json={"url": ""})
    assert r.json()["rate_limited"] is True and int(r.headers["retry-after"]) > 0


def test_visitors_are_limited_separately_and_global_cap_holds():
    from ratelimit import RateLimiter

    rl = RateLimiter(per_visitor=2, window_s=3600, global_per_day=3)
    assert rl.allow_visitor("a") and rl.allow_visitor("a") and not rl.allow_visitor("a")
    assert rl.allow_visitor("b")  # b is unaffected by a
    assert rl.allow_ai_run() and rl.allow_ai_run() and rl.allow_ai_run() and not rl.allow_ai_run()


# ---------------------------------------------------------------- Build 3: visitor brings their own resume
def _client(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from ratelimit import RateLimiter

    monkeypatch.setattr(main, "limiter", RateLimiter(per_visitor=100, window_s=3600, global_per_day=100))
    return TestClient(main.app), main


def test_tailor_requires_a_resume(monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.post("/api/tailor", json={"job_text": "PM role " * 100, "resume_text": "  "})
    assert r.status_code == 400 and "resume" in r.json()["error"].lower()


def test_oversized_resume_is_rejected_before_any_ai_call(monkeypatch):
    client, main = _client(monkeypatch)
    monkeypatch.setattr(main, "tailor", lambda *a: pytest.fail("AI must not be called"))
    r = client.post("/api/tailor", json={"job_text": "PM role " * 100, "resume_text": "x" * 15001})
    assert r.status_code == 413


def test_defaults_on_a_server_without_pdfs_is_the_clean_sample(monkeypatch, tmp_path):
    client, main = _client(monkeypatch)
    (tmp_path / "base_resume.md").write_text("<!-- PLACEHOLDER: replace -->\n# Alex Placeholder\n")
    (tmp_path / "base_cover_letter.md").write_text("<!-- PLACEHOLDER -->\nDear team,\n")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    d = client.get("/api/defaults").json()
    assert d["source"] == "sample" and d["resume"].startswith("# Alex Placeholder") and "PLACEHOLDER" not in d["cover"]


def test_pdf_text_rejects_non_pdfs_and_huge_files(monkeypatch):
    import base64

    client, _ = _client(monkeypatch)
    r = client.post("/api/pdf-text", json={"data_b64": base64.b64encode(b"hello, not a pdf").decode()})
    assert r.status_code == 400
    r = client.post("/api/pdf-text", json={"data_b64": base64.b64encode(b"%PDF" + b"0" * 2_100_000).decode()})
    assert r.status_code == 413


def test_huge_request_bodies_are_refused(monkeypatch):
    # A 512 MB free server must not buffer arbitrarily large uploads.
    client, _ = _client(monkeypatch)
    big = '{"url": "", "resume_text": "' + "x" * 5_000_000 + '"}'
    r = client.post("/api/tailor", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
    # Chunked upload with no Content-Length must be cut off too.
    r = client.post("/api/tailor", content=(b"x" * 1_000_000 for _ in range(5)), headers={"content-type": "application/json"})
    assert r.status_code == 413
