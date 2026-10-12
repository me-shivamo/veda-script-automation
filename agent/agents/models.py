"""Which model each agent runs on, through OpenRouter."""

import json
import os
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

load_dotenv()
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

DEFAULT_MODEL = "openai/gpt-5.6-luna"


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


@lru_cache(maxsize=None)
def make_model(model_id: str) -> OpenRouterModel:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set. Put it in the .env file at the project root.")
    return OpenRouterModel(model_id, provider=OpenRouterProvider(api_key=key, app_title="veda-script-automation"))


def model_settings(effort: str) -> OpenRouterModelSettings:
    return OpenRouterModelSettings(
        openrouter_usage={"include": True},
        # Only route to providers that support tool calling and the other parameters we send.
        openrouter_provider={"require_parameters": True},
        openrouter_reasoning={"effort": effort},
    )


@lru_cache(maxsize=1)
def _price_table() -> dict[str, tuple[float, float]]:
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as response:
            data = json.load(response)["data"]
        return {m["id"]: (float(m["pricing"]["prompt"]) * 1e6, float(m["pricing"]["completion"]) * 1e6) for m in data}
    except Exception:
        return {}


def price(model_id: str) -> tuple[float, float] | None:
    """(input, output) dollars per million tokens; a fallback for when a response carries no cost."""
    return _price_table().get(model_id)
