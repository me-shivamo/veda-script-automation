"""Which model each agent runs on: through the Claude API, or on a Claude plan through Claude Code (see subscription.py)."""

import os
from dataclasses import dataclass
from functools import lru_cache

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider

from .subscription import PREFIX as SUBSCRIPTION, SubscriptionModel

load_dotenv()
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

DEFAULT_MODEL = "claude-opus-5-5"

# Dollars per million tokens: (input, output). Anthropic's list prices; Haiku's are for prompts up to 100K tokens.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}


@dataclass
class ModelConfig:
    analyst: str = DEFAULT_MODEL
    labeler: str = DEFAULT_MODEL
    reviewer: str = DEFAULT_MODEL
    escalation: str | None = None  # a stronger model for chunks the labeler could not finish
    effort: str = "medium"

    @classmethod
    def all(cls, model_id: str, **kwargs) -> "ModelConfig":
        return cls(analyst=model_id, labeler=model_id, reviewer=model_id, **kwargs)


def make_model(model_id: str) -> Model:
    """The model for one agent run. Pass it to close_model when the run ends."""
    if model_id.startswith(SUBSCRIPTION):
        return SubscriptionModel(model_id.removeprefix(SUBSCRIPTION))
    return api_model(model_id)


async def close_model(model: Model) -> None:
    if isinstance(model, SubscriptionModel):
        await model.close()


@lru_cache(maxsize=None)
def api_model(model_id: str) -> AnthropicModel:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Put it in the .env file at the project root.")
    # Several labelers run at once, so allow more retries than the SDK's two for rate limits and overloads.
    client = AsyncAnthropic(api_key=key, max_retries=5)
    return AnthropicModel(model_id, provider=AnthropicProvider(anthropic_client=client))


def model_settings(effort: str) -> AnthropicModelSettings:
    return AnthropicModelSettings(
        anthropic_effort=effort,
        # Every agent turn resends the whole conversation: cache the instructions and tools, which parallel
        # labelers share, and the growing history, so later turns read it at a tenth of the price.
        anthropic_cache_instructions=True,
        anthropic_cache=True,
    )


def price(model_id: str) -> tuple[float, float] | None:
    """(input, output) dollars per million tokens, or None for a model not in the table or one run on a Claude plan."""
    return PRICES.get(model_id)
