from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.auth import Profile, current_profile, current_role, current_user, device_token
from app.files import safe_path, tenant_id
from app.main import app


def team_of(user: str) -> str:
    """A person's personal team is the hash that names their directory."""
    return f"/teams/{tenant_id(user)}"


T = team_of("alice")
B = f"{T}/bundles/default"
PAGE = "---\ntype: Concept\ntitle: Jane\nsources:\n  - id: a\n    title: A\n---\n\nbody\n"


@pytest.fixture
def ingested(monkeypatch):
    """Captures ingest triggers instead of queueing them for Claude."""
    calls: list[tuple] = []
    monkeypatch.setattr("app.files.enqueue", lambda *a: calls.append(a))
    return calls


@pytest.fixture
def database(tmp_path, monkeypatch):
    """SQLite, so the suite needs no Postgres to run — the schema is plain enough."""
    from app.db import Base, engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    engine.cache_clear()
    Base.metadata.create_all(engine())
    yield
    engine.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch, ingested, database):
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    monkeypatch.setenv("DEVICE_SECRET", "test-secret")

    def as_user(request: Request) -> str:
        return request.headers.get("x-test-user", "alice")

    app.dependency_overrides[current_user] = as_user
    # role and profile come from the token's claims; the tests hand them in directly
    app.dependency_overrides[current_role] = lambda: "Owner"
    app.dependency_overrides[current_profile] = lambda: Profile(
        first_name="Ada", last_name="Lovelace", email="ada@example.com", picture="https://pics/ada.png"
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def page(client, tmp_path):
    """Write a wiki page straight to disk — no API route lets a user create one."""

    def write(rel: str, text: str = PAGE, user: str = "alice") -> None:
        # ensure the tenant exists
        client.get(f"{team_of(user)}/bundles", headers={"x-test-user": user})
        target = tmp_path / tenant_id(user) / "default" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    return write


def test_new_tenant_gets_one_seeded_bundle(client):
    assert client.get(f"{T}/bundles").json() == ["default"]
    assert set(client.get(f"{B}/tree").json()) == {"CLAUDE.md", "index.md", "log.md", "todo.md"}
    assert 'okf_version: "0.2"' in client.get(f"{B}/files/index.md").text


# both paths go under carol's team
@pytest.mark.parametrize("path", ["/bundles", "/bundles/default/files/index.md"])
def test_seeding_survives_a_login_burst(client, path):
    """A fresh sign-in fires several requests at once against an unseeded tenant.

    Both halves matter: two seeds racing used to crash, and a request arriving mid-seed
    used to read a tenant directory that existed but had no files in it yet.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = [
            pool.submit(client.get, team_of("carol") + path, headers={"x-test-user": "carol"})
            for _ in range(8)
        ]
        results = [c.result() for c in calls]

    assert [r.status_code for r in results] == [200] * 8


def test_tree_hashes_let_a_client_tell_what_changed(client, page):
    before = client.get(f"{B}/tree").json()
    page("wiki/note.md", "one")
    after = client.get(f"{B}/tree").json()
    assert after["index.md"] == before["index.md"]  # untouched files keep their hash
    assert len(after["wiki/note.md"]) == 64

    page("wiki/note.md", "two")
    assert client.get(f"{B}/tree").json()["wiki/note.md"] != after["wiki/note.md"]


def test_bundles_are_created_and_isolated(client, page):
    assert client.post(f"{T}/bundles", json={"name": "work"}).status_code == 201
    assert client.get(f"{T}/bundles").json() == ["default", "work"]
    assert client.post(f"{T}/bundles", json={"name": "work"}).status_code == 409

    page("wiki/note.md", "personal")
    assert "wiki/note.md" not in client.get(f"{T}/bundles/work/tree").json()


@pytest.mark.parametrize("name", ["../alice", "Work", "with space", ""])
def test_bad_bundle_names_are_rejected(client, name):
    assert client.post(f"{T}/bundles", json={"name": name}).status_code in (400, 422)


def test_unknown_bundle_is_404(client):
    assert client.get(f"{T}/bundles/nope/tree").status_code == 404


def test_the_user_owns_raw_and_the_agent_owns_wiki(client, page):
    # the user may correct their own source...
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.put(f"{B}/files/raw/notes.txt", content=b"corrected").status_code == 200
    assert client.get(f"{B}/files/raw/notes.txt").text == "corrected"

    # ...but a wiki page belongs to the agent, and no route lets a user write one
    page("wiki/jane.md")
    assert client.put(f"{B}/files/wiki/jane.md", content=b"mine now").status_code == 409
    assert "body" in client.get(f"{B}/files/wiki/jane.md").text


def test_binary_files_survive_the_round_trip(client):
    pdf = bytes(range(256)) * 4  # not valid utf-8; raw/ holds PDFs and images
    client.post(f"{B}/raw/scan.pdf", content=pdf)
    assert client.get(f"{B}/files/raw/scan.pdf").content == pdf


def test_upload_triggers_ingest(client, ingested):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert [c[1] for c in ingested] == ["raw/notes.txt"]
    assert ingested[0][0].name == "default"  # scoped to the bundle, not the tenant


def test_uploads_never_clobber_an_existing_source(client):
    first = client.post(f"{B}/raw/notes.txt", content=b"one").json()
    second = client.post(f"{B}/raw/notes.txt", content=b"two").json()
    assert first["path"] == "raw/notes.txt"
    assert second["path"] == "raw/notes-2.txt"
    assert client.get(f"{B}/files/raw/notes.txt").text == "one"


@pytest.mark.parametrize(
    ("upload", "stored"),
    [
        ("Believe what repeats.md", "raw/Believe what repeats.md"),
        ("Q3 report (final).pdf", "raw/Q3 report (final).pdf"),
        ("weird*name.txt", "raw/weird-name.txt"),  # sanitised, but still recognisable
        ("...", "raw/upload"),
    ],
)
def test_uploads_keep_their_human_name(client, upload, stored):
    assert client.post(f"{B}/raw/{upload}", content=b"x").json()["path"] == stored


def test_sources_can_be_organised_into_folders(client, ingested):
    """raw/ is the user's, and someone tidying their own material keeps their structure."""
    got = client.post(f"{B}/raw/papers/2026/synthetic users.md", content=b"x").json()
    assert got["path"] == "raw/papers/2026/synthetic users.md"
    assert client.get(f"{B}/files/raw/papers/2026/synthetic users.md").text == "x"
    assert [c[1] for c in ingested] == ["raw/papers/2026/synthetic users.md"]

    # nested sources are listed, and the folder goes when its last file does
    assert [s["path"] for s in client.get(f"{B}/sources").json()] == [
        "raw/papers/2026/synthetic users.md"
    ]
    client.delete(f"{B}/raw/papers/2026/synthetic users.md")
    assert client.get(f"{B}/sources").json() == []
    assert not [p for p in client.get(f"{B}/tree").json() if p.startswith("raw/")]


@pytest.mark.parametrize("upload", ["%2e%2e%2fetc%2fpasswd", "..%2Fpasswd", "../../x.txt"])
def test_an_upload_cannot_climb_out_of_raw(client, tmp_path, upload):
    """Folders are allowed; leaving raw/ is not.

    Asserted on where the bytes end up rather than on the status: an encoded `..` reaches
    the handler and is stripped there, while a literal one is normalised away by the URL
    itself and never routes. Both are fine; landing outside raw/ would not be.
    """
    client.post(f"{B}/raw/{upload}", content=b"climbed")

    written = [p for p in tmp_path.rglob("*") if p.is_file() and p.read_bytes() == b"climbed"]
    raw = tmp_path / tenant_id("alice") / "default" / "raw"
    assert all(raw in p.parents for p in written)


def test_a_source_reports_whether_it_was_ingested(client, page):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.get(f"{B}/sources").json() == [
        {
            "path": "raw/notes.txt",
            "ingesting": False,
            "seconds": 0,
            "pages": 0,
            "error": "",
            "took": 0,
            "note": "",
            "ingested": False,
            "moved": False,
        }
    ]

    # "ingested" is not a flag — it is whether a page cites the source
    page("wiki/summary.md", "---\ntype: Summary\n---\n\nFrom raw/notes.txt.\n")
    assert client.get(f"{B}/sources").json()[0]["pages"] == 1


def test_a_raw_source_can_be_deleted(client):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.delete(f"{B}/raw/notes.txt").json() == {"deleted": "raw/notes.txt"}
    assert "raw/notes.txt" not in client.get(f"{B}/tree").json()
    assert client.delete(f"{B}/raw/notes.txt").status_code == 404  # already gone


def test_a_user_cannot_delete_a_wiki_page(client, page):
    page("wiki/jane.md")
    # no delete route reaches wiki/, and raw/ cannot be walked out of
    assert client.delete(f"{B}/files/wiki/jane.md").status_code == 405
    assert client.delete(f"{B}/raw/%2e%2e%2fwiki%2fjane.md").status_code == 404
    assert "wiki/jane.md" in client.get(f"{B}/tree").json()


def test_no_sidecar_is_written(client):
    client.post(f"{B}/raw/notes.md", content=b"x")
    tree = client.get(f"{B}/tree").json()
    assert "raw/notes.md" in tree
    assert not [p for p in tree if p.endswith(".meta.md")]


def test_verify_stamps_the_authenticated_user(client, page):
    page("wiki/jane.md")
    assert client.post(f"{B}/verify/wiki/jane.md").json()["verified_by"] == "human:ada@example.com"

    after = client.get(f"{B}/files/wiki/jane.md").text
    assert "verified: { by: human:ada@example.com," in after
    assert "title: Jane" in after and "  - id: a" in after  # nothing else reformatted
    assert after.endswith("body\n")


def test_verify_replaces_rather_than_repeats(client, page):
    page("wiki/jane.md")
    client.post(f"{B}/verify/wiki/jane.md")
    client.post(f"{B}/verify/wiki/jane.md")
    assert client.get(f"{B}/files/wiki/jane.md").text.count("verified:") == 1


def test_verify_refuses_raw_and_frontmatterless_pages(client, page):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.post(f"{B}/verify/raw/notes.txt").status_code == 404

    page("wiki/plain.md", "no frontmatter")
    assert client.post(f"{B}/verify/wiki/plain.md").status_code == 409


def test_tenants_cannot_see_each_other(client, page):
    page("wiki/secret.md", "alice's")
    bob = {"x-test-user": "bob"}
    assert "wiki/secret.md" not in client.get(f"{B}/tree", headers=bob).json()
    assert client.get(f"{B}/files/wiki/secret.md", headers=bob).status_code == 404


def test_device_token_names_its_own_owner(client, monkeypatch):
    app.dependency_overrides.clear()  # exercise the real auth path
    alice = device_token("alice")
    assert alice.startswith("alice.")
    assert client.get("/device-token", headers={"Authorization": f"Bearer {alice}"}).json() == {
        "token": alice
    }

    # a token is only good for its own tenant, and rotating the secret revokes everyone
    assert device_token("bob") != alice
    monkeypatch.setenv("DEVICE_SECRET", "rotated")
    stale = client.get("/device-token", headers={"Authorization": f"Bearer {alice}"})
    assert stale.status_code == 401


@pytest.mark.parametrize("path", ["../bob/secret.md", "a/../../bob/secret.md", "/etc/passwd"])
def test_traversal_is_rejected(tmp_path, path):
    with pytest.raises(HTTPException):
        safe_path(tmp_path / tenant_id("alice") / "default", path)


# httpx strips literal `..` from URLs, so percent-encoded is what actually reaches the handler.
def test_encoded_traversal_is_rejected(client):
    escaped = "%2e%2e%2f%2e%2e%2fbob%2fx.md"
    assert client.get(f"{B}/files/{escaped}").status_code == 400
    assert client.put(f"{B}/files/{escaped}", content=b"x").status_code in (400, 409)


def test_a_docx_is_read_as_text_and_a_pdf_is_refused(tmp_path):
    import zipfile

    from app.files import readable_text

    docx = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr(
            "word/document.xml",
            "<w:body><w:p><w:t>Order Form</w:t></w:p><w:p><w:t>R&amp;D terms</w:t></w:p></w:body>",
        )
    assert readable_text(docx) == "Order Form\nR&D terms"

    # a real binary has no text to offer, and saying so beats returning mojibake
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n\xff\xfe\x00binary")
    assert readable_text(pdf) is None

    plain = tmp_path / "notes.md"
    plain.write_text("hello", encoding="utf-8")
    assert readable_text(plain) == "hello"


def test_run_history_outlives_the_process(client, database, tmp_path):
    """The point of the table: a duration survives the restart that killed its run."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")  # seed the tenant

    finished = runs.start(home, "raw/done.md", "claude-sonnet-5")
    runs.finish(home, finished, turns=7, chars=6367)
    runs.start(home, "raw/killed.md", "claude-sonnet-5")  # never finishes

    # mid-flight, one is running and the other reports what it took
    live = runs.latest(home)
    assert live["raw/done.md"].seconds is not None
    assert live["raw/killed.md"].finished_at is None

    # the process dies here; the next boot closes out whatever was open and says what it
    # was, so the caller can put it back in the queue
    assert runs.sweep_interrupted() == [(tenant_id("alice"), "default", "raw/killed.md")]
    after = runs.latest(home)
    assert after["raw/killed.md"].finished_at is not None
    assert "Interrupted" in after["raw/killed.md"].error
    assert after["raw/done.md"].error == ""
    assert after["raw/done.md"].turns == 7

    assert runs.sweep_interrupted() == []  # nothing left open to sweep


def test_a_failed_run_is_reported_with_its_reason(client, tmp_path):
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/notes.txt", content=b"one")
    runs.finish(home, runs.start(home, "raw/notes.txt", "m"), 0, 0, "credit balance is too low")

    source = client.get(f"{B}/sources").json()[0]
    assert source["error"] == "credit balance is too low"
    assert source["ingesting"] is False


def test_correcting_a_source_re_ingests_it(client, ingested):
    """A raw file is the user's to edit, and the pages built from it are now stale."""
    client.post(f"{B}/raw/notes.txt", content=b"one")
    client.put(f"{B}/files/raw/notes.txt", content=b"corrected")

    assert [c[1] for c in ingested] == ["raw/notes.txt"] * 2  # the upload, then the edit
    assert client.get(f"{B}/files/raw/notes.txt").text == "corrected"


def test_lint_runs_and_refuses_to_stack(client, tmp_path, ingested):
    """A lint is one more agent run over the bundle, recorded like any other."""
    from app import runs
    from app.ingest import LINT

    assert client.post(f"{B}/lint").json() == {"linting": "default"}
    assert [c[1] for c in ingested] == [LINT]

    # Two agents editing the same wiki at once would clobber each other, so an open
    # lint refuses the next one rather than queueing it.
    runs.start(tmp_path / tenant_id("alice") / "default", LINT, "m")
    assert client.post(f"{B}/lint").status_code == 409


def test_lint_state_reports_the_last_one(client, tmp_path, ingested):
    from app import runs
    from app.ingest import LINT

    client.get(f"{T}/bundles")  # seed alice/default
    home = tmp_path / tenant_id("alice") / "default"
    assert client.get(f"{B}/lint").json()["at"] == ""  # never linted

    run = runs.start(home, LINT, "m")
    assert client.get(f"{B}/lint").json()["linting"] is True
    runs.finish(home, run, 1, 10)
    assert client.get(f"{B}/lint").json()["linting"] is False


def test_a_running_agent_reports_what_it_is_doing(client, tmp_path, ingested):
    """The point of the note: a lint that reads for two minutes is not a hang."""
    from app import runs
    from app.ingest import LINT

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    run = runs.start(home, LINT, "m")

    runs.progress(home, run, note="3 · reading wiki/people/jane.md", turns=2)
    live = client.get(f"{B}/lint").json()
    assert live["note"] == "3 · reading wiki/people/jane.md"
    assert live["turns"] == 2

    # a finished run reports its duration, not a step it is no longer taking
    runs.finish(home, run, 4, 100)
    assert client.get(f"{B}/lint").json()["note"] == ""


def test_the_note_follows_an_ingest_too(client, tmp_path):
    from app import runs

    client.post(f"{B}/raw/notes.txt", content=b"one")  # ingest_safely is stubbed by the fixture
    home = tmp_path / tenant_id("alice") / "default"
    runs.progress(home, runs.start(home, "raw/notes.txt", "m"), note="1 · reading raw/notes.txt")

    source = client.get(f"{B}/sources").json()[0]
    assert source["ingesting"] is True
    assert source["note"] == "1 · reading raw/notes.txt"


def test_the_nightly_lint_runs_once_a_day(client, tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from app import runs, schedule
    from app.ingest import LINT

    client.get(f"{T}/bundles")  # seed alice/default
    home = tmp_path / tenant_id("alice") / "default"
    monkeypatch.setenv("LINT_HOUR", str(datetime.now(UTC).hour))

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    schedule.sweep()
    assert ran == [home]

    # record it as the sweep really would, then a second sweep the same day does nothing
    runs.finish(home, runs.start(home, LINT, "m"), 1, 10)
    ran.clear()
    schedule.sweep()
    assert ran == []

    # ...unless that lint failed, which means the wiki was never actually linted
    runs.finish(home, runs.start(home, LINT, "m"), 0, 0, error="killed by a deploy")
    schedule.sweep()
    assert ran == [home]


def test_the_next_automatic_lint_is_announced(client, tmp_path, monkeypatch):
    """The UI has to be able to say when this will happen next, or "nightly" is a promise."""
    from datetime import UTC, datetime, timedelta

    from app import runs, schedule
    from app.ingest import LINT

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    moment = datetime.now(UTC)

    # the next slot is always within a day, and always on the hour that was configured
    monkeypatch.setenv("LINT_HOUR", str((moment.hour + 2) % 24))
    soon = datetime.fromisoformat(schedule.next_run(home))
    assert moment < soon <= moment + timedelta(days=1)
    assert soon.hour == (moment.hour + 2) % 24

    # a bundle linted today waits for a later day, whichever way the slot falls
    runs.finish(home, runs.start(home, LINT, "m"), 1, 10)
    assert datetime.fromisoformat(schedule.next_run(home)).date() > moment.date()

    monkeypatch.setenv("LINT_HOUR", "off")
    assert schedule.next_run(home) == ""
    assert client.get(f"{B}/lint").json()["next"] == ""


def test_the_nightly_lint_is_off_outside_its_hour(client, monkeypatch):
    from datetime import UTC, datetime

    from app import schedule

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    monkeypatch.setenv("LINT_HOUR", str((datetime.now(UTC).hour + 5) % 24))
    schedule.sweep()
    monkeypatch.setenv("LINT_HOUR", "off")  # unparseable disables it entirely
    schedule.sweep()
    assert ran == []


def test_the_agent_runs_on_the_current_manual(client, tmp_path, monkeypatch):
    """CLAUDE.md ships with the app, so an old bundle must not run on an old manual."""
    from app import ingest as agent
    from app.files import TEMPLATES

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    (home / "CLAUDE.md").write_text("stale manual", encoding="utf-8")

    seen: dict = {}
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))
    agent.ingest(home, "raw/notes.txt")

    current = (TEMPLATES / "CLAUDE.md").read_text(encoding="utf-8")
    assert seen["system"] == current
    assert (home / "CLAUDE.md").read_text(encoding="utf-8") == current


