"""Application bootstrap."""

from __future__ import annotations

import sys

from .. import APP_DISPLAY_NAME, APP_NAME, __version__


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..context import AppContext
    from .main_window import MainWindow

    app = QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    try:
        ctx = AppContext()
    except Exception as exc:  # e.g. an unwritable application-data folder
        QMessageBox.critical(None, APP_DISPLAY_NAME, f"Could not start: {exc}")
        return 1
    win = MainWindow(ctx)
    win.show()
    code = app.exec()
    ctx.close()
    return code
