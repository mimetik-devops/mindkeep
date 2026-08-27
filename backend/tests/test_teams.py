"""Teams: the app's own membership, keyed by the provider's subject and nothing else."""

from datetime import timedelta

from app.files import tenant_id

as_ = lambda user: {"x-test-user": user}  # noqa: E731


def test_everyone_has_a_personal_team_named_after_them(client):
    """Made on first sight; its id is the hash that already names the directory."""
    [team] = client.get("/teams").json()
    assert team == {
        "id": tenant_id("alice"),
        "name": "Ada Lovelace",
        "personal": True,
        "role": "owner",
        "permissions": ["bundles", "members", "read", "team", "write"],
    }
    assert client.get(f"/teams/{team['id']}/bundles").json() == ["default"]


def test_a_team_you_are_not_in_does_not_exist_for_you(client):
    """404, never 403 — a team id is not a fact to hand out."""
    mine = tenant_id("alice")
    assert client.get(f"/teams/{mine}/bundles", headers=as_("bob")).status_code == 404
    assert client.get(f"/teams/{mine}/members", headers=as_("bob")).status_code == 404
    assert client.get("/teams/nope/bundles").status_code == 404


def test_creating_a_team_makes_you_its_owner_with_a_bundle_to_start(client):
    team = client.post("/teams", json={"name": "Acme"}).json()
    assert team["role"] == "owner" and not team["personal"] and len(team["id"]) == 32
    assert [t["name"] for t in client.get("/teams").json()] == ["Ada Lovelace", "Acme"]
    assert client.get(f"/teams/{team['id']}/bundles").json() == ["default"]
    assert client.post("/teams", json={"name": " "}).status_code == 400


def test_an_invite_link_joins_whoever_opens_it(client):
    """No email, no provider API: the person who accepts is added under their own sub."""
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    invite = client.post(f"/teams/{team}/invites", json={"role": "contributor"}).json()

    peek = client.get(f"/invites/{invite['token']}", headers=as_("bob")).json()
    assert peek == {"team": {"id": team, "name": "Acme"}, "role": "contributor"}
    joined = client.post(f"/invites/{invite['token']}/accept", headers=as_("bob")).json()
    assert joined["id"] == team and joined["role"] == "contributor"

    assert client.get(f"/teams/{team}/bundles", headers=as_("bob")).json() == ["default"]
    members = client.get(f"/teams/{team}/members").json()
    assert [(m["sub"], m["role"]) for m in members] == [("alice", "owner"), ("bob", "contributor")]
    # one use — and the link stays on the list, marked as used and by whom
    assert (
        client.post(f"/invites/{invite['token']}/accept", headers=as_("carol")).status_code == 404
    )
    [used] = client.get(f"/teams/{team}/invites").json()
    assert (used["state"], used["accepted_by"], used["accepted_name"]) == (
        "used",
        "bob",
        "Ada Lovelace",  # every test user carries the fixture's profile
    )
    assert used["accepted_at"] is not None


def test_a_personal_team_takes_no_invites(client):
    """Yours alone; a team meant for sharing is made on purpose."""
    [mine] = [t["id"] for t in client.get("/teams").json()]
    refused = client.post(f"/teams/{mine}/invites")
    assert refused.status_code == 403
    assert "create a team" in refused.json()["detail"]
    assert client.get(f"/teams/{mine}/invites").json() == []


def test_you_cannot_leave_your_personal_team(client):
    [mine] = [t["id"] for t in client.get("/teams").json()]
    refused = client.delete(f"/teams/{mine}/members/alice")
    assert refused.status_code == 409
    assert "yours to keep" in refused.json()["detail"]
    assert client.get(f"/teams/{mine}/bundles").status_code == 200


