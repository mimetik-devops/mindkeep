"""A website, kept as Markdown: the page at an address, and the pages it links to on the
same site, fetched again on schedule and folded into the wiki when they change.

Markdown, not HTML — for the same reason a web clipper saves Markdown. The agent reads a
page's words, not its markup; a marketing page of 14,000 characters of HTML is 4,000 of
Markdown with every heading, list and link intact, and it is what a person sees in the
Library too. Whole-page conversion, not readability-style extraction: that drops the
headings and lists of any page that is not an article, and most of the pages worth
tracking are not articles. Stable across fetches, so the plumbing's digest is a real
"did it change".

An address that is not HTML — a PDF, a CSV, a feed — is saved as the file it is.

This is also the worked example of a connector: a form, a `check` that tries the
address, a `pull` that returns items. Everything else is the plumbing's.
"""

import re
from collections import deque
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from app.connectors.base import Connector, ConnectorError, Field, Item, Pull

TIMEOUT = 30
AGENT = "Mindkeep (+https://mindkeep.io)"
PAGES_DEFAULT, PAGES_MAX = 20, 200
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
    """Where a page lands, from its address: `/` is index.md, `/docs/setup.html` is
    docs/setup.md, `/docs` is docs.md."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "index.md"
    path = re.sub(r"\.html?$", "", path)
    return f"{path}.md"


def file_path(url: str, kind: str) -> str:
    """A file that is not a page keeps its own name, with a suffix from the content type
    when the address carries none."""
    parts = urlparse(url)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "-", last or parts.netloc).strip(" .-") or "file"
    if (not last or "." not in last) and (suffix := SUFFIX.get(kind)):
        stem += suffix
    return stem


class WebsiteConnector(Connector):
    kind = "website"
    title = "A website"
    blurb = (
        "A page and the pages it links to on the same site, kept as Markdown and folded in "
        "again whenever one changes. An address that is not a page — a PDF, a feed — is "
        "kept as the file it is."
    )
    fields = (
        Field("url", "Address", help="https://…  — something the web serves without a login."),
        Field(
            "pages",
            "Pages at most",
            required=False,
            help=f"How far to follow links on the same site. {PAGES_DEFAULT} unless you say; "
            f"1 for the one page; {PAGES_MAX} at most.",
        ),
    )

    def _limit(self, config: dict[str, str]) -> int:
        given = config.get("pages", "").strip()
        if not given:
            return PAGES_DEFAULT
        if not given.isdigit() or not 1 <= int(given) <= PAGES_MAX:
            raise ConnectorError(f"pages: a number from 1 to {PAGES_MAX}")
        return int(given)

    def check(self, config: dict[str, str]) -> None:
        url = config.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            raise ConnectorError("the address has to start with http:// or https://")
        self._limit(config)
        fetch(url)

    def pull(self, config: dict[str, str], cursor: dict[str, Any]) -> Pull:
        start = canonical(config["url"].strip())
        limit = self._limit(config)
        content, kind = fetch(start)
        if kind != "text/html":
            return Pull(items=[Item(id=start, path=file_path(start, kind), content=content)])

        # breadth first from the start page, same host only, fetched as they are reached
        # and never past the limit — a link is fetched when its turn comes, not when seen
        items: list[Item] = []
        taken: set[str] = set()
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
                seen.add(link)
                try:
                    queue.append((link, *fetch(link)))
                except ConnectorError:
                    continue  # a dead link is the site's problem, not the sync's
        return Pull(items=items)
