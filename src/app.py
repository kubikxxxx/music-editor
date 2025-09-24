import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("PracticeMaster")

    win = MainWindow()
    win.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