class _FakeAnthropic:
    """Just enough of the SDK to see what the runner was given, and to end immediately."""

    def __init__(self, seen: dict) -> None:
        self.seen = seen

    def Anthropic(self):  # noqa: N802 - matching the SDK's name
        return self

    @property
    def beta(self):
        return self

    @property
    def messages(self):
        return self

    def tool_runner(self, **kwargs):
        self.seen.update(kwargs)
        return iter(())


def test_the_graph_route_names_the_gaps_between_areas(client, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")
    for side in ("cooking", "tax"):
        names = ["a", "b", "c"]
        for name in names:
            links = "".join(f"[{o}](/wiki/{side}/{o}.md)\n" for o in names if o != name)
            page = home / "wiki" / side / f"{name}.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(f"---\ntitle: {side} {name}\n---\n{links}", encoding="utf-8")

    got = client.get(f"{B}/graph").json()

    assert len(got["pages"]) == 6
    assert got["gaps"] == [{"a": 0, "b": 1, "links": 0, "expected": 3.0}]


def test_clean_says_what_a_name_will_be_stored_as(client):
    """One rule, on the server: the client renames a new file to this before uploading."""
    got = client.post(
        "/clean",
        json={"paths": ["papers/Futuros — how it works (arch, stack).md", "../x.md", ""]},
    ).json()

    assert got == {"paths": ["papers/Futuros - how it works (arch- stack).md", "x.md", "upload"]}


def test_a_bundle_chooses_its_own_lint_hour(client, tmp_path, monkeypatch):
    """The server default is only a default — the hour belongs to the bundle."""
    from datetime import UTC, datetime

    from app import schedule

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    monkeypatch.setenv("LINT_HOUR", "3")
    assert client.get(f"{B}/lint").json()["hour"] == 3

    assert client.put(f"{B}/lint", json={"hour": 20}).json() == {"hour": 20}
    assert client.get(f"{B}/lint").json()["hour"] == 20
    assert datetime.fromisoformat(client.get(f"{B}/lint").json()["next"]).hour == 20

    # a second bundle is unaffected, and still follows the server default
    client.post(f"{T}/bundles", json={"name": "work"})
    assert client.get(f"{T}/bundles/work/lint").json()["hour"] == 3

    # and the sweep honours the choice rather than the default
    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    client.put(f"{B}/lint", json={"hour": datetime.now(UTC).hour})
    schedule.sweep()
    assert ran == [home]


def test_a_bundle_can_switch_its_nightly_lint_off(client, monkeypatch):
    from datetime import UTC, datetime

    from app import schedule

    client.get(f"{T}/bundles")
    monkeypatch.setenv("LINT_HOUR", str(datetime.now(UTC).hour))  # would fire right now

    assert client.put(f"{B}/lint", json={"hour": -1}).json() == {"hour": -1}
    assert client.get(f"{B}/lint").json()["next"] == ""

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    schedule.sweep()
    assert ran == []

    # the manual button still works — "off" is about the schedule, not the feature
    assert client.post(f"{B}/lint").status_code == 200


@pytest.mark.parametrize("hour", [24, -2, 100])
def test_an_impossible_lint_hour_is_rejected(client, hour):
    assert client.put(f"{B}/lint", json={"hour": hour}).status_code == 400


def test_a_tenant_lives_under_a_hash_of_its_subject(client, tmp_path):
    """The subject is the provider's; the directory has to be everyone's."""
    client.get(f"{T}/bundles")
    assert (tmp_path / tenant_id("alice") / "default" / "index.md").is_file()
    assert not (tmp_path / "alice").exists()
    assert tenant_id("alice") != tenant_id("alicia")


def test_a_tenant_named_by_its_subject_is_moved_once(client, tmp_path):
    """Tenants from before the hash come along, run history and settings included."""
    from app import runs

    legacy = tmp_path / "alice" / "default"
    (legacy / "raw").mkdir(parents=True)
    (legacy / "index.md").write_text("# mine\n", encoding="utf-8", newline="\n")
    (legacy / "raw" / "deck.md").write_text("x", encoding="utf-8")
    run = runs.start(legacy, "raw/deck.md", "m")
    runs.finish(legacy, run, turns=1, chars=1)

    assert client.get(f"{B}/files/index.md").text == "# mine\n"
    assert not (tmp_path / "alice").exists()
    assert (tmp_path / tenant_id("alice") / "default" / "raw" / "deck.md").is_file()
    [deck] = client.get(f"{B}/sources").json()
    assert deck["path"] == "raw/deck.md" and deck["ingested"]


def test_the_profile_is_whatever_the_token_says(client):
    """Mindstash keeps no user table, so /me is the token's own claims."""
    me = client.get("/me").json()
    assert me["id"] == "alice"
    assert me["name"] == "Ada Lovelace"
    assert me["email"] == "ada@example.com"
    assert me["role"] == "Owner"
    assert me["picture"] == "https://pics/ada.png"


def test_a_token_with_no_profile_still_identifies_someone(client):
    """A device token, or an access token without profile claims: the id is always known."""
    app.dependency_overrides[current_profile] = lambda: Profile()
    me = client.get("/me").json()
    assert me["id"] == "alice"
    assert me["name"] == "" and me["picture"] == ""


def test_folders_can_be_made_and_removed_while_empty(client):
    """An empty folder has no file to carry it, so it needs a route of its own."""
    assert client.post(f"{B}/folders/papers/2026").status_code == 201
    assert client.get(f"{B}/folders").json() == ["papers", "papers/2026"]
    assert "raw/papers" not in client.get(f"{B}/tree").json()  # still a file map

    assert client.post(f"{B}/folders/papers/2026").status_code == 409  # already there
    assert client.delete(f"{B}/folders/papers/2026").status_code == 200
    assert client.get(f"{B}/folders").json() == []  # the emptied parent goes too


def test_a_folder_with_a_source_in_it_is_not_deleted_by_accident(client):
    client.post(f"{B}/raw/papers/note.md", content=b"x")
    assert client.delete(f"{B}/folders/papers").status_code == 409
    assert client.get(f"{B}/files/raw/papers/note.md").text == "x"


def test_moving_a_source_is_free(client, page, ingested):
    """Reorganising a folder must not cost an agent run per file.

    The citation left pointing at the old path is the lint's to repoint — the manual
    tells it that a file of the same name elsewhere under raw/ moved rather than went.
    """
    client.post(f"{B}/raw/note.md", content=b"x")
    page("wiki/summary.md", "---\ntype: Summary\n---\n\nFrom raw/note.md.\n")
    ingested.clear()  # the upload's own ingest

    got = client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    assert got.json() == {"from": "raw/note.md", "to": "raw/papers/note.md"}
    assert client.get(f"{B}/files/raw/papers/note.md").text == "x"
    assert client.get(f"{B}/files/raw/note.md").status_code == 404
    assert ingested == []


@pytest.mark.parametrize(
    ("source", "target", "status"),
    [
        ("raw/gone.md", "raw/a.md", 404),  # no such source
        ("wiki/jane.md", "raw/a.md", 404),  # the agent's half is not ours to move
        ("raw/note.md", "wiki/jane.md", 400),  # ...and not ours to move into
    ],
)
def test_a_move_stays_inside_raw(client, page, source, target, status):
    client.post(f"{B}/raw/note.md", content=b"x")
    page("wiki/jane.md")
    assert client.post(f"{B}/move", json={"source": source, "target": target}).status_code == status


def test_a_move_is_recorded_for_the_next_lint(client, tmp_path):
    """The server did the moving, so it knows exactly what moved — the lint should not guess."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})

    assert [(o, n) for _, o, n in runs.pending_moves(home)] == [
        ("raw/note.md", "raw/papers/note.md")
    ]


def test_a_chain_of_moves_collapses_to_one_fact(client, tmp_path):
    """A page citing the first path only needs to know the last one."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/a/note.md"})
    client.post(f"{B}/move", json={"source": "raw/a/note.md", "target": "raw/b/note.md"})
    assert [(o, n) for _, o, n in runs.pending_moves(home)] == [("raw/note.md", "raw/b/note.md")]

    # ...and a file put back where it started leaves nothing to repoint
    client.post(f"{B}/move", json={"source": "raw/b/note.md", "target": "raw/note.md"})
    assert runs.pending_moves(home) == []


