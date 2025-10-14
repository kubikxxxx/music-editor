import sys
import os
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

if os.name == "nt":
    os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PracticeMaster")

    win = MainWindow()

    win.show()

    screen = win.screen() or app.primaryScreen()
    rect = screen.availableGeometry()
    win.setGeometry(rect)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
