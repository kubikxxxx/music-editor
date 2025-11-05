# ui/main_window.py
from __future__ import annotations
import os
import json
import tempfile
import random
import sys
import time
from dataclasses import dataclass
from ui.widgets.track_list import TrackListWidget
from datetime import datetime
from typing import Optional, Tuple, List

import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog,
    QListWidgetItem, QMessageBox, QSplitter, QLineEdit, QSlider, QMenu,
    QSizePolicy, QCheckBox, QInputDialog, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QRegularExpression,
    QCoreApplication, QEvent
)
from PyQt6.QtGui import QShortcut, QKeySequence, QColor

from audio.player import AudioPlayer
from audio.processing import render_variant  # (zůstává; tempo mixdownu teď neřešíme)
from library.manager import Library
from ui.timeline import TimelineWidget

from ui.widgets.play_pause_button import PlayPauseButton
from ui.widgets.win_media import WinMediaKeyFilter
from ui.track_editor import TrackEditorWidget, Cell


def fmt_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def probe_duration_ms(path: str) -> int:
    from pydub import AudioSegment
    return int(len(AudioSegment.from_file(path)))


@dataclass
class PracticaItem:
    kind: str
    path: str
    start_ms: int
    duration_ms: int


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PracticeMaster")
        self.showMaximized()

        self.library = Library()

        self._original_path: str | None = None
        self._original_duration_ms: int | None = None
        self._applied_tempo = 1.0
        self._pending_tempo = 1.0
        self._render_to_orig = 1.0
        self._gain_regions: list[tuple[int, int, float]] = []
        self._filtered_ids: list[str] = []
        self._current_track_id: str | None = None

        self._favorites_local: set[str] = set()
        self._favorites_only = False
        self._load_favorites_local()

        # --- transport ---
        self._arr_time_ms: int = 0
        self._arr_total_ms: int = 0
        self._transport_playing: bool = False
        self._transport_timer = QTimer(self)
        self._transport_timer.setInterval(16)  # ~60 FPS
        self._transport_timer.timeout.connect(self._transport_tick)

        # kotvy pro absolutní 1:1 čas
        self._t0_monotonic: float | None = None  # kdy jsme spustili/přeseekovali (monotonic)
        self._t_anchor_ms: int = 0  # jaká arr pozice v ms odpovídá _t0_monotonic

        # --- mixdown cache ---
        self._mix_sig: str = ""             # podpis aktuální aranže (pro cache)
        self._mixdown_tmp_path: Optional[str] = None
        self._loaded_player_path: Optional[str] = None

        self._tempo_cache: dict[tuple[str, float], str] = {}
        self._tempo_tmp_paths: set[str] = set()

        # --- UI prvky ---
        self.open_btn = QPushButton("Open…")
        self.open_btn.setStyleSheet(self._purple_btn_css())
        self.prev_btn = QPushButton("Previous")
        self.prev_btn.setStyleSheet(self._purple_btn_css())
        self.playpause_btn = PlayPauseButton()
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet(self._purple_btn_css())
        self.next_btn = QPushButton("Next")
        self.next_btn.setStyleSheet(self._purple_btn_css())

        self.practice_btn = QPushButton("Poskládat a přehrát practice")
        self.practice_btn.setMinimumWidth(280)
        self.practice_btn.setStyleSheet(self._purple_btn_css())
        self.practice_btn.clicked.connect(self.generate_practice_and_play)

        self.volume_label = QLabel("Vol")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(120)

        self.timeline = TimelineWidget()
        self.timeline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.timeline.setMinimumHeight(80)
        self.timeline.setMaximumHeight(140)

        self.pos_label = QLabel("00:00 / 00:00")

        self.tempo_label = QLabel("Tempo: 1.00x (aplikováno)")
        self.tempo_slider = QSlider(Qt.Orientation.Horizontal)
        self.tempo_slider.setRange(50, 200)
        self.tempo_slider.setValue(100)
        self.tempo_slider.setFixedHeight(20)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Hledat v knihovně…")
        self.list_widget = TrackListWidget(self)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_list_context_menu)

        self.import_btn = QPushButton("Import…")
        self.delete_btn = QPushButton("Smazat")
        self.repair_btn = QPushButton("Opravit")
        self.relink_btn = QPushButton("Relink…")
        self.fav_filter_chk = QCheckBox("Jen oblíbené ★")
        self.fav_filter_chk.stateChanged.connect(self._toggle_favorites_filter)

        self._last_player_resync_t = 0.0
        self._resync_min_interval = 0.30
        self._resync_drift_ms = 120

        self.info_label = QLabel("")

        self._tempo_tmp_path: Optional[str] = None
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # levý panel
        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(8, 8, 8, 8)
        left.setSpacing(6)
        left.addWidget(self.search_edit)
        left.addWidget(self.list_widget)
        rowL = QHBoxLayout()
        rowL.setSpacing(6)
        for b in (self.import_btn, self.delete_btn, self.repair_btn, self.relink_btn, self.fav_filter_chk):
            rowL.addWidget(b)
        left.addLayout(rowL)
        left.addWidget(self.info_label)

        # pravý panel
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        for b in (self.open_btn, self.prev_btn, self.playpause_btn, self.stop_btn, self.next_btn):
            b.setMinimumWidth(76)
            row1.addWidget(b)

        self._auto_next_chk = QCheckBox("Autoplay")
        self._auto_next_chk.setChecked(True)
        row1.addWidget(self._auto_next_chk)

        row1.addWidget(self.practice_btn)
        row1.addStretch(1)
        row1.addWidget(self.volume_label)
        row1.addWidget(self.volume_slider)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(self.timeline, 1)
        row2.addWidget(self.pos_label)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        row3.addWidget(self.tempo_label)
        row3.addWidget(self.tempo_slider, 1)

        right.addLayout(row1)
        right.addLayout(row2)
        right.addLayout(row3)

        # track editor
        self.track_editor = TrackEditorWidget()
        right.addWidget(self.track_editor)

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
        self.player.duration_changed.connect(self.on_duration_changed)

        # timeline signály
        self.timeline.seekRequested.connect(self.on_seek_requested)
        self.timeline.scrubbed.connect(self.on_scrubbed)
        self.timeline.loopAChanged.connect(self.on_loop_a_changed)
        self.timeline.loopBChanged.connect(self.on_loop_b_changed)
        self.timeline.clearLoopRequested.connect(self.on_clear_loop)

        # editor -> timeline / transport
        self.track_editor.clipOffsetChanged.connect(self._on_clip_offset_changed)
        self.track_editor.arrangementChanged.connect(self._on_editor_changed)   # změna aranže → přepočet + invalidace mixu
        self.track_editor.externalFileDropped.connect(self._on_external_file_dropped)
        self.track_editor.libraryTrackDropped.connect(self._on_library_track_dropped)
        self.track_editor.currentCellChanged.connect(self._on_cell_selected)

        # ovládání
        self.open_btn.clicked.connect(self.open_file_direct)
        self.playpause_btn.clicked.connect(self.toggle_play_pause)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.prev_btn.clicked.connect(self.play_previous_in_filter)
        self.next_btn.clicked.connect(self.play_next_in_filter)

        self.tempo_slider.valueChanged.connect(self.on_tempo_slider_changed)
        self.tempo_slider.sliderReleased.connect(self._apply_pending_tempo_on_release)
        self.volume_slider.valueChanged.connect(self.player.set_volume)

        self.import_btn.clicked.connect(self.import_tracks)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.repair_btn.clicked.connect(self._do_bulk_repair)
        self.relink_btn.clicked.connect(self._do_bulk_relink)
        self.list_widget.itemDoubleClicked.connect(self.play_selected)
        self.search_edit.textChanged.connect(lambda *_: self.refresh_list(show_locations=False))

        self._install_shortcuts()

        self._autonext_timer = QTimer(self)
        self._autonext_timer.setInterval(200)
        self._autonext_timer.timeout.connect(self._check_autonext)
        self._autonext_timer.start()

        if sys.platform.startswith("win"):
            def _do_toggle():
                self.toggle_play_pause()
            self._win_media_filter = WinMediaKeyFilter(
                on_playpause=_do_toggle, on_play=_do_toggle, on_pause=_do_toggle, debug=True
            )
            QCoreApplication.instance().installNativeEventFilter(self._win_media_filter)
            def _late_register():
                try:
                    self._win_media_filter.register_global_playpause_hotkey(int(self.winId()))
                    if not getattr(self._win_media_filter, "_hotkey_registered", False):
                        self._win_media_filter.register_global_playpause_hotkey(None)
                except Exception:
                    pass
            QTimer.singleShot(0, _late_register)

        self.refresh_list(show_locations=True)
        self.repair_if_needed()

    # ---------- helpers ----------
    @staticmethod
    def _purple_btn_css() -> str:
        return """
        QPushButton {
            background-color: #6A0DAD;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 14px;
            width: 74px;
            height: 20px;
            border-width: 37px 0px 37px 74px;
        }
        QPushButton:hover { background-color: #8A2BE2; }
        QPushButton:pressed { background-color: #5B0092; }
        """

    # ---------- favorites ----------
    def _fav_store_path(self) -> str | None:
        try:
            lib_dir, _ = self.library.locations()
            if not lib_dir:
                return None
            return os.path.join(lib_dir, "_favorites.json")
        except Exception:
            return None

    def _load_favorites_local(self):
        p = self._fav_store_path()
        if not p or not os.path.isfile(p):
            self._favorites_local = set(); return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._favorites_local = set(data if isinstance(data, list) else [])
        except Exception:
            self._favorites_local = set()

    def _save_favorites_local(self):
        p = self._fav_store_path()
        if not p: return
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
                if value: self._favorites_local.add(track_id)
                else:     self._favorites_local.discard(track_id)
                self._save_favorites_local()
        except Exception:
            if value: self._favorites_local.add(track_id)
            else:     self._favorites_local.discard(track_id)
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
                self._start_playback()
        else:
            act_reload = menu.addAction("Obnovit seznam")
            chosen = menu.exec(self.list_widget.mapToGlobal(pos))
            if chosen == act_reload:
                self.refresh_list(show_locations=False)

    # ---------- shortcuts / events ----------
    def _install_shortcuts(self):
        # PyQt6-safe: vytvořit QShortcut a pak připojit signál; používat Qt.Key.*
        def mk(key, slot):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
            return sc

        mk(Qt.Key.Key_Space, self.toggle_play_pause)
        mk(Qt.Key.Key_R, self._reset_tempo)
        mk(Qt.Key.Key_Left, lambda: self.on_seek_requested(max(0, self._arr_time_ms - 5000)))
        mk(Qt.Key.Key_Right, lambda: self.on_seek_requested(self._arr_time_ms + 5000))
        mk(Qt.Key.Key_MediaNext, self.play_next_in_filter)
        mk(Qt.Key.Key_MediaPrevious, self.play_previous_in_filter)
        mk(Qt.Key.Key_F11, self._toggle_fullscreen)
        mk(Qt.Key.Key_Escape, self._exit_fullscreen)

    def changeEvent(self, ev):
        if sys.platform.startswith("win") and ev.type() == QEvent.Type.WinIdChange:
            try:
                self._win_media_filter.register_global_playpause_hotkey(int(self.winId()))
            except Exception:
                pass
        return super().changeEvent(ev)

    def closeEvent(self, ev):
        try:
            if self._mixdown_tmp_path and os.path.isfile(self._mixdown_tmp_path):
                os.remove(self._mixdown_tmp_path)
        except Exception:
            pass
        try:
            self._cleanup_tempo_tmp()
        except Exception:
            pass
        if sys.platform.startswith("win"):
            try:
                self._win_media_filter.unregister_global_playpause_hotkey()
            except Exception:
                pass
        return super().closeEvent(ev)

    # ---------- TRANSPORT ----------
    def _clip_bounds(self) -> tuple[int, int]:
        return 0, max(0, int(self._arr_total_ms))

    def _on_editor_changed(self):
        self._mix_sig = ""  # invalidace cache
        self._recompute_total_and_overlays()
        if self._transport_playing or self._arr_time_ms > 0:
            cur = self._arr_time_ms
            self._ensure_mixdown_loaded(rebuild=True)
            self.on_seek_requested(cur)

    def _recompute_total_and_overlays(self):
        self._arr_total_ms = self.track_editor.totalDurationMs()
        self.timeline.setDuration(self._arr_total_ms)

        wf_mix = self.track_editor.exportMixdownWaveform(buckets=3000)
        self.timeline.setWaveform(wf_mix)

        intervals = []
        for c in getattr(self.track_editor, "_cells", []):
            if c.duration_ms > 0:
                intervals.append((c.offset_ms, c.offset_ms + c.duration_ms))
        intervals.sort()
        merged = []
        for a, b in intervals:
            if not merged or a > merged[-1][1]:
                merged.append([a, b])
            else:
                merged[-1][1] = max(merged[-1][1], b)

        overlays = []
        cur = 0
        for a, b in merged:
            if a > cur:
                overlays.append({"type": "region", "start": cur, "end": a, "color": QColor(120,120,120,110)})
            overlays.append({"type": "region", "start": a, "end": b, "color": QColor(60,200,140,110)})
            cur = b
        if cur < self._arr_total_ms:
            overlays.append({"type": "region", "start": cur, "end": self._arr_total_ms, "color": QColor(120,120,120,110)})
        self.timeline.setOverlays(overlays)

        self.timeline.setPosition(min(self._arr_time_ms, self._arr_total_ms))
        self.pos_label.setText(f"{fmt_ms(self._arr_time_ms)} / {fmt_ms(self._arr_total_ms)}")
        self.timeline.update()

    def _transport_tick(self):
        if not self._transport_playing or self._t0_monotonic is None:
            return

        elapsed_ms = int((time.monotonic() - self._t0_monotonic) * 1000.0)
        new_ms = min(self._t_anchor_ms + max(0, elapsed_ms), self._arr_total_ms)

        self._arr_time_ms = new_ms
        self.timeline.setPosition(new_ms)
        self.track_editor.setPlayhead(new_ms)
        self.pos_label.setText(f"{fmt_ms(new_ms)} / {fmt_ms(self._arr_total_ms)}")

        if new_ms >= self._arr_total_ms:
            # konec aranže
            if self.player.is_playing():
                self.player.stop()
            self._transport_playing = False
            self._transport_timer.stop()
            self.playpause_btn.setChecked(False)
            self._t0_monotonic = None
            self._t_anchor_ms = new_ms

    def _player_position_ms(self) -> Optional[int]:
        """Preferuj pozici z přehrávače, pokud ji umí vrátit (AudioPlayer.position_ms)."""
        try:
            getpos = getattr(self.player, "position_ms", None)
            if callable(getpos):
                pos = getpos()
                if pos is not None:
                    return int(pos)
        except Exception:
            pass
        return None

    def toggle_play_pause(self):
        if self._transport_playing:
            # pauza
            self._transport_playing = False
            self._transport_timer.stop()
            try:
                self.player.pause()
            except AttributeError:
                self.player.stop()
            self.playpause_btn.setChecked(False)

            # zruš kotvu – pozice zůstává v _arr_time_ms
            self._t0_monotonic = None
            self._t_anchor_ms = self._arr_time_ms
            return

        # play
        self._ensure_mixdown_loaded(rebuild=(self._mix_sig == ""))
        self.playpause_btn.setChecked(True)

        # zajisti, že audio je v aktuální pozici (ale UI pojede 1:1 nezávisle)
        if self._loaded_player_path == (self._mixdown_tmp_path or ""):
            self.player.seek(self._arr_time_ms)
            if not self.player.is_playing():
                self.player.play()

        # nastav kotvy pro 1:1 čas
        self._t0_monotonic = time.monotonic()
        self._t_anchor_ms = self._arr_time_ms

        self._transport_playing = True
        self._transport_timer.start()

    def _on_stop_clicked(self):
        self._transport_playing = False
        self._transport_timer.stop()
        self.player.stop()
        self.playpause_btn.setChecked(False)

        # reset časových kotev
        self._t0_monotonic = None
        self._t_anchor_ms = 0

        # vrať pozici na začátek (volitelné – takhle to děláš i teď)
        self._arr_time_ms = 0
        self.timeline.setPosition(0)
        self.track_editor.setPlayhead(0)
        self.pos_label.setText(f"{fmt_ms(0)} / {fmt_ms(self._arr_total_ms)}")

    def _start_playback(self):
        """Spustí přehrávání od aktuální pozice a rozběhne UI čas."""
        self._transport_playing = True
        self.playpause_btn.setChecked(True)

        # zajisti audio/mixdown
        self._ensure_mixdown_loaded(rebuild=(self._mix_sig == ""))

        # pokud už je nahráno, okamžitě spusť
        if self._loaded_player_path == (self._mixdown_tmp_path or ""):
            self.player.seek(self._arr_time_ms)
            if not self.player.is_playing():
                self.player.play()

        # ukotvi 1:1 čas a rozběhni tick
        self._t0_monotonic = time.monotonic()
        self._t_anchor_ms = self._arr_time_ms
        self._transport_timer.start()

    def on_seek_requested(self, ms_orig: int):
        self._arr_time_ms = max(0, min(ms_orig, self._arr_total_ms))
        self.timeline.setPosition(self._arr_time_ms)
        self.track_editor.setPlayhead(self._arr_time_ms)
        self.pos_label.setText(f"{fmt_ms(self._arr_time_ms)} / {fmt_ms(self._arr_total_ms)}")

        if self._loaded_player_path == (self._mixdown_tmp_path or ""):
            self.player.seek(self._arr_time_ms)
            if self._transport_playing and not self.player.is_playing():
                self.player.play()
            if self._transport_playing:
                self._t0_monotonic = time.monotonic()
                self._t_anchor_ms = self._arr_time_ms

    def on_scrubbed(self, _ms_orig: int):
        pass

    def on_loop_a_changed(self, ms_orig: int):
        self.player.set_loop_ms(a=None if ms_orig < 0 else int(ms_orig), b=None)

    def on_loop_b_changed(self, ms_orig: int):
        self.player.set_loop_ms(a=None, b=None if ms_orig < 0 else int(ms_orig))

    def on_clear_loop(self):
        self.player.clear_loop()

    def _on_clip_offset_changed(self, _start_ms: int):
        self._on_editor_changed()

    def _on_cell_selected(self, _idx: int):
        """Synchronizuje tempo-slider s právě vybranou buňkou."""
        t = float(self.track_editor.currentCellTempo())
        self._applied_tempo = self._pending_tempo = t
        self.tempo_slider.blockSignals(True)
        self.tempo_slider.setValue(int(round(t * 100)))
        self.tempo_slider.blockSignals(False)
        self._update_tempo_label(pending=False)

    # ---------- mixdown builder ----------
    def _arrangement_signature(self) -> str:
        cells: List[Cell] = getattr(self.track_editor, "_cells", [])
        parts = [
            f"{c.path}|{c.lane}|{c.offset_ms}|{getattr(c, 'natural_ms', c.duration_ms)}|tempo={getattr(c, 'tempo', 1.0):.5f}"
            for c in cells
        ]
        return "|".join(parts)

    def _ensure_mixdown_loaded(self, rebuild: bool = False):
        sig = self._arrangement_signature()
        if (not rebuild) and sig == self._mix_sig and self._loaded_player_path == (self._mixdown_tmp_path or ""):
            return

        path = self._render_arrangement_to_temp_wav()
        if not path:
            return

        was_playing = self._transport_playing
        cur_ms = self._arr_time_ms

        def _resume():
            try:
                self.player.media_loaded.disconnect(_resume)
            except Exception:
                pass
            self.on_duration_changed(0)
            self.player.seek(min(cur_ms, self.player.duration_ms()))
            if was_playing:
                self.player.play()
                self._t0_monotonic = time.monotonic()
                self._t0_arr_ms = self._arr_time_ms

        self.player.media_loaded.connect(_resume)
        self.player.load(path, autostart=False)

        self._loaded_player_path = path
        self._mix_sig = sig

    def _render_arrangement_to_temp_wav(self) -> Optional[str]:
        try:
            from pydub import AudioSegment
        except Exception:
            QMessageBox.critical(self, "Chybí pydub", "Pro mixdown je potřeba pydub a FFmpeg v PATH.")
            return None

        cells: List[Cell] = getattr(self.track_editor, "_cells", [])
        total_ms = max(1, int(self.track_editor.totalDurationMs()))
        if not cells:
            return None

        mix = AudioSegment.silent(duration=total_ms)

        for c in cells:
            src_orig = c.path or self._original_path
            if not src_orig or not os.path.isfile(src_orig):
                continue
            tempo = float(getattr(c, "tempo", 1.0))
            src_path = self._get_tempo_variant(src_orig, tempo) if abs(tempo - 1.0) > 1e-6 else src_orig
            try:
                seg = AudioSegment.from_file(src_path)
                seg = seg[:max(0, int(c.duration_ms))]
                if len(seg) <= 0:
                    continue
                mix = mix.overlay(seg, position=max(0, int(c.offset_ms)))
            except Exception as e:
                print("Mixdown segment error:", e)

        try:
            if self._mixdown_tmp_path and os.path.isfile(self._mixdown_tmp_path):
                os.remove(self._mixdown_tmp_path)
        except Exception:
            pass

        tmp_dir = tempfile.gettempdir()
        out_path = os.path.join(tmp_dir, f"pm_arr_mix_{int(time.time() * 1000)}_{random.randint(0, 999999)}.wav")
        try:
            mix.export(out_path, format="wav")
        except Exception as e:
            QMessageBox.critical(self, "Export mixdown selhal", str(e))
            return None

        self._mixdown_tmp_path = out_path
        return out_path

    # ---------- WAV/obálka builder pro hlavní skladbu ----------
    def _load_waveform(self, path: str, buckets: int = 1500):
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(path).set_channels(1)
            sw = seg.sample_width
            max_val = float(1 << (8 * sw - 1))
            arr = np.array(seg.get_array_of_samples(), dtype=np.float32) / max_val

            n = len(arr)
            if n == 0:
                self.timeline.setWaveform(None)
                self.track_editor.setWaveform(None, self._original_duration_ms or 0)
                return

            buckets = max(200, min(buckets, 8000))
            step = int(np.ceil(n / buckets))
            vals = []
            for i in range(0, n, step):
                chunk = arr[i:i+step]
                rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
                vals.append(rms)
            m = max(vals) if vals else 1.0
            wf = None if m <= 0 else [v / m for v in vals]
            self.timeline.setWaveform(wf)
            self.track_editor.setWaveform(wf, self._original_duration_ms or 0)

            self._recompute_total_and_overlays()
            self._arr_time_ms = 0
            self.timeline.setPosition(0)
            self.pos_label.setText(f"{fmt_ms(0)} / {fmt_ms(self._arr_total_ms)}")
        except Exception:
            self.timeline.setWaveform(None)
            self.track_editor.setWaveform(None, self._original_duration_ms or 0)

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

    def play_next_in_filter(self, *, force_play: bool = False):
        if not self._filtered_ids:
            return
        idx = self._index_in_filter(self._current_track_id)
        next_idx = 0 if idx < 0 or idx + 1 >= len(self._filtered_ids) else idx + 1
        self._play_track_id(self._filtered_ids[next_idx])

        # pokud už něco hrálo, pokračuj plynule; případně vynuceně (autoplay)
        if force_play or self._transport_playing:
            self._start_playback()

    def play_previous_in_filter(self, *, force_play: bool = False):
        if not self._filtered_ids:
            return
        # při pauznutém „replay“ chování ponecháme návrat na začátek
        if not force_play and not self._transport_playing and self._arr_time_ms > 10_000:
            self.on_seek_requested(0)
            return
        idx = self._index_in_filter(self._current_track_id)
        prev_idx = len(self._filtered_ids) - 1 if idx <= 0 else idx - 1
        self._play_track_id(self._filtered_ids[prev_idx])
        if force_play or self._transport_playing:
            self._start_playback()

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
        title_for_label = self._track_title_by_id(track_id) or os.path.splitext(os.path.basename(path))[0]
        self.track_editor.setClipLabel(title_for_label)
        self._current_track_id = track_id
        self._gain_regions = []
        self._load_and_play_original(path)

        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == track_id:
                self.list_widget.setCurrentItem(it)
                break

    def _get_tempo_variant(self, src_path: str, tempo: float) -> str:
        """Vrátí cestu k WAV s upraveným tempem (cache podle src+tempo)."""
        key = (os.path.abspath(src_path), round(float(tempo), 5))
        cached = self._tempo_cache.get(key)
        if cached and os.path.isfile(cached):
            return cached
        try:
            try:
                out_path = render_variant(src_path, tempo=tempo)
            except TypeError:
                out_path = render_variant(src_path, tempo)  # starší signatura
        except Exception as e:
            QMessageBox.critical(self, "Tempo", f"Nepodařilo se vytvořit tempo-variantu:\n{e}")
            return src_path
        self._tempo_cache[key] = out_path
        self._tempo_tmp_paths.add(out_path)
        return out_path

    def play_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        self._play_track_id(track_id)
        self._start_playback()

    def _load_and_play_original(self, path: str):
        self._cleanup_tempo_tmp()
        self._original_path = path
        try:
            self._original_duration_ms = probe_duration_ms(path)
        except Exception:
            self._original_duration_ms = None

        title = os.path.splitext(os.path.basename(path))[0]

        # DŮLEŽITÉ: reset aranže pro novou skladbu (vymaže staré buňky/obálku)
        self.track_editor.resetForNewSource(title)

        # teprve teď nastav délku a načti waveform (to založí 1. buňku s novou obálkou)
        self.track_editor.setSourceDuration(self._original_duration_ms or 0)
        self._load_waveform(path)

        self._applied_tempo = 1.0
        self._pending_tempo = 1.0
        self._update_tempo_label(pending=False)
        self.tempo_slider.blockSignals(True);
        self.tempo_slider.setValue(100);
        self.tempo_slider.blockSignals(False)
        self.timeline.setLoopPoints(None, None)

        self._arr_time_ms = 0
        self._mix_sig = ""
        self._recompute_total_and_overlays()

        self._loaded_player_path = None
        self.player.stop()

        self.timeline.setPosition(0)
        self.track_editor.setPlayhead(0)
        self.pos_label.setText(f"{fmt_ms(0)} / {fmt_ms(self._arr_total_ms)}")

    # ---------- tempo / render ----------
    def on_duration_changed(self, _ms_render: int):
        render_total = self.player.duration_ms() or 1
        orig_total = self._original_duration_ms or render_total
        self.track_editor.setSourceDuration(self._original_duration_ms or 0)
        render_total = max(1, int(render_total))
        orig_total = max(1, int(orig_total))
        self._render_to_orig = orig_total / render_total

    def on_tempo_slider_changed(self, val: int):
        self._pending_tempo = val / 100.0
        if self.tempo_slider.isSliderDown():
            self._update_tempo_label(pending=True)
            return
        self._apply_pending_tempo()

    def _reset_tempo(self):
        self._pending_tempo = 1.0
        self.tempo_slider.blockSignals(True)
        self.tempo_slider.setValue(100)
        self.tempo_slider.blockSignals(False)
        self._apply_pending_tempo()

    def _apply_pending_tempo_on_release(self):
        if abs(self._pending_tempo - self._applied_tempo) < 1e-6:
            self._update_tempo_label(pending=False)
            return
        self._apply_pending_tempo()

    def _apply_pending_tempo(self):
        """Aplikuje tempo na AKTUÁLNÍ buňku (přepočet z originálu),
        přerenderuje mixdown, zachová pozici a 1:1 čas."""
        rate = float(self._pending_tempo)
        self._applied_tempo = rate

        # změň tempo jen u vybrané buňky (přepočítá duration_ms z natural_ms)
        self.track_editor.setCurrentCellTempo(rate)
        self._update_tempo_label(pending=False)

        # rebuild mixu se zachováním stavu
        was_playing = self._transport_playing
        cur_ms = self._arr_time_ms
        self._mix_sig = ""
        self._ensure_mixdown_loaded(rebuild=True)
        self.on_seek_requested(min(cur_ms, self._arr_total_ms))
        if was_playing and not self.player.is_playing():
            self.player.play()
        if self._transport_playing:
            self._t0_monotonic = time.monotonic()
            self._t_anchor_ms = self._arr_time_ms

    def _update_tempo_label(self, pending: bool):
        val = self._pending_tempo if (pending and self.tempo_slider.isSliderDown()) else self._applied_tempo
        if pending and self.tempo_slider.isSliderDown():
            self.tempo_label.setText(f"Tempo (buňka): {val:.2f}x (čeká – pusť myš)")
        else:
            self.tempo_label.setText(f"Tempo (buňka): {val:.2f}x (aplikováno)")

    # ---------- Open ----------
    def open_file_direct(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open audio (bez uložení do knihovny)", "", "Audio (*.mp3 *.wav *.flac)")
        if not path:
            return
        self._current_track_id = None
        self._gain_regions = []
        self.track_editor.setClipLabel(os.path.splitext(os.path.basename(path))[0])
        self._load_and_play_original(path)

    # ---------- DnD z editoru ----------
    def _on_external_file_dropped(self, path: str, lane: int, offset_ms: int):
        wf, dur = self._make_waveform_for_path(path, buckets=8000)
        title = os.path.splitext(os.path.basename(path))[0]
        if not wf or dur <= 0:
            QMessageBox.warning(self, "Soubor", "Nepodařilo se načíst zvuk (nepodporovaný?).")
            return
        self.track_editor.addCellWithWaveform(path, title, lane, offset_ms, dur, wf)

    def _on_library_track_dropped(self, track_id: str, lane: int, offset_ms: int):
        path = self.library.get_track_path(track_id)
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Knihovna", "Soubor v knihovně chybí.")
            return
        title = self._track_title_by_id(track_id) or os.path.splitext(os.path.basename(path))[0]
        wf, dur = self._make_waveform_for_path(path, buckets=8000)
        if not wf or dur <= 0:
            QMessageBox.warning(self, "Knihovna", "Nepodařilo se načíst zvuk.")
            return
        self.track_editor.addCellWithWaveform(path, title, lane, offset_ms, dur, wf)

    # ---------- PRACTICE ----------
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

    def _pick_dance_style(self) -> int:
        items = ["LAT", "STT"]
        text, ok = QInputDialog.getItem(self, "Typ tance", "Zvol typ practicu:", items, 0, True)
        if not ok or not text:
            return -1
        text = text.strip()
        try:
            return 1 if text == "LAT" else 0
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

    def _get_save_type(self) -> int:
        dlg = QDialog(self)
        dlg.setWindowTitle("Uložit practice")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Chceš uložit practice do knihovny?"))
        cb = QCheckBox("uložit practice", dlg)
        layout.addWidget(cb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return -1
        return 1 if cb.isChecked() else 0

    def _find_track_for_dance(self, dance_name: str, *, use_ui_filters: bool = True,
                              used_ids: set[str] | None = None) -> Optional[Tuple[str, int]]:
        synonyms = {
            "samba": [r"\bsamba\b"],
            "cha cha": [r"\bcha\b.*\bcha\b", r"\bchacha\b", r"\bcha-cha\b", r"\bcha\s*cha\b"],
            "rumba": [r"\brumba\b", r"\brhumba\b"],
            "paso doble": [r"\bpaso\b.*\bdoble\b", r"\bpasodoble\b", r"\bpaso\b"],
            "jive": [r"\bjive\b"],
            "waltz": [r"(?<!viennese\s)\bwaltz\b"],
            "tango": [r"\btango\b"],
            "viennese waltz": [r"\bviennese\b.*\bwaltz\b", r"\bvalčík\b"],
            "slowfox": [r"\bslow\s*fox\b", r"\bslowfox\b", r"\bfoxtrot\b"],
            "quickstep": [r"\bquick\s*step\b", r"\bquickstep\b"],
        }
        pats = [QRegularExpression(p, QRegularExpression.PatternOption.CaseInsensitiveOption)
                for p in synonyms.get(dance_name, [dance_name])]

        query = (self.search_edit.text() or "").strip().lower() if use_ui_filters else ""
        fav_only = self._favorites_only if use_ui_filters else False

        candidates: List[Tuple[str, str, int]] = []

        try:
            tracks = self.library.list_tracks()
            for t in tracks:
                title = (t.title or "")
                title_l = title.lower()
                if use_ui_filters and query and query not in title_l:
                    continue
                if use_ui_filters and fav_only and not self._is_favorite(t.id):
                    continue
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

        if used_ids:
            fresh = [c for c in candidates if c[0] not in used_ids]
            if fresh:
                candidates = fresh

        tid, path, dur = random.choice(candidates)
        if used_ids is not None:
            used_ids.add(tid)
        return path, dur

    def _make_gap_segment(self, seconds: int):
        from pydub import AudioSegment
        duration_ms = max(0, int(seconds * 1000))
        try:
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
        return AudioSegment.silent(duration=duration_ms)

    def _load_clip(self, path: str, clip_seconds: int, dance: str):
        from pydub import AudioSegment
        seg = AudioSegment.from_file(path)
        need_ms = max(1000, int(clip_seconds * 1000))
        cut = seg if (dance == "paso doble") else (seg[:need_ms] if len(seg) >= need_ms else seg)
        fade = len(cut)//20
        return cut.fade_in(fade).fade_out(fade)

    def generate_practice_and_play(self):
        type = self._pick_dance_style()
        clip_len_s = self._pick_length_seconds()
        if clip_len_s <= 0:
            return
        gap_s = self._pick_gap_seconds()
        if gap_s < 0:
            return
        dances = ["samba", "cha cha", "rumba", "paso doble", "jive"] if type == 1 else \
                 ["waltz", "tango", "viennese waltz", "slowfox", "quickstep"]

        try:
            from pydub import AudioSegment
        except Exception:
            QMessageBox.critical(self, "Chybí závislosti",
                                 "K generování je potřeba pydub a funkční FFmpeg v PATH.")
            return

        segments: list[AudioSegment] = []
        missing: list[str] = []

        used_ids: set[str] = set()
        for d in dances:
            found = self._find_track_for_dance(d, used_ids=used_ids)
            if not found:
                missing.append(d); continue
            path, _dur_ms = found
            try:
                clip = self._load_clip(path, clip_len_s, d)
                clip = self._normalize_loudness(clip, target_lufs=-16.0)
                segments.append(clip)
                if gap_s > 0 and d != dances[-1]:
                    segments.append(self._make_gap_segment(gap_s))
            except Exception as e:
                missing.append(f"{d} ({os.path.basename(path)}: {e})")

        if not segments:
            QMessageBox.warning(self, "Nenalezeno", "V knihovně se nenašly skladby pro zvolený practice.")
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

        save_to_library = self._get_save_type()

        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = (f"Practice_Latin_{clip_len_s}s_gap{gap_s}s_{stamp}.wav"
                         if type == 1 else
                         f"Practice_Standart_{clip_len_s}s_gap{gap_s}s_{stamp}.wav")
            if save_to_library:
                lib_dir, _db_path = self.library.locations()
                out_dir = os.path.join(lib_dir, "_practice_temp")
                os.makedirs(out_dir, exist_ok=True)
            else:
                out_dir = tempfile.gettempdir()
            out_path = os.path.join(out_dir, base_name)
            final_mix.export(out_path, format="wav")
        except Exception as e:
            QMessageBox.critical(self, "Export WAV selhal", str(e))
            return

        if save_to_library:
            try:
                self.library.add_file(out_path)
            except Exception as e:
                # nepodařilo se zapsat do DB – aspoň přehraj přímo
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
        else:
            self._current_track_id = None
            self._gain_regions = []
            self._load_and_play_original(out_path)
            QMessageBox.information(self, "Practice připraven (dočasně)",
                                    f"Soubor nebyl uložen do knihovny.\nCesta k dočasnému WAV:\n{out_path}")

    # ---------- pomocné ----------
    def _check_autonext(self):
        if not self._auto_next_chk.isChecked():
            return
        # Dohráno a nic nehraje → přehraj další
        if (not self._transport_playing) and self._arr_time_ms >= self._arr_total_ms and self._filtered_ids:
            try:
                self.play_next_in_filter(force_play=True)
            except Exception:
                pass

    def _arr_time_from_player(self) -> int:
        return self._arr_time_ms

    def _track_title_by_id(self, track_id: str) -> str:
        try:
            for t in self.library.list_tracks():
                if t.id == track_id:
                    return (t.title or "").strip()
        except Exception:
            pass
        return ""

    def _make_waveform_for_path(self, path: str, buckets: int = 8000) -> tuple[list[float] | None, int]:
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(path).set_channels(1)
            duration_ms = int(len(seg))
            sw = seg.sample_width
            max_val = float(1 << (8 * sw - 1))
            import numpy as np
            arr = np.array(seg.get_array_of_samples(), dtype=np.float32) / max_val
            if len(arr) == 0:
                return None, duration_ms
            buckets = max(400, min(buckets, 20000))
            step = int(np.ceil(len(arr) / buckets))
            vals = []
            for i in range(0, len(arr), step):
                chunk = arr[i:i + step]
                rms = float(np.sqrt((chunk * chunk).mean())) if len(chunk) else 0.0
                vals.append(rms)
            m = max(vals) if vals else 1.0
            wf = None if m <= 0 else [v / m for v in vals]
            return wf, duration_ms
        except Exception:
            return None, 0

    def _toggle_fullscreen(self):
        """Přepne okno mezi fullscreen a normálním režimem."""
        if not hasattr(self, "_normal_geometry"):
            self._normal_geometry = None

        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _exit_fullscreen(self):
        """Opustí fullscreen, pokud v něm zrovna jsme."""
        if self.isFullScreen():
            self.showNormal()

    def _cleanup_tempo_tmp(self):
        try:
            for p in list(self._tempo_tmp_paths):
                try:
                    if os.path.isfile(p) and p != (self._mixdown_tmp_path or ""):
                        os.remove(p)
                except Exception:
                    pass
                finally:
                    self._tempo_tmp_paths.discard(p)
        except Exception:
            pass
        self._tempo_cache.clear()

    def _apply_player_rate_or_render(self):
        """Tempo vždy vyrenderuj z originálu a pak přenačti přehrávání/mixdown."""
        rate = float(self._applied_tempo)

        src = self._original_path
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "Tempo", "Nenalezen originální zdroj pro změnu tempa.")
            return

        was_playing = self._transport_playing
        cur_ms = self._arr_time_ms

        # render z originálu (žádné řetězení)
        try:
            try:
                out_path = render_variant(src, tempo=rate)  # pokud tvoje verze umí pojmenovaný argument
            except TypeError:
                out_path = render_variant(src, rate)  # fallback na poziční
        except Exception as e:
            QMessageBox.critical(self, "Tempo", f"Nepodařilo se změnit tempo:\n{e}")
            return

        # ulož a ukliď starý tempo-render
        self._cleanup_tempo_tmp()
        self._tempo_tmp_path = out_path

        # přizpůsob délku první "originální" buňky (bez cesty) tak, aby seděla vizuálně
        try:
            orig_ms = int(self._original_duration_ms or 0)
            new_ms = int(round(orig_ms / max(1e-6, rate)))
            changed = False
            for c in getattr(self.track_editor, "_cells", []):
                if not c.path:  # první buňka odkazující na originál
                    if c.duration_ms != new_ms:
                        c.duration_ms = new_ms
                        changed = True
            if changed:
                self.track_editor.arrangementChanged.emit()
        except Exception:
            pass

        # invaliduj signaturu a přerenderuj mixdown (už z nové tempo-varianty)
        self._mix_sig = ""
        self._ensure_mixdown_loaded(rebuild=True)

        # vrátíme se na původní pozici a ukotvíme 1:1 čas
        self.on_seek_requested(cur_ms)
        if was_playing and not self.player.is_playing():
            self.player.play()
        if self._transport_playing:
            self._t0_monotonic = time.monotonic()
            self._t_anchor_ms = self._arr_time_ms

    def _normalize_loudness(self, seg, *, target_lufs=-16.0, true_peak_limit_db=-1.0):
        """
        Vrátí segment srovnaný na jednotnou hlasitost.
        1) Preferuje EBU R128 (pyloudnorm), jinak 2) fallback na dBFS (RMS) s headroomem.
        """
        # 1) EBU R128 (pokud je knihovna k dispozici)
        try:
            import pyloudnorm as pyln
            import numpy as _np

            # pydub -> float32 (-1..1), shape: (n,) nebo (n, channels)
            arr = _np.array(seg.get_array_of_samples()).astype(_np.float32)
            peak = float(1 << (8 * seg.sample_width - 1))
            arr = arr / peak
            if seg.channels > 1:
                arr = arr.reshape((-1, seg.channels))

            meter = pyln.Meter(seg.frame_rate)  # EBU R128
            lufs = meter.integrated_loudness(arr)
            gain_db = target_lufs - lufs
            seg = seg.apply_gain(gain_db)

            # omez true-peak, ať nic neclipuje (ponecháme ~ -1 dBFS)
            try:
                allowed_boost = true_peak_limit_db - seg.max_dBFS  # (-1) - (aktuální peak)
                if allowed_boost < 0:
                    seg = seg.apply_gain(allowed_boost)  # záporný – lehce stáhneme
            except Exception:
                pass
            return seg
        except Exception:
            pass

        # 2) Fallback: jednoduché RMS srovnání na cílové dBFS
        target_dbfs = -16.0  # přibližně odpovídá -16 LUFS pro pop/EDM
        gain_db = target_dbfs - seg.dBFS
        # pohlídej headroom (true peak ~ -1 dBFS)
        try:
            allowed_boost = true_peak_limit_db - seg.max_dBFS  # např. -1 - (-3.2) = +2.2 dB
            if gain_db > allowed_boost:
                gain_db = allowed_boost
        except Exception:
            pass
        return seg.apply_gain(gain_db)