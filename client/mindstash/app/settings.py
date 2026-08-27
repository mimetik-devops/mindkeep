"""The app's window: a Settings tab — where the server is, who you are, where the
folders go, which bundles to watch — and a Log tab showing what the sync is doing.
Network work — signing in, listing teams — runs off the UI thread so the window never
freezes on a slow server."""

import urllib.error
from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mindstash import connect
from mindstash import sync as engine
from mindstash.app import config
from mindstash.app.log import Log


class Task(QThread):
    """Run one function off the UI thread; hand back its result or its error text."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self.work = work

    def run(self) -> None:
        try:
            self.done.emit(self.work())
        except urllib.error.HTTPError as e:
            self.failed.emit("The token was refused." if e.code == 401 else f"{e.code} {e.reason}")
        except Exception as e:  # noqa: BLE001 - shown to the person, whatever it was
            self.failed.emit(str(e) or type(e).__name__)


def teams_and_bundles(server: str, token: str) -> list[dict]:
    """Every team you are in, each with its bundles."""
    probe = {"server": server, "token": token}
    teams = engine.call_json(probe, "teams")
    for team in teams:
        team["bundles"] = engine.call_json(probe, f"teams/{team['id']}/bundles")
    return teams


class Settings(QDialog):
    saved = Signal()

    def __init__(self, log: Log) -> None:
        super().__init__()
        self.setWindowTitle("Mindstash")
        self.setMinimumSize(520, 460)
        self.cfg = config.load()
        self.log = log
        self.task: Task | None = None

        self.server = QLineEdit(self.cfg["server"])
        self.token = QLineEdit(self.cfg["token"])
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("paste a device token, or sign in through the browser")
        self.sign_in = QPushButton("Sign in through the browser")
        self.sign_in.clicked.connect(self.start_sign_in)
        self.root = QLineEdit(self.cfg["root"])
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.pick_root)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Bundles to watch"])
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.load_teams)

        form = QFormLayout()
        form.addRow("API address", self.server)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token)
        token_row.addWidget(self.sign_in)
        form.addRow("Device token", token_row)
        root_row = QHBoxLayout()
        root_row.addWidget(self.root)
        root_row.addWidget(browse)
        form.addRow("Keep the wikis in", root_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        settings = QWidget()
        page = QVBoxLayout(settings)
        page.addLayout(form)
        page.addWidget(self.note)
        head = QHBoxLayout()
        head.addStretch()
        head.addWidget(self.refresh)
        page.addLayout(head)
        page.addWidget(self.tree)

        # the log: what the worker said, from before this window opened
        self.lines = QPlainTextEdit(self.log.text())
        self.lines.setReadOnly(True)
        self.lines.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.lines.moveCursor(self.lines.textCursor().MoveOperation.End)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear_log)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log.text()))
        logpage = QWidget()
        logs = QVBoxLayout(logpage)
        logs.addWidget(self.lines)
        tools = QHBoxLayout()
        tools.addStretch()
        tools.addWidget(copy)
        tools.addWidget(clear)
        logs.addLayout(tools)

        self.tabs = QTabWidget()
        self.tabs.addTab(settings, "Settings")
        self.tabs.addTab(logpage, "Log")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

        if self.cfg["token"]:
            self.load_teams()
        else:
            self.note.setText("Sign in to see your teams and bundles.")

    # --- the log ---------------------------------------------------------------------------------
    def show_log(self) -> None:
        self.tabs.setCurrentIndex(1)

    def append(self, line: str) -> None:
        """A line the worker just said; the buffer already has it."""
        self.lines.appendPlainText(line)

    def clear_log(self) -> None:
        self.log.clear()
        self.lines.clear()

    # --- signing in --------------------------------------------------------------------------
    def start_sign_in(self) -> None:
        server = self.server.text().strip()
        self.busy("Waiting for the browser… click Connect there.")

        def work() -> str:
            web = connect.about(server)["web"]
            return connect.sign_in(web, connect.hostname())

        self.run(work, self.signed_in)

    def signed_in(self, token: object) -> None:
        self.token.setText(str(token))
        self.load_teams()

    # --- the teams -----------------------------------------------------------------------------
    def load_teams(self) -> None:
        server, token = self.server.text().strip(), self.token.text().strip()
        if not token:
            self.note.setText("Sign in to see your teams and bundles.")
            return
        self.busy("Loading your teams…")
        self.run(lambda: teams_and_bundles(server, token), self.show_teams)

    def show_teams(self, teams: object) -> None:
        self.note.setText("")
        self.tree.clear()
        for team in teams:  # type: ignore[attr-defined]
            branch = QTreeWidgetItem([team["name"] + (" (personal)" if team["personal"] else "")])
            branch.setData(0, Qt.ItemDataRole.UserRole, (team["id"], team["name"]))
            branch.setFlags(branch.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for bundle in team["bundles"]:
                leaf = QTreeWidgetItem([bundle])
                leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                on = config.watched(self.cfg, team["id"], bundle) is not None
                leaf.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                branch.addChild(leaf)
            self.tree.addTopLevelItem(branch)
            branch.setExpanded(True)

    # --- plumbing ---------------------------------------------------------------------------------
    def run(self, work: Callable[[], object], then: Callable[[object], None]) -> None:
        self.task = Task(work)
        self.task.done.connect(then)
        self.task.failed.connect(self.failed)
        self.task.finished.connect(self.idle)
        self.task.start()

    def busy(self, text: str) -> None:
        self.note.setText(text)
        self.sign_in.setEnabled(False)
        self.refresh.setEnabled(False)

    def idle(self) -> None:
        self.sign_in.setEnabled(True)
        self.refresh.setEnabled(True)

    def failed(self, text: str) -> None:
        self.note.setText(f"That did not work: {text}")

    def pick_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Keep the wikis in", self.root.text())
        if folder:
            self.root.setText(folder)

    def save(self) -> None:
        self.cfg["server"] = self.server.text().strip()
        self.cfg["token"] = self.token.text().strip()
        self.cfg["root"] = self.root.text().strip() or self.cfg["root"]
        checked: set[tuple[str, str]] = set()
        for t in range(self.tree.topLevelItemCount()):
            branch = self.tree.topLevelItem(t)
            team, name = branch.data(0, Qt.ItemDataRole.UserRole)
            for b in range(branch.childCount()):
                leaf = branch.child(b)
                if leaf.checkState(0) == Qt.CheckState.Checked:
                    config.watch(self.cfg, team, name, leaf.text(0))
                    checked.add((team, leaf.text(0)))
        if self.tree.topLevelItemCount():  # an empty tree means nothing was listed, not "none"
            for entry in list(self.cfg["watch"]):
                if (entry["team"], entry["bundle"]) not in checked:
                    config.unwatch(self.cfg, entry["team"], entry["bundle"])
        config.save(self.cfg)
        self.saved.emit()
        self.accept()
