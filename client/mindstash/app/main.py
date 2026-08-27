"""Mindstash in the tray: keeps every bundle you watch in sync and says so only when a
person is needed. One instance per login; a second launch just opens the first's
settings. Where there is no tray to sit in, the settings window stands in for it."""

import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from mindstash.app import config
from mindstash.app.settings import Settings
from mindstash.app.tray import Tray
from mindstash.app.worker import Worker

INSTANCE = "io.mindstash.app"


def already_running() -> bool:
    """Hand the running instance a nudge to show its settings, if there is one."""
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return True
    return False


class App:
    def __init__(self, qt: QApplication) -> None:
        self.qt = qt
        self.settings: Settings | None = None
        self.worker = Worker()
        self.tray = Tray() if QSystemTrayIcon.isSystemTrayAvailable() else None

        self.worker.said.connect(lambda line: print(line, flush=True))
        self.worker.status.connect(self.on_status)
        self.worker.alert.connect(self.on_alert)
        self.worker.signed_out.connect(self.open_settings)
        if self.tray:
            self.tray.sync_now.connect(self.worker.sync_now)
            self.tray.paused.connect(self.on_pause)
            self.tray.settings.connect(self.open_settings)
            self.tray.quit.connect(self.quit)
            self.tray.set_folders(config.load()["watch"])
            self.tray.show()
        else:
            # nowhere to sit: the window is the app, and closing it ends it
            qt.setQuitOnLastWindowClosed(True)
            self.open_settings()

        self.server = QLocalServer()
        QLocalServer.removeServer(INSTANCE)  # a crash leaves the socket behind
        self.server.listen(INSTANCE)
        self.server.newConnection.connect(self.open_settings)

        cfg = config.load()
        if not cfg["token"] or not cfg["watch"]:
            self.open_settings()
        self.worker.start()

    def on_status(self, text: str) -> None:
        if self.tray:
            self.tray.set_status(text)

    def on_alert(self, title: str, text: str) -> None:
        if self.tray:
            self.tray.notify(title, text)
        else:
            print(f"{title}: {text}", flush=True)

    def on_pause(self, paused: bool) -> None:
        self.worker.paused = paused
        self.on_status("Paused" if paused else "Resuming…")
        if not paused:
            self.worker.sync_now()

    def open_settings(self) -> None:
        if self.settings is None:
            self.settings = Settings()
            self.settings.saved.connect(self.on_saved)
            self.settings.finished.connect(self.on_closed)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def on_saved(self) -> None:
        if self.tray:
            self.tray.set_folders(config.load()["watch"])
        self.worker.alerts.signed_in()
        self.worker.sync_now()

    def on_closed(self) -> None:
        self.settings = None

    def quit(self) -> None:
        self.worker.stop()
        self.worker.wait(5000)
        self.qt.quit()


def main() -> None:
    qt = QApplication(sys.argv)
    qt.setApplicationName("Mindstash")
    qt.setQuitOnLastWindowClosed(False)
    if already_running():
        print("Mindstash is already running; its settings are open.", flush=True)
        return
    app = App(qt)
    qt.aboutToQuit.connect(app.worker.stop)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
