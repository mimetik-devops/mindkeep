"""Notion, and the three ways its sign-in departs from the textbook. Notion is never
called: the token endpoint and the API are stood in for, so what is tested is the dance
as Notion runs it (Basic auth, a JSON body, no scopes, no PKCE), the grant named after the
workspace, and the connector's own logic: pages under the pages above them, a retitled
parent moving its children, a title shared by two pages, a subtree chosen, blocks to
Markdown."""

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app import connections, grants, vault
from app.connectors import ConnectorError, registry
from app.connectors.notion import markdown, page_id
from app.db import Grant, session
from app.files import tenant_id
from tests.test_files import B, T

C = f"{B}/connections"

CLIENTS, ACME, BRIEF, NOTES, NOTES2, OLD = (
    "c1" * 16,
    "ac" * 16,
    "b1" * 16,
    "e1" * 16,
    "e2" * 16,
    "0d" * 16,
)


def dashed(pid: str) -> str:
    """An id as Notion writes it in a response."""
    return f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"


def run(t: str, **marks: Any) -> dict[str, Any]:
    """One run of rich text."""
    href = marks.pop("href", None)
    return {"type": "text", "plain_text": t, "href": href, "annotations": marks}


def rt(t: str) -> list[dict[str, Any]]:
    return [run(t)]


@pytest.fixture
def notion(monkeypatch):
    """A configured Notion app, an exchange that never leaves the process, and a
    workspace small enough to hold in a dict."""
    monkeypatch.setenv("NOTION_CLIENT_ID", "app-id")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "app-secret")
    monkeypatch.setenv("WEB_URL", "http://app.test")
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))
    registry.cache_clear()
    calls: list[dict[str, str]] = []
    space = FakeNotion()

    def exchange(oauth: Any, data: dict[str, str]) -> dict[str, Any]:
        calls.append(data)
        assert "code_verifier" not in data  # Notion knows no PKCE
        code = data["grant_type"] == "authorization_code"
        if code:
            assert data["code"] == "the-code"
        # never an expires_in; a refresh token only when the integration rotates tokens
        return {
            "access_token": "at-1" if code else "at-2",
            "refresh_token": ("rt-1" if code else "rt-2") if space.rotation else None,
        }

    monkeypatch.setattr(grants, "exchange", exchange)
    monkeypatch.setattr("app.connectors.notion.call", space.call)
    yield calls, space
    registry.cache_clear()


