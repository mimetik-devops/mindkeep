"""Teams: who may open a tenant's bundles.

The identity provider says who a person is; Mindstash says which people work together.
That split is what keeps teams provider-agnostic — Kinde, Clerk and Keycloak each have
their own notion of an organisation with its own API, and building on any of them is
building on one of them. So membership, roles and invites are three tables here, keyed
by the provider's `sub`, and the provider is never asked about groups.

Every person has a personal team, made the first time they show up, whose id is the
same hash of their subject that already named their directory — so a wiki from before
teams *is* its owner's personal team, untouched. Other teams are made on purpose and
joined through an invite link: whoever opens it, signed in with whatever provider, is
added under the `sub` they signed in with.

Routes are gated by *permission*, and a role is a named set of permissions (GRANTS), so
a role can change or a new one appear without a route knowing. Today: viewer reads a
team's bundles; contributor writes to them — sources, answers, verifications; admin
runs the team — members, invites, its shelf of bundles; owner also renames and deletes
it, and a team keeps at least one. The provider's role claim is a separate thing, for
platform-level gating, and the two do not meet.
"""

import os
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.auth import CurrentProfile, CurrentUser, Profile
from app.db import Invite, Membership, Team, now, session
from app.runs import forget_tenant, utc

router = APIRouter()

Role = Literal["owner", "admin", "contributor", "viewer"]
ROLES: tuple[Role, ...] = ("owner", "admin", "contributor", "viewer")
INVITE_DAYS = 7

# What a route asks for. Add one here and grant it below; no route names a role.
Permission = Literal["read", "write", "bundles", "members", "team"]
GRANTS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "contributor": frozenset({"read", "write"}),
    "admin": frozenset({"read", "write", "bundles", "members"}),
    "owner": frozenset({"read", "write", "bundles", "members", "team"}),
}
# The sentence a refusal carries, per permission: says who can, not merely that you cannot.
REFUSED: dict[str, str] = {
    "read": "not found",
    "write": "viewers read — ask an admin for the contributor role",
    "bundles": "only owners and admins change a team's bundles",
    "members": "only owners and admins manage members",
    "team": "only an owner renames or deletes a team, or makes another owner",
}


def allowed(role: str | None, permission: Permission) -> bool:
    return permission in GRANTS.get(role or "", ())


def require(role: str | None, permission: Permission) -> None:
    if not allowed(role, permission):
        raise HTTPException(403, REFUSED[permission])


def personal_id(sub: str) -> str:
    """A person's own team: the hash that already names their directory (files.tenant_id)."""
    from app.files import tenant_id  # local import: files.py imports this module

    return tenant_id(sub)


def ensure_personal(sub: str, who: Profile) -> Team:
    """The personal team, made on first sight. Named after the person when the token
    says who they are, "Personal" when it does not."""
    with session() as s:
        team = s.get(Team, personal_id(sub))
        if team is None:
            team = Team(
                id=personal_id(sub),
                name=who.name or "Personal",
                personal=True,
                created_by=sub,
                created_at=now(),
            )
            s.add(team)
            s.add(_membership(team.id, sub, "owner", who))
            try:
                s.commit()
            except IntegrityError:
                # a sign-in fires several requests at once; the first to commit wins and
                # the rest read what it wrote — the unique keys are the lock
                s.rollback()
                team = s.get(Team, personal_id(sub))
                assert team is not None
            s.refresh(team)
        return team


def _membership(team_id: str, sub: str, role: Role, who: Profile) -> Membership:
    return Membership(
        team_id=team_id,
        sub=sub,
        role=role,
        name=who.name,
        email=who.email,
        added_at=now(),
    )


def role_of(team_id: str, sub: str) -> Role | None:
    """This person's role in the team, or None: not a member, or no such team."""
    with session() as s:
        found = s.scalar(
            select(Membership.role).where(Membership.team_id == team_id, Membership.sub == sub)
        )
        return found  # type: ignore[return-value]


def mine(sub: str, who: Profile) -> list[Team]:
    """Every team this person belongs to, personal first, then by name. The personal team
    is made here if it is missing, and the person's own snapshot refreshed — the token is
    the truth about their name, and this is where the team list reads it."""
    ensure_personal(sub, who)
    with session() as s:
        rows = s.execute(select(Membership).where(Membership.sub == sub)).scalars().all()
        for m in rows:
            if who.name and (m.name != who.name or m.email != who.email):
                m.name, m.email = who.name, who.email
        s.commit()
        teams = (
            s.execute(select(Team).where(Team.id.in_([m.team_id for m in rows]))).scalars().all()
        )
        return sorted(teams, key=lambda t: (not t.personal, t.name.lower(), t.id))


def create(name: str, sub: str, who: Profile) -> Team:
    with session() as s:
        team = Team(
            id=secrets.token_hex(16), name=name, personal=False, created_by=sub, created_at=now()
        )
        s.add(team)
        s.add(_membership(team.id, sub, "owner", who))
        s.commit()
        s.refresh(team)
        return team


# --- routes -------------------------------------------------------------------------------


