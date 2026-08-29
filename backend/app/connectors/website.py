"""Websites, kept as Markdown: for each site, the page at its address and the pages it
links to, fetched again on the site's own schedule and folded into the wiki when they
change.

Markdown, not HTML — for the same reason a web clipper saves Markdown. The agent reads a
page's words, not its markup; a marketing page of 14,000 characters of HTML is 4,000 of
Markdown with every heading, list and link intact, and it is what a person sees in the
Library too. Whole-page conversion, not readability-style extraction: that drops the
headings and lists of any page that is not an article, and most of the pages worth
tracking are not articles. Stable across fetches, so the plumbing's digest is a real
"did it change".

One connection holds the sites, each with its own depth and its own frequency. The
plumbing ticks every fifteen minutes; each tick, only the sites that are due are fetched —
the cursor remembers when each was last pulled, and which pages it produced, so a site
that shrank or was dropped from the list has its pages removed. Pages land under the
site's host: `raw/connectors/website/<host>/…`. An address with a path — `x.com/docs` —
bounds its crawl to that section. An address that is not HTML — a PDF, a CSV, a feed — is
saved as the file it is.

This is also the worked example of a connector: a form with a list of rows, a `check`
that tries each address, a `pull` that keeps its own clock. No grant: the web serves
these without a login.
"""

import re
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from app.connectors.base import Connector, ConnectorError, Field, Grant, Item, Pull, rows_of

TIMEOUT = 30
AGENT = "Mindkeep (+https://mindkeep.io)"
PAGES_DEFAULT, PAGES_MAX = 20, 200
EVERY = (
    ("15", "every 15 minutes"),
    ("60", "every hour"),
    ("360", "every 6 hours"),
    ("1440", "every day"),
    ("10080", "every week"),
)
EVERY_DEFAULT = "1440"
# what a page carries that is not its content
NOISE = ("script", "style", "noscript", "svg", "iframe", "form", "button", "nav", "footer")
# what a fetched file is saved as, by the type it came with — the ingest reads by suffix
SUFFIX = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/rss+xml": ".xml",
    "application/atom+xml": ".xml",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


def fetch(url: str) -> tuple[bytes, str]:
    """The bytes at the address and the content type they came with."""
    try:
        with httpx.Client(
            follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": AGENT}
        ) as web:
            got = web.get(url)
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach {url}: {e}") from e
    if got.status_code >= 400:
        raise ConnectorError(f"{url} answered {got.status_code}")
    return got.content, got.headers.get("content-type", "").split(";")[0].strip()


def canonical(url: str) -> str:
    """One name per page: no fragment, no trailing slash but the root's."""
    bare = urldefrag(url)[0]
    parts = urlparse(bare)
    path = parts.path.rstrip("/") or "/"
    return parts._replace(path=path).geturl()