def test_owners_and_admins_rename_a_team_and_members_do_not(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    token = client.post(f"/teams/{team}/invites").json()["token"]
    client.post(f"/invites/{token}/accept", headers=as_("bob"))

    assert (
        client.put(f"/teams/{team}", json={"name": "Acme Ltd"}, headers=as_("bob")).status_code
        == 403
    )
    assert client.put(f"/teams/{team}", json={"name": "Acme Ltd"}).json()["name"] == "Acme Ltd"
    assert client.put(f"/teams/{team}", json={"name": " "}).status_code == 400
    assert [t["name"] for t in client.get("/teams", headers=as_("bob")).json()][-1] == "Acme Ltd"
    # a personal team can be renamed too: its name came from a token
    [mine] = [t["id"] for t in client.get("/teams").json() if t["personal"]]
    assert client.put(f"/teams/{mine}", json={"name": "Home"}).json()["name"] == "Home"


def test_deleting_a_team_takes_everything_with_it(client, tmp_path):
    from app import runs

    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    token = client.post(f"/teams/{team}/invites").json()["token"]
    client.post(f"/invites/{token}/accept", headers=as_("bob"))
    client.post(f"/teams/{team}/bundles/default/raw/plan.md", content=b"x")
    assert (tmp_path / team / "default" / "raw" / "plan.md").is_file()
    runs.start(tmp_path / team / "default", "raw/plan.md", "m")  # some history to lose
    assert runs.latest(tmp_path / team / "default")

    assert client.delete(f"/teams/{team}", headers=as_("bob")).status_code == 403  # a member
    assert client.delete(f"/teams/{team}").status_code == 200

    assert not (tmp_path / team).exists()
    assert client.get(f"/teams/{team}/bundles").status_code == 404
    assert client.get(f"/teams/{team}/bundles", headers=as_("bob")).status_code == 404
    assert [t["id"] for t in client.get("/teams", headers=as_("bob")).json()] == [tenant_id("bob")]
    assert runs.latest(tmp_path / team / "default") == {}
    assert client.post(f"/invites/{token}/accept", headers=as_("carol")).status_code == 404


def test_a_personal_team_cannot_be_deleted(client):
    [mine] = [t["id"] for t in client.get("/teams").json()]
    assert client.delete(f"/teams/{mine}").status_code == 409


def test_a_bundle_moves_to_another_team_with_its_history(client, tmp_path):
    from app import runs

    mine = tenant_id("alice")
    acme = client.post("/teams", json={"name": "Acme"}).json()["id"]
    client.post(f"/teams/{mine}/bundles", json={"name": "notes"})
    client.post(f"/teams/{mine}/bundles/notes/raw/a.md", content=b"a")
    run = runs.start(tmp_path / mine / "notes", "raw/a.md", "m")
    runs.finish(tmp_path / mine / "notes", run, turns=1, chars=1)

    moved = client.put(f"/teams/{mine}/bundles/notes/team", json={"to": acme}).json()
    assert moved == {"team": acme, "bundle": "notes"}

    assert client.get(f"/teams/{mine}/bundles").json() == ["default"]
    assert client.get(f"/teams/{acme}/bundles").json() == ["default", "notes"]
    assert (tmp_path / acme / "notes" / "raw" / "a.md").read_bytes() == b"a"
    assert not (tmp_path / mine / "notes").exists()
    [src] = client.get(f"/teams/{acme}/bundles/notes/sources").json()
    assert src["path"] == "raw/a.md" and src["ingested"]  # the run row came along
    assert runs.latest(tmp_path / mine / "notes") == {}


def test_a_bundle_move_needs_a_free_name_and_a_quiet_bundle(client, monkeypatch):
    mine = tenant_id("alice")
    acme = client.post("/teams", json={"name": "Acme"}).json()["id"]
    # both teams start with `default`
    taken = client.put(f"/teams/{mine}/bundles/default/team", json={"to": acme})
    assert taken.status_code == 409 and "already has" in taken.json()["detail"]

    client.post(f"/teams/{mine}/bundles", json={"name": "notes"})
    monkeypatch.setattr("app.files.busy", lambda home: True)  # where the route looks it up
    mid_run = client.put(f"/teams/{mine}/bundles/notes/team", json={"to": acme})
    assert mid_run.status_code == 409 and "ingested" in mid_run.json()["detail"]


def test_a_bundle_move_needs_management_on_both_sides(client):
    acme = client.post("/teams", json={"name": "Acme"}).json()["id"]
    token = client.post(f"/teams/{acme}/invites").json()["token"]
    client.post(f"/invites/{token}/accept", headers=as_("bob"))
    bobs = tenant_id("bob")
    client.get(f"/teams/{bobs}/bundles", headers=as_("bob"))

    # bob is a member of Acme: he may not move its bundle out
    assert (
        client.put(
            f"/teams/{acme}/bundles/default/team", json={"to": bobs}, headers=as_("bob")
        ).status_code
        == 403
    )
    # alice manages her own team but is not in bob's at all: it does not exist for her
    client.post(f"/teams/{tenant_id('alice')}/bundles", json={"name": "notes"})
    assert (
        client.put(f"/teams/{tenant_id('alice')}/bundles/notes/team", json={"to": bobs}).status_code
        == 404
    )


def test_a_role_is_a_named_set_of_permissions(client):
    """The table is the contract: a route asks for a permission, never a role."""
    from app import teams

    assert teams.GRANTS == {
        "viewer": {"read"},
        "contributor": {"read", "write"},
        "admin": {"read", "write", "bundles", "members"},
        "owner": {"read", "write", "bundles", "members", "team"},
    }
    [mine] = client.get("/teams").json()
    assert mine["permissions"] == ["bundles", "members", "read", "team", "write"]


def test_a_viewer_reads_and_a_contributor_writes(client):
    """The four roles, at the bundle: viewer reads, contributor writes, admin shelves."""
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]

    def join(user: str, role: str) -> dict[str, str]:
        token = client.post(f"/teams/{team}/invites", json={"role": role}).json()["token"]
        assert client.post(f"/invites/{token}/accept", headers=as_(user)).status_code == 200
        return as_(user)

    viewer, contributor, admin = (
        join("bob", "viewer"),
        join("carol", "contributor"),
        join("dan", "admin"),
    )
    B = f"/teams/{team}/bundles/default"

    # everyone reads
    for who in (viewer, contributor, admin):
        assert client.get(f"{B}/tree", headers=who).status_code == 200
        assert client.get(f"{B}/files/index.md", headers=who).status_code == 200
    # a viewer changes nothing
    refused = client.post(f"{B}/raw/note.md", content=b"x", headers=viewer)
    assert refused.status_code == 403 and "viewers read" in refused.json()["detail"]
    assert client.put(f"{B}/files/todo.md", content=b"- [ ] q", headers=viewer).status_code == 403
    assert client.post(f"{B}/folders/papers", headers=viewer).status_code == 403
    assert client.post(f"{B}/lint", headers=viewer).status_code == 403
    # a contributor writes, but does not shelve
    assert client.post(f"{B}/raw/note.md", content=b"x", headers=contributor).status_code == 200
    assert (
        client.post(
            f"/teams/{team}/bundles", json={"name": "more"}, headers=contributor
        ).status_code
        == 403
    )
    assert client.put(f"{B}/lint", json={"hour": 4}, headers=contributor).status_code == 403
    # an admin shelves
    assert (
        client.post(f"/teams/{team}/bundles", json={"name": "more"}, headers=admin).status_code
        == 201
    )
    assert client.put(f"{B}/lint", json={"hour": 4}, headers=admin).status_code == 200