def _as_dict(team: Team, role: str) -> dict[str, object]:
    """With what the role lets you do, so a UI gates on permissions and never on names."""
    return {
        "id": team.id,
        "name": team.name,
        "personal": team.personal,
        "role": role,
        "permissions": sorted(GRANTS.get(role, ())),
    }


@router.get("/teams")
def list_teams(user: CurrentUser, who: CurrentProfile) -> list[dict[str, object]]:
    """The teams you belong to, personal first. Made on first sight, so never empty."""
    teams = mine(user, who)
    return [_as_dict(t, role_of(t.id, user) or "viewer") for t in teams]


@router.post("/teams", status_code=201)
def create_team(
    user: CurrentUser, who: CurrentProfile, name: Annotated[str, Body(embed=True)]
) -> dict[str, object]:
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "a team name is 1 to 80 characters")
    team = create(name, user, who)
    # a team starts with a bundle, made now rather than on first visit — a bundle moved in
    # before anyone opened the team would otherwise be its only one
    from app.files import seed  # local import: files.py imports this module

    root = Path(os.environ.get("WIKI_ROOT", "/data")).resolve()
    seed(root / team.id / "default")
    return _as_dict(team, "owner")


def acting_as(team: str, user: CurrentUser) -> Role:
    """The caller's role in the team named by the URL. A team you are not in does not
    exist as far as you are concerned: 404, never 403."""
    role = role_of(team, user)
    if role is None:
        raise HTTPException(404, "not found")
    return role


ActingAs = Annotated[Role, Depends(acting_as)]


def needs(permission: Permission) -> Any:
    """A route parameter: `_: Annotated[None, needs("write")]` refuses anyone whose role
    in the team named by the URL does not carry the permission."""

    def check(role: ActingAs) -> None:
        require(role, permission)

    return Depends(check)


Members = Annotated[None, needs("members")]
Owns = Annotated[None, needs("team")]


@router.put("/teams/{team}")
def rename_team(
    team: str, acting: ActingAs, name: Annotated[str, Body(embed=True)]
) -> dict[str, object]:
    """Owners rename — a personal team too, since it was named from a token."""
    require(acting, "team")
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, "a team name is 1 to 80 characters")
    with session() as s:
        found = s.get(Team, team)
        assert found is not None  # acting_as found a membership, so the team exists
        found.name = name
        s.commit()
        return _as_dict(found, acting)


@router.delete("/teams/{team}")
def delete_team(team: str, acting: ActingAs) -> dict[str, str]:
    """Gone: the team, everyone's membership, its invites, its run history — and its
    bundles on disk. Owners only, never a personal team. The UI asks for the team's
    name to be typed first; the server trusts an owner who got that far."""
    require(acting, "team")
    with session() as s:
        found = s.get(Team, team)
        if found is None:
            raise HTTPException(404, "not found")
        if found.personal:
            raise HTTPException(409, "your personal team is yours to keep")
        s.execute(delete(Membership).where(Membership.team_id == team))
        s.execute(delete(Invite).where(Invite.team_id == team))
        s.delete(found)
        s.commit()
    forget_tenant(team)
    root = Path(os.environ.get("WIKI_ROOT", "/data")).resolve()
    shutil.rmtree(root / team, ignore_errors=True)
    return {"deleted": team}


@router.get("/teams/{team}/members")
def list_members(team: str, _: ActingAs) -> list[dict[str, object]]:
    with session() as s:
        rows = (
            s.execute(
                select(Membership).where(Membership.team_id == team).order_by(Membership.added_at)
            )
            .scalars()
            .all()
        )
        return [
            {
                "sub": m.sub,
                "name": m.name,
                "email": m.email,
                "role": m.role,
                "since": utc(m.added_at),
            }
            for m in rows
        ]


def _owners(s, team: str) -> int:  # type: ignore[no-untyped-def]
    return len(
        s.execute(
            select(Membership).where(Membership.team_id == team, Membership.role == "owner")
        ).all()
    )


@router.put("/teams/{team}/members/{sub}")
def set_role(
    team: str, sub: str, acting: ActingAs, role: Annotated[Role, Body(embed=True)]
) -> dict[str, str]:
    """Anyone with `members` sets roles; giving or taking ownership needs `team`."""
    require(acting, "members")
    if role == "owner":
        require(acting, "team")
    with session() as s:
        m = s.scalar(select(Membership).where(Membership.team_id == team, Membership.sub == sub))
        if m is None:
            raise HTTPException(404, "not a member")
        if m.role == "owner":
            require(acting, "team")
        if m.role == "owner" and role != "owner" and _owners(s, team) == 1:
            raise HTTPException(409, "a team keeps at least one owner")
        m.role = role
        s.commit()
    return {"sub": sub, "role": role}


