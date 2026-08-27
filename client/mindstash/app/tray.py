"""The tray icon and its menu — the whole of the app's everyday face."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from mindstash.app import autostart

CLAY = "#c0603d"
CREAM = "#f6f1e9"


def icon() -> QIcon:
    """Drawn here rather than shipped: a clay square with the wordmark's M."""
    result = QIcon()
    for size in (16, 32, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CLAY))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        painter.setPen(QColor(CREAM))
        font = QFont("Arial", int(size * 0.55))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, size, size * 1.02), Qt.AlignmentFlag.AlignCenter, "M")
        painter.end()
        result.addPixmap(pixmap)
    return result


class Tray(QSystemTrayIcon):
    sync_now = Signal()
    paused = Signal(bool)
    settings = Signal()
    log = Signal()
    quit = Signal()

    def __init__(self) -> None:
        super().__init__(icon())
        self.setToolTip("Mindstash")
        self.menu = QMenu()
        self.status = self.menu.addAction("Starting…")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Sync now", self.sync_now.emit)
        self.pause = QAction("Pause", self.menu)
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self.paused.emit)
        self.menu.addAction(self.pause)
        self.folders = self.menu.addMenu("Open folder")
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.settings.emit)
        self.menu.addAction("Log…", self.log.emit)
        self.login = QAction("Start at login", self.menu)
        self.login.setCheckable(True)
        self.login.setChecked(autostart.enabled())
        self.login.toggled.connect(autostart.set_enabled)
        self.menu.addAction(self.login)
        self.menu.addSeparator()
        self.menu.addAction("Quit", self.quit.emit)
        self.setContextMenu(self.menu)
        self.activated.connect(self._clicked)

    def _clicked(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings.emit()

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.setToolTip(f"Mindstash — {text}")

    def set_folders(self, entries: list[dict]) -> None:
        self.folders.clear()
        for entry in entries:
            label = f"{entry['name']} / {entry['bundle']}"
            self.folders.addAction(label, lambda folder=entry["folder"]: self.open(folder))
        self.folders.setEnabled(bool(entries))

    @staticmethod
    def open(folder: str) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def notify(self, title: str, text: str) -> None:
        self.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 10000)
