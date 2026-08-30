"""The model, through OpenRouter: one API, every model behind it.

Mindkeep's two agents — the ingest and the assistant — used the Anthropic SDK's tool
runner. This is its replacement: OpenRouter speaks the OpenAI chat-completions shape and
routes to whichever model `LLM_MODEL` names (Anthropic's included), so the choice of
model is an environment variable rather than an SDK. What the SDK did is done here, in
the open: tool schemas from the functions themselves, and the loop — send, run the tool
calls, send the results back — until the model stops asking.

The agents keep their own tools, texts and permissions; this module knows none of that.
It knows three things: how to turn a Python function into a tool declaration, how to run
the conversation, and how to say what went wrong in a sentence a person can act on.
"""

import inspect
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
TIMEOUT = 600  # one completion; an agent turn reads pages and thinks
TURNS_MAX = 100  # no conversation runs away


def model_for(role: str) -> str:
    """The model an agent runs on, read when it runs: `INGEST_MODEL` or `ASSIST_MODEL`
    when set, else `LLM_MODEL`, else the default — so the two agents can differ, and a
    redeploy with a new variable is all a change takes."""
    return os.environ.get(f"{role.upper()}_MODEL") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL


class LLMError(RuntimeError):
    """What went wrong, as the sentence to show and to match holds against."""


# --- a Python function as a tool ---------------------------------------------------------


def _arg_docs(fn: Callable[..., Any]) -> dict[str, str]:
    """The `Args:` block of a docstring, as {name: first sentence of its description}."""
    doc = inspect.getdoc(fn) or ""
    block = doc.split("Args:", 1)
    if len(block) < 2:
        return {}
    out: dict[str, str] = {}
    name = ""
    for line in block[1].splitlines():
        m = re.match(r"\s{2,}(\w+):\s*(.*)", line)
        if m:
            name = m.group(1)
            out[name] = m.group(2).strip()
        elif name and line.strip():
            out[name] += " " + line.strip()
    return out


def _type_schema(annotation: Any) -> dict[str, Any]:
    """The JSON schema for the parameter types the agents use. Anything unknown is a
    string — the tools take paths and text, and a wrong guess fails loudly in tests."""
    if annotation in (str, inspect.Parameter.empty):
        return {"type": "string"}
    if annotation in (int, float):
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    text = str(annotation)
    if text.startswith("list[dict["):
        return {"type": "array", "items": {"type": "object", "additionalProperties": True}}
    if text.startswith("list["):
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def declare(fn: Callable[..., Any]) -> dict[str, Any]:
    """The tool declaration for one function: its name, its docstring, and a schema from
    its signature — the description of each argument from the docstring's Args block."""
    doc = inspect.getdoc(fn) or ""
    described = _arg_docs(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(fn).parameters.items():
        schema = _type_schema(param.annotation)
        if name in described:
            schema = {**schema, "description": described[name]}
        properties[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": doc.split("Args:", 1)[0].strip(),
            # always an object, even empty: a missing `parameters` is refused by some models
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# --- the conversation --------------------------------------------------------------------


def complete(body: dict[str, Any]) -> dict[str, Any]:
    """One request to OpenRouter. Raises LLMError with the sentence, and the HTTP status
    spelled out — `(HTTP 402)` — which is what the ingest worker's holds match against."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set (HTTP 401)")
    try:
        got = httpx.post(
            URL,
            json=body,
            headers={
                "Authorization": f"Bearer {key}",
                # who is calling, for OpenRouter's own listings; neither is required
                "HTTP-Referer": "https://mindkeep.io",
                "X-Title": "Mindkeep",
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"could not reach OpenRouter: {e}") from e
    answer: dict[str, Any] = got.json() if got.content else {}
    # OpenRouter reports failures as {error: {code, message}} — sometimes with HTTP 200
    if got.status_code >= 400 or "error" in answer:
        error = answer.get("error") or {}
        code = error.get("code") or got.status_code
        message = error.get("message") or f"OpenRouter answered {got.status_code}"
        raise LLMError(f"{message} (HTTP {code})")
    return answer


def loop(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[Callable[..., Any]],
    max_tokens: int,
    effort: str = "medium",
    on_turn: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run the conversation until the model stops asking for tools.

    Yields one dict per assistant turn: `finish` (the finish_reason — "length" is a reply
    cut off, the caller's to refuse) and `text` (what the model said, "" on a pure tool
    turn). A tool call is dispatched to the function of that name; arguments that do not
    parse, or a tool that raises, come back to the model as an error sentence — the run
    goes on, one bad call must not kill it. Messages are the caller's list, appended to
    in place, so the caller may keep the transcript.
    """
    by_name = {fn.__name__: fn for fn in tools}
    declared = [declare(fn) for fn in tools]
    turns = 0
    while turns < TURNS_MAX:
        turns += 1
        answer = complete(
            {
                "model": model,
                "max_tokens": max_tokens,
                "reasoning": {"effort": effort},
                "messages": [{"role": "system", "content": system}, *messages],
                "tools": declared,
            }
        )
        choice = (answer.get("choices") or [{}])[0]
        reply: dict[str, Any] = choice.get("message") or {}
        finish = str(choice.get("finish_reason") or "")
        # the assistant message goes back verbatim — tool_calls and all, or the next
        # request is refused for tool results with no call to answer
        echo: dict[str, Any] = {"role": "assistant", "content": reply.get("content")}
        if reply.get("tool_calls"):
            echo["tool_calls"] = reply["tool_calls"]
        messages.append(echo)
        turn = {"finish": finish, "text": str(reply.get("content") or "")}
        if on_turn:
            on_turn(turn)
        yield turn
        calls = reply.get("tool_calls") or []
        if finish == "length" or not calls:
            return
        for call in calls:
            fn_name = str(call.get("function", {}).get("name") or "")
            raw = str(call.get("function", {}).get("arguments") or "{}")
            try:
                args = json.loads(raw) if raw.strip() else {}
                if not isinstance(args, dict):
                    raise TypeError("arguments must be an object")
                result = by_name[fn_name](**args)
            except KeyError:
                result = f"No tool called {fn_name}. The tools are: {', '.join(by_name)}."
            except (TypeError, ValueError) as e:
                # the model's call was malformed: tell it, in its own conversation
                result = f"That call to {fn_name} was not usable: {e}. Try again."
            except Exception as e:  # a tool's own bug: the run survives, the log says
                log.exception("tool %s failed", fn_name)
                result = f"{fn_name} failed: {e}"
            messages.append(
                {"role": "tool", "tool_call_id": str(call.get("id") or ""), "content": str(result)}
            )
    raise LLMError(f"the conversation did not finish within {TURNS_MAX} turns")


# --- what rides on a message -------------------------------------------------------------


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def file_part(filename: str, data_url: str) -> dict[str, Any]:
    """A document on the message — a PDF as a data URL. The default model reads files
    natively; a model that cannot needs OpenRouter's file-parser plugin (.env.example)."""
    return {"type": "file", "file": {"filename": filename, "file_data": data_url}}