class FakeNotion:
    """A workspace: Clients > Acme > Brief; two pages both called Notes at the top; and
    Old, in the trash. Brief has blocks; the rest are empty."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {
            CLIENTS: {"title": "Clients", "parent": "", "edited": "t1"},
            ACME: {"title": "Acme", "parent": CLIENTS, "edited": "t1"},
            BRIEF: {"title": "Brief", "parent": ACME, "edited": "t1"},
            NOTES: {"title": "Notes", "parent": "", "edited": "t1"},
            NOTES2: {"title": "Notes", "parent": "", "edited": "t1"},
            OLD: {"title": "Old", "parent": "", "edited": "t1", "trash": True},
        }
        self.blocks: dict[str, list[dict[str, Any]]] = {
            BRIEF: [
                {"id": "h1", "type": "heading_1", "heading_1": {"rich_text": rt("Summary")}},
                {
                    "id": "p1",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            run("Acme wants "),
                            run("more", bold=True),
                            run(" — see "),
                            run("the deck", href="https://x.test/deck"),
                        ]
                    },
                },
                {
                    "id": "l1",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rt("First")},
                    "has_children": True,
                },
                {
                    "id": "l2",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rt("Second")},
                },
                {
                    "id": "t1",
                    "type": "to_do",
                    "to_do": {"rich_text": rt("Ship it"), "checked": True},
                },
                {
                    "id": "c1",
                    "type": "code",
                    "code": {"rich_text": rt("print(1)"), "language": "python"},
                },
                # a subpage: listed, not entered — it is a page of its own
                {
                    "id": "cp",
                    "type": "child_page",
                    "child_page": {"title": "Appendix"},
                    "has_children": True,
                },
            ],
            "l1": [
                {
                    "id": "l1a",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rt("Under first")},
                }
            ],
        }
        self.read: list[str] = []  # whose children were fetched
        self.fail: set[str] = set()  # blocks Notion will not serve
        self.rotation = True  # the integration's token-rotation setting
        self.tokens: list[str] = []

    def _page(self, pid: str) -> dict[str, Any]:
        p = self.pages[pid]
        parent = (
            {"type": "page_id", "page_id": dashed(p["parent"])}
            if p["parent"]
            else {"type": "workspace"}
        )
        return {
            "object": "page",
            "id": dashed(pid),
            "last_edited_time": p["edited"],
            "parent": parent,
            "in_trash": bool(p.get("trash")),
            "properties": {"title": {"type": "title", "title": rt(p["title"])}},
        }

    def call(self, token: str, method: str, path: str, *, params=None, body=None) -> dict[str, Any]:
        self.tokens.append(token)
        if token == "at-expired":
            raise ConnectorError("API token is invalid.")
        if path == "users/me":
            return {
                "object": "user",
                "type": "bot",
                "name": "Mindkeep",
                "bot": {"workspace_name": "Ada's workspace"},
            }
        if path == "search":
            assert method == "POST" and body["filter"] == {"property": "object", "value": "page"}
            return {
                "results": [self._page(p) for p in self.pages],
                "has_more": False,
                "next_cursor": None,
            }
        if path.startswith("pages/"):
            pid = path.removeprefix("pages/")
            if pid not in self.pages:
                raise ConnectorError(f"Could not find page with ID: {pid}.")
            return self._page(pid)
        if path.startswith("blocks/") and path.endswith("/children"):
            bid = path.removeprefix("blocks/").removesuffix("/children")
            assert bid != "cp", "a subpage's blocks are its own"
            if bid in self.fail:
                raise ConnectorError("Notion answered 502")
            self.read.append(bid)
            return {"results": self.blocks.get(bid, []), "has_more": False, "next_cursor": None}
        raise AssertionError(f"unexpected call {method} {path}")


def signin(client, calls) -> dict:
    """The dance as the browser would do it, checked against Notion's: no scopes, no
    challenge, `owner=user`; then the callback with Notion's code."""
    started = client.get("/grants/oauth/notion/start")
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "api.notion.com" and url.path == "/v1/oauth/authorize"
    assert query["client_id"] == ["app-id"] and query["owner"] == ["user"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://app.test/api/grants/oauth/notion/callback"]
    assert "scope" not in query and "code_challenge" not in query
    back = client.get(
        f"/grants/oauth/notion/callback?state={query['state'][0]}&code=the-code",
        follow_redirects=False,
    )
    assert (
        back.status_code == 302 and back.headers["location"] == "http://app.test/?connected=notion"
    )
    assert calls[-1]["grant_type"] == "authorization_code"
    return client.get("/grants").json()[-1]


def test_notion_is_offered_only_when_configured(client, monkeypatch):
    registry.cache_clear()
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert kinds["notion"]["auth"] == "oauth2" and kinds["notion"]["available"] is False
    assert kinds["notion"]["tick"] == 0  # the connection keeps its own interval
    assert client.get("/grants/oauth/notion/start").status_code == 400
    monkeypatch.setenv("NOTION_CLIENT_ID", "x")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "y")
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert kinds["notion"]["available"] is True


def test_the_sign_in_is_notions_own_and_the_grant_is_named_after_the_workspace(client, notion):
    calls, space = notion
    made = signin(client, calls)
    assert made["kind"] == "notion" and made["label"] == "Ada's workspace" and made["error"] == ""
    assert space.tokens[-1] == "at-1"  # the workspace was asked with the token just given


def test_the_token_call_speaks_notions_dialect_and_googles_is_unchanged(monkeypatch):
    """The real `exchange`, against a stood-in `httpx.post`: Basic auth and a JSON body
    with the version header for Notion; credentials in a form body for Google."""
    for k, v in (
        ("NOTION_CLIENT_ID", "n-id"),
        ("NOTION_CLIENT_SECRET", "n-secret"),
        ("GOOGLE_CLIENT_ID", "g-id"),
        ("GOOGLE_CLIENT_SECRET", "g-secret"),
    ):
        monkeypatch.setenv(k, v)
    registry.cache_clear()
    sent: list[tuple[str, dict[str, Any]]] = []

    class Answer:
        status_code = 200
        content = b"{}"

        def json(self) -> dict[str, str]:
            return {"access_token": "at", "refresh_token": "rt"}

    monkeypatch.setattr(
        grants.httpx, "post", lambda url, **kw: (sent.append((url, kw)), Answer())[1]
    )

    grants.exchange(registry()["notion"].oauth, {"grant_type": "authorization_code", "code": "c"})
    url, kw = sent[-1]
    assert url == "https://api.notion.com/v1/oauth/token"
    assert kw["auth"] == ("n-id", "n-secret")
    assert kw["json"] == {"grant_type": "authorization_code", "code": "c"} and kw["data"] is None
    assert kw["headers"]["Notion-Version"] == "2026-03-11"

    grants.exchange(
        registry()["drive"].oauth, {"grant_type": "refresh_token", "refresh_token": "r"}
    )
    url, kw = sent[-1]
    assert url == "https://oauth2.googleapis.com/token"
    assert kw["auth"] is None and kw["json"] is None
    assert kw["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "r",
        "client_id": "g-id",
        "client_secret": "g-secret",
    }
    registry.cache_clear()