def to_markdown(html: bytes, url: str) -> tuple[str, list[str]]:
    """The page as Markdown with a title and its source up top, and the same-site pages
    it links to. Links and images are made absolute, so a citation can follow them."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = str(meta.get("content", "")) if meta else ""
    for tag in soup.find_all(NOISE):
        tag.decompose()
    for tag in soup.find_all(href=True):
        tag["href"] = urljoin(url, str(tag["href"]))
    for tag in soup.find_all(src=True):
        tag["src"] = urljoin(url, str(tag["src"]))
    host = urlparse(url).netloc
    links = []
    for tag in soup.find_all("a", href=True):
        target = canonical(str(tag["href"]))
        if urlparse(target).scheme in ("http", "https") and urlparse(target).netloc == host:
            links.append(target)
    body = markdownify(str(soup.body or soup), heading_style="ATX", bullets="-")
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    head = ["---", f"title: {_yaml(title)}", f"source: {url}"]
    if description:
        head.append(f"description: {_yaml(description)}")
    return "\n".join([*head, "---", "", body]), links


def _yaml(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def page_path(url: str) -> str:
    """Where a page lands: under its host, `/` is index.md, `/docs/setup.html` is
    docs/setup.md, `/docs` is docs.md."""
    parts = urlparse(url)
    path = parts.path.strip("/")
    name = "index.md" if not path else re.sub(r"\.html?$", "", path) + ".md"
    return f"{parts.netloc}/{name}"


def file_path(url: str, kind: str) -> str:
    """A file that is not a page keeps its own name under its host, with a suffix from
    the content type when the address carries none."""
    parts = urlparse(url)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "-", last or parts.netloc).strip(" .-") or "file"
    if (not last or "." not in last) and (suffix := SUFFIX.get(kind)):
        stem += suffix
    return f"{parts.netloc}/{stem}"


def crawl(start: str, limit: int, taken: set[str]) -> list[Item]:
    """One site, breadth first from its start page — same host, and under the start's
    path when it has one — fetched as pages are reached and never past the limit."""
    content, kind = fetch(start)
    if kind != "text/html":
        return [Item(id=start, path=file_path(start, kind), content=content)]
    section = urlparse(start).path.rstrip("/")
    items: list[Item] = []
    seen = {start}
    queue = deque([(start, content, kind)])
    while queue and len(items) < limit:
        url, html, kind = queue.popleft()
        if kind != "text/html":
            continue  # a linked file is not a page of the site
        markdown, links = to_markdown(html, url)
        path = page_path(url)
        while path in taken:  # /a and /a.html would land on one name
            path = path[:-3] + "-2.md"
        taken.add(path)
        items.append(Item(id=url, path=path, content=markdown.encode()))
        for link in links:
            if link in seen or len(seen) >= limit:
                continue
            if section and not urlparse(link).path.startswith(section):
                continue  # outside the section the address names
            seen.add(link)
            try:
                queue.append((link, *fetch(link)))
            except ConnectorError:
                continue  # a dead link is the site's problem, not the sync's
    return items


class WebsiteConnector(Connector):
    kind = "website"
    title = "Websites"
    blurb = (
        "Pages at public addresses and the pages they link to on the same site, kept as "
        "Markdown and folded in again whenever one changes — each site on its own schedule. "
        "An address that is not a page — a PDF, a feed — is kept as the file it is."
    )
    folder = "website"
    tick = 15  # the sites keep their own clocks; the plumbing looks in every quarter hour
    fields = (
        Field(
            "sites",
            "Sites",
            rows=(
                Field("url", "Address", help="https://…"),
                Field(
                    "pages",
                    "Pages at most",
                    required=False,
                    help=f"{PAGES_DEFAULT} unless you say; 1 for the one page; {PAGES_MAX} at most",
                ),
                Field("every", "Check for changes", options=EVERY, help=EVERY_DEFAULT),
            ),
        ),
    )

    def _sites(self, config: dict[str, str]) -> list[dict[str, Any]]:
        """The sites, each checked: address, depth, frequency."""
        sites: list[dict[str, Any]] = []
        for row in rows_of(config, "sites"):
            url = canonical(str(row.get("url", "")).strip())
            if not url.startswith(("http://", "https://")):
                raise ConnectorError(
                    f"{url or 'an address'}: it has to start with http:// or https://"
                )
            pages = str(row.get("pages", "")).strip() or str(PAGES_DEFAULT)
            if not pages.isdigit() or not 1 <= int(pages) <= PAGES_MAX:
                raise ConnectorError(f"{url}: pages, a number from 1 to {PAGES_MAX}")
            every = str(row.get("every", "")).strip() or EVERY_DEFAULT
            if every not in dict(EVERY):
                raise ConnectorError(f"{url}: an interval from the list")
            if any(s["url"] == url for s in sites):
                raise ConnectorError(f"{url} is listed twice")
            sites.append({"url": url, "pages": int(pages), "every": int(every)})
        if not sites:
            raise ConnectorError("at least one site")
        return sites

    def name(self, config: dict[str, str]) -> str:
        hosts = [urlparse(s["url"]).netloc for s in self._sites(config)]
        shown = ", ".join(dict.fromkeys(hosts[:3]))
        return shown + (f" +{len(hosts) - 3}" if len(hosts) > 3 else "")

    def check(self, config: dict[str, str], grant: Grant | None) -> None:
        for site in self._sites(config):
            fetch(site["url"])

    def pull(self, config: dict[str, str], cursor: dict[str, Any], grant: Grant | None) -> Pull:
        """The sites that are due, and the pages of sites that shrank or went away. The
        cursor is the clock: when each site was last pulled and what it produced."""
        now = datetime.now(UTC)
        state: dict[str, dict[str, Any]] = dict(cursor.get("sites", {}))
        items: list[Item] = []
        removed: list[str] = []
        taken: set[str] = set()
        wanted = {s["url"] for s in self._sites(config)}
        for site in self._sites(config):
            url = site["url"]
            was = state.get(url)
            if was and now - datetime.fromisoformat(was["pulled"]) < timedelta(
                minutes=site["every"]
            ):
                continue  # not its time yet
            found = crawl(url, site["pages"], taken)
            ids = [i.id for i in found]
            removed.extend(old for old in (was or {}).get("ids", []) if old not in ids)
            items.extend(found)
            state[url] = {"pulled": now.isoformat(timespec="seconds"), "ids": ids}
        for url in list(state):
            if url not in wanted:  # dropped from the list: its pages go
                removed.extend(state.pop(url).get("ids", []))
        # a page another site produced this time stays — a section replacing its site, say
        kept = {i.id for i in items}
        removed = [r for r in removed if r not in kept]
        return Pull(items=items, cursor={"sites": state}, complete=False, removed=removed)
