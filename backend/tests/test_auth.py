"""What is read off a verified token — without a provider in the loop."""

import pytest

from app import auth


def test_kinde_shaped_roles_are_read_by_default(monkeypatch):
    monkeypatch.delenv("AUTH_ROLE_CLAIM", raising=False)
    claims = {"roles": [{"id": "1", "key": "admin", "name": "Admin"}, {"key": "editor"}]}
    assert auth.role_from(claims) == "Admin, Editor"


@pytest.mark.parametrize(
    "found, shown",
    [
        (["owner", "billing"], "Owner, Billing"),  # a list of strings
        ("owner", "Owner"),  # a single string
        ([], "Member"),  # defined but empty
        (None, "Member"),  # not in the token at all
    ],
)
def test_other_providers_role_shapes_are_read_too(monkeypatch, found, shown):
    monkeypatch.setenv("AUTH_ROLE_CLAIM", "https://example.com/roles")
    assert auth.role_from({"https://example.com/roles": found}) == shown


def test_the_profile_comes_from_standard_claims():
    who = auth.profile_from(
        {"given_name": "Ada", "family_name": "Lovelace", "email": "ada@x.io", "picture": "p"}
    )
    assert (who.name, who.email, who.picture) == ("Ada Lovelace", "ada@x.io", "p")
    assert auth.profile_from({}) == auth.Profile()
    assert auth.profile_from({"first_name": "Ada"}).name == "Ada"  # the other spelling


@pytest.mark.parametrize(
    "sub", ["kp_4618ab86eaf84c1b879791a61908309a", "user_2abcDEF", "auth0|abc123", "3f2c-1d"]
)
def test_the_subjects_real_providers_issue_are_accepted(sub):
    assert auth.SAFE_SUB.match(sub)


@pytest.mark.parametrize("sub", ["a.b", "with space", "", "x" * 129])
def test_a_subject_no_provider_would_issue_is_not(sub):
    assert not auth.SAFE_SUB.match(sub)
