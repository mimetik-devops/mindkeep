"""The settings window: where the server is, who you are, where the folders go, and
which bundles to watch. Network work — signing in, listing teams — runs off the UI
thread so the window never freezes on a slow server."""

import urllib.error
from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from mindstash import connect
from mindstash import sync as engine
from mindstash.app import config


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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mindstash settings")
        self.setMinimumWidth(480)
        self.cfg = config.load()
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

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        head = QHBoxLayout()
        head.addStretch()
        head.addWidget(self.refresh)
        layout.addLayout(head)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)

        if self.cfg["token"]:
            self.load_teams()
        else:
            self.note.setText("Sign in to see your teams and bundles.")

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