def test_deleting_a_moved_source_drops_the_hint(client, tmp_path):
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    client.delete(f"{B}/raw/papers/note.md")
    assert runs.pending_moves(home) == []  # nowhere left to repoint anything to


def test_the_lint_is_told_what_moved_and_forgets_only_when_it_worked(client, tmp_path, monkeypatch):
    """An interrupted lint must not swallow the hint it never got to act on."""
    from app import ingest as agent
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})

    seen: dict = {}
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))

    def die(*_a, **_k):
        raise RuntimeError("killed by a deploy")

    monkeypatch.setattr(agent, "ingest", die)
    agent.ingest_safely(home, agent.LINT)
    assert len(runs.pending_moves(home)) == 1  # the run failed, so the hint survives

    monkeypatch.undo()
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))
    agent.ingest_safely(home, agent.LINT)
    assert "`raw/note.md` is now `raw/papers/note.md`" in seen["messages"][0]["content"]
    assert runs.pending_moves(home) == []  # dealt with


def test_a_moved_source_is_still_ingested(client, tmp_path, page):
    """A move must not read as "never ingested" — the run happened, the file just lives
    somewhere else now. Its history follows it."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 4, 900)
    assert client.get(f"{B}/sources").json()[0]["ingested"] is True

    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    after = client.get(f"{B}/sources").json()[0]
    assert after["path"] == "raw/papers/note.md"
    assert after["ingested"] is True  # the run row moved with the file
    assert runs.ingested_sources(home) == {"raw/papers/note.md"}  # not left at the old path
    assert after["moved"] is True  # the UI can say why the links still read the old path


def test_a_failed_reingest_does_not_unmake_the_pages_already_written(client, tmp_path):
    """"Ingested" is whether a run ever succeeded, not whether the last one did."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 4, 900)
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 0, 0, error="credit balance too low")

    source = client.get(f"{B}/sources").json()[0]
    assert source["ingested"] is True
    assert source["error"] == "credit balance too low"  # both facts, neither hiding the other


