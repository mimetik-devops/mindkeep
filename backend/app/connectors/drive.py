"""Google Drive: folders, each on its own schedule, their files kept as sources.

A grant is the person's Google sign-in (`drive.readonly`, nothing more — the grant's name
is read from Drive's own `about`, so no other scope is asked for). One connection holds
the folders, each a row with its own frequency; the plumbing ticks every quarter hour
and only the folders that are due are listed. A folder is named the way a person sees
it — `Clients/Acme`, from the top of My Drive — or given as its link or id, which also
reaches a shared drive. A name that matches two folders is refused with both, rather
than guessed.

Google's own documents are exported into what the ingest reads: a Doc as Markdown, a
Sheet as CSV, a Slides deck as text. Everything else is downloaded as it is, up to a
size (a Drive folder can hold a two-gigabyte video that no wiki wants). What cannot
become a source — a form, a shortcut, a site — is skipped, and so is a file whose
download fails: one bad file must not stop the folder. The cursor remembers, per
folder, each file's `modifiedTime`, so a tick fetches only what changed and a file that
went away has its source removed. The file's Drive id is the item's identity, so a
rename is a move.

REST over httpx, no Google SDK: three endpoints, one scope.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.connectors.base import Connector, ConnectorError, Field, Grant, Item, OAuth, Pull, rows_of

API = "https://www.googleapis.com/drive/v3"
TIMEOUT = 60
FILES_MAX, FOLDERS_MAX = 500, 100  # per folder row, per tick
SIZE_MAX = 20 * 1024 * 1024
EVERY = (
    ("15", "every 15 minutes"),
    ("60", "every hour"),
    ("360", "every 6 hours"),
    ("1440", "every day"),
    ("10080", "every week"),
)
EVERY_DEFAULT = "60"
FOLDER = "application/vnd.google-apps.folder"
# Google's own kinds, exported into what the ingest reads; the rest of google-apps is skipped
EXPORT = {
    "application/vnd.google-apps.document": ("text/markdown", ".md"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
FOLDER_LINK = re.compile(r"drive\.google\.com/.*folders/([A-Za-z0-9_-]+)")


def call(token: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """One GET against the Drive API, as JSON."""
    try:
        got = httpx.get(
            f"{API}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach Google Drive: {e}") from e
    if got.status_code >= 400:
        raise ConnectorError(_why(got))
    return dict(got.json())


def download(token: str, file_id: str, export: str | None) -> bytes:
    """A file's bytes: exported for Google's own kinds, as they are for the rest."""
    url = f"{API}/files/{file_id}/export" if export else f"{API}/files/{file_id}"
    params = {"mimeType": export} if export else {"alt": "media", "supportsAllDrives": "true"}
    try:
        got = httpx.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
        )
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach Google Drive: {e}") from e
    if got.status_code >= 400:
        raise ConnectorError(_why(got))
    return got.content


def _why(got: httpx.Response) -> str:
    try:
        return str(got.json()["error"]["message"])
    except Exception:
        return f"Google Drive answered {got.status_code}"


def listing(token: str, query: str) -> list[dict[str, Any]]:
    """Every file a query matches, page after page."""
    found: list[dict[str, Any]] = []
    params = {
        "q": query,
        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size)",
        "pageSize": "1000",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    while True:
        page = call(token, "files", params)
        found.extend(page.get("files", []))
        token_next = page.get("nextPageToken")
        if not token_next:
            return found
        params = {**params, "pageToken": str(token_next)}


def resolve(token: str, given: str) -> tuple[str, str]:
    """A folder as a person names it, to its id and its clean name: a link or a bare id
    is taken as it is; a path is walked from the top of My Drive, one segment at a time,
    refusing a segment that matches more than one folder."""
    given = given.strip().strip("/")
    if m := FOLDER_LINK.search(given):
        return m.group(1), given
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", given):
        return given, given
    parent = "root"
    walked: list[str] = []
    for segment in [s for s in given.split("/") if s.strip()]:
        name = segment.strip().replace("'", "\\'")
        query = f"'{parent}' in parents and mimeType = '{FOLDER}' and name = '{name}'"
        hits = listing(token, f"{query} and trashed = false")
        if not hits:
            raise ConnectorError(f"no folder called {segment} in {'/'.join(walked) or 'My Drive'}")
        if len(hits) > 1:
            raise ConnectorError(
                f"{len(hits)} folders called {segment} in {'/'.join(walked) or 'My Drive'} — "
                "give the folder's link instead"
            )
        parent, walked = str(hits[0]["id"]), [*walked, segment.strip()]
    if parent == "root":
        raise ConnectorError("a folder, not all of My Drive")
    return parent, "/".join(walked)


def walk(token: str, folder_id: str) -> list[tuple[str, dict[str, Any]]]:
    """Every file under a folder, with its path inside it — breadth first, bounded."""
    out: list[tuple[str, dict[str, Any]]] = []
    queue = [(folder_id, "")]
    visited = 0
    while queue and visited < FOLDERS_MAX and len(out) < FILES_MAX:
        current, prefix = queue.pop(0)
        visited += 1
        for f in listing(token, f"'{current}' in parents and trashed = false"):
            if f["mimeType"] == FOLDER:
                queue.append((str(f["id"]), f"{prefix}{f['name']}/"))
            elif len(out) < FILES_MAX:
                out.append((prefix, f))
    return out


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ()._-]", "-", name).strip(" .") or "file"


class DriveConnector(Connector):
    kind = "drive"
    title = "Google Drive"
    blurb = (
        "Folders in your Google Drive, each on its own schedule: Docs, Sheets and Slides as "
        "Markdown, CSV and text, everything else as it is. Read-only."
    )
    auth = "oauth2"
    oauth = OAuth(
        provider="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=("https://www.googleapis.com/auth/drive.readonly",),
        # offline: a refresh token, so the sign-in outlives the hour. consent: Google
        # only hands the refresh token over on a consent screen, so ask for one each time.
        params=(("access_type", "offline"), ("prompt", "consent")),
    )
    folder = "drive"
    tick = 15
    fields = (
        Field(
            "folders",
            "Folders",
            rows=(
                Field("path", "Folder", help="Clients/Acme from the top of My Drive, or its link"),
                Field("every", "Check for changes", options=EVERY, help=EVERY_DEFAULT),
            ),
        ),
    )

    def _folders(self, config: dict[str, str]) -> list[dict[str, Any]]:
        folders: list[dict[str, Any]] = []
        for row in rows_of(config, "folders"):
            path = str(row.get("path", "")).strip().strip("/")
            if not path:
                raise ConnectorError("a folder: its path from My Drive, or its link")
            every = str(row.get("every", "")).strip() or EVERY_DEFAULT
            if every not in dict(EVERY):
                raise ConnectorError(f"{path}: an interval from the list")
            if any(f["path"] == path for f in folders):
                raise ConnectorError(f"{path} is listed twice")
            folders.append({"path": path, "every": int(every)})
        if not folders:
            raise ConnectorError("at least one folder")
        return folders

    def name(self, config: dict[str, str]) -> str:
        """The folders' own names — the last segment of a path, the id of a link."""
        names = []
        for f in self._folders(config):
            m = FOLDER_LINK.search(f["path"])
            names.append(m.group(1) if m else f["path"].rsplit("/", 1)[-1])
        return ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")

    def check_grant(self, secrets: dict[str, str]) -> str:
        token = secrets.get("access_token", "")
        me = call(token, "about", {"fields": "user(emailAddress,displayName)"}).get("user", {})
        return str(me.get("emailAddress") or me.get("displayName") or "Google Drive")

    def check(self, config: dict[str, str], grant: Grant | None) -> None:
        if grant is None:
            raise ConnectorError("a Google sign-in")
        for folder in self._folders(config):
            resolve(grant.token, folder["path"])

    def pull(self, config: dict[str, str], cursor: dict[str, Any], grant: Grant | None) -> Pull:
        """The folders that are due: what changed in each since last time, by
        `modifiedTime`, and what went away. The cursor is the clock and the memory."""
        if grant is None:
            raise ConnectorError("a Google sign-in")
        now = datetime.now(UTC)
        state: dict[str, dict[str, Any]] = dict(cursor.get("folders", {}))
        items: list[Item] = []
        removed: list[str] = []
        wanted = {f["path"] for f in self._folders(config)}
        for folder in self._folders(config):
            path, was = folder["path"], state.get(folder["path"])
            if was and now - datetime.fromisoformat(was["pulled"]) < timedelta(
                minutes=folder["every"]
            ):
                continue  # not its time yet
            folder_id, clean = resolve(grant.token, path)
            known: dict[str, str] = dict((was or {}).get("files", {}))
            seen: dict[str, str] = {}
            for prefix, f in walk(grant.token, folder_id):
                export, suffix = EXPORT.get(f["mimeType"], (None, ""))
                if f["mimeType"].startswith("application/vnd.google-apps.") and not export:
                    continue  # a form, a shortcut, a site: nothing to keep
                if not export and int(f.get("size") or 0) > SIZE_MAX:
                    continue
                stamp = str(f.get("modifiedTime", ""))
                if any(i.id == f["id"] for i in items):
                    continue  # reached through another folder of the list already
                seen[f["id"]] = stamp
                if known.get(f["id"]) == stamp:
                    continue  # as it was
                try:
                    content = download(grant.token, f["id"], export)
                except ConnectorError:
                    seen.pop(f["id"], None)  # try again next time, as if never seen
                    continue
                name = safe(f["name"])
                if suffix and not name.lower().endswith(suffix):
                    name += suffix
                items.append(
                    Item(id=f["id"], path=f"{safe_path(clean)}/{prefix}{name}", content=content)
                )
            removed.extend(fid for fid in known if fid not in seen)
            state[path] = {"pulled": now.isoformat(timespec="seconds"), "files": seen}
        for path in list(state):
            if path not in wanted:  # dropped from the list: its files go
                removed.extend(state.pop(path).get("files", {}))
        kept = {i.id for i in items}
        removed = [r for r in removed if r not in kept]
        return Pull(items=items, cursor={"folders": state}, complete=False, removed=removed)


def safe_path(clean: str) -> str:
    """A folder's place under the connection: its path, each segment made safe; a link
    or an id keeps the link's last segment."""
    if m := FOLDER_LINK.search(clean):
        return safe(m.group(1))
    return "/".join(safe(seg) for seg in clean.split("/") if seg.strip())
