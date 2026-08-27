"""Write the release version into pyproject.toml from the tag.

    python stamp.py v0.2.0      (from client/)

The tag is the only place a version is typed. pyproject.toml says 0.0.0 in git — what
a local build or `pip install -e .` gets — and the release workflow stamps the tag's
number over it before Briefcase reads it, so the installers, the package metadata and
the changelog all carry the tag's version without anyone editing a file.
"""

import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent / "pyproject.toml"


def stamp(tag: str) -> str:
    version = tag.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"{tag!r} is not a vX.Y.Z tag")
    text = PYPROJECT.read_text(encoding="utf-8")
    # the first `version =` line is [project]'s; Briefcase reads the same one
    new, count = re.subn(r'^version = "[^"]*"', f'version = "{version}"', text, count=1, flags=re.M)
    if count != 1:
        sys.exit("pyproject.toml has no version line to stamp")
    PYPROJECT.write_text(new, encoding="utf-8", newline="\n")
    return version


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print("version", stamp(sys.argv[1]))
