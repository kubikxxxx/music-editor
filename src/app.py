import os
os.environ["QT_MEDIA_BACKEND"] = "windows"
import sys

# === BOOTSTRAP: musí být před jakýmkoli importem, který by nepřímo natahoval QtMultimedia ===
# Nastav preferovaný backend a přidej Qt plugin/Qt6\bin cesty ještě před importem UI.
from PyQt6.QtCore import QLibraryInfo, QCoreApplication  # tohle NEtahá QtMultimedia

# 1) FFmpeg backend – stabilnější než Windows Media
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

# 2) Přidej cestu k Qt pluginům (aby Qt našlo plugins/multimedia)
QCoreApplication.addLibraryPath(
    QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
)

# 3) Přidej Qt6\bin do DLL vyhledávání (kvůli avcodec/avformat/avutil *.dll)
qt_bin = QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath)
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(qt_bin)

# --- až teď importuj UI, které uvnitř natahuje QtMultimedia ---
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
# =================================================================

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