def test_pages_land_as_markdown_under_the_pages_above_them(client, notion, tmp_path):
    calls, space = notion
    made = signin(client, calls)
    home = tmp_path / tenant_id("alice") / "default"
    base = home / "raw/connectors/notion"
    made_c = client.post(C, json={"kind": "notion", "config": {"pages": ""}, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert made_c.json()["name"] == "Notion"
    cid = made_c.json()["id"]

    def files() -> list[str]:
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())

    # the tree, as far as it is shared; two Notes at the top each take their id; the
    # page in the trash is not a source
    assert files() == [
        "Clients.md",
        "Clients/Acme.md",
        "Clients/Acme/Brief.md",
        f"Notes-{NOTES[:8]}.md",
        f"Notes-{NOTES2[:8]}.md",
    ]
    assert client.get(C).json()[0]["summary"] == "+5 ~0 -0"
    assert (base / "Clients/Acme/Brief.md").read_text(encoding="utf-8") == (
        "# Brief\n"
        "\n"
        "# Summary\n"
        "\n"
        "Acme wants **more** — see [the deck](https://x.test/deck)\n"
        "\n"
        "- First\n"
        "  - Under first\n"
        "- Second\n"
        "- [x] Ship it\n"
        "\n"
        "```python\n"
        "print(1)\n"
        "```\n"
        "\n"
        "- Appendix\n"
    )
    assert (base / "Clients.md").read_bytes() == b"# Clients\n"

    # nothing changed: nothing is read again
    space.read.clear()
    client.post(f"{C}/{cid}/sync")
    assert space.read == [] and client.get(C).json()[0]["summary"] == "+0 ~0 -0"

    # Acme retitled: it moves, and Brief under it moves too though it was not edited;
    # Notes edited; the other Notes gone, so the one left drops the suffix — a move
    space.pages[ACME].update(title="ACME Corp", edited="t2")
    space.pages[NOTES]["edited"] = "t2"
    del space.pages[NOTES2]
    client.post(f"{C}/{cid}/sync")
    assert sorted(space.read) == sorted([ACME, BRIEF, NOTES, "l1"])
    # a move is counted twice by the plumbing — the old path removed, the new one changed
    assert client.get(C).json()[0]["summary"] == "+0 ~3 -4"
    assert files() == [
        "Clients.md",
        "Clients/ACME Corp.md",
        "Clients/ACME Corp/Brief.md",
        "Notes.md",
    ]
    assert not (base / "Clients/Acme").exists()


def test_a_connection_can_be_narrowed_to_a_few_pages_and_what_is_under_them(
    client, notion, tmp_path
):
    calls, space = notion
    made = signin(client, calls)
    base = tmp_path / tenant_id("alice") / "default" / "raw/connectors/notion"
    link = f"https://www.notion.so/ada/Acme-{ACME}?pvs=4"
    made_c = client.post(C, json={"kind": "notion", "config": {"pages": link}, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert made_c.json()["name"] == "Notion, 1 page"
    assert sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()) == [
        "Clients/Acme.md",
        "Clients/Acme/Brief.md",
    ]
    cid = made_c.json()["id"]
    # not a page; a page Notion does not know
    for bad, why in (("nope", "a page's link or id"), ("f" * 32, "Could not find page")):
        refused = client.put(f"{C}/{cid}", json={"config": {"pages": bad}})
        assert refused.status_code == 400 and why in refused.json()["detail"], bad


def test_page_ids_are_read_from_ids_uuids_and_links():
    assert page_id(ACME) == ACME
    assert page_id(dashed(ACME)) == ACME
    assert page_id(f"https://www.notion.so/ada/Acme-{ACME}?pvs=4") == ACME
    # a slug that ends in hex digits does not steal the front of the id
    assert page_id(f"https://www.notion.so/Cafe-{ACME}") == ACME
    assert page_id("https://www.notion.so/ada/nothing-here") == ""


def test_blocks_become_markdown():
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},  # a spacer: nothing
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt("one")}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt("two")}},
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [run("bold ", bold=True), run("then "), run("x", code=True)]
            },
        },
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt("anew")}},
        {
            "type": "toggle",
            "toggle": {"rich_text": rt("More")},
            "children": [{"type": "paragraph", "paragraph": {"rich_text": rt("hidden")}}],
        },
        {"type": "quote", "quote": {"rich_text": rt("a\nb")}},
        {
            "type": "callout",
            "callout": {"rich_text": rt("mind"), "icon": {"type": "emoji", "emoji": "💡"}},
        },
        {"type": "divider", "divider": {}},
        {
            "type": "table",
            "table": {"has_column_header": True},
            "children": [
                {"type": "table_row", "table_row": {"cells": [rt("A"), rt("B")]}},
                {"type": "table_row", "table_row": {"cells": [rt("1"), rt("2|3")]}},
            ],
        },
        {
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://x.test/a.png"},
                "caption": rt("A"),
            },
        },
        {"type": "bookmark", "bookmark": {"url": "https://x.test", "caption": []}},
        {
            "type": "column_list",
            "column_list": {},
            "children": [
                {
                    "type": "column",
                    "column": {},
                    "children": [{"type": "paragraph", "paragraph": {"rich_text": rt("left")}}],
                },
                {
                    "type": "column",
                    "column": {},
                    "children": [{"type": "paragraph", "paragraph": {"rich_text": rt("right")}}],
                },
            ],
        },
        {"type": "equation", "equation": {"expression": "E=mc^2"}},
        {"type": "breadcrumb", "breadcrumb": {}},  # nothing to say
        {"type": "unsupported", "unsupported": {}},
    ]
    assert markdown(blocks).replace("\n\n\n", "\n\n").strip() == (
        "1. one\n"
        "2. two\n"
        "\n"
        "**bold** then `x`\n"
        "\n"
        "1. anew\n"
        "- More\n"
        "  hidden\n"
        "\n"
        "> a\n"
        "> b\n"
        "\n"
        "> 💡 mind\n"
        "\n"
        "---\n"
        "\n"
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2\\|3 |\n"
        "\n"
        "![A](https://x.test/a.png)\n"
        "\n"
        "[https://x.test](https://x.test)\n"
        "\n"
        "left\n"
        "\n"
        "right\n"
        "\n"
        "$$\n"
        "E=mc^2\n"
        "$$"
    )


