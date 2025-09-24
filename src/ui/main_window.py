import os
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QMessageBox, QSplitter, QLineEdit, QSlider, QMenu,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QShortcut, QKeySequence

from audio.player import AudioPlayer
from audio.processing import render_variant
from library.manager import Library
from ui.timeline import TimelineWidget


def fmt_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def probe_duration_ms(path: str) -> int:
    from pydub import AudioSegment
    return int(len(AudioSegment.from_file(path)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PracticeMaster")
        self.showMaximized()

        self.library = Library()

        # originál
        self._original_path: str | None = None
        self._original_duration_ms: int | None = None

        # tempo staging
        self._applied_tempo = 1.0
        self._pending_tempo = 1.0
        self._tempo_timer = QTimer(self)
        self._tempo_timer.setSingleShot(True)
        self._tempo_timer.setInterval(1000)
        self._tempo_timer.timeout.connect(self._apply_pending_tempo)

        # render→originál time-scale
        self._render_to_orig = 1.0

        # efekty (gain) nad originálem
        self._gain_regions: list[tuple[int, int, float]] = []

        # seznam vyfiltrovaných ID (kvůli Next/Prev)
        self._filtered_ids: list[str] = []
        self._current_track_id: str | None = None

        # --- UI ---
        self.open_btn = QPushButton("Open…")
        self.prev_btn = QPushButton("Previous")
        self.play_btn = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        self.next_btn = QPushButton("Next")

        # malý posuvník hlasitosti aplikace
        self.volume_label = QLabel("Vol")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(120)

        # timeline nahoře, pevnější výška
        self.timeline = TimelineWidget()
        self.timeline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.timeline.setMinimumHeight(80)
        self.timeline.setMaximumHeight(140)

        self.pos_label = QLabel("00:00 / 00:00")

        # tempo nahoře u timeline
        self.tempo_label = QLabel("Tempo: 1.00x (aplikováno)")
        self.tempo_slider = QSlider(Qt.Orientation.Horizontal)
        self.tempo_slider.setRange(50, 200)
        self.tempo_slider.setValue(100)
        self.tempo_slider.setFixedHeight(20)

        # knihovna vlevo
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Hledat v knihovně…")
        self.list_widget = QListWidget()
        self.import_btn = QPushButton("Import…")
        self.delete_btn = QPushButton("Smazat")
        self.repair_btn = QPushButton("Opravit")
        self.relink_btn = QPushButton("Relink…")
        self.info_label = QLabel("")

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- levý panel
        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(6)

        left.addWidget(self.search_edit)
        left.addWidget(self.list_widget)

        rowL = QHBoxLayout()
        rowL.setSpacing(6)
        rowL.addWidget(self.import_btn)
        rowL.addWidget(self.delete_btn)
        rowL.addWidget(self.repair_btn)
        rowL.addWidget(self.relink_btn)
        left.addLayout(rowL)
        left.addWidget(self.info_label)

        # ---- pravý panel (zarovnaný NAHORU)
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)  # << zarovnání nahoru

        # horní řádek: transport + volume
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        for b in (self.open_btn, self.prev_btn, self.play_btn, self.pause_btn, self.stop_btn, self.next_btn):
            b.setMinimumWidth(76)
            row1.addWidget(b)
        row1.addStretch(1)
        row1.addWidget(self.volume_label)
        row1.addWidget(self.volume_slider)

        # střed: timeline + pozice (nahoře)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(self.timeline, 1)
        row2.addWidget(self.pos_label)

        # tempo řádek (taky nahoře)
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(self.tempo_label)
        row3.addWidget(self.tempo_slider, 1)

        right.addLayout(row1)
        right.addLayout(row2)
        right.addLayout(row3)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

        # player
        self.player = AudioPlayer()
        self.player.position_changed.connect(self.on_position_changed)
        self.player.duration_changed.connect(self.on_duration_changed)

        # timeline signály
        self.timeline.seekRequested.connect(self.on_seek_requested)
        self.timeline.scrubbed.connect(self.on_scrubbed)
        self.timeline.loopAChanged.connect(self.on_loop_a_changed)
        self.timeline.loopBChanged.connect(self.on_loop_b_changed)
        self.timeline.clearLoopRequested.connect(self.on_clear_loop)
        self.timeline.selectionFinalized.connect(self.on_selection_finalized)

        # ovládání
        self.open_btn.clicked.connect(self.open_file_direct)
        self.play_btn.clicked.connect(self.on_play_clicked)
        self.pause_btn.clicked.connect(self.player.pause)
        self.stop_btn.clicked.connect(self.player.stop)
        self.prev_btn.clicked.connect(self.play_previous_in_filter)
        self.next_btn.clicked.connect(self.play_next_in_filter)

        self.tempo_slider.valueChanged.connect(self.on_tempo_slider_changed)
        self.volume_slider.valueChanged.connect(self.player.set_volume)

        self.import_btn.clicked.connect(self.import_tracks)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.repair_btn.clicked.connect(self._do_bulk_repair)
        self.relink_btn.clicked.connect(self._do_bulk_relink)
        self.list_widget.itemDoubleClicked.connect(self.play_selected)
        self.search_edit.textChanged.connect(lambda *_: self.refresh_list(show_locations=False))

        self._install_shortcuts()

        self.refresh_list(show_locations=True)
        self.repair_if_needed()

    # ---------- shortcuts ----------
    def _install_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, activated=self._toggle_play_pause)
        QShortcut(QKeySequence("R"), self, activated=self._reset_tempo)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self._nudge(-5000))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self._nudge(5000))
        QShortcut(QKeySequence("MediaNext"), self, activated=self.play_next_in_filter)
        QShortcut(QKeySequence("MediaPrevious"), self, activated=self.play_previous_in_filter)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), self, activated=self._exit_fullscreen)

    def _toggle_play_pause(self):
        if self.player.is_playing():
            self.player.pause()
        else:
            self.on_play_clicked()

    def _reset_tempo(self):
        self._pending_tempo = 1.0
        self.tempo_slider.blockSignals(True); self.tempo_slider.setValue(100); self.tempo_slider.blockSignals(False)
        self._tempo_timer.start()
        self._update_tempo_label(pending=True)

    def _nudge(self, delta_ms: int):
        pos_orig = self.displayed_position_ms()
        new_orig = max(0, pos_orig + delta_ms)
        render_ms = int(new_orig / max(1e-6, self._render_to_orig))
        self.player.seek(render_ms)

    def _toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()

    # ---------- waveform builder ----------
    def _load_waveform(self, path: str, buckets: int = 1500):
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(path).set_channels(1)
            sw = seg.sample_width
            max_val = float(1 << (8 * sw - 1))
            arr = np.array(seg.get_array_of_samples(), dtype=np.float32) / max_val

            n = len(arr)
            if n == 0:
                self.timeline.setWaveform(None); return

            buckets = max(200, min(buckets, 4000))
            step = int(np.ceil(n / buckets))
            vals = []
            for i in range(0, n, step):
                chunk = arr[i:i+step]
                rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
                vals.append(rms)
            m = max(vals) if vals else 1.0
            self.timeline.setWaveform(None if m <= 0 else [v / m for v in vals])
        except Exception:
            self.timeline.setWaveform(None)

    # ---------- knihovna + filtr ----------
    def refresh_list(self, show_locations: bool = False):
        query = (self.search_edit.text() or "").strip().lower()
        self.list_widget.clear()
        self._filtered_ids = []
        try:
            tracks = self.library.list_tracks()
            for t in tracks:
                if query and query not in t.title.lower():
                    continue
                item = QListWidgetItem(f"{t.title}  ({t.duration_ms//1000}s)")
                item.setData(Qt.ItemDataRole.UserRole, t.id)
                self.list_widget.addItem(item)
                self._filtered_ids.append(t.id)
        except Exception as e:
            QMessageBox.warning(self, "Library error", str(e))
        if show_locations:
            lib_dir, db_path = self.library.locations()
            self.info_label.setText(f"Knihovna: {lib_dir}\nDB: {db_path}")

    def _index_in_filter(self, track_id: str | None) -> int:
        if not track_id:
            return -1
        try:
            return self._filtered_ids.index(track_id)
        except ValueError:
            return -1

    def play_next_in_filter(self):
        if not self._filtered_ids:
            return
        idx = self._index_in_filter(self._current_track_id)
        next_idx = 0 if idx < 0 or idx + 1 >= len(self._filtered_ids) else idx + 1
        self._play_track_id(self._filtered_ids[next_idx])

    def play_previous_in_filter(self):
        if not self._filtered_ids:
            return
        idx = self._index_in_filter(self._current_track_id)
        prev_idx = len(self._filtered_ids) - 1 if idx <= 0 else idx - 1
        self._play_track_id(self._filtered_ids[prev_idx])

    # ---------- integrita ----------
    def repair_if_needed(self):
        checked, removed = self.library.verify_integrity()
        if removed:
            QMessageBox.information(self, "Knihovna opravena",
                                    f"Odstraněno {removed} neplatných záznamů z {checked}.")
            self.refresh_list()

    def _do_bulk_repair(self):
        checked, removed = self.library.bulk_repair()
        self.refresh_list()
        QMessageBox.information(self, "Hromadná oprava",
                                f"Zkontrolováno: {checked}\nOdstraněno chybějících: {removed}")

    def _do_bulk_relink(self):
        folder = QFileDialog.getExistingDirectory(self, "Vyber složku pro relink (prohledá i podsložky)")
        if not folder:
            return
        missing, relinked = self.library.relink_missing_by_basename(folder)
        self.refresh_list()
        QMessageBox.information(self, "Relink dokončen",
                                f"Nalezeno chybějících v DB: {missing}\n"
                                f"Úspěšně přelinkováno: {relinked}")

    # ---------- akce na knihovně ----------
    def import_tracks(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import audio do knihovny", "", "Audio (*.mp3 *.wav *.flac)")
        if not paths:
            return
        for p in paths:
            try:
                self.library.add_file(p)
            except Exception as e:
                QMessageBox.warning(self, "Import error", f"{os.path.basename(p)}: {e}")
        self.refresh_list()

    def delete_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "Smazat",
                                "Opravdu smazat vybranou skladbu z knihovny (včetně souboru)?"
                                ) == QMessageBox.StandardButton.Yes:
            try:
                self.library.remove(track_id)
                self.refresh_list()
            except Exception as e:
                QMessageBox.critical(self, "Delete error", str(e))

    # ---------- přehrávání ----------
    def _play_track_id(self, track_id: str):
        path = self.library.get_track_path(track_id)
        if not path or not os.path.isfile(path):
            self.repair_if_needed()
            path = self.library.get_track_path(track_id)
            if not path or not os.path.isfile(path):
                QMessageBox.warning(self, "Soubor chybí",
                                    "Soubor v knihovně neexistuje. Znovu jej naimportuj (Import…).")
                return
        self._current_track_id = track_id
        self._gain_regions = []
        self._load_and_play_original(path)

        # vyber ve widgetu odpovídající položku
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == track_id:
                self.list_widget.setCurrentItem(it)
                break

    def play_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        self._play_track_id(track_id)

    def _load_and_play_original(self, path: str):
        self._original_path = path
        try:
            self._original_duration_ms = probe_duration_ms(path)
        except Exception:
            self._original_duration_ms = None

        self._load_waveform(path)
        self._applied_tempo = 1.0
        self._pending_tempo = 1.0
        self._update_tempo_label(pending=False)
        self.tempo_slider.blockSignals(True); self.tempo_slider.setValue(100); self.tempo_slider.blockSignals(False)
        self.timeline.setLoopPoints(None, None)
        self.timeline.clearSelection()

        # start hned od začátku, ale vše nahoď až po media_loaded
        def _resume():
            try:
                self.player.media_loaded.disconnect(_resume)
            except Exception:
                pass
            self._update_time_scale()
            self.player.seek(0)
            self.player.play()

        self.player.media_loaded.connect(_resume)
        self.player.load(path, autostart=False)

    # ---------- timeline & player (mapování) ----------
    def _update_time_scale(self):
        render_total = self.player.duration_ms() or 1
        orig_total = self._original_duration_ms or render_total
        render_total = max(1, int(render_total))
        orig_total = max(1, int(orig_total))
        self._render_to_orig = orig_total / render_total

        self.timeline.setDuration(orig_total)
        self.pos_label.setText(f"{fmt_ms(self.displayed_position_ms())} / {fmt_ms(orig_total)}")

    def displayed_position_ms(self) -> int:
        render_ms = self.player.current_position_ms()
        return int(render_ms * self._render_to_orig)

    def on_position_changed(self, _ms_render: int):
        ms_orig = self.displayed_position_ms()
        self.timeline.setPosition(ms_orig)
        total = self._original_duration_ms or self.player.duration_ms()
        self.pos_label.setText(f"{fmt_ms(ms_orig)} / {fmt_ms(total)}")

    def on_duration_changed(self, _ms_render: int):
        self._update_time_scale()

    def on_seek_requested(self, ms_orig: int):
        render_ms = int(ms_orig / max(1e-6, self._render_to_orig))
        self.player.seek(render_ms)

    def on_scrubbed(self, ms_orig: int):
        pass  # UI při scrubu neměníme

    def on_loop_a_changed(self, ms_orig: int):
        a = None if ms_orig < 0 else int(ms_orig / max(1e-6, self._render_to_orig))
        self.player.set_loop_ms(a=a, b=None)

    def on_loop_b_changed(self, ms_orig: int):
        b = None if ms_orig < 0 else int(ms_orig / max(1e-6, self._render_to_orig))
        self.player.set_loop_ms(a=None, b=b)

    def on_clear_loop(self):
        self.player.clear_loop()

    # ---------- výběr & kontext ----------
    def on_selection_finalized(self, a_ms: int, b_ms: int, gx: int, gy: int):
        if a_ms == b_ms:
            return
        menu = QMenu(self)
        act_loop = menu.addAction("Loop z výběru")
        menu.addSeparator()
        act_amp = menu.addAction("Zesílit +3 dB")
        act_att = menu.addAction("Zeslabit −3 dB")
        act_norm = menu.addAction("Normalizovat na −1 dBFS")
        menu.addSeparator()
        act_export = menu.addAction("Exportovat výběr jako WAV…")
        act_clear = menu.addAction("Zrušit výběr")

        chosen = menu.exec(QPoint(gx, gy))
        if not chosen:
            return

        if chosen == act_clear:
            self.timeline.clearSelection()
            return

        if chosen == act_loop:
            self.timeline.setLoopPoints(a_ms, b_ms)
            a_r = int(a_ms / max(1e-6, self._render_to_orig))
            b_r = int(b_ms / max(1e-6, self._render_to_orig))
            self.player.set_loop_ms(a=a_r, b=b_r)
            return

        if chosen == act_export:
            self._export_selection(a_ms, b_ms)
            return

        if chosen in (act_amp, act_att, act_norm):
            if chosen == act_amp:
                gain_db = +3.0
            elif chosen == act_att:
                gain_db = -3.0
            else:
                gain_db = self._estimate_normalize_gain(a_ms, b_ms, target_dbfs=-1.0)
            self._apply_gain_region(a_ms, b_ms, gain_db)
            return

    def _estimate_normalize_gain(self, a_ms: int, b_ms: int, target_dbfs: float = -1.0) -> float:
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(self._original_path)
            cut = seg[a_ms:b_ms]
            peak_dbfs = cut.max_dBFS if cut.max_dBFS != float("-inf") else -90.0
            return float(target_dbfs - peak_dbfs)
        except Exception:
            return 0.0

    def _export_selection(self, a_ms: int, b_ms: int):
        path, _ = QFileDialog.getSaveFileName(self, "Uložit výběr jako WAV", "", "WAV (*.wav)")
        if not path:
            return
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(self._original_path)
            seg[a_ms:b_ms].export(path, format="wav")
            QMessageBox.information(self, "Export hotov", f"Uloženo:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export selhal", str(e))

    # ---------- render & hladké obnovení po změně (tempo/gain) ----------
    def _load_variant_and_resume(self, new_path: str, cur_pos_orig: int,
                                 a_orig: int | None, b_orig: int | None,
                                 was_playing: bool):
        """Bezpečně načti variantu a až bude LoadedMedia, obnov čas/loop/play."""
        def _resume():
            try:
                self.player.media_loaded.disconnect(_resume)
            except Exception:
                pass
            self._update_time_scale()
            render_ms = int(cur_pos_orig / max(1e-6, self._render_to_orig))
            self.player.seek(min(render_ms, self.player.duration_ms()))
            if a_orig is not None or b_orig is not None:
                a_r = None if a_orig is None else int(a_orig / max(1e-6, self._render_to_orig))
                b_r = None if b_orig is None else int(b_orig / max(1e-6, self._render_to_orig))
                self.player.set_loop_ms(a=a_r, b=b_r)
            if was_playing:
                self.player.play()

        self.player.media_loaded.connect(_resume)
        self.player.load(new_path, autostart=False)

    def _apply_gain_region(self, a_ms: int, b_ms: int, gain_db: float):
        a = max(0, int(min(a_ms, b_ms)))
        b = max(a + 1, int(max(a_ms, b_ms)))
        self._gain_regions.append((a, b, float(gain_db)))

        cur_pos_orig = self.displayed_position_ms()
        a_orig, b_orig = self.timeline.loopPoints()
        was_playing = self.player.is_playing()

        try:
            new_path = render_variant(self._original_path,
                                      tempo_factor=self._applied_tempo,
                                      gain_regions=self._gain_regions)
            self._load_variant_and_resume(new_path, cur_pos_orig, a_orig, b_orig, was_playing)
        except Exception as e:
            QMessageBox.critical(self, "Gain render error", str(e))

    # ---------- tempo ----------
    def on_play_clicked(self):
        if self._tempo_timer.isActive():
            self._tempo_timer.stop()
            self._apply_pending_tempo()
            return
        self.player.play()

    def on_tempo_slider_changed(self, val: int):
        self._pending_tempo = val / 100.0
        self._update_tempo_label(pending=True)
        self._tempo_timer.start()

    def _apply_pending_tempo(self):
        if not self._original_path:
            return
        if abs(self._pending_tempo - self._applied_tempo) < 1e-6:
            return

        cur_pos_orig = self.displayed_position_ms()
        a_orig, b_orig = self.timeline.loopPoints()
        was_playing = self.player.is_playing()
        try:
            path = render_variant(self._original_path,
                                  tempo_factor=self._pending_tempo,
                                  gain_regions=self._gain_regions)
            self._load_variant_and_resume(path, cur_pos_orig, a_orig, b_orig, was_playing)
            self._applied_tempo = self._pending_tempo
            self._update_tempo_label(pending=False)
        except Exception as e:
            QMessageBox.critical(self, "Tempo render error", str(e))

    def _update_tempo_label(self, pending: bool):
        if pending and abs(self._pending_tempo - self._applied_tempo) > 1e-6:
            self.tempo_label.setText(f"Tempo: {self._pending_tempo:.2f}x (čeká)")
        else:
            self.tempo_label.setText(f"Tempo: {self._applied_tempo:.2f}x (aplikováno)")

    # ---------- Open ----------
    def open_file_direct(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open audio (bez uložení do knihovny)", "", "Audio (*.mp3 *.wav *.flac)")
        if not path:
            return
        self._current_track_id = None
        self._gain_regions = []
        self._load_and_play_original(path)
