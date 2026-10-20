"""Run the agents on a Claude Pro or Max plan, through Claude Code on this computer.

Pydantic AI still runs each agent: its tools, checks, retries and limits work as they do with an API key.
SubscriptionModel only replaces the API call. It opens one Claude Code session per agent run, gives it the
agent's instructions and its tools, and passes each tool call Claude makes back to Pydantic AI as the model's
response. Pydantic AI runs the real tool, and its result goes back into the session.

For your own computer only: Anthropic does not allow a claude.ai login to power a service for other people.
Claude Code signs in with CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) if it is set, otherwise with the
login saved by `claude` on this computer.
"""

import asyncio
import os
import shutil
import tempfile
from itertools import count
from typing import Any

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, SdkMcpTool, TextBlock,
                              create_sdk_mcp_server, query, tool)
from mcp.types import ToolAnnotations
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import (ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart,
                                  UserPromptPart)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

PREFIX = "subscription/"  # model IDs that start with this run on the Claude plan, e.g. subscription/claude-opus-5-5
SERVER = "veda"
SIBLING_WAIT = 0.05  # calls Claude makes together reach the tools together; wait this long for the rest of them
STEP_TIMEOUT = 20 * 60  # seconds without any reply from Claude Code before the run is given up
MAX_TURNS = 1000  # Pydantic AI's request limit is what stops a run; this only guards against a runaway session


def options(model: str, system_prompt_file: str, cwd: str, **extra: Any) -> ClaudeAgentOptions:
    """A Claude Code session that sees only what we give it: no built-in tools, no settings, hooks, CLAUDE.md or
    connectors from this computer, and the Claude plan's login rather than any API key in the environment."""
    return ClaudeAgentOptions(
        system_prompt={"type": "file", "path": system_prompt_file}, tools=[], strict_mcp_config=True, setting_sources=[],
        permission_mode="dontAsk", model=model, cwd=cwd, env={"ANTHROPIC_API_KEY": ""}, **extra,
    )