def test_a_bundle_keeps_its_two_halves_however_empty(client, tmp_path):
    """Deleting the last source must not leave a bundle with nowhere to put the next one."""
    import shutil

    client.post(f"{B}/raw/note.md", content=b"x")
    client.delete(f"{B}/raw/note.md")
    home = tmp_path / tenant_id("alice") / "default"
    assert (home / "raw").is_dir() and (home / "wiki").is_dir()

    # and they come back if something outside the app removes them
    shutil.rmtree(home / "raw")
    shutil.rmtree(home / "wiki")
    assert client.get(f"{B}/tree").status_code == 200
    assert (home / "raw").is_dir() and (home / "wiki").is_dir()


def test_an_upload_never_blocks_on_the_ingest_it_starts(client, tmp_path, monkeypatch):
    """The bug behind a hung API: forty uploads meant forty background tasks parked on the
    ingest lock, each holding one of Starlette's forty threadpool threads."""
    import threading

    from app import ingest as agent

    started = threading.Event()
    release = threading.Event()

    def slow(home, source):
        started.set()
        release.wait(5)

    monkeypatch.setattr(agent, "ingest_safely", slow)
    monkeypatch.setattr("app.files.enqueue", agent.enqueue)  # the real queue, not the stub

    client.post(f"{B}/raw/first.md", content=b"x")
    assert started.wait(2), "the worker should have picked the first one up"

    # the second upload returns while the first is still running, and so does a read
    assert client.post(f"{B}/raw/second.md", content=b"y").status_code == 200
    assert client.get(f"{B}/sources").status_code == 200
    release.set()


