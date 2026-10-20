"""Runs on a Claude plan through Claude Code, with a stand-in for Claude Code, and the page's Claude connection settings."""

import asyncio
import os

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fastapi.testclient import TestClient

import web.app as web
from agent.agents.labeler import ChunkDeps, run_labeler
from agent.agents.models import make_model
from agent.agents.subscription import SubscriptionModel
from agent.core.labels import LabelStore
from agent.core.pieces import make_pieces
from agent.agents.deps import Workspace

from fixtures import mandala_spec
from test_agents_offline import BOOK, rule_based_spans


class FakeClaudeCode:
    """Answers the first message in words only, then labels the chunk, checks it in the same step, and finishes."""

    def __init__(self, model: SubscriptionModel, options):
        self.model, self.options = model, options
        self.prompts: list[str] = []
        self.stream: asyncio.Queue = asyncio.Queue()
        self.turns: list[asyncio.Task] = []
        self.closed = False

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)
        self.turns.append(asyncio.create_task(self.turn()))

    async def receive_messages(self):
        while True:
            yield await self.stream.get()

    async def disconnect(self) -> None:
        self.closed = True
        for turn in self.turns:
            turn.cancel()

    async def turn(self) -> None:
        tools = self.model._tools
        if len(self.prompts) == 1:
            self.stream.put_nowait(AssistantMessage(content=[TextBlock("The chunk looks fine to me.")], model="fake"))
            self.stream.put_nowait(ResultMessage(subtype="success", duration_ms=0, duration_api_ms=0, is_error=False, num_turns=1, session_id="s"))
            return
        spans = rule_based_spans(self.prompts[0])
        await asyncio.gather(tools["label"].handler({"spans": spans}), tools["check"].handler({}))
        await tools["finish"].handler({"summary": "labelled"})


def test_a_labeler_runs_on_the_claude_plan_through_claude_code(monkeypatch):
    sessions = []

    async def fake_open(model, options):
        sessions.append(FakeClaudeCode(model, options))
        return sessions[-1]

    monkeypatch.setattr(SubscriptionModel, "_open", fake_open)
    ws = Workspace(pieces=make_pieces(BOOK), store=LabelStore())
    outcome = asyncio.run(run_labeler(ChunkDeps(ws=ws, spec=mandala_spec(), first_page=1, last_page=3), "subscription/claude-haiku-5-5", "low"))

    assert outcome.ok and outcome.summary == "labelled"
    [claude] = sessions
    assert claude.closed
    # The session sees only the agent's own tools and none of this computer's settings, and never an API key.
    options = claude.options
    assert options.model == "claude-haiku-5-5" and options.effort == "low"
    assert options.tools == [] and options.setting_sources == [] and options.strict_mcp_config
    assert sorted(options.allowed_tools) == sorted(f"mcp__veda__{name}" for name in ("label", "split_piece", "check", "preview", "flag", "finish"))
    assert options.env["ANTHROPIC_API_KEY"] == ""
    # The words-only answer was refused, and the refusal went back to Claude as a new message.
    assert len(claude.prompts) == 2 and "tool" in claude.prompts[1]
    # label and check came back as one step, as parallel calls from the API would.
    assert outcome.requests == 3
    assert ws.usage.cost == 0


def test_model_ids_pick_the_api_or_the_plan(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test")
    assert isinstance(make_model("subscription/claude-opus-5-5"), SubscriptionModel)
    assert make_model("subscription/claude-opus-5-5") is not make_model("subscription/claude-opus-5-5")  # one session per run
    assert make_model("claude-opus-5-5").model_name == "claude-opus-5-5"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """The web app on this computer, with no Claude login, no keys, and a .env file of its own."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude")
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(web, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(web, "LOCAL", True)
    return TestClient(web.app)


def test_the_page_saves_a_plan_token_and_then_defaults_to_the_plan(settings, tmp_path):
    config = settings.get("/api/config").json()
    assert not config["key_configured"] and config["default_model"] == "claude-opus-5-5"
    assert "subscription/claude-opus-5-5" in [m["id"] for m in config["models"]]

    refused = settings.post("/api/connection", json={"plan_token": "sk-ant-api03-abc"})
    assert refused.status_code == 422 and "API key" in refused.json()["detail"]

    saved = settings.post("/api/connection", json={"plan_token": "sk-ant-oat01-abc"}).json()
    assert saved["plan"] == "token"
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abc" in (tmp_path / ".env").read_text()
    config = settings.get("/api/config").json()
    assert config["key_configured"] and config["default_model"] == "subscription/claude-opus-5-5"
    assert config["models"][0]["id"] == config["default_model"]

    assert settings.post("/api/connection", json={"plan_token": ""}).json()["plan"] is None
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in (tmp_path / ".env").read_text()


def test_the_page_saves_an_api_key_and_shows_only_its_end(settings, tmp_path):
    refused = settings.post("/api/connection", json={"api_key": "sk-ant-oat01-abc"})
    assert refused.status_code == 422 and "Claude plan" in refused.json()["detail"]
    saved = settings.post("/api/connection", json={"api_key": "sk-ant-api03-secret-wxyz"}).json()
    assert saved == {"local": True, "plan": None, "api_key": True, "api_key_end": "wxyz"}
    assert "secret" not in settings.get("/api/config").text


def test_a_copy_others_can_reach_never_uses_a_claude_plan(settings, monkeypatch):
    monkeypatch.setattr(web, "LOCAL", False)
    assert settings.post("/api/connection", json={"plan_token": "sk-ant-oat01-abc"}).status_code == 403
    assert settings.post("/api/connection/test", json={"via": "plan"}).status_code == 403
    config = settings.get("/api/config").json()
    assert not any(m["id"].startswith("subscription/") for m in config["models"])
    started = settings.post("/api/runs", json={"input": "x", "request": "x", "model": "subscription/claude-opus-5-5"})
    assert started.status_code == 422
