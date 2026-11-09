"""Deterministic tests (no network, no API key). Run: app/.venv/bin/pytest app/tests"""

import json

import pytest

from jobfetch import FetchError, extract_text, fetch_job_text
from tailor import _parse_json, new_numbers


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
