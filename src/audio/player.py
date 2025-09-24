from PyQt6.QtCore import QObject, pyqtSignal, QTimer
import os, tempfile, threading, time
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from pydub import AudioSegment  # pro dekódování MP3/FLAC → WAV


class AudioPlayer(QObject):
    position_changed = pyqtSignal(int)   # ms
    duration_changed = pyqtSignal(int)   # ms

    def __init__(self):
        super().__init__()
        self.current_source: Optional[str] = None

        # dočasný WAV (dekódovaný zdroj nebo tempo-varianta)
        self._tmp_wav: Optional[str] = None
        self._sf: Optional[sf.SoundFile] = None

        # stream
        self._stream: Optional[sd.OutputStream] = None
        self._lock = threading.Lock()

        # stav
        self._samplerate = 0
        self._channels = 2
        self._frames_total = 0
        self._frame_pos = 0
        self._playing = False

        # loop
        self._loop_a: Optional[int] = None  # v ms
        self._loop_b: Optional[int] = None  # v ms

        # UI ticker
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._on_tick)

    # ---------- public API ----------

    def load(self, path: str, autostart: bool = False):
        """Načti libovolné audio (mp3/wav/flac). Převod do dočasného WAV a otevření pro stream."""
        self.stop()
        self.current_source = path

        # Dekódování přes pydub (ffmpeg) → WAV
        audio = AudioSegment.from_file(path)
        self._tmp_wav = os.path.join(
            tempfile.gettempdir(),
            os.path.splitext(os.path.basename(path))[0] + ".pm_tmp.wav"
        )
        audio.export(self._tmp_wav, format="wav")

        # Otevři WAV přes soundfile
        self._sf = sf.SoundFile(self._tmp_wav, mode="r")
        self._samplerate = int(self._sf.samplerate)
        self._channels = int(self._sf.channels)
        self._frames_total = len(self._sf)
        self._frame_pos = 0

        self.duration_changed.emit(self._frames_to_ms(self._frames_total))

        if autostart:
            self.play()

    def play(self):
        if not self._sf:
            return
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # přesuň čtecí pozici na self._frame_pos
        self._sf.seek(self._frame_pos)

        self._stream = sd.OutputStream(
            samplerate=self._samplerate,
            channels=self._channels,
            dtype="float32",
            callback=self._callback,
            blocksize=0,  # automaticky
        )
        self._stream.start()
        self._playing = True
        self._timer.start()

    def pause(self):
        if self._stream and self._playing:
            self._stream.stop()
            self._playing = False
            self._timer.stop()

    def stop(self):
        self._timer.stop()
        self._playing = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._sf:
            try:
                self._sf.close()
            except Exception:
                pass
            self._sf = None
        self._frame_pos = 0

    def seek(self, pos_ms: int):
        if not self._sf:
            return
        pos_ms = max(0, min(pos_ms, self._frames_to_ms(self._frames_total)))
        with self._lock:
            self._frame_pos = self._ms_to_frames(pos_ms)
            self._sf.seek(self._frame_pos)
        if not self._playing:
            self.position_changed.emit(pos_ms)
        else:
            # stream běží → nic, callback načte od nové pozice
            pass

    def set_loop_point(self, which: str):
        cur_ms = self._frames_to_ms(self._frame_pos)
        if which == "A":
            self._loop_a = cur_ms
        elif which == "B":
            self._loop_b = cur_ms
        if self._loop_a is not None and self._loop_b is not None:
            if self._loop_a > self._loop_b:
                self._loop_a, self._loop_b = self._loop_b, self._loop_a

    def clear_loop(self):
        self._loop_a = None
        self._loop_b = None

    # ---------- internal ----------

    def _callback(self, outdata, frames, time_info, status):
        if status:
            # print(status)  # můžeš logovat
            pass
        if not self._sf:
            outdata[:] = 0
            return

        with self._lock:
            # loop kontrola – když jsme za B, skoč na A
            if self._loop_a is not None and self._loop_b is not None:
                if self._frame_pos >= self._ms_to_frames(self._loop_b):
                    self._frame_pos = self._ms_to_frames(self._loop_a)
                    self._sf.seek(self._frame_pos)

            # načti blok
            need = frames
            data = self._sf.read(need, dtype="float32", always_2d=True)
            read_frames = len(data)

            if read_frames < need:
                # konec tracku
                rest = np.zeros((need - read_frames, self._channels), dtype="float32")
                data = np.vstack([data, rest])
                self._frame_pos += read_frames
                outdata[:] = data
                # zastavíme po vyhrání posledního bufferu
                self._playing = False
                try:
                    self._stream.stop()
                except Exception:
                    pass
                return

            self._frame_pos += read_frames
            outdata[:] = data

    def _on_tick(self):
        self.position_changed.emit(self._frames_to_ms(self._frame_pos))

    def _frames_to_ms(self, frames: int) -> int:
        return int(frames * 1000 / max(1, self._samplerate))

    def _ms_to_frames(self, ms: int) -> int:
        return int(ms * self._samplerate / 1000)
