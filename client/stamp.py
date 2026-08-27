"""Write the release version into the files that carry one, from the tag.

    python stamp.py v0.2.0      (from client/)

The tag is the only place a version is typed. In git, pyproject.toml and
mindstash/__init__.py both say 0.0.0 — what a local build or `pip install -e .` gets —
and the release workflow stamps the tag's number over both before Briefcase reads
them, so the installers, the package metadata, the changelog and the app's own "about"
all carry the tag's version without anyone editing a file.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
FILES = {
    "pyproject.toml": r'^version = "[^"]*"',  # the first one is [project]'s; Briefcase reads it
    "mindstash/__init__.py": r'^__version__ = "[^"]*"',
}


def stamp(tag: str, root: Path = HERE) -> str:
    version = tag.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"{tag!r} is not a vX.Y.Z tag")
    for name, pattern in FILES.items():
        path = root / name
        text = path.read_text(encoding="utf-8")
        field = pattern.split(" ")[0].lstrip("^")
        new, count = re.subn(pattern, f'{field} = "{version}"', text, count=1, flags=re.M)
        if count != 1:
            sys.exit(f"{name} has no version line to stamp")
        path.write_text(new, encoding="utf-8", newline="\n")
    return version


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print("version", stamp(sys.argv[1]))
