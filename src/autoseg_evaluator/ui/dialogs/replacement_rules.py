"""Replacement Rules dialog — manage Find→Replace pairs applied before matching.

Rules persist in ``settings.json`` under ``replacement_rules`` as a list of
``{"find": str, "replace": str}`` dicts. The matching pipeline applies them
in declaration order (top-to-bottom).
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ReplacementRulesDialog(QDialog):
    """Edit the Find → Replace rule list.

    On accept, :meth:`rules` returns the new list of dicts (empty rows skipped).
    """

    def __init__(
        self,
        existing_rules: Sequence[dict] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Replacement Rules")
        self.resize(640, 420)
        self._result_rules: list[dict] = list(existing_rules)
        self._build_ui()
        self._populate(existing_rules)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Rewrite recurring organ-name patterns before matching. "
            "For example: <code>Musc_Constrict</code> → <code>Pharyngeal_Constrictor</code>. "
            "Rules apply top-to-bottom and are case-insensitive."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["Find", "Replace with"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, stretch=1)

        # Row-management buttons
        row_btns = QHBoxLayout()
        add_btn = QPushButton("Add Row")
        add_btn.clicked.connect(self._on_add_row)
        row_btns.addWidget(add_btn)

        del_btn = QPushButton("Remove Selected")
        del_btn.clicked.connect(self._on_remove_selected)
        row_btns.addWidget(del_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(lambda: self._table.setRowCount(0))
        row_btns.addWidget(clear_btn)
        row_btns.addStretch(1)
        layout.addLayout(row_btns)

        # OK/Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, rules: Sequence[dict]) -> None:
        for rule in rules:
            self._add_row(str(rule.get("find", "")), str(rule.get("replace", "")))
        if not rules:
            # Always start with at least one empty row so the user has somewhere to type
            self._add_row("", "")

    def _add_row(self, find: str, replace: str) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(find))
        self._table.setItem(row, 1, QTableWidgetItem(replace))

    def _on_add_row(self) -> None:
        self._add_row("", "")
        self._table.setCurrentCell(self._table.rowCount() - 1, 0)
        self._table.editItem(self._table.item(self._table.rowCount() - 1, 0))

    def _on_remove_selected(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        for r in rows:
            self._table.removeRow(r)

    def _on_accept(self) -> None:
        out: list[dict] = []
        for row in range(self._table.rowCount()):
            find_item = self._table.item(row, 0)
            replace_item = self._table.item(row, 1)
            find = (find_item.text() if find_item else "").strip()
            replace = (replace_item.text() if replace_item else "").strip()
            if not find:
                continue
            out.append({"find": find, "replace": replace})
        self._result_rules = out
        self.accept()

    def rules(self) -> list[dict]:
        """Return the rule list (only meaningful when the dialog was accepted)."""
        return list(self._result_rules)
