"""Mindstash in the tray: keeps every bundle you watch in sync and says so only when a
person is needed. One instance per login; a second launch just opens the first's
settings. Where there is no tray to sit in, the settings window stands in for it."""

import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from mindstash import __version__
from mindstash.app import config, mark
from mindstash.app.log import Log
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
        self.log = Log()
        self.last_status = ""
        self.worker = Worker()
        self.tray = Tray() if QSystemTrayIcon.isSystemTrayAvailable() else None

        self.on_said(f"Mindstash {__version__}")  # the log opens with which release this is
        self.worker.said.connect(self.on_said)
        self.worker.status.connect(self.on_status)
        self.worker.alert.connect(self.on_alert)
        self.worker.signed_out.connect(self.open_settings)
        if self.tray:
            self.tray.sync_now.connect(self.worker.sync_now)
            self.tray.paused.connect(self.on_pause)
            self.tray.settings.connect(self.open_settings)
            self.tray.log.connect(self.open_log)
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

    def on_said(self, text: str) -> None:
        print(text, flush=True)
        line = self.log.add(text)
        if self.settings:
            self.settings.append(line)

    def on_status(self, text: str) -> None:
        if self.tray:
            self.tray.set_status(text)
        # a status is logged when it changes: "Watching 2 bundles" every 30 s is noise
        if text != self.last_status:
            self.last_status = text
            self.on_said(text)

    def on_alert(self, title: str, text: str) -> None:
        if self.tray:
            self.tray.notify(title, text)
        else:
            print(f"{title}: {text}", flush=True)
        self.on_said(f"! {title}: {text}")

    def on_pause(self, paused: bool) -> None:
        self.worker.paused = paused
        self.on_status("Paused" if paused else "Resuming…")
        if not paused:
            self.worker.sync_now()

    def open_settings(self) -> None:
        if self.settings is None:
            self.settings = Settings(self.log)
            self.settings.saved.connect(self.on_saved)
            self.settings.finished.connect(self.on_closed)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def open_log(self) -> None:
        self.open_settings()
        assert self.settings
        self.settings.show_log()

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
    if sys.platform == "win32":
        # Without its own application id the taskbar files these windows under
        # python.exe — its icon, its grouping. Set before any window exists.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(INSTANCE)
    qt = QApplication(sys.argv)
    qt.setApplicationName("Mindstash")
    qt.setWindowIcon(mark.icon())  # every window's title bar and taskbar entry
    qt.setQuitOnLastWindowClosed(False)
    if already_running():
        print("Mindstash is already running; its settings are open.", flush=True)
        return
    app = App(qt)
    qt.aboutToQuit.connect(app.worker.stop)
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
