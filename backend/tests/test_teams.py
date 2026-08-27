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
    invite = client.post(f"/teams/{team}/invites", json={"role": "member"}).json()

    peek = client.get(f"/invites/{invite['token']}", headers=as_("bob")).json()
    assert peek == {"team": {"id": team, "name": "Acme"}, "role": "member"}
    joined = client.post(f"/invites/{invite['token']}/accept", headers=as_("bob")).json()
    assert joined["id"] == team and joined["role"] == "member"

    assert client.get(f"/teams/{team}/bundles", headers=as_("bob")).json() == ["default"]
    members = client.get(f"/teams/{team}/members").json()
    assert [(m["sub"], m["role"]) for m in members] == [("alice", "owner"), ("bob", "member")]
    # one use
    assert (
        client.post(f"/invites/{invite['token']}/accept", headers=as_("carol")).status_code == 404
    )
    assert client.get(f"/teams/{team}/invites").json() == []


def test_a_personal_team_takes_no_invites(client):
    """Yours alone; a team meant for sharing is made on purpose."""
    [mine] = [t["id"] for t in client.get("/teams").json()]
    refused = client.post(f"/teams/{mine}/invites")
    assert refused.status_code == 403
    assert "create a team" in refused.json()["detail"]
    assert client.get(f"/teams/{mine}/invites").json() == []


def test_an_expired_invite_is_not_open(client, monkeypatch):
    from app import teams

    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    monkeypatch.setattr(teams, "INVITE_DAYS", 0)
    token = client.post(f"/teams/{team}/invites").json()["token"]
    assert client.get(f"/invites/{token}", headers=as_("bob")).status_code == 404
    assert client.post(f"/invites/{token}/accept", headers=as_("bob")).status_code == 404


def test_members_do_not_manage_admins_do_owners_do_everything(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]

    def join(user: str, role: str) -> None:
        token = client.post(f"/teams/{team}/invites", json={"role": role}).json()["token"]
        assert client.post(f"/invites/{token}/accept", headers=as_(user)).status_code == 200

    join("bob", "admin")
    join("carol", "member")

    # a member sees the team but manages nothing
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

    # the owner does, and hands ownership on
    assert client.put(f"/teams/{team}/members/bob", json={"role": "owner"}).status_code == 200
    assert client.delete(f"/teams/{team}/members/carol").status_code == 200
    assert [m["sub"] for m in client.get(f"/teams/{team}/members").json()] == ["alice", "bob"]


def test_a_team_keeps_at_least_one_owner(client):
    team = client.post("/teams", json={"name": "Acme"}).json()["id"]
    assert client.put(f"/teams/{team}/members/alice", json={"role": "member"}).status_code == 409
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
