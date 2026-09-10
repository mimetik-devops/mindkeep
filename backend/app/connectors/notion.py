"""Notion: the pages a workspace shares with Mindkeep, kept as Markdown.

A grant is the person's Notion sign-in, made through Notion's own picker — which is where
the scope is set: the pages the person hands over, and everything under them. One
connection is those pages in one bundle, narrowed if wanted to a few of them and what sits
under those, so one sign-in can feed a work bundle and a personal one with different
corners of the same workspace.

A page is one Markdown file, filed under the shared pages above it — `Clients/Acme/Brief.md`
mirrors the tree in Notion as far as Mindkeep can see it. Blocks are fetched a level at a
time, as Notion serves them, and rendered plainly: headings, lists, to-dos, toggles,
quotes, callouts, code, tables, links to files and to subpages. What has no Markdown — an
embed, a breadcrumb — keeps its text if it has any and is dropped if it has none. A
database is not pulled as such: its rows are pages, and each one shared arrives as one.

`/v1/search` lists every page the sign-in can see, children included, each with its
`last_edited_time`; the cursor remembers those and each page's path, so a sync fetches
the blocks of what was edited, moves what was retitled (or whose parent was), and removes
the source of a page that went away or into the trash. The page id is the item's
identity.

Notion's sign-in departs from textbook OAuth 2 in three ways, all declared on the `OAuth`
below and honoured by grants.py: the app's credentials go as HTTP Basic auth on the token
call, that call's body is JSON with a `Notion-Version` header, and `owner=user` stands in
for scopes. PKCE is absent from Notion's documentation, so it is not sent. REST over
httpx, no SDK.
"""

import re
import time
from collections import Counter
from typing import Any

import httpx

from app.connectors.base import (
    Connector,
    ConnectorError,
    Field,
    Grant,
    Item,
    OAuth,
    Pull,
    lines,
)

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
TIMEOUT = 60
PAGES_MAX = 500  # per connection
BLOCKS_MAX = 2000  # per page, every level counted
# a page id as it appears anywhere — the id itself, a dashed uuid, the tail of a link —
# once the dashes are gone: the last run of 32 hex digits
HEX32 = re.compile(r"[0-9a-f]{32}(?![0-9a-f])")
# blocks whose children sit at the same level, not one in
FLAT = {"column_list", "column", "synced_block"}
# blocks that run together, no blank line between them; everything else gets one after
LISTY = {
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "child_page",
    "child_database",
}
# a subpage is a page of its own and arrives as one: its blocks are not entered here
PAGES = {"child_page", "child_database"}
MEDIA = {"image", "video", "file", "pdf", "audio"}
LINKS = {"bookmark", "embed", "link_preview"}


# --- the API ------------------------------------------------------------------------------------


