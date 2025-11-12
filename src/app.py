"""
PracticeMaster — app entry point (původní fungující verze + start přes celou obrazovku bez 'Maximized')
- schovává ffmpeg/ffprobe konzole na Windows (cíleně jen pro pydub)
- preferuje bundlované ffmpeg/ffprobe při PyInstalleru
- High-DPI pro Windows
- nastaví Qt Multimedia backend na 'windows'
- po spuštění natáhne okno na dostupnou plochu aktuální obrazovky (není maximalizované)
"""
from __future__ import annotations

import os, sys

# --- HARD-PIN stdlib subprocess (musí být před jakýmkoli dalším použitím) ---
import inspect, sysconfig, importlib.util
try:
    import subprocess as _sp_test
    if not inspect.isclass(getattr(_sp_test, "Popen", None)):
        raise ImportError("shadowed subprocess")
except Exception:
    stdlib_dir = sysconfig.get_paths().get("stdlib") or ""
    sp_path = os.path.join(stdlib_dir, "subprocess.py")
    if os.path.isfile(sp_path):
        spec = importlib.util.spec_from_file_location("subprocess", sp_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        assert spec and spec.loader
        spec.loader.exec_module(mod)  # type: ignore[arg-type]
        sys.modules["subprocess"] = mod
# teď je bezpečně stdlib:
import subprocess  # noqa: E402

# --- pydub tiše + správný QtMultimedia backend ---
os.environ.setdefault("PYDUB_SILENCE_LOGGING", "1")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

# --- preferuj bundlované ffmpeg/ffprobe při PyInstalleru ---
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    base = sys._MEIPASS  # type: ignore[attr-defined]
    ffmpeg_path = os.path.join(base, "ffmpeg.exe")
    ffprobe_path = os.path.join(base, "ffprobe.exe")
    if os.path.isfile(ffmpeg_path):
        os.environ["FFMPEG_BINARY"] = ffmpeg_path
    if os.path.isfile(ffprobe_path):
        os.environ["FFPROBE_BINARY"] = ffprobe_path

# --- (NOVĚ) tichý Popen jen pro pydub, NE globálně ---
def _patch_pydub_quiet_popen_for_windows():
    if not sys.platform.startswith("win"):
        return
    try:
        import pydub.utils as _pdu
        CREATE_NO_WINDOW = 0x08000000
        STARTF_USESHOWWINDOW = 0x00000001
        SW_HIDE = 0

        def _quiet_popen(*args, **kwargs):
            # skryj okno pouze pro ffmpeg/ffprobe volání pydubu
            try:
                cmd = kwargs.get("args", args[0])
                text = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
                low = text.lower()
                if "ffmpeg" in low or "ffprobe" in low:
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= STARTF_USESHOWWINDOW
                    si.wShowWindow = SW_HIDE
                    kwargs["startupinfo"] = si
                    kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
            except Exception:
                pass
            return subprocess.Popen(*args, **kwargs)

        # pydub (nové verze) používá _pdu.Popen, které je alias na subprocess.Popen -> přepiš jen to
        if hasattr(_pdu, "Popen"):
            _pdu.Popen = _quiet_popen  # type: ignore[assignment]
    except Exception:
        pass

# NOTE: Qt importy až teď (pydub patch je nezávislý)
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
            if win.isMaximized():
                win.showNormal()
            scr = QGuiApplication.screenAt(QCursor.pos()) or app.primaryScreen()
            if scr:
                ag = scr.availableGeometry()
                win.move(ag.topLeft())
                win.resize(ag.size())
        except Exception:
            pass

    win.show()
    QTimer.singleShot(0, _apply)
    QTimer.singleShot(120, _apply)


def main() -> int:
    _enable_windows_hidpi()

    app = QApplication(sys.argv)
    app.setApplicationName("PracticeMaster")
    app.setOrganizationName("PracticeMaster")

    aa = getattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps", None)
    if aa is not None:
        app.setAttribute(aa, True)

    # Patchni pydub Popen až teď (po vytvoření venv, před prvním použitím pydubu):
    _patch_pydub_quiet_popen_for_windows()

    win = MainWindow()
    _place_window_on_available_screen(win, app)
    return app.exec()


if __name__ == "__main__":
    try:
        import multiprocessing as _mp
        _mp.freeze_support()
    except Exception:
        pass
    sys.exit(main())
