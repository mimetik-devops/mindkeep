"""The client's changelog, written from git rather than by hand.

    python changelog.py         (from client/; prints the entry, writes CHANGELOG.md)

The entry for the version in pyproject.toml is the subjects of the commits that touched
client/ since the previous `v*` tag — commit subjects here are written as sentences,
so they read as release notes. Briefcase ships the file inside the Linux package, and
the release workflow uses the same text as the release body. Nobody types it.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).parent


def git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], capture_output=True, encoding="utf-8", check=True, cwd=HERE
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def entry() -> str:
    version = tomllib.load((HERE / "pyproject.toml").open("rb"))["project"]["version"]
    # the previous release: the newest v* tag that is not this version's own tag
    tags = git("tag", "--list", "v*", "--sort=-v:refname")
    previous = next((t for t in tags if t != f"v{version}"), "")
    since = f"{previous}..HEAD" if previous else "HEAD"
    subjects = git("log", "--no-merges", "--format=%s", since, "--", ".")
    body = "\n".join(f"- {s}" for s in subjects) or "- No changes to the client."
    return f"## {version}\n\n{body}\n"


def main() -> None:
    text = entry()
    (HERE / "CHANGELOG.md").write_text(f"# Changelog\n\n{text}", encoding="utf-8", newline="\n")
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