def test_an_expired_invite_is_not_open(client, monkeypatch):
    from app import teams

    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    monkeypatch.setattr(teams, "INVITE_DAYS", 0)
    token = client.post(f"/teams/{team}/invites").json()["token"]
    assert client.get(f"/invites/{token}", headers=as_("bob")).status_code == 404
    assert client.post(f"/invites/{token}/accept", headers=as_("bob")).status_code == 404
    assert [i["state"] for i in client.get(f"/teams/{team}/invites").json()] == ["expired"]


def test_contributors_do_not_manage_admins_do_owners_do_everything(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]

    def join(user: str, role: str) -> None:
        token = client.post(f"/teams/{team}/invites", json={"role": role}).json()["token"]
        assert client.post(f"/invites/{token}/accept", headers=as_(user)).status_code == 200

    join("bob", "admin")
    join("carol", "contributor")

    # a contributor sees the team but manages nothing
    assert client.get(f"/teams/{team}/members", headers=as_("carol")).status_code == 200
    assert client.post(f"/teams/{team}/invites", headers=as_("carol")).status_code == 403
    assert client.delete(f"/teams/{team}/members/bob", headers=as_("carol")).status_code == 403

    # an admin manages members and admins, never owners
    assert (
        client.put(
            f"/teams/{team}/members/carol", json={"role": "admin"}, headers=as_("bob")
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/teams/{team}/members/carol", json={"role": "owner"}, headers=as_("bob")
        ).status_code
        == 403
    )
    assert client.delete(f"/teams/{team}/members/alice", headers=as_("bob")).status_code == 403
    assert (
        client.post(
            f"/teams/{team}/invites", json={"role": "owner"}, headers=as_("bob")
        ).status_code
        == 403
    )

    # an admin does not rename or delete; the owner does, and hands ownership on
    assert (
        client.put(f"/teams/{team}", json={"name": "Acme Ltd"}, headers=as_("bob")).status_code
        == 403
    )
    assert client.delete(f"/teams/{team}", headers=as_("bob")).status_code == 403
    assert client.put(f"/teams/{team}/members/bob", json={"role": "owner"}).status_code == 200
    assert client.delete(f"/teams/{team}/members/carol").status_code == 200
    assert [m["sub"] for m in client.get(f"/teams/{team}/members").json()] == ["alice", "bob"]


