import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from construction_manager.database import Database
from construction_manager.main_window import MainWindow


def configure_font(app: QApplication) -> None:
    """Prefer a bundled Windows CJK font so Chinese text renders consistently."""
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    )
    for path in candidates:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 10))
            return


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("施工排程")
    app.setOrganizationName("LocalScheduleManager")
    icon_path = (
        Path(__file__).resolve().parent
        / "construction_manager"
        / "assets"
        / "schedule_engine_logo_v2.png"
    )
    app_icon = QIcon(str(icon_path))
    app.setWindowIcon(app_icon)
    configure_font(app)

    database = Database()
    window = MainWindow(database)
    window.setWindowIcon(app_icon)
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
