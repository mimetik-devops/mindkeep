"""Start at login, the way each OS wants it: a Run key, a LaunchAgent, an autostart
.desktop. Nothing else is touched, and turning it off removes exactly what was added."""

import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

NAME = "Mindkeep"
LABEL = "io.mindkeep.app"


def command() -> list[str]:
    """How to launch this app again: the frozen binary itself, or the module."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "mindkeep.app"]


def plist_for(cmd: list[str]) -> bytes:
    return plistlib.dumps({"Label": LABEL, "ProgramArguments": cmd, "RunAtLoad": True})


def desktop_for(cmd: list[str]) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        f"Exec={shlex.join(cmd)}\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _desktop() -> Path:
    return Path.home() / ".config" / "autostart" / "mindkeep.desktop"


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, NAME)
                return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return _plist().exists()
    return _desktop().exists()


def set_enabled(on: bool) -> None:
    cmd = command()
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except OSError:
                    pass
        return
    target = _plist() if sys.platform == "darwin" else _desktop()
    if on:
        target.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            target.write_bytes(plist_for(cmd))
        else:
            target.write_text(desktop_for(cmd), encoding="utf-8")
    else:
        target.unlink(missing_ok=True)
