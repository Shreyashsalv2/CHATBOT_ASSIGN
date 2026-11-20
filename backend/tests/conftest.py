"""Test fixtures.

The real LiteLLM Router, fallback path and cache are used; only the provider call is
replaced with LiteLLM's built-in `mock_response`, so no network or real keys are needed.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import LLMGateway
from app.main import create_app

MOCK_REPLY = "Mocked reply."
MOCK_DELAY_S = 0.2


def make_client(tmp_path, *, simulation: bool = True, keys: str = "fake-1,fake-2,fake-3") -> TestClient:
    settings = Settings(
        _env_file=None,
        llm_api_keys=keys,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        allowed_origins="http://localhost:3000",
        enable_failure_simulation=simulation,
    )
    gateway = LLMGateway(
        api_keys=settings.api_keys,
        model=settings.llm_model,
        extra_litellm_params={"mock_response": MOCK_REPLY, "mock_delay": MOCK_DELAY_S},
    )
    return TestClient(create_app(settings, gateway))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


@pytest.fixture
def client_no_simulation(tmp_path):
    with make_client(tmp_path, simulation=False) as c:
        yield c


def new_session() -> str:
    return f"s-{uuid.uuid4()}"


def unique(text: str) -> str:
    """A message that has never been sent before (the LiteLLM cache is process-wide)."""
    return f"{text} [{uuid.uuid4()}]"