def call(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One call against the Notion API, as JSON. A 429 is waited out once."""
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": VERSION}
    got = _request(method, path, params, body, headers)
    if got.status_code == 429:
        try:
            wait = float(got.headers.get("Retry-After", "1"))
        except ValueError:
            wait = 1.0
        time.sleep(min(wait, 10))
        got = _request(method, path, params, body, headers)
    if got.status_code >= 400:
        raise ConnectorError(_why(got))
    return dict(got.json())


def _request(
    method: str,
    path: str,
    params: dict[str, str] | None,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> httpx.Response:
    try:
        return httpx.request(
            method, f"{API}/{path}", params=params, json=body, headers=headers, timeout=TIMEOUT
        )
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach Notion: {e}") from e


def _why(got: httpx.Response) -> str:
    try:
        return str(got.json()["message"])
    except Exception:
        return f"Notion answered {got.status_code}"


def search(token: str) -> list[dict[str, Any]]:
    """Every page the sign-in can see — children included, Notion says — up to a limit."""
    found: list[dict[str, Any]] = []
    body: dict[str, Any] = {"filter": {"property": "object", "value": "page"}, "page_size": 100}
    while len(found) < PAGES_MAX:
        page = call(token, "POST", "search", body=body)
        found.extend(page.get("results", []))
        if not page.get("has_more") or not page.get("next_cursor"):
            break
        body = {**body, "start_cursor": page["next_cursor"]}
    return found[:PAGES_MAX]


def read(token: str, page_id: str) -> list[dict[str, Any]]:
    """A page's blocks, each with its own under `children` — the tree Notion serves a
    level at a time, within a budget of blocks for the page."""
    left = BLOCKS_MAX

    def under(block_id: str) -> list[dict[str, Any]]:
        nonlocal left
        found: list[dict[str, Any]] = []
        params = {"page_size": "100"}
        while left > 0:
            page = call(token, "GET", f"blocks/{block_id}/children", params=params)
            for b in page.get("results", []):
                if left <= 0:
                    break
                left -= 1
                if b.get("has_children") and b.get("type") not in PAGES:
                    b["children"] = under(str(b["id"]))
                found.append(b)
            if not page.get("has_more") or not page.get("next_cursor"):
                break
            params = {**params, "start_cursor": str(page["next_cursor"])}
        return found

    return under(page_id)


# --- pages ----------------------------------------------------------------------------------------


def page_id(value: str) -> str:
    """A page's id in one form — from the id itself, the dashed uuid Notion answers with,
    or a link with the id at its tail. Empty when there is none."""
    hits = HEX32.findall(value.strip().replace("-", "").lower())
    return hits[-1] if hits else ""


def title_of(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain(prop.get("title") or []).strip() or "Untitled"
    return "Untitled"


def parent_of(page: dict[str, Any]) -> str:
    """The page above this one, when it is a page; nothing when it is a database or the
    workspace itself."""
    parent = page.get("parent") or {}
    return page_id(str(parent.get("page_id", ""))) if parent.get("type") == "page_id" else ""


def chain(pid: str, parents: dict[str, str]) -> list[str]:
    """A page and its ancestors, nearest first, as far as the results reach."""
    out: list[str] = []
    while pid and pid not in out:
        out.append(pid)
        pid = parents.get(pid, "")
    return out


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ()._-]", "-", name).strip(" .") or "page"


# --- Markdown ------------------------------------------------------------------------------------


def plain(rich: list[dict[str, Any]]) -> str:
    return "".join(str(r.get("plain_text", "")) for r in rich)


def text(rich: list[dict[str, Any]]) -> str:
    """Rich text as inline Markdown: bold, italic, strikethrough, code, links, equations.
    The marks hug the words — a run's edge spaces stay outside them."""
    out: list[str] = []
    for r in rich:
        t = str(r.get("plain_text", ""))
        if r.get("type") == "equation":
            out.append(f"${(r.get('equation') or {}).get('expression', t)}$")
            continue
        core = t.strip()
        if not core:
            out.append(t)
            continue
        lead, trail = t[: len(t) - len(t.lstrip())], t[len(t.rstrip()) :]
        a = r.get("annotations") or {}
        if a.get("code"):
            core = f"`{core}`"
        if a.get("bold"):
            core = f"**{core}**"
        if a.get("italic"):
            core = f"*{core}*"
        if a.get("strikethrough"):
            core = f"~~{core}~~"
        if r.get("href"):
            core = f"[{core}]({r['href']})"
        out.append(f"{lead}{core}{trail}")
    return "".join(out)


def markdown(blocks: list[dict[str, Any]], depth: int = 0) -> str:
    """Blocks as Markdown, children indented under their parent, a table from its rows."""
    out: list[str] = []
    n = 0
    listy_before = False
    for b in blocks:
        kind = str(b.get("type", ""))
        body = b.get(kind) or {}
        kids = b.get("children") or []
        if kind in FLAT:
            out.append(markdown(kids, depth))
            continue
        n = n + 1 if kind == "numbered_list_item" else 0
        line = _line(kind, body, kids, n)
        if line is None:
            continue
        listy = kind in LISTY
        if listy_before and not listy:
            out.append("")
        out.append(_indent(line, depth))
        if kids and kind != "table":
            out.append(markdown(kids, depth + 1))
        if not listy:
            out.append("")
        listy_before = listy
    return "\n".join(out)


def _line(kind: str, body: dict[str, Any], kids: list[dict[str, Any]], n: int) -> str | None:
    rich = text(body.get("rich_text") or [])
    if kind == "paragraph":
        return rich or None
    if kind.startswith("heading_"):
        return f"{'#' * int(kind[-1])} {rich}"
    if kind in ("bulleted_list_item", "toggle"):
        return f"- {rich}"
    if kind == "numbered_list_item":
        return f"{n}. {rich}"
    if kind == "to_do":
        return f"- [{'x' if body.get('checked') else ' '}] {rich}"
    if kind == "quote":
        return "\n".join(f"> {ln}" for ln in (rich or " ").splitlines())
    if kind == "callout":
        icon = body.get("icon") or {}
        mark = f"{icon['emoji']} " if icon.get("type") == "emoji" else ""
        return "\n".join(f"> {ln}" for ln in (mark + rich or " ").splitlines())
    if kind == "code":
        return f"```{body.get('language', '')}\n{plain(body.get('rich_text') or [])}\n```"
    if kind == "divider":
        return "---"
    if kind == "equation":
        return f"$$\n{body.get('expression', '')}\n$$"
    if kind == "table":
        return _table(body, kids)
    if kind in PAGES:
        return f"- {body.get('title', '')}"
    if kind in MEDIA:
        src = body.get(str(body.get("type", ""))) or {}
        url = str(src.get("url", ""))
        label = text(body.get("caption") or []) or str(body.get("name") or kind)
        return f"![{label}]({url})" if kind == "image" else f"[{label}]({url})"
    if kind in LINKS:
        url = str(body.get("url", ""))
        return f"[{text(body.get('caption') or []) or url}]({url})"
    return rich or None


def _table(body: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    cells = [
        [_cell(c) for c in (r.get("table_row") or {}).get("cells", [])]
        for r in rows
        if r.get("type") == "table_row"
    ]
    if not cells:
        return None
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    head = cells.pop(0) if body.get("has_column_header") else [""] * width
    row = "| {} |".format
    return "\n".join(
        [row(" | ".join(head)), "|" + "---|" * width, *(row(" | ".join(r)) for r in cells)]
    )


def _cell(rich: list[dict[str, Any]]) -> str:
    return text(rich).replace("|", "\\|").replace("\n", " ")


def _indent(s: str, depth: int) -> str:
    pad = "  " * depth
    return "\n".join(pad + ln if ln else ln for ln in s.splitlines())


def render(title: str, blocks: list[dict[str, Any]]) -> bytes:
    """A page as a file: its title as the heading, its blocks under it."""
    body = re.sub(r"\n{3,}", "\n\n", markdown(blocks)).strip()
    return (f"# {title}\n\n{body}" if body else f"# {title}").rstrip().encode() + b"\n"


# --- the connector --------------------------------------------------------------------------------


class NotionConnector(Connector):
    kind = "notion"
    title = "Notion"
    blurb = (
        "The pages your Notion workspace shares with Mindkeep, each kept as Markdown under "
        "the pages above it. Read-only."
    )
    auth = "oauth2"
    oauth = OAuth(
        provider="notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        scopes=(),
        params=(("owner", "user"),),
        basic_auth=True,
        json_body=True,
        headers=(("Notion-Version", VERSION),),
        pkce=False,
    )
    folder = "notion"
    fields = (
        Field(
            "pages",
            "Pages",
            required=False,
            multiline=True,
            help="Empty: everything shared with Mindkeep. Or the links or ids of a few pages, "
            "one per line, to keep only those and what is under them",
        ),
    )

    def _wanted(self, config: dict[str, str]) -> set[str]:
        wanted: set[str] = set()
        for given in lines(config.get("pages", "")):
            pid = page_id(given)
            if not pid:
                raise ConnectorError(f"{given}: a page's link or id")
            wanted.add(pid)
        return wanted

    def name(self, config: dict[str, str]) -> str:
        n = len(lines(config.get("pages", "")))
        return f"Notion, {n} page{'s' if n != 1 else ''}" if n else "Notion"

    def check_grant(self, secrets: dict[str, str]) -> str:
        me = call(secrets.get("access_token", ""), "GET", "users/me")
        return str((me.get("bot") or {}).get("workspace_name") or me.get("name") or "Notion")

    def check(self, config: dict[str, str], grant: Grant | None) -> None:
        if grant is None:
            raise ConnectorError("a Notion sign-in")
        for pid in self._wanted(config):
            call(grant.token, "GET", f"pages/{pid}")

    def pull(self, config: dict[str, str], cursor: dict[str, Any], grant: Grant | None) -> Pull:
        """Every page in reach, narrowed to the chosen ones and their descendants; the
        blocks of those that were edited or moved; the ids of those that went away."""
        if grant is None:
            raise ConnectorError("a Notion sign-in")
        token = grant.token
        pages = {
            page_id(str(p["id"])): p
            for p in search(token)
            if not (p.get("in_trash") or p.get("archived"))
        }
        parents = {pid: parent_of(p) for pid, p in pages.items()}
        wanted = self._wanted(config)
        chosen = [pid for pid in pages if not wanted or wanted & set(chain(pid, parents))]
        paths = {
            pid: "/".join(
                safe(title_of(pages[a])) for a in reversed(chain(pid, parents)) if a in pages
            )
            + ".md"
            for pid in chosen
        }
        shared = Counter(paths.values())
        for pid, path in paths.items():
            if shared[path] > 1:  # two pages at one path: each takes its id
                paths[pid] = f"{path[:-3]}-{pid[:8]}.md"
        was: dict[str, dict[str, str]] = dict(cursor.get("pages", {}))
        items: list[Item] = []
        state: dict[str, dict[str, str]] = {}
        for pid in chosen:
            now = {"edited": str(pages[pid].get("last_edited_time", "")), "path": paths[pid]}
            if was.get(pid) == now:
                state[pid] = now
                continue
            try:
                content = render(title_of(pages[pid]), read(token, pid))
            except ConnectorError:
                if pid in was:
                    state[pid] = was[pid]  # as it was: tried again next time
                continue
            state[pid] = now
            items.append(Item(id=pid, path=paths[pid], content=content))
        removed = [pid for pid in was if pid not in state]
        return Pull(items=items, cursor={"pages": state}, complete=False, removed=removed)