def test_a_team_keeps_at_least_one_owner(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    assert (
        client.put(f"/teams/{team}/members/alice", json={"role": "contributor"}).status_code == 409
    )
    assert client.delete(f"/teams/{team}/members/alice").status_code == 409  # cannot even leave


def test_anyone_may_leave_a_team_they_do_not_own(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    token = client.post(f"/teams/{team}/invites").json()["token"]
    client.post(f"/invites/{token}/accept", headers=as_("bob"))

    assert client.delete(f"/teams/{team}/members/bob", headers=as_("bob")).status_code == 200
    assert client.get(f"/teams/{team}/bundles", headers=as_("bob")).status_code == 404


def test_an_invite_can_be_revoked_before_it_is_used(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    token = client.post(f"/teams/{team}/invites").json()["token"]
    assert [i["token"] for i in client.get(f"/teams/{team}/invites").json()] == [token]

    assert client.delete(f"/teams/{team}/invites/{token}").status_code == 200
    assert client.get(f"/teams/{team}/invites").json() == []
    assert client.post(f"/invites/{token}/accept", headers=as_("bob")).status_code == 404


def test_a_teams_work_is_kept_apart_from_its_owners_own(client, tmp_path):
    """Two directories, two histories: the personal wiki and the team's share nothing."""
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    client.post(f"/teams/{team}/bundles/default/raw/plan.md", content=b"team")
    client.post(f"{'/teams/' + tenant_id('alice')}/bundles/default/raw/diary.md", content=b"mine")

    assert (tmp_path / team / "default" / "raw" / "plan.md").read_bytes() == b"team"
    assert not (tmp_path / team / "default" / "raw" / "diary.md").exists()
    assert [s["path"] for s in client.get(f"/teams/{team}/bundles/default/sources").json()] == [
        "raw/plan.md"
    ]


def test_the_invite_window_is_a_week(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    invite = client.post(f"/teams/{team}/invites").json()
    from datetime import datetime

    expires = datetime.fromisoformat(invite["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(expires.tzinfo) <= timedelta(days=7)