class SubscriptionModel(Model):
    """One agent run's Claude Code session. Use a new one per run and close it afterwards."""

    def __init__(self, model_name: str):
        super().__init__()
        self._model_name = model_name
        self._client: ClaudeSDKClient | None = None
        self._reader: asyncio.Task | None = None
        self._folder: str | None = None
        self._events: asyncio.Queue = asyncio.Queue()
        self._waiting: dict[str, asyncio.Future] = {}  # tool call ID -> Claude Code's call, waiting for Pydantic AI's result
        self._ids = count(1)
        self._tools: dict[str, SdkMcpTool] = {}
        self._text: list[str] = []
        self._usage: dict[str, dict] = {}  # Claude's message ID -> its token counts
        self._reported: set[str] = set()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "anthropic"

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)
        latest = messages[-1]
        assert isinstance(latest, ModelRequest)
        if self._client is None:
            await self._start(latest, model_settings or {}, model_request_parameters)
        else:
            await self._answer(latest)
        return await self._next_response()

    async def close(self) -> None:
        """End the Claude Code session. Pydantic AI stops asking once the agent has its output, so this is always needed."""
        if self._client is not None:
            try:
                await asyncio.wait_for(self._client.disconnect(), 30)
            except Exception:  # the session is being thrown away; a failure to stop it cleanly changes nothing
                pass
        for waiting in self._waiting.values():
            waiting.cancel()
        self._waiting.clear()
        if self._reader is not None:
            self._reader.cancel()
        if self._folder is not None:
            shutil.rmtree(self._folder, ignore_errors=True)

    async def _start(self, first: ModelRequest, settings: ModelSettings, parameters: ModelRequestParameters) -> None:
        self._tools = {d.name: self._tool(d) for d in [*parameters.function_tools, *parameters.output_tools]}
        # An empty folder to run in, so Claude Code finds no project files; the instructions go in a file
        # because they are too long for a command-line argument.
        self._folder = tempfile.mkdtemp(prefix="veda-claude-")
        instructions = os.path.join(self._folder, "instructions.md")
        with open(instructions, "w", encoding="utf-8") as f:
            f.write(first.instructions or "")
        session = options(
            self._model_name, instructions, self._folder, mcp_servers={SERVER: create_sdk_mcp_server(SERVER, tools=list(self._tools.values()))},
            allowed_tools=[f"mcp__{SERVER}__{name}" for name in self._tools], effort=settings.get("anthropic_effort"), max_turns=MAX_TURNS,
        )
        self._client = await self._open(session)
        self._reader = asyncio.create_task(self._read())
        await self._client.query(_text_of(first))

    async def _open(self, session: ClaudeAgentOptions) -> ClaudeSDKClient:
        client = ClaudeSDKClient(session)
        await client.connect()
        return client

    def _tool(self, definition: ToolDefinition) -> SdkMcpTool:
        async def call(args: dict[str, Any]) -> dict[str, Any]:
            call_id = f"call_{next(self._ids)}"
            result = asyncio.get_running_loop().create_future()
            self._waiting[call_id] = result
            self._events.put_nowait(("call", ToolCallPart(definition.name, args, tool_call_id=call_id)))
            text, failed = await result
            return {"content": [{"type": "text", "text": text}], "is_error": failed}

        # Marked read-only so that Claude Code runs calls Claude makes together at the same time; they then come back
        # to Pydantic AI together, as one response, the way they would from the API.
        return tool(definition.name, definition.description or "", definition.parameters_json_schema,
                    annotations=ToolAnnotations(readOnlyHint=True))(call)

    async def _answer(self, latest: ModelRequest) -> None:
        """Hand Pydantic AI's tool results to the calls waiting in Claude Code; anything else is a new message to Claude."""
        follow_up = []
        for part in latest.parts:
            if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id in self._waiting:
                failed = isinstance(part, RetryPromptPart)
                text = part.model_response() if failed else part.model_response_str()
                self._waiting.pop(part.tool_call_id).set_result((text, failed))
            elif isinstance(part, RetryPromptPart):
                follow_up.append(part.model_response())
            elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                follow_up.append(part.content)
        if follow_up:
            await self._client.query("\n\n".join(follow_up))

    async def _next_response(self) -> ModelResponse:
        """Wait for Claude's next tool calls, or for the end of its turn if it answers without one."""
        calls: list[ToolCallPart] = []
        while True:
            try:
                kind, value = await asyncio.wait_for(self._events.get(), SIBLING_WAIT if calls else STEP_TIMEOUT)
            except TimeoutError:
                if calls:
                    break
                raise ModelAPIError(self._model_name, f"Claude Code sent nothing for {STEP_TIMEOUT // 60} minutes.")
            if kind == "error":
                raise value
            if kind == "call":
                calls.append(value)
            elif kind == "end" and not calls:
                return self._response([TextPart("")])
        return self._response(calls)

    def _response(self, parts: list) -> ModelResponse:
        text = "\n\n".join(t for t in self._text if t.strip())
        self._text = []
        if text:
            parts = [TextPart(text), *[p for p in parts if not (isinstance(p, TextPart) and not p.content)]]
        usage = RequestUsage()
        for message_id, counts in self._usage.items():
            if message_id not in self._reported:
                self._reported.add(message_id)
                usage.incr(_usage_of(counts))
        return ModelResponse(parts=parts, usage=usage, model_name=self._model_name)

    async def _read(self) -> None:
        try:
            async for message in self._client.receive_messages():
                if isinstance(message, AssistantMessage):
                    self._text += [block.text for block in message.content if isinstance(block, TextBlock)]
                    if message.message_id and message.usage:
                        self._usage[message.message_id] = message.usage
                elif isinstance(message, ResultMessage):
                    if message.is_error:
                        self._events.put_nowait(("error", ModelAPIError(self._model_name, _failure(message))))
                    else:
                        self._events.put_nowait(("end", None))
        except Exception as error:
            self._events.put_nowait(("error", ModelAPIError(self._model_name, f"Claude Code stopped: {error}")))


def _text_of(request: ModelRequest) -> str:
    return "\n\n".join(p.content for p in request.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))


def _usage_of(counts: dict) -> RequestUsage:
    written = counts.get("cache_creation_input_tokens") or 0
    read = counts.get("cache_read_input_tokens") or 0
    return RequestUsage(input_tokens=(counts.get("input_tokens") or 0) + written + read, cache_write_tokens=written,
                        cache_read_tokens=read, output_tokens=counts.get("output_tokens") or 0)


def _failure(result: ResultMessage) -> str:
    detail = result.result or "; ".join(result.errors or []) or result.subtype
    return f"Claude Code (your Claude plan) stopped: {detail}"


async def check_connection(model: str = "claude-haiku-5-5") -> str:
    """Ask Claude for a one-word reply through Claude Code. Returns the reply; raises RuntimeError with Claude Code's reason."""
    folder = tempfile.mkdtemp(prefix="veda-claude-")
    try:
        prompt = os.path.join(folder, "instructions.md")
        with open(prompt, "w", encoding="utf-8") as f:
            f.write("Reply with the single word OK.")
        reply = ""
        async for message in query(prompt="Are you there?", options=options(model, prompt, folder, effort="low", max_turns=1)):
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(_failure(message))
                reply = message.result or ""
        return reply
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"Claude Code could not start: {type(error).__name__}: {error}") from error
    finally:
        shutil.rmtree(folder, ignore_errors=True)
