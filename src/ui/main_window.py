import os
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
import random
from PyQt6.QtGui import QPainter, QPaintEvent, QPen, QBrush, QColor, QPolygon
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog,
    QListWidget, QListWidgetItem, QMessageBox, QSplitter, QLineEdit, QSlider, QMenu,
    QSizePolicy, QCheckBox, QInputDialog
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRegularExpression, QSize
from PyQt6.QtGui import QShortcut, QKeySequence

from audio.player import AudioPlayer
from audio.processing import render_variant
from library.manager import Library
from ui.timeline import TimelineWidget


# ---------- utility ----------
def fmt_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def probe_duration_ms(path: str) -> int:
    from pydub import AudioSegment
    return int(len(AudioSegment.from_file(path)))


@dataclass
class PracticaItem:
    kind: str          # "track" nebo "gap"
    path: str
    start_ms: int
    duration_ms: int


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

        # favorites (★)
        self._favorites_local: set[str] = set()
        self._favorites_only = False
        self._load_favorites_local()

        # --- UI ---
        self.open_btn = QPushButton("Open…")
        self.open_btn.setStyleSheet("""
            QPushButton {
                background-color: #6A0DAD;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 14px;
                box-sizing: border-box;
                width: 74px;
                height: 74px;
                border-width: 37px 0px 37px 74px;
            }
            QPushButton:hover {
                background-color: #8A2BE2;
            }
            QPushButton:pressed {
                background-color: #5B0092;
            }
        """)
        self.prev_btn = QPushButton("Previous")
        self.playpause_btn = PlayPauseButton()
        self.stop_btn = QPushButton("Stop")
        self.next_btn = QPushButton("Next")

        # practice
        self.practice_btn = QPushButton("Poskládat a přehrát practice (Latina)")
        self.practice_btn.setMinimumWidth(280)
        self.practice_btn.clicked.connect(self.generate_practice_latina_and_play)

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
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_list_context_menu)

        self.import_btn = QPushButton("Import…")
        self.delete_btn = QPushButton("Smazat")
        self.repair_btn = QPushButton("Opravit")
        self.relink_btn = QPushButton("Relink…")
        self.fav_filter_chk = QCheckBox("Jen oblíbené ★")
        self.fav_filter_chk.stateChanged.connect(self._toggle_favorites_filter)

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
        rowL.addWidget(self.fav_filter_chk)
        left.addLayout(rowL)
        left.addWidget(self.info_label)

        # ---- pravý panel (zarovnaný NAHORU)
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        # horní řádek: transport + volume
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        for b in (self.open_btn, self.prev_btn, self.playpause_btn, self.stop_btn, self.next_btn):
            b.setMinimumWidth(76)
            row1.addWidget(b)
        row1.addWidget(self.practice_btn)
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
        # Play/Pause – jednotné ovládání
        self.playpause_btn.clicked.connect(self.toggle_play_pause)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
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

    # ---------- favorites (★) ----------
    def _fav_store_path(self) -> str | None:
        try:
            lib_dir, _ = self.library.locations()
            if not lib_dir:
                return None
            p = os.path.join(lib_dir, "_favorites.json")
            return p
        except Exception:
            return None

    def _load_favorites_local(self):
        p = self._fav_store_path()
        if not p or not os.path.isfile(p):
            self._favorites_local = set()
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._favorites_local = set(data if isinstance(data, list) else [])
        except Exception:
            self._favorites_local = set()

    def _save_favorites_local(self):
        p = self._fav_store_path()
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(sorted(self._favorites_local), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _library_has_fav_api(self) -> bool:
        return all(hasattr(self.library, name) for name in ("set_favorite", "is_favorite"))

    def _is_favorite(self, track_id: str) -> bool:
        try:
            if self._library_has_fav_api():
                return bool(self.library.is_favorite(track_id))
        except Exception:
            pass
        return track_id in self._favorites_local

    def _set_favorite(self, track_id: str, value: bool):
        try:
            if self._library_has_fav_api():
                self.library.set_favorite(track_id, value)
            else:
                if value:
                    self._favorites_local.add(track_id)
                else:
                    self._favorites_local.discard(track_id)
                self._save_favorites_local()
        except Exception:
            # fallback na lokální
            if value:
                self._favorites_local.add(track_id)
            else:
                self._favorites_local.discard(track_id)
            self._save_favorites_local()

    def _toggle_favorites_filter(self, *_):
        self._favorites_only = self.fav_filter_chk.isChecked()
        self.refresh_list(show_locations=False)

    def _show_list_context_menu(self, pos: QPoint):
        item = self.list_widget.itemAt(pos)
        menu = QMenu(self)
        if item:
            track_id = item.data(Qt.ItemDataRole.UserRole)
            fav_now = self._is_favorite(track_id)
            act_fav = menu.addAction("Odebrat z oblíbených ★" if fav_now else "Označit jako oblíbené ★")
            menu.addSeparator()
            act_play = menu.addAction("Přehrát")
            chosen = menu.exec(self.list_widget.mapToGlobal(pos))
            if not chosen:
                return
            if chosen == act_fav:
                self._set_favorite(track_id, not fav_now)
                self.refresh_list(show_locations=False)
            elif chosen == act_play:
                self._play_track_id(track_id)
        else:
            act_reload = menu.addAction("Obnovit seznam")
            chosen = menu.exec(self.list_widget.mapToGlobal(pos))
            if chosen == act_reload:
                self.refresh_list(show_locations=False)

    # ---------- shortcuts ----------
    def _install_shortcuts(self):
        QShortcut(QKeySequence("Space"), self, activated=self.toggle_play_pause)
        QShortcut(QKeySequence("R"), self, activated=self._reset_tempo)
        QShortcut(QKeySequence("Left"), self, activated=lambda: self._nudge(-5000))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self._nudge(5000))
        QShortcut(QKeySequence("MediaNext"), self, activated=self.play_next_in_filter)
        QShortcut(QKeySequence("MediaPrevious"), self, activated=self.play_previous_in_filter)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), self, activated=self._exit_fullscreen)

    def toggle_play_pause(self):
        """Jednotné ovládání Play/Pause z tlačítka i klávesnice."""
        # pokud čeká změna tempa, nejdříve ji aplikuj
        if self._tempo_timer.isActive():
            self._tempo_timer.stop()
            self._apply_pending_tempo()
            # _apply_pending_tempo po loadu a resume pustí play
            self.playpause_btn.setChecked(True)
            return

        if self.player.is_playing():
            # Pauza
            try:
                self.player.pause()
            except AttributeError:
                # fallback, pokud AudioPlayer nemá pause()
                pos = self.player.current_position_ms()
                self.player.stop()
                self.player.seek(pos)
            self.playpause_btn.setChecked(False)
        else:
            # Play
            self.player.play()
            self.playpause_btn.setChecked(True)

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
                title = t.title or ""
                if query and query not in title.lower():
                    continue
                tid = t.id
                if self._favorites_only and not self._is_favorite(tid):
                    continue
                star = " ★" if self._is_favorite(tid) else ""
                item = QListWidgetItem(f"{title}{star}  ({(t.duration_ms or 0)//1000}s)")
                item.setData(Qt.ItemDataRole.UserRole, tid)
                self.list_widget.addItem(item)
                self._filtered_ids.append(tid)
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
                # smaž i z oblíbených
                self._set_favorite(track_id, False)
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
            self.playpause_btn.setChecked(True)  # synchronizace stavu tlačítka

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
                self.playpause_btn.setChecked(True)
            else:
                self.playpause_btn.setChecked(False)

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
        """Pokud chceš mít i separátní 'Play' akci mimo toggle – zachováno kvůli kompatibilitě."""
        if self._tempo_timer.isActive():
            self._tempo_timer.stop()
            self._apply_pending_tempo()
            return
        self.player.play()
        self.playpause_btn.setChecked(True)

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

    # ---------- PRÁCTICA: poskládat WAV, přidat do knihovny a přehrát ----------
    def _pick_length_seconds(self) -> int:
        items = ["01:30", "01:40", "02:00"]
        text, ok = QInputDialog.getItem(self, "Délka skladby", "Zvol délku každé skladby:", items, 0, True)
        if not ok or not text:
            return -1
        text = text.strip()
        if ":" in text:
            m, s = text.split(":", 1)
            try:
                return max(1, int(m) * 60 + int(s))
            except Exception:
                return -1
        try:
            return max(1, int(text))
        except Exception:
            return -1

    def _pick_gap_seconds(self) -> int:
        items = ["20", "15", "30"]
        text, ok = QInputDialog.getItem(self, "Mezihudba", "Délka tiché mezery mezi tanci (s):", items, 0, True)
        if not ok or not text:
            return -1
        try:
            return max(0, int(text))
        except Exception:
            return -1

    def _find_track_for_dance(self, dance_name: str, *, use_ui_filters: bool = True,
                              used_ids: set[str] | None = None) -> tuple[str, int] | None:
        """
        Vrátí (path, duration_ms) NÁHODNÉ vyhovující skladby z knihovny.
        - use_ui_filters=True => respektuje text v search boxu a „Jen oblíbené ★“.
        - used_ids => pokud je předáno, pokusí se vyhnout už použitým ID (pro různá kola výběru).
        """
        synonyms = {
            "samba": [r"\bsamba\b"],
            "cha cha": [r"\bcha\b.*\bcha\b", r"\bchacha\b", r"\bcha-cha\b", r"\bcha\s*cha\b"],
            "rumba": [r"\brumba\b", r"\brhumba\b"],
            "paso doble": [r"\bpaso\b.*\bdoble\b", r"\bpasodoble\b", r"\bpaso\b"],
            "jive": [r"\bjive\b"],
        }
        pats = [QRegularExpression(p, QRegularExpression.PatternOption.CaseInsensitiveOption)
                for p in synonyms.get(dance_name, [dance_name])]

        query = (self.search_edit.text() or "").strip().lower() if use_ui_filters else ""
        fav_only = self._favorites_only if use_ui_filters else False

        candidates: list[tuple[str, str, int]] = []  # (track_id, path, dur_ms)

        try:
            tracks = self.library.list_tracks()
            for t in tracks:
                title = (t.title or "")
                title_l = title.lower()

                # UI filtr: text + jen oblíbené
                if use_ui_filters and query and query not in title_l:
                    continue
                if use_ui_filters and fav_only and not self._is_favorite(t.id):
                    continue

                # match na název dle tance
                if any(rx.match(title_l).hasMatch() for rx in pats):
                    path = self.library.get_track_path(t.id)
                    if not path or not os.path.isfile(path):
                        continue
                    dur = int(getattr(t, "duration_ms", 0)) or probe_duration_ms(path)
                    candidates.append((t.id, path, dur))
        except Exception:
            pass

        if not candidates:
            return None

        # pokud máme použité ID, zkus je vyloučit
        if used_ids:
            fresh = [c for c in candidates if c[0] not in used_ids]
            if fresh:
                candidates = fresh

        tid, path, dur = random.choice(candidates)
        if used_ids is not None:
            used_ids.add(tid)
        return path, dur

    def _make_gap_segment(self, seconds: int):
        """
        Vrátí úsek 'mezihudby' o délce `seconds`.
        - Pokud v knihovně existuje skladba obsahující 'mezihudba' v názvu, vezme se její začátek.
        - Jinak se vytvoří tichý segment.
        """
        from pydub import AudioSegment

        duration_ms = max(0, int(seconds * 1000))

        try:
            # projdi knihovnu a hledej "mezihudba"
            for t in self.library.list_tracks():
                if "mezihudba" in (t.title or "").lower():
                    path = self.library.get_track_path(t.id)
                    if path and os.path.isfile(path):
                        seg = AudioSegment.from_file(path)
                        if len(seg) > duration_ms:
                            seg = seg[:duration_ms]
                        return seg
        except Exception as e:
            print(f"Chyba při hledání mezihudby: {e}")

        # fallback: ticho
        return AudioSegment.silent(duration=duration_ms)

    def _load_clip(self, path: str, clip_seconds: int, dance):
        """Načte audio a vrátí segment délky `clip_seconds` se short fade-in/out, aby to nelupalo."""
        from pydub import AudioSegment
        seg = AudioSegment.from_file(path)
        need_ms = max(1000, int(clip_seconds * 1000))
        if(dance != "paso doble"):
            cut = seg[:need_ms] if len(seg) >= need_ms else seg
        else:
            cut = seg
        fade = min(400, len(cut)//10)
        return cut.fade_in(fade).fade_out(fade)

    def generate_practice_latina_and_play(self):
        """
        Sestaví jeden WAV (Samba → Cha-cha → Rumba → Paso doble → Jive) s tichými mezerami,
        dočasně uloží do knihovny a ihned ho pustí jako jednu skladbu.
        """
        clip_len_s = self._pick_length_seconds()
        if clip_len_s <= 0:
            return
        gap_s = self._pick_gap_seconds()
        if gap_s < 0:
            return

        dances = ["samba", "cha cha", "rumba", "paso doble", "jive"]

        try:
            from pydub import AudioSegment
        except Exception:
            QMessageBox.critical(self, "Chybí závislosti",
                                 "K generování je potřeba pydub a funkční FFmpeg v PATH.")
            return

        segments: list[AudioSegment] = []
        missing: list[str] = []

        for d in dances:
            found = self._find_track_for_dance(d)
            if not found:
                missing.append(d); continue
            path, _dur_ms = found
            try:
                clip = self._load_clip(path, clip_len_s, d)
                segments.append(clip)
                if gap_s > 0 and d != dances[-1]:
                    segments.append(self._make_gap_segment(gap_s))
            except Exception as e:
                missing.append(f"{d} ({os.path.basename(path)}: {e})")

        if not segments:
            QMessageBox.warning(self, "Nenalezeno", "V knihovně se nenašly skladby pro Latinské tance.")
            return

        if missing:
            QMessageBox.information(self, "Upozornění",
                                    "Něco chybí/nešlo zpracovat: " + ", ".join(missing))

        try:
            final_mix = segments[0]
            for seg in segments[1:]:
                final_mix += seg
        except Exception as e:
            QMessageBox.critical(self, "Chyba mixu", str(e))
            return

        # uložit do dočasné složky knihovny a přidat do Library
        try:
            lib_dir, _db_path = self.library.locations()
            out_dir = os.path.join(lib_dir, "_practice_temp")
            os.makedirs(out_dir, exist_ok=True)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"Practice_Latin_{clip_len_s}s_gap{gap_s}s_{stamp}.wav"
            out_path = os.path.join(out_dir, base_name)

            final_mix.export(out_path, format="wav")
        except Exception as e:
            QMessageBox.critical(self, "Export dočasného WAV selhal", str(e))
            return

        # přidat do knihovny + přehrát
        try:
            self.library.add_file(out_path)
        except Exception as e:
            # fallback – přehrajeme přímo
            self._current_track_id = None
            self._gain_regions = []
            self._load_and_play_original(out_path)
            QMessageBox.warning(self, "Nelze přidat do knihovny",
                                f"Soubor se nepodařilo zapsat do knihovny, přehrávám přímo.\n\n{e}")
            return

        try:
            self.refresh_list(show_locations=False)
            target_title = os.path.splitext(base_name)[0].lower()
            track_id = None
            for t in self.library.list_tracks():
                if (t.title or "").lower() == target_title:
                    track_id = t.id
                    break

            if track_id:
                self._play_track_id(track_id)
            else:
                self._current_track_id = None
                self._gain_regions = []
                self._load_and_play_original(out_path)
        except Exception:
            self._current_track_id = None
            self._gain_regions = []
            self._load_and_play_original(out_path)

        QMessageBox.information(self, "Practice připraven",
                                f"Poskládaná skladba je v knihovně jako:\n{base_name}")

    # ---------- pomocné ovládací akce ----------
    def _on_stop_clicked(self):
        self.player.stop()
        self.playpause_btn.setChecked(False)


class PlayPauseButton(QPushButton):
    """
    Vykreslí Play (trojúhelník) / Pause (dvě svislé čáry) bez ikon.
    Stav se řídí .isChecked(): checked=True => Playing (Pause ikonka), False => Paused/Stopped (Play ikonka).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bg = QColor("#222")  # pozadí
        self._fg = QColor("#fff")  # symbol
        self._bg_hover = QColor("#333")
        self._bg_press = QColor("#111")
        self._radius = 8
        self.setMinimumSize(44, 36)
        self.setMaximumHeight(36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Play/Pause (Space)")

    def sizeHint(self) -> QSize:
        return QSize(52, 36)

    def _bg_color(self):
        if self.isDown():
            return self._bg_press
        if self.underMouse():
            return self._bg_hover
        return self._bg

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)

        # background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._bg_color()))
        p.drawRoundedRect(rect, self._radius, self._radius)

        # symbol (play/pause)
        p.setBrush(QBrush(self._fg))
        p.setPen(Qt.PenStyle.NoPen)

        w = rect.width()
        h = rect.height()
        cx = rect.x() + w // 2
        cy = rect.y() + h // 2

        if not self.isChecked():
            # PLAY ▶ (trojúhelník směřující doprava)
            side = int(min(w, h) * 0.3)
            x0 = cx - side // 2
            poly = QPolygon([
                QPoint(x0, cy - side),
                QPoint(x0, cy + side),
                QPoint(x0 + int(side * 1.15), cy),
            ])
            p.drawConvexPolygon(poly)
        else:
            # PAUSE ‖ (dva sloupky)
            bar_w = max(3, int(w * 0.08))
            bar_h = int(h * 0.52)
            gap = int(w * 0.07)
            x1 = cx - gap - bar_w
            x2 = cx + gap
            y = cy - bar_h // 2
            p.drawRoundedRect(x1, y, bar_w, bar_h, 2, 2)
            p.drawRoundedRect(x2, y, bar_w, bar_h, 2, 2)

        p.end()
