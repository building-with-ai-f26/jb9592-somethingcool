import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def no_paid_api_calls(monkeypatch):
    # main.py loads the real key from .env. Hide it so no test can ever spend money:
    # any code path that reaches OpenRouter fails fast with "OPENROUTER_API_KEY is not set".
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
