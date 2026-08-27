"""What the app has already told the person, so it never says the same thing twice.

A balloon is for what needs a person now. A failing ingest is announced once per file
and again only if it fails again after having been fixed; a server that is down is
announced once, after three misses in a row, and recovery is quiet.
"""

MISSES = 3


class Alerts:
    def __init__(self) -> None:
        self.failed: set[tuple[str, str]] = set()  # (bundle key, path) already announced
        self.misses = 0
        self.announced_down = False
        self.announced_dead = False

    def failures(self, key: str, sources: list[dict]) -> list[str]:
        """The paths whose ingest failed since last asked. A path that stopped failing is
        forgotten, so a later failure of the same file is news again."""
        failing = {s["path"] for s in sources if s.get("error") and not s.get("ingesting")}
        self.failed = {(k, p) for k, p in self.failed if k != key or p in failing}
        fresh = sorted(p for p in failing if (key, p) not in self.failed)
        self.failed.update((key, p) for p in fresh)
        return fresh

    def unreachable(self) -> bool:
        """True once, when the misses reach the threshold."""
        self.misses += 1
        if self.misses >= MISSES and not self.announced_down:
            self.announced_down = True
            return True
        return False

    def reachable(self) -> None:
        self.misses = 0
        self.announced_down = False

    def dead_token(self) -> bool:
        """True once per signing-out; signing in again resets it."""
        if self.announced_dead:
            return False
        self.announced_dead = True
        return True

    def signed_in(self) -> None:
        self.announced_dead = False