@router.delete("/teams/{team}/members/{sub}")
def remove_member(team: str, sub: str, user: CurrentUser, acting: ActingAs) -> dict[str, str]:
    """Owners and admins remove others; anyone may leave — except the last owner, and
    except from a personal team, which is its owner and cannot be walked out of."""
    if sub != user:
        require(acting, "members")
    with session() as s:
        owned = s.get(Team, team)
        if owned is not None and owned.personal:
            raise HTTPException(409, "your personal team is yours to keep")
        m = s.scalar(select(Membership).where(Membership.team_id == team, Membership.sub == sub))
        if m is None:
            raise HTTPException(404, "not a member")
        if m.role == "owner" and sub != user:
            require(acting, "team")
        if m.role == "owner" and _owners(s, team) == 1:
            raise HTTPException(409, "a team keeps at least one owner")
        s.delete(m)
        s.commit()
    return {"removed": sub}


def _open(i: Invite | None) -> bool:
    """Not spent and not expired. Judged here rather than in SQL: SQLite hands back
    naive datetimes, and a handful of invites per team is nothing to filter."""
    return i is not None and i.accepted_by is None and utc(i.expires_at) > now()


def _invite_dict(i: Invite, accepted_name: str = "") -> dict[str, object]:
    state = "used" if i.accepted_by else ("open" if _open(i) else "expired")
    return {
        "token": i.token,
        "role": i.role,
        "created_by": i.created_by,
        "expires_at": utc(i.expires_at),
        "accepted_by": i.accepted_by,
        "accepted_at": utc(i.accepted_at) if i.accepted_at else None,
        "accepted_name": accepted_name,
        "state": state,
    }


@router.get("/teams/{team}/invites")
def list_invites(team: str, acting: ActingAs) -> list[dict[str, object]]:
    """Every invite, newest first, with what became of it: open, used (and by whom), or
    expired. A link that was used stays on the list marked so, rather than vanishing —
    the person who sent it wants to see that it landed."""
    require(acting, "members")
    with session() as s:
        rows = (
            s.execute(
                select(Invite).where(Invite.team_id == team).order_by(Invite.created_at.desc())
            )
            .scalars()
            .all()
        )
        names = {
            m.sub: m.name or m.email
            for m in s.execute(select(Membership).where(Membership.team_id == team)).scalars()
        }
        return [_invite_dict(i, names.get(i.accepted_by or "", "")) for i in rows]


@router.post("/teams/{team}/invites", status_code=201)
def create_invite(
    team: str,
    user: CurrentUser,
    acting: ActingAs,
    role: Annotated[Role, Body(embed=True)] = "contributor",
) -> dict[str, object]:
    """An invite is a link. Whoever opens it, signed in with whatever provider, joins as
    the `sub` they signed in with — which is the whole reason it needs no email and no
    provider API. Seven days, one use.

    Not into a personal team: that one is its owner's alone, and a team meant for
    sharing is made on purpose. Ruben's call, 2026-08-27."""
    require(acting, "members")
    if role == "owner":
        require(acting, "team")
    with session() as s:
        owner_only = s.get(Team, team)
        if owner_only is None or owner_only.personal:
            raise HTTPException(403, "a personal team takes no invites — create a team to share")
        invite = Invite(
            token=secrets.token_urlsafe(24),
            team_id=team,
            role=role,
            created_by=user,
            created_at=now(),
            expires_at=now() + timedelta(days=INVITE_DAYS),
        )
        s.add(invite)
        s.commit()
        s.refresh(invite)
        return _invite_dict(invite)


@router.delete("/teams/{team}/invites/{token}")
def revoke_invite(team: str, token: str, acting: ActingAs) -> dict[str, str]:
    require(acting, "members")
    with session() as s:
        invite = s.scalar(select(Invite).where(Invite.team_id == team, Invite.token == token))
        if invite is None or invite.accepted_by is not None:
            raise HTTPException(404, "no such open invite")
        s.delete(invite)
        s.commit()
    return {"revoked": token}


@router.get("/invites/{token}")
def peek_invite(token: str, _: CurrentUser) -> dict[str, object]:
    """What an invite is for, so the page can say "join Acme as a member" before accepting."""
    with session() as s:
        invite = s.scalar(select(Invite).where(Invite.token == token))
        if not _open(invite):
            raise HTTPException(404, "this invite is not open")
        assert invite is not None
        team = s.get(Team, invite.team_id)
        return {"team": {"id": team.id, "name": team.name} if team else None, "role": invite.role}


@router.post("/invites/{token}/accept")
def accept_invite(token: str, user: CurrentUser, who: CurrentProfile) -> dict[str, object]:
    """Join. Already a member: the invite is spent anyway, and the existing role stands."""
    with session() as s:
        invite = s.scalar(select(Invite).where(Invite.token == token))
        if not _open(invite):
            raise HTTPException(404, "this invite is not open")
        assert invite is not None
        team = s.get(Team, invite.team_id)
        if team is None:
            raise HTTPException(404, "this invite is not open")
        existing = s.scalar(
            select(Membership).where(Membership.team_id == team.id, Membership.sub == user)
        )
        role = existing.role if existing else invite.role
        if existing is None:
            s.add(_membership(team.id, user, invite.role, who))  # type: ignore[arg-type]
        invite.accepted_by = user
        invite.accepted_at = now()
        s.commit()
        return _as_dict(team, role)
