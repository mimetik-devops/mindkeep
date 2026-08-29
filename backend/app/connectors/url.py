"""The simplest connector: one thing at a URL, fetched on schedule.

A public document, a shared CSV, a feed, a page someone keeps current — anything the
web serves without a login. It is also the worked example: a `kind`, a form, a
`check` that tries the address, and a `pull` that returns one `Item`. Everything else
is the plumbing's.
"""

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.base import Connector, ConnectorError, Field, Item, Pull

TIMEOUT = 30


def fetch(url: str) -> tuple[bytes, str]:
    """The bytes at the address and the content type they came with."""
    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as web:
            got = web.get(url)
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach {url}: {e}") from e
    if got.status_code >= 400:
        raise ConnectorError(f"{url} answered {got.status_code}")
    return got.content, got.headers.get("content-type", "").split(";")[0].strip()


# what a fetched body is saved as, by the type it came with — the ingest reads by suffix
SUFFIX = {
    "text/html": ".html",
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


def filename(url: str, kind: str) -> str:
    """A name for the file: the URL's last segment when it has one, the host otherwise,
    with a suffix from the content type when the segment carries none."""
    parts = urlparse(url)

    def clean(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9 ._-]", "-", s).strip(" .-")

    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    stem = clean(last) or clean(parts.netloc) or "page"
    # a host name has dots but no suffix the ingest could read by; a bare segment has none
    if (not last or "." not in last) and (suffix := SUFFIX.get(kind)):
        stem += suffix
    return stem


class UrlConnector(Connector):
    kind = "url"
    title = "A web address"
    blurb = "One page, file or feed at a public URL, fetched again on schedule."
    fields = (
        Field("url", "Address", help="https://…  — something the web serves without a login."),
    )

    def check(self, config: dict[str, str]) -> None:
        url = config.get("url", "").strip()
        if not url.startswith(("http://", "https://")):
            raise ConnectorError("the address has to start with http:// or https://")
        fetch(url)

    def pull(self, config: dict[str, str], cursor: dict[str, Any]) -> Pull:
        url = config["url"].strip()
        content, kind = fetch(url)
        return Pull(items=[Item(id=url, path=filename(url, kind), content=content)])
