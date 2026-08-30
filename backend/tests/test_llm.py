"""The OpenRouter seam: the schemas built from the agents' own function shapes, and the
loop — tool calls dispatched, results sent back, malformed calls survived. OpenRouter is
never called; `complete` is stood in for."""

import json

import pytest

from app import llm

# --- the shapes the agents actually use --------------------------------------------------


def read_file(path: str) -> str:
    """Read a file from the knowledge base.

    Args:
        path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
    """
    return path


def edit_file(path: str, edits: list[dict[str, str]]) -> str:
    """Change parts of a page.

    Args:
        path: Path relative to the knowledge base root.
        edits: Each entry is {"old": exact text appearing once in the file,
            "new": its replacement}. Applied in order; all must match or none apply.
    """
    return f"{path}: {len(edits)}"


def resolve(answered: str, add: str = "") -> str:
    """Tick a question off.

    Args:
        answered: The exact text of the question line to tick off.
        add: A new question to append, if the conversation raised one. Optional.
    """
    return answered + add


def list_files() -> str:
    """List every file in the knowledge base."""
    return "files"


def test_a_functions_declaration_carries_its_docs_its_types_and_what_is_required():
    d = llm.declare(read_file)["function"]
    assert d["name"] == "read_file" and d["description"].startswith("Read a file")
    assert "Args:" not in d["description"]
    assert d["parameters"]["properties"]["path"]["type"] == "string"
    assert d["parameters"]["properties"]["path"]["description"].startswith("Path relative")
    assert d["parameters"]["required"] == ["path"]

    d = llm.declare(edit_file)["function"]
    assert d["parameters"]["properties"]["edits"]["type"] == "array"
    assert d["parameters"]["properties"]["edits"]["items"]["type"] == "object"
    assert "Applied in order" in d["parameters"]["properties"]["edits"]["description"]
    assert d["parameters"]["required"] == ["path", "edits"]

    d = llm.declare(resolve)["function"]
    assert d["parameters"]["required"] == ["answered"]  # `add` has a default

    d = llm.declare(list_files)["function"]
    assert d["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_the_model_is_an_env_var_per_agent_with_a_shared_fallback(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("INGEST_MODEL", raising=False)
    monkeypatch.delenv("ASSIST_MODEL", raising=False)
    assert llm.model_for("ingest") == llm.DEFAULT_MODEL
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-5")
    assert llm.model_for("ingest") == "openai/gpt-5"
    assert llm.model_for("assist") == "openai/gpt-5"
    monkeypatch.setenv("ASSIST_MODEL", "google/gemini-3-flash")
    assert llm.model_for("assist") == "google/gemini-3-flash"
    assert llm.model_for("ingest") == "openai/gpt-5"


def turn(content=None, calls=None, finish=None):
    message = {"content": content}
    if calls:
        message["tool_calls"] = calls
        finish = finish or "tool_calls"
    return {"choices": [{"message": message, "finish_reason": finish or "stop"}]}


def call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def test_the_loop_runs_tools_sends_results_back_and_survives_a_malformed_call(monkeypatch):
    answers = iter(
        [
            turn(calls=[call("read_file", '{"path": "wiki/a.md"}')]),
            turn(
                calls=[
                    call("read_file", "not json", "c2"),
                    call("read_file", '{"nope": 1}', "c3"),
                    call("missing", "{}", "c4"),
                ]
            ),
            turn(content="done", finish="stop"),
        ]
    )
    bodies = []

    def fake_complete(body):
        bodies.append(body)
        return next(answers)

    monkeypatch.setattr(llm, "complete", fake_complete)
    messages = [{"role": "user", "content": "go"}]
    turns = list(
        llm.loop(
            model="test/model",
            system="be brief",
            messages=messages,
            tools=[read_file],
            max_tokens=100,
        )
    )
    assert [t["finish"] for t in turns] == ["tool_calls", "tool_calls", "stop"]
    assert turns[-1]["text"] == "done"
    # the request carries the system text, the model, the declared tool
    assert bodies[0]["model"] == "test/model"
    assert bodies[0]["messages"][0] == {"role": "system", "content": "be brief"}
    assert bodies[0]["tools"][0]["function"]["name"] == "read_file"
    assert bodies[0]["reasoning"] == {"effort": "medium"}
    # the assistant echo keeps its tool_calls, and every call got a tool result
    echoed = [m for m in messages if m["role"] == "assistant"]
    assert echoed[0]["tool_calls"][0]["id"] == "c1"
    results = {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}
    assert results["c1"] == "wiki/a.md"
    assert "not usable" in results["c2"]
    assert "not usable" in results["c3"]
    assert "No tool called missing" in results["c4"]


def test_a_reply_cut_off_is_yielded_as_length_and_ends_the_loop(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda body: turn(content="half", finish="length"))
    turns = list(llm.loop(model="m", system="s", messages=[], tools=[], max_tokens=10))
    assert len(turns) == 1 and turns[0]["finish"] == "length"


def test_an_error_says_the_sentence_and_spells_the_status(monkeypatch):
    class Got:
        status_code = 402
        content = b"x"

        @staticmethod
        def json():
            return {"error": {"code": 402, "message": "Insufficient credits"}}

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: Got())
    with pytest.raises(llm.LLMError, match=r"Insufficient credits \(HTTP 402\)"):
        llm.complete({"model": "m", "messages": []})
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(llm.LLMError, match="OPENROUTER_API_KEY"):
        llm.complete({"model": "m", "messages": []})


def test_message_parts_for_documents():
    assert llm.text_part("hi") == {"type": "text", "text": "hi"}
    part = llm.file_part("deck.pdf", "data:application/pdf;base64,QUJD")
    assert part["type"] == "file" and part["file"]["filename"] == "deck.pdf"
    assert json.dumps(part)  # serialisable as sent
