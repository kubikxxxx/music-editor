"""
PracticeMaster — app entry point (původní fungující verze + start přes celou obrazovku bez 'Maximized')
- schovává ffmpeg/ffprobe konzole na Windows
- preferuje bundlované ffmpeg/ffprobe při PyInstalleru
- High-DPI pro Windows
- nastaví Qt Multimedia backend na 'windows'
- po spuštění natáhne okno na dostupnou plochu aktuální obrazovky (není maximalizované)
"""
from __future__ import annotations

import os, sys, subprocess

# --- pydub tiše + správný QtMultimedia backend ---
os.environ.setdefault("PYDUB_SILENCE_LOGGING", "1")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

# --- schovat ffmpeg/ffprobe konzole na Windows ---
if sys.platform.startswith("win"):
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESHOWWINDOW = 0x00000001
    SW_HIDE = 0
    _real_popen = subprocess.Popen

    def _quiet_popen(*args, **kwargs):
        try:
            cmd = kwargs.get("args", args[0])
            s = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
            s_low = s.lower()
            if "ffmpeg" in s_low or "ffprobe" in s_low:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= STARTF_USESHOWWINDOW
                si.wShowWindow = SW_HIDE
                kwargs["startupinfo"] = si
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        except Exception:
            pass
        return _real_popen(*args, **kwargs)

    subprocess.Popen = _quiet_popen  # type: ignore[assignment]

# --- preferuj bundlované ffmpeg/ffprobe při PyInstalleru ---
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    base = sys._MEIPASS  # type: ignore[attr-defined]
    ffmpeg_path = os.path.join(base, "ffmpeg.exe")
    ffprobe_path = os.path.join(base, "ffprobe.exe")
    if os.path.isfile(ffmpeg_path):
        os.environ["FFMPEG_BINARY"] = ffmpeg_path
    if os.path.isfile(ffprobe_path):
        os.environ["FFPROBE_BINARY"] = ffprobe_path

# NOTE: Import až po monkey-patchi subprocess, aby to pydub „viděl“.
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QCursor

from ui.main_window import MainWindow


def _enable_windows_hidpi():
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        # 1 = PROCESS_SYSTEM_DPI_AWARE (funguje s Qt6 autoscalingem)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def _place_window_on_available_screen(win: MainWindow, app: QApplication) -> None:
    """Nastaví okno na celou dostupnou plochu obrazovky, bez režimu Maximalizované.
       Aplikuje se dvakrát (po zobrazení a s malým odkladem), aby nic nepřepsalo velikost."""
    def _apply():
        try:
            # zruš případnou maximalizaci (pokud to někde uvnitř voláš)
            if win.isMaximized():
                win.showNormal()

            # obrazovka pod kurzorem, fallback na primární
            scr = QGuiApplication.screenAt(QCursor.pos()) or app.primaryScreen()
            if scr:
                ag = scr.availableGeometry()  # „celá“ plocha bez taskbaru
                win.move(ag.topLeft())
                win.resize(ag.size())
        except Exception:
            pass

    # 1) nejdřív okno ukaž (některé WM jinak ignorují resize/move)
    win.show()
    # 2) nastav geometrii hned po zobrazení
    QTimer.singleShot(0, _apply)
    # 3) a ještě jednou po chvilce (DPI/layou t mohou mezitím pohnout)
    QTimer.singleShot(120, _apply)


def main() -> int:
    _enable_windows_hidpi()

    app = QApplication(sys.argv)
    app.setApplicationName("PracticeMaster")
    app.setOrganizationName("PracticeMaster")

    aa = getattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None)
    if aa is not None:
        app.setAttribute(aa, True)

    win = MainWindow()  # i kdyby uvnitř volalo showMaximized(), přepíšeme to

    _place_window_on_available_screen(win, app)

    return app.exec()


if __name__ == "__main__":
    try:
        import multiprocessing as _mp
        _mp.freeze_support()
    except Exception:
        pass
    sys.exit(main())
