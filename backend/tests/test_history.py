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


def test_a_diff_since_a_commit_is_one_file_and_no_header_noise(tmp_path):
    home = bundle(tmp_path)
    (home / "raw" / "deck.md").write_text(
        "Alice is CTO\nBob is CFO\n", encoding="utf-8", newline="\n"
    )
    then = history.commit(home, "run 1")
    assert history.head(home) == then
    (home / "raw" / "deck.md").write_text(
        "Alice is CEO\nBob is CFO\n", encoding="utf-8", newline="\n"
    )
    (home / "wiki" / "x.md").write_text("unrelated\n", encoding="utf-8")
    history.commit(home, "before run 2")

    out = history.diff(home, then, "raw/deck.md")
    assert "-Alice is CTO" in out and "+Alice is CEO" in out
    assert "unrelated" not in out and "diff --git" not in out
    assert history.diff(home, then, "raw/nope.md") == ""


def test_take_back_reverts_the_run_and_puts_the_source_back_in_one_commit(tmp_path):
    home = bundle(tmp_path)
    history.commit(home, "seeded")
    (home / "raw" / "deck.md").write_text("v1", encoding="utf-8")
    before1 = history.commit(home, "before run 1")
    (home / "wiki" / "deck.md").write_text("# Deck\n", encoding="utf-8")
    run1 = history.commit(home, "run 1")
    (home / "raw" / "deck.md").write_text("v2", encoding="utf-8")
    history.commit(home, "before run 2")
    (home / "wiki" / "deck.md").write_text("# Deck v2\n", encoding="utf-8")
    run2 = history.commit(home, "run 2")

    assert history.blob(home, before1, "raw/deck.md") != history.blob(home, "HEAD", "raw/deck.md")
    assert history.blob(home, "HEAD", "raw/nope.md") == ""

    # the second run back: the page to its first version, the source to v1
    history.take_back(home, run2, "undo run 2", restore=(before1, "raw/deck.md"))
    assert (home / "wiki" / "deck.md").read_text(encoding="utf-8") == "# Deck\n"
    assert (home / "raw" / "deck.md").read_text(encoding="utf-8") == "v1"
    # then the first: the page and the source gone
    history.take_back(home, run1, "undo run 1", remove="raw/deck.md")
    assert not (home / "wiki" / "deck.md").exists()
    assert not (home / "raw" / "deck.md").exists()
    assert history.commit(home, "clean") == ""


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