def test_questions_are_a_shared_list_neither_side_owns(client, ingested):
    """todo.md is the third kind of file: the wiki agent writes it, the assistant ticks it
    off, a person edits it in the synced folder — and changing it re-ingests nothing,
    because it is a record about the knowledge rather than knowledge."""
    written = (
        "# Todo\n\n- [ ] Which figure is current, 85% or 92%?\n  Used 85% for now.\n"
    )
    assert client.put(f"{B}/files/todo.md", content=written.encode()).status_code == 200
    assert ingested == []  # emphatically not a source

    assert client.get(f"{B}/todos").json() == [
        {
            "id": 0,
            "done": False,
            "text": "Which figure is current, 85% or 92%?",
            "detail": "Used 85% for now.",
        }
    ]

    assert client.post(f"{B}/todos/0", json={"done": True}).json() == {"done": True}
    assert client.get(f"{B}/todos").json()[0]["done"] is True
    # ticking rewrites one checkbox and leaves every other byte alone
    assert "Used 85% for now." in client.get(f"{B}/files/todo.md").text
    assert client.post(f"{B}/todos/9", json={"done": True}).status_code == 404


def test_a_bundle_made_before_the_list_existed_gets_one(client, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{B}/tree")
    (home / "todo.md").unlink()

    assert client.get(f"{B}/todos").json() == []
    assert (home / "todo.md").is_file()  # the request that read it also restored it


def test_a_docx_can_be_read_as_the_agent_reads_it(client):
    """The viewer said "not markdown, open it elsewhere" for a file the backend can
    already extract — the agent had read it, and the web had not asked."""
    import io
    import zipfile

    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:body><w:p><w:t>Acuerdo de colaboraci&#243;n</w:t></w:p>"
            "<w:p><w:t>Robert</w:t></w:p></w:body>",
        )
    client.post(f"{B}/raw/agreement.docx", content=body.getvalue())

    got = client.get(f"{B}/text/raw/agreement.docx")
    assert got.status_code == 200
    assert "Acuerdo de colaboración" in got.text
    assert "Robert" in got.text
    # the bytes route still serves the original, which is what a download wants
    assert client.get(f"{B}/files/raw/agreement.docx").content == body.getvalue()


def test_a_format_we_cannot_read_says_so(client):
    client.post(f"{B}/raw/scan.pdf", content=bytes(range(256)))
    refused = client.get(f"{B}/text/raw/scan.pdf")
    assert refused.status_code == 415
    assert ".pdf" in refused.json()["detail"]
