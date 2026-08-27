"""A run is a commit; an undo is a revert. Against real git — that is the point."""

import pytest

from app import history


def bundle(tmp_path):
    home = tmp_path / "b"
    (home / "raw").mkdir(parents=True)
    (home / "wiki").mkdir()
    (home / "index.md").write_text("# Index\n", encoding="utf-8", newline="\n")
    return home


def test_a_commit_is_everything_since_the_last_one_and_nothing_is_no_commit(tmp_path):
    home = bundle(tmp_path)
    first = history.commit(home, "seeded")
    assert len(first) >= 7
    assert history.commit(home, "again") == ""  # nothing changed

    (home / "raw" / "deck.md").write_text("x", encoding="utf-8")
    second = history.commit(home, "before run 1")
    assert second and second != first
    assert history.changed(home, second) == [{"status": "A", "path": "raw/deck.md"}]


def test_undoing_a_run_keeps_what_people_added_and_takes_back_what_the_agent_wrote(tmp_path):
    home = bundle(tmp_path)
    history.commit(home, "seeded")
    (home / "raw" / "deck.md").write_text("the deck", encoding="utf-8")
    history.commit(home, "before run 1")  # the upload, as people's change

    (home / "wiki" / "deck.md").write_text("# Deck\n", encoding="utf-8")
    (home / "index.md").write_text("# Index\n- deck\n", encoding="utf-8", newline="\n")
    run = history.commit(home, "run 1: raw/deck.md")

    undone = history.undo(home, run, "undo run 1")
    assert undone and undone != run
    assert (home / "raw" / "deck.md").read_text(encoding="utf-8") == "the deck"  # stays
    assert not (home / "wiki" / "deck.md").exists()  # gone
    assert (home / "index.md").read_text(encoding="utf-8") == "# Index\n"  # back
    assert sorted(r["path"] for r in history.changed(home, undone)) == ["index.md", "wiki/deck.md"]


def test_an_undo_that_a_later_run_wrote_over_is_refused_cleanly(tmp_path):
    home = bundle(tmp_path)
    history.commit(home, "seeded")
    (home / "wiki" / "jane.md").write_text("Jane is CTO\n", encoding="utf-8")
    first = history.commit(home, "run 1")
    (home / "wiki" / "jane.md").write_text("Jane is CEO\n", encoding="utf-8")
    history.commit(home, "run 2")

    with pytest.raises(history.Conflict):
        history.undo(home, first, "undo run 1")
    # the tree is left as it was, not half-reverted
    assert (home / "wiki" / "jane.md").read_text(encoding="utf-8") == "Jane is CEO\n"
    assert history.commit(home, "nothing pending") == ""
