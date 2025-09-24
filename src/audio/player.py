from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import QObject, QUrl, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer


class AudioPlayer(QObject):
    position_changed = pyqtSignal(int)   # ms (render time)
    duration_changed = pyqtSignal(int)   # ms (render time)
    media_loaded = pyqtSignal()          # emitne se, když je médium načtené (LoadedMedia)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._player = QMediaPlayer()
        self._audio_out = QAudioOutput()
        self._player.setAudioOutput(self._audio_out)

        # volume default 100 %
        self.set_volume(100)

        self._player.positionChanged.connect(lambda ms: self.position_changed.emit(int(ms)))
        self._player.durationChanged.connect(lambda ms: self.duration_changed.emit(int(ms)))
        self._player.mediaStatusChanged.connect(self._on_media_status)

        # loop (render-time ms)
        self._loop_a: Optional[int] = None
        self._loop_b: Optional[int] = None

        self._loop_timer = QTimer(self)
        self._loop_timer.setInterval(15)
        self._loop_timer.timeout.connect(self._tick_loop)
        self._loop_timer.start()

    # ---------- public API ----------
    def load(self, path: str, autostart: bool = False):
        # Nastavíme zdroj a případné autostart řeší nadřízený kód po media_loaded
        self._player.setSource(QUrl.fromLocalFile(path))
        if autostart:
            # Pro kompatibilitu – pokud někdo volá s autostart=True, spustíme po loadu
            def _auto():
                try:
                    self.media_loaded.disconnect(_auto)
                except Exception:
                    pass
                self.play()
            self.media_loaded.connect(_auto)

    def play(self):
        self._player.play()

    def pause(self):
        self._player.pause()

    def stop(self):
        self._player.stop()

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def seek(self, ms: int):
        self._player.setPosition(max(0, int(ms)))

    def duration_ms(self) -> int:
        return int(self._player.duration() or 0)

    def current_position_ms(self) -> int:
        return int(self._player.position() or 0)

    def set_loop_ms(self, a: Optional[int] = None, b: Optional[int] = None):
        if a is not None:
            self._loop_a = max(0, int(a))
        if b is not None:
            self._loop_b = max(0, int(b))
        # sanity: if both set and a>b, swap
        if self._loop_a is not None and self._loop_b is not None and self._loop_a > self._loop_b:
            self._loop_a, self._loop_b = self._loop_b, self._loop_a

    def clear_loop(self):
        self._loop_a = None
        self._loop_b = None

    def set_volume(self, percent: int):
        """percent 0..100"""
        p = max(0, min(100, int(percent)))
        # Qt6 QAudioOutput volume je 0..1 (lineární)
        self._audio_out.setVolume(p / 100.0)

    # ---------- interní ----------
    def _on_media_status(self, status):
        # Signál „LoadedMedia“ je spolehlivý moment k provedení seeku atd.
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.media_loaded.emit()

    def _tick_loop(self):
        if self._loop_a is None or self._loop_b is None:
            return
        pos = self.current_position_ms()
        if pos >= self._loop_b:
            # vrať se na A a pokračuj
            self.seek(self._loop_a)
