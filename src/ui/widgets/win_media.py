# src/ui/widgets/win_media.py
import sys

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes
    from PyQt6.QtCore import QAbstractNativeEventFilter

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd",    wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam",  wintypes.WPARAM),
            ("lParam",  wintypes.LPARAM),
            ("time",    wintypes.DWORD),
            ("pt",      _POINT),
        ]

    class WinMediaKeyFilter(QAbstractNativeEventFilter):
        """Přijímá jen globální WM_HOTKEY pro VK_MEDIA_PLAY_PAUSE a WM_APPCOMMAND.
        ŽÁDNÝ fallback na WM_KEYDOWN – tím vyloučíme falešné triggery na jakoukoli klávesu.
        """
        WM_APPCOMMAND = 0x0319
        WM_HOTKEY     = 0x0312

        VK_MEDIA_PLAY_PAUSE = 0xB3
        HOTKEY_ID_PLAYPAUSE = 0xA110

        APPCOMMAND_MEDIA_PLAY_PAUSE = 14
        APPCOMMAND_MEDIA_PLAY       = 46
        APPCOMMAND_MEDIA_PAUSE      = 47

        def __init__(self, on_playpause, on_play, on_pause, *, debug=True):
            super().__init__()
            self.on_playpause = on_playpause
            self.on_play = on_play
            self.on_pause = on_pause
            self.debug = bool(debug)
            self._hotkey_registered = False
            self._hotkey_hwnd = None

        def _log(self, msg: str):
            if self.debug:
                print(msg, flush=True)

        def register_global_playpause_hotkey(self, hwnd: int | None = None):
            user32 = ctypes.windll.user32
            if self._hotkey_registered:
                try:
                    user32.UnregisterHotKey(self._hotkey_hwnd or wintypes.HWND(0), self.HOTKEY_ID_PLAYPAUSE)
                except Exception:
                    pass
                self._hotkey_registered = False

            h = wintypes.HWND(hwnd) if hwnd else wintypes.HWND(0)
            ok = user32.RegisterHotKey(h, self.HOTKEY_ID_PLAYPAUSE, 0, self.VK_MEDIA_PLAY_PAUSE)
            if not ok:
                try:
                    gle = ctypes.GetLastError()
                    self._log(f"[HOTKEY] RegisterHotKey failed, GetLastError={gle}")
                except Exception:
                    pass
            self._hotkey_registered = bool(ok)
            self._hotkey_hwnd = h if ok else None
            self._log(f"[HOTKEY] Register VK_MEDIA_PLAY_PAUSE (hwnd={int(hwnd) if hwnd else 0}) -> {'OK' if ok else 'FAIL'}")

        def unregister_global_playpause_hotkey(self):
            if not self._hotkey_registered:
                return
            user32 = ctypes.windll.user32
            user32.UnregisterHotKey(self._hotkey_hwnd or wintypes.HWND(0), self.HOTKEY_ID_PLAYPAUSE)
            self._hotkey_registered = False
            self._hotkey_hwnd = None
            self._log("[HOTKEY] Unregistered")

        def nativeEventFilter(self, eventType, message):
            try:
                etype = bytes(eventType).decode(errors="ignore") if eventType is not None else ""
            except Exception:
                etype = str(eventType or "")
            if not etype.startswith("windows_"):
                return False, 0

            try:
                addr = int(message)
            except Exception:
                try:
                    addr = int(message.__int__())
                except Exception:
                    return False, 0

            msg = _MSG.from_address(addr)
            m = int(msg.message)
            wParam = int(msg.wParam)
            lParam = int(msg.lParam)

            if m == self.WM_HOTKEY:
                hot_id = wParam
                vk = (lParam >> 16) & 0xFFFF
                if hot_id == self.HOTKEY_ID_PLAYPAUSE and vk == self.VK_MEDIA_PLAY_PAUSE:
                    self.on_playpause(); return True, 0
                return False, 0

            if m == self.WM_APPCOMMAND:
                cmd = (lParam >> 16) & 0xFFFF
                if cmd == self.APPCOMMAND_MEDIA_PLAY_PAUSE:
                    self.on_playpause(); return True, 0
                if cmd == self.APPCOMMAND_MEDIA_PLAY:
                    self.on_play(); return True, 0
                if cmd == self.APPCOMMAND_MEDIA_PAUSE:
                    self.on_pause(); return True, 0
                return False, 0

            return False, 0
else:
    class WinMediaKeyFilter:  # type: ignore
        def __init__(self, *a, **kw): pass
        def register_global_playpause_hotkey(self, *a, **kw): pass
        def unregister_global_playpause_hotkey(self, *a, **kw): pass