def test_a_token_that_rotates_is_renewed_before_use(client, notion):
    calls, space = notion
    made = signin(client, calls)
    with session() as s:
        row = s.get(Grant, made["id"])
        assert row.expires_at is not None  # booked for the hourly renewal
        assert vault.unseal_all(row.secret) == {"access_token": "at-1", "refresh_token": "rt-1"}
        row.expires_at = datetime.now(UTC) + timedelta(seconds=30)  # about to expire
        s.commit()
    made_c = client.post(C, json={"kind": "notion", "config": {"pages": ""}, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert calls[-1] == {"grant_type": "refresh_token", "refresh_token": "rt-1"}
    assert space.tokens[-1] == "at-2"
    with session() as s:
        row = s.get(Grant, made["id"])
        assert vault.unseal_all(row.secret) == {"access_token": "at-2", "refresh_token": "rt-2"}
        assert row.expires_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)


def test_a_token_that_does_not_rotate_is_never_refreshed(client, notion):
    """Notion sends no expires_in; with token rotation off, no refresh token either. Such
    a grant must not be booked for a renewal it cannot make."""
    calls, space = notion
    space.rotation = False
    made = signin(client, calls)
    with session() as s:
        row = s.get(Grant, made["id"])
        assert row.expires_at is None
        assert vault.unseal_all(row.secret) == {"access_token": "at-1", "refresh_token": ""}
    made_c = client.post(C, json={"kind": "notion", "config": {"pages": ""}, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert client.get(C).json()[0]["summary"] == "+5 ~0 -0"
    assert all(c["grant_type"] == "authorization_code" for c in calls)  # never a refresh


def test_a_page_whose_blocks_cannot_be_read_is_kept_and_tried_again(client, notion, tmp_path):
    calls, space = notion
    made = signin(client, calls)
    brief = tmp_path / tenant_id("alice") / "default/raw/connectors/notion/Clients/Acme/Brief.md"
    made_c = client.post(C, json={"kind": "notion", "config": {"pages": ""}, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    cid = made_c.json()["id"]
    # Brief edited, but Notion will not serve its blocks this time: the source stays
    space.pages[BRIEF]["edited"] = "t2"
    space.blocks[BRIEF][0]["heading_1"]["rich_text"] = rt("Summary v2")
    space.fail.add(BRIEF)
    client.post(f"{C}/{cid}/sync")
    assert client.get(C).json()[0]["summary"] == "+0 ~0 -0"
    assert "# Summary\n" in brief.read_text(encoding="utf-8")
    # served again: read now, as if the edit had just happened
    space.fail.clear()
    client.post(f"{C}/{cid}/sync")
    assert client.get(C).json()[0]["summary"] == "+0 ~1 -0"
    assert "# Summary v2\n" in brief.read_text(encoding="utf-8")
