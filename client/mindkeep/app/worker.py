"""The one thread that syncs.

One, not one per bundle: the sync's state file is read-modify-written whole, so two
syncs at once would drop each other's state — and stale state is what decides that a
file was deleted. The server serialises per bundle anyway, so nothing is lost by taking
them in turn. Everything the person should see leaves here as a signal.
"""

import threading
import urllib.error

from PySide6.QtCore import QThread, Signal

from mindkeep import sync as engine
from mindkeep.app import config
from mindkeep.app.alerts import Alerts


class Worker(QThread):
    said = Signal(str)  # one line of what the sync did
    alert = Signal(str, str)  # title, text — a balloon
    status = Signal(str)  # the tray's status line
    signed_out = Signal()  # the token stopped working

    def __init__(self) -> None:
        super().__init__()
        self.alerts = Alerts()
        self.wake = threading.Event()
        self.paused = False
        self.stopping = False

    def sync_now(self) -> None:
        self.wake.set()

    def stop(self) -> None:
        self.stopping = True
        self.wake.set()

    def run(self) -> None:
        # the engine reports through module hooks; this thread is its only caller in the app
        engine.say = lambda *parts: self.said.emit(" ".join(str(p) for p in parts))
        engine.notify = lambda cfg, kind, text: self.alert.emit(
            f"Conflict in {cfg['bundle']}", text
        )
        while not self.stopping:
            if not self.paused:
                self.round()
            self.wake.wait(engine.INTERVAL)
            self.wake.clear()

    def round(self) -> None:
        cfg = config.load()
        if not cfg["token"] or not cfg["watch"]:
            self.status.emit("Not set up yet — open Settings")
            return
        for entry in cfg["watch"]:
            if self.stopping:
                return
            one = config.sync_config(cfg, entry)
            key = f"{entry['team']}/{entry['bundle']}"
            self.status.emit(f"Syncing {entry['name']} / {entry['bundle']}…")
            try:
                engine.sync(one)
                sources = engine.call_json(
                    one, f"teams/{one['team']}/bundles/{one['bundle']}/sources"
                )
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.status.emit("Signed out — open Settings to sign in again")
                    if self.alerts.dead_token():
                        self.alert.emit("Sign in again", "Your device token no longer works.")
                    self.signed_out.emit()
                    return
                self.said.emit(f"{key}: {e.code} {e.reason}")
                continue
            except (urllib.error.URLError, engine.Unreachable, OSError) as e:
                self.said.emit(f"{key}: {e}")
                self.status.emit("Cannot reach the server")
                if self.alerts.unreachable():
                    self.alert.emit("Cannot reach Mindkeep", f"{cfg['server']} is not answering.")
                return
            self.alerts.reachable()
            self.alerts.signed_in()
            for path in self.alerts.failures(key, sources):
                self.alert.emit(f"Ingest failed in {entry['bundle']}", path)
        self.status.emit(
            f"Watching {len(cfg['watch'])} bundle{'s' if len(cfg['watch']) != 1 else ''}"
        )
