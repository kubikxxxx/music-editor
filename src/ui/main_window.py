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
from ml.infer import DanceAI
import shutil

import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog,
    QListWidgetItem, QMessageBox, QSplitter, QLineEdit, QSlider, QMenu,
    QSizePolicy, QCheckBox, QInputDialog, QDialog, QDialogButtonBox,
    QColorDialog, QApplication
)
from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QRegularExpression,
    QCoreApplication, QEvent
)
from PyQt6.QtGui import QShortcut, QKeySequence, QColor, QAction

from audio.player import AudioPlayer
from audio.processing import render_variant
from library.manager import Library
from ui.timeline import TimelineWidget

from ui.widgets.play_pause_button import PlayPauseButton
from ui.widgets.win_media import WinMediaKeyFilter
from ui.track_editor import TrackEditorWidget, Cell

from ui.theme import Theme, qss_for_theme


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
        self._transport_timer.setInterval(20)
        self._transport_timer.timeout.connect(self._transport_tick)

        self._t0_monotonic: float | None = None
        self._t_anchor_ms: int = 0

        # --- mixdown cache ---
        self._mix_sig: str = ""
        self._mixdown_tmp_path: Optional[str] = None
        self._loaded_player_path: Optional[str] = None

        self._tempo_cache: dict[tuple[str, float], str] = {}
        self._tempo_tmp_paths: set[str] = set()

        # --- UI ---
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

        self.track_editor = TrackEditorWidget()
        right.addWidget(self.track_editor)

        self.track_editor.cellDragStarted.connect(self._on_cell_drag_started)
        self.track_editor.cellDragFinished.connect(self._on_cell_drag_finished)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)
        self.track_editor.loopRangeChanged.connect(self._on_editor_loop_changed)

        # player
        self.player = AudioPlayer()
        self.player.duration_changed.connect(self.on_duration_changed)

        # timeline signals
        self.timeline.seekRequested.connect(self.on_seek_requested)
        self.timeline.scrubbed.connect(self.on_scrubbed)
        self.timeline.loopAChanged.connect(self.on_loop_a_changed)
        self.timeline.loopBChanged.connect(self.on_loop_b_changed)
        self.timeline.clearLoopRequested.connect(self.on_clear_loop)

        # editor signals
        self.track_editor.clipOffsetChanged.connect(self._on_clip_offset_changed)
        self.track_editor.arrangementChanged.connect(self._on_editor_changed)
        self.track_editor.externalFileDropped.connect(self._on_external_file_dropped)
        self.track_editor.libraryTrackDropped.connect(self._on_library_track_dropped)
        self.track_editor.currentCellChanged.connect(self._on_cell_selected)

        # actions
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

        self._editor_change_timer = QTimer(self)
        self._editor_change_timer.setSingleShot(True)
        self._editor_change_timer.timeout.connect(self._apply_editor_change_coalesced)

        self._editor_dragging = False
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(140)
        self._rebuild_timer.timeout.connect(self._do_rebuild_mixdown)

        self._segment_cache: dict[str, "AudioSegment"] = {}
        self._segment_cache_order: list[str] = []
        self._segment_cache_max = 12

        self._rebuilding: bool = False

        # media keys
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

        # --- THEME state ---
        self._theme = self._load_theme_or_default()
        self.accent_timer = QTimer(self)  # přidej do __init__
        self.accent_timer.timeout.connect(self.updatedynamicaccent)
        self._apply_theme(self._theme)
        self._install_theme_menu()
        self.time_counter = 0.0
        self.dynamic_mode = False

        self.refresh_list(show_locations=True)
        self.repair_if_needed()
        self._ai = DanceAI()
        self._install_export_menu()


        # ---------- helpers ----------
    @staticmethod
    def _purple_btn_css() -> str:
        # nepřebíjej globální black/gold theme
        return ""

    # ---------- THEME (black/gold) ----------
    @staticmethod
    def _black_gold_qss() -> str:
        return """
        /* ---- Base ---- */
        QMainWindow, QWidget {
            background-color: #0B0B0D;
            color: #EDEDED;
            font-size: 13px;
        }
        QLabel { color: #EDEDED; }

        /* ---- Inputs ---- */
        QLineEdit {
            background: #121214;
            border: 1px solid #2A2A2E;
            border-radius: 8px;
            padding: 7px 10px;
            selection-background-color: #D4AF37;
            selection-color: #000000;
        }
        QLineEdit:focus { border: 1px solid #D4AF37; }

        /* ---- Lists ---- */
        QListWidget {
            background: #0F0F11;
            border: 1px solid #2A2A2E;
            border-radius: 10px;
            padding: 4px;
            outline: 0;
        }
        QListWidget::item {
            padding: 7px 10px;
            margin: 1px 2px;
            border-radius: 8px;
        }
        QListWidget::item:hover { background: #1A1A1F; }
        QListWidget::item:selected {
            background: #D4AF37;
            color: #000000;
        }

        /* ---- Buttons (default dark) ---- */
        QPushButton, QToolButton {
            background-color: #16161B;
            color: #EDEDED;
            border: 1px solid #2A2A2E;
            border-radius: 8px;
            padding: 8px 12px;
        }
        QPushButton:hover, QToolButton:hover { border: 1px solid #D4AF37; }
        QPushButton:pressed, QToolButton:pressed { background-color: #101014; }
        QPushButton:disabled, QToolButton:disabled {
            color: #7C7C7C;
            border: 1px solid #232327;
            background: #0F0F11;
        }

        /* ---- Roles ---- */
        QPushButton[pmRole="primary"], QToolButton[pmRole="primary"] {
            background-color: #D4AF37;
            color: #000000;
            border: 1px solid #D4AF37;
            font-weight: 600;
        }
        QPushButton[pmRole="primary"]:hover, QToolButton[pmRole="primary"]:hover {
            background-color: #E2C15A;
            border: 1px solid #E2C15A;
        }
        QPushButton[pmRole="primary"]:pressed, QToolButton[pmRole="primary"]:pressed {
            background-color: #B69127;
            border: 1px solid #B69127;
        }

        QPushButton[pmRole="danger"], QToolButton[pmRole="danger"] {
            background-color: #16161B;
            color: #FFD88A;
            border: 1px solid #B88A2E;
            font-weight: 600;
        }
        QPushButton[pmRole="danger"]:hover, QToolButton[pmRole="danger"]:hover {
            background-color: #241B08;
            border: 1px solid #D4AF37;
            color: #FFDFA3;
        }

        /* ---- Checkboxes ---- */
        QCheckBox { spacing: 8px; }
        QCheckBox::indicator {
            width: 16px; height: 16px;
            border-radius: 4px;
            border: 1px solid #2A2A2E;
            background: #121214;
        }
        QCheckBox::indicator:checked {
            background: #D4AF37;
            border: 1px solid #D4AF37;
        }

        /* ---- Sliders ---- */
        QSlider::groove:horizontal {
            height: 6px;
            background: #26262B;
            border-radius: 3px;
        }
        QSlider::sub-page:horizontal {
            background: #D4AF37;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            width: 14px;
            margin: -6px 0px;
            border-radius: 7px;
            background: #D4AF37;
            border: 1px solid #000000;
        }

        /* ---- Menus ---- */
        QMenu {
            background: #121214;
            border: 1px solid #2A2A2E;
            border-radius: 10px;
            padding: 6px;
        }
        QMenu::item {
            padding: 7px 16px;
            border-radius: 8px;
        }
        QMenu::item:selected {
            background: #D4AF37;
            color: #000000;
        }

        /* ---- Splitter ---- */
        QSplitter::handle { background: #141417; }
        QSplitter::handle:hover { background: #D4AF37; }

        /* ---- Scrollbars ---- */
        QScrollBar:vertical {
            background: #0F0F11;
            width: 12px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #2A2A2E;
            border-radius: 6px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover { background: #D4AF37; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

        /* ---- Tooltips ---- */
        QToolTip {
            background-color: #121214;
            color: #EDEDED;
            border: 1px solid #D4AF37;
            padding: 6px;
            border-radius: 6px;
        }

        /* ---- Optional: custom widgets borders ---- */
        TimelineWidget, TrackEditorWidget, TrackListWidget {
            background: #0F0F11;
            border: 1px solid #2A2A2E;
            border-radius: 10px;
        }
        """

    def _apply_black_gold_theme(self):
        from PyQt6.QtWidgets import QApplication

        # role styling
        self.practice_btn.setProperty("pmRole", "primary")
        self.playpause_btn.setProperty("pmRole", "primary")
        self.delete_btn.setProperty("pmRole", "danger")

        # apply globally (affects dialogs/menus too)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(self._black_gold_qss())
        else:
            self.setStyleSheet(self._black_gold_qss())

        # repolish widgets after setting properties
        for w in (
            self.practice_btn, self.playpause_btn, self.delete_btn,
            self.open_btn, self.prev_btn, self.stop_btn, self.next_btn,
            self.import_btn, self.repair_btn, self.relink_btn,
            self.search_edit, self.list_widget, self.timeline, self.track_editor
        ):
            try:
                w.style().unpolish(w)
                w.style().polish(w)
            except Exception:
                pass
            w.update()

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
            # --- NOVÉ: AI rozpoznání ---
            act_ai = menu.addAction("Rozpoznat taneční styl (AI)…")

            chosen = menu.exec(self.list_widget.mapToGlobal(pos))
            if not chosen:
                return
            if chosen == act_fav:
                self._set_favorite(track_id, not fav_now)
                self.refresh_list(show_locations=False)
            elif chosen == act_play:
                self._play_track_id(track_id)
                self._start_playback()
            elif chosen == act_ai:
                self._recognize_style_for_track_id(track_id)  # << nová metoda níže
        else:
            act_reload = menu.addAction("Obnovit seznam")
            chosen = menu.exec(self.list_widget.mapToGlobal(pos))
            if chosen == act_reload:
                self.refresh_list(show_locations=False)

    def _recognize_style_for_track_id(self, track_id: str):
        path = self.library.get_track_path(track_id)
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Knihovna", "Soubor v knihovně chybí.")
            return

        # jednoduché blokování UI kurzorem (rychlé a nenásilné)
        from PyQt6.QtWidgets import QApplication
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            probs, aux = self._ai.predict_proba_all(path)
        except Exception as e:
            QMessageBox.critical(self, "AI rozpoznání selhalo", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()

        # seřadit od nejvyšší pravděpodobnosti
        items = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)

        # poskládat hezký text s procenty (1 desetinné místo)
        lines = [f"{name}: {p * 100:.1f} %" for name, p in items]
        tempo = aux.get("tempo_med", None)
        if isinstance(tempo, (int, float)) and tempo > 0:
            lines.append("")
            lines.append(f"Odhad BPM (medián): {tempo:.1f}")

        text = "\n".join(lines)

        # zobrazit v dialogu
        title = self._track_title_by_id(track_id) or os.path.basename(path)
        QMessageBox.information(self, f"AI rozpoznání – {title}", text)

    # ---------- shortcuts / events ----------
    def _install_shortcuts(self):
        # PyQt6-safe: vytvořit QShortcut a pak připojit signál; používat Qt.Key.*
        def mk(key, slot):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
            return sc
        mk(Qt.Key.Key_L, self._mark_loop_start)
        sc_end = QShortcut(QKeySequence("Shift+L"), self)
        sc_end.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_end.activated.connect(self._mark_loop_end)

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
        self._recompute_total_and_overlays()
        if self._editor_dragging:
            return
        self._rebuild_timer.start()


    def _recompute_total_and_overlays(self):
        self._arr_total_ms = self.track_editor.totalDurationMs()
        self.timeline.setDuration(self._arr_total_ms)

        wf_mix = self.track_editor.exportMixdownWaveform(buckets=3000)
        self.timeline.setWaveform(wf_mix)

        # theme-aware overlay colors
        c = self._theme.colors()
        empty = QColor(c["border"].red(), c["border"].green(), c["border"].blue(), 90)
        full = QColor(c["border"].red(), c["border"].green(), c["border"].blue(), 55)

        intervals = []
        for cell in getattr(self.track_editor, "_cells", []):
            if cell.duration_ms > 0:
                intervals.append((cell.offset_ms, cell.offset_ms + cell.duration_ms))
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
                overlays.append({"type": "region", "start": cur, "end": a, "color": empty})
            overlays.append({"type": "region", "start": a, "end": b, "color": full})
            cur = b
        if cur < self._arr_total_ms:
            overlays.append({"type": "region", "start": cur, "end": self._arr_total_ms, "color": empty})
        self.timeline.setOverlays(overlays)

        self.timeline.setPosition(min(self._arr_time_ms, self._arr_total_ms))
        self.pos_label.setText(f"{fmt_ms(self._arr_time_ms)} / {fmt_ms(self._arr_total_ms)}")
        self.timeline.update()

    def _transport_tick(self):
        if not self._transport_playing:
            return

        if self._rebuilding:
            return  # během rebuildu UI nehneme

        # Preferuj reálnou pozici z přehrávače
        pos = self._player_position_ms()
        if pos is not None:
            new_ms = int(min(max(0, pos), self._arr_total_ms))
        else:
            if self._t0_monotonic is None:
                return
            elapsed_ms = int((time.monotonic() - self._t0_monotonic) * 1000.0)
            new_ms = int(min(self._t_anchor_ms + max(0, elapsed_ms), self._arr_total_ms))

        # (volitelné) pokud je aktivní loop, můžeš UI wrapovat jak chceš
        a, b = self.track_editor.currentLoop()
        if a is not None and b is not None and b > a and new_ms >= b:
            span = max(1, b - a)
            new_ms = a + (new_ms - b) % span

        self._arr_time_ms = new_ms
        self.timeline.setPosition(new_ms)
        self.track_editor.setPlayhead(new_ms)
        self.pos_label.setText(f"{fmt_ms(new_ms)} / {fmt_ms(self._arr_total_ms)}")

        if new_ms >= self._arr_total_ms and not (a is not None and b is not None and b > a):
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
        self._rebuilding = False

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
        a = None if ms_orig < 0 else int(ms_orig)
        cur_a, cur_b = self.track_editor.currentLoop()
        b = cur_b
        self.track_editor.setLoopPoints(a, b)

    def on_loop_b_changed(self, ms_orig: int):
        b = None if ms_orig < 0 else int(ms_orig)
        cur_a, cur_b = self.track_editor.currentLoop()
        a = cur_a
        self.track_editor.setLoopPoints(a, b)

    def on_clear_loop(self):
        self.track_editor.clearLoop()

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

            # přepočet délky renderu
            self.on_duration_changed(0)

            # přesně seekni na uloženou pozici
            target = min(cur_ms, self.player.duration_ms())
            self.player.seek(target)

            if was_playing:
                # znovu spustit přehrávání až PO seeku
                self.player.play()

            # pevné ukotvení 1:1 času pro UI
            self._arr_time_ms = target
            self.timeline.setPosition(target)
            self.track_editor.setPlayhead(target)
            self.pos_label.setText(f"{fmt_ms(target)} / {fmt_ms(self._arr_total_ms)}")
            self._t0_monotonic = time.monotonic()
            self._t_anchor_ms = self._arr_time_ms

            # znovu rozběhni transport tick jen pokud předtím hrál
            self._transport_playing = bool(was_playing)
            if was_playing and not self._transport_timer.isActive():
                self._transport_timer.start()

            # konec rebuildu
            self._rebuilding = False

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

        def _apply_echo(seg, delay_ms: int, decay_db: float, repeats: int):
            delay_ms = max(1, int(delay_ms or 0))
            repeats = max(1, int(repeats or 0))
            decay_db = abs(float(decay_db or 0.0))
            out = seg
            for i in range(1, repeats + 1):
                d = delay_ms * i
                if d >= len(seg):
                    break
                out = out.overlay(seg.apply_gain(-(decay_db * i)), position=d)
            return out

        cells: List[Cell] = getattr(self.track_editor, "_cells", [])
        total_ms = max(1, int(self.track_editor.totalDurationMs()))
        if not cells:
            return None

        mix = AudioSegment.silent(duration=total_ms)

        for c in cells:
            src_orig = c.path or self._original_path
            if not src_orig or not os.path.isfile(src_orig):
                continue

            tempo = float(getattr(c, "tempo", 1.0) or 1.0)

            # použij tempo-variantu celého souboru (cache) – jako doposud
            src_path = self._get_tempo_variant(src_orig, tempo) if abs(tempo - 1.0) > 1e-6 else src_orig

            try:
                seg_full = self._get_segment_cached(src_path)
                if len(seg_full) <= 0:
                    continue

                # --- výřez v ZDROJI ---
                # src_in_ms je v "natural" čase; u tempo-varianty ho musíme přepočítat
                src_in_nat = int(getattr(c, "src_in_ms", 0) or 0)
                if abs(tempo - 1.0) > 1e-6:
                    src_in_render = int(round(src_in_nat / max(1e-6, tempo)))
                else:
                    src_in_render = src_in_nat

                src_in_render = max(0, min(src_in_render, max(0, len(seg_full) - 1)))

                # cílová délka buňky v přehrávaném čase = duration_ms
                want_ms = max(0, int(getattr(c, "duration_ms", 0) or 0))
                if want_ms <= 0:
                    continue

                seg = seg_full[src_in_render: src_in_render + want_ms]
                if len(seg) <= 0:
                    continue

                # --- efekty na buňku (v PŘEHRÁVANÉM čase) ---
                gain_db = float(getattr(c, "gain_db", 0.0) or 0.0)
                if abs(gain_db) > 1e-6:
                    seg = seg.apply_gain(gain_db)

                fade_in_ms = int(getattr(c, "fade_in_ms", 0) or 0)
                fade_out_ms = int(getattr(c, "fade_out_ms", 0) or 0)

                if fade_in_ms > 0:
                    seg = seg.fade_in(min(fade_in_ms, len(seg)))
                if fade_out_ms > 0:
                    seg = seg.fade_out(min(fade_out_ms, len(seg)))
                echo_on = bool(getattr(c, "echo_enabled", False))
                if echo_on:
                    seg = _apply_echo(
                        seg,
                        delay_ms=int(getattr(c, "echo_delay_ms", 180) or 180),
                        decay_db=float(getattr(c, "echo_decay_db", 6.0) or 6.0),
                        repeats=int(getattr(c, "echo_repeats", 3) or 3),
                    )

                # --- overlay do mixu ---
                mix = mix.overlay(seg, position=max(0, int(getattr(c, "offset_ms", 0) or 0)))

            except Exception as e:
                print("Mixdown segment error:", e)

        # uklid starý mixdown
        try:
            if self._mixdown_tmp_path and os.path.isfile(self._mixdown_tmp_path):
                os.remove(self._mixdown_tmp_path)
        except Exception:
            pass

        tmp_dir = tempfile.gettempdir()
        out_path = os.path.join(
            tmp_dir,
            f"pm_arr_mix_{int(time.time() * 1000)}_{random.randint(0, 999999)}.wav"
        )
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
        try:
            lab, conf, aux = self._ai.predict(path)
            self.info_label.setText(f"AI: {lab} ({conf:.0%}), tempo≈{aux.get('tempo_med', 0):.1f} BPM")
        except Exception:
            pass

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
                              used_ids: set[str] | None = None):
        """
        Hybridní výběr:
        1) Nejprve vybere skladbu podle názvu/BPM přes _find_track_for_dance_baseline.
        2) U vybrané skladby jednorázově ověří styl pomocí AI (self._ai).
        3) Pokud AI potvrdí styl (labels_equal + min_conf), skladbu přijme.
        4) Pokud AI nesedí, zkusí další kandidáta (zase baseline + AI), max. pár pokusů.

        Vrací (path, duration_ms) nebo None.
        Používá used_ids k tomu, aby se skladby v rámci jednoho practice neopakovaly.
        """

        target = (dance_name or "").strip().lower()
        if not target:
            return None

        # kolik různých kandidátů maximálně zkusíme ověřit přes AI
        max_attempts = 3

        # lokální sada „už zkusených, ale AI je zamítlo“
        tried_ids: set[str] = set()

        # helper: najít track_id podle cesty (kvůli used_ids)
        def _track_id_for_path(path: str) -> str | None:
            try:
                for t in self.library.list_tracks():
                    p = self.library.get_track_path(t.id)
                    if p and os.path.abspath(p) == os.path.abspath(path):
                        return t.id
            except Exception:
                pass
            return None

        import os

        attempts = 0
        while attempts < max_attempts:
            effective_used: set[str] = set(used_ids or [])
            effective_used.update(tried_ids)

            cand = self._find_track_for_dance_baseline(
                dance_name,
                use_ui_filters=use_ui_filters,
                used_ids=effective_used,
            )
            if not cand:
                return None

            path, dur_ms = cand
            track_id = _track_id_for_path(path)

            if track_id and track_id in tried_ids:
                attempts += 1
                continue

            # --- AI ověření pouze pro JEDNU vybranou skladbu ---
            try:
                label, conf, aux = self._ai.predict(path)
                min_conf = getattr(self._ai, "min_conf", 0.0)
                if self._ai.labels_equal(target, label) and conf >= min_conf:
                    if used_ids is not None and track_id:
                        used_ids.add(track_id)
                    return path, dur_ms
            except Exception:
                if used_ids is not None and track_id:
                    used_ids.add(track_id)
                return path, dur_ms

            if track_id:
                tried_ids.add(track_id)

            attempts += 1

        effective_used: set[str] = set(used_ids or [])
        effective_used.update(tried_ids)
        cand = self._find_track_for_dance_baseline(
            dance_name,
            use_ui_filters=use_ui_filters,
            used_ids=effective_used,
        )
        if not cand:
            return None

        path, dur_ms = cand
        track_id = _track_id_for_path(path)
        if used_ids is not None and track_id:
            used_ids.add(track_id)
        return path, dur_ms

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
        if not hasattr(self, "_normal_geometry"):
            self._normal_geometry = None

        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()

    def _apply_player_rate_or_render(self):
        """Tempo vždy vyrenderuj z originálu a pak přenačti přehrávání/mixdown."""
        rate = float(self._applied_tempo)

        src = self._original_path
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "Tempo", "Nenalezen originální zdroj pro změnu tempa.")
            return

        was_playing = self._transport_playing
        cur_ms = self._arr_time_ms

        try:
            try:
                out_path = render_variant(src, tempo=rate)
            except TypeError:
                out_path = render_variant(src, rate)
        except Exception as e:
            QMessageBox.critical(self, "Tempo", f"Nepodařilo se změnit tempo:\n{e}")
            return

        self._cleanup_tempo_tmp()
        self._tempo_tmp_path = out_path

        try:
            orig_ms = int(self._original_duration_ms or 0)
            new_ms = int(round(orig_ms / max(1e-6, rate)))
            changed = False
            for c in getattr(self.track_editor, "_cells", []):
                if not c.path:
                    if c.duration_ms != new_ms:
                        c.duration_ms = new_ms
                        changed = True
            if changed:
                self.track_editor.arrangementChanged.emit()
        except Exception:
            pass

        self._mix_sig = ""
        self._ensure_mixdown_loaded(rebuild=True)

        self.on_seek_requested(cur_ms)
        if was_playing and not self.player.is_playing():
            self.player.play()
        if self._transport_playing:
            self._t0_monotonic = time.monotonic()
            self._t_anchor_ms = self._arr_time_ms

    def _normalize_loudness(self, seg, *, target_lufs=-16.0, true_peak_limit_db=-1.0):
        try:
            import pyloudnorm as pyln
            import numpy as _np

            arr = _np.array(seg.get_array_of_samples()).astype(_np.float32)
            peak = float(1 << (8 * seg.sample_width - 1))
            arr = arr / peak
            if seg.channels > 1:
                arr = arr.reshape((-1, seg.channels))

            meter = pyln.Meter(seg.frame_rate)
            lufs = meter.integrated_loudness(arr)
            gain_db = target_lufs - lufs
            seg = seg.apply_gain(gain_db)

            try:
                allowed_boost = true_peak_limit_db - seg.max_dBFS
                if allowed_boost < 0:
                    seg = seg.apply_gain(allowed_boost)
            except Exception:
                pass
            return seg
        except Exception:
            pass

        target_dbfs = -16.0
        gain_db = target_dbfs - seg.dBFS
        try:
            allowed_boost = true_peak_limit_db - seg.max_dBFS
            if gain_db > allowed_boost:
                gain_db = allowed_boost
        except Exception:
            pass
        return seg.apply_gain(gain_db)

    def _on_editor_loop_changed(self, a_ms: int, b_ms: int):
        if a_ms < 0 or b_ms < 0:
            self.player.clear_loop()
            try:
                self.timeline.setLoopPoints(None, None)
            except Exception:
                pass
            return

        a = int(a_ms);
        b = int(b_ms)
        if b < a:
            a, b = b, a
        self.player.set_loop_ms(a=a, b=b)
        try:
            self.timeline.setLoopPoints(a, b)
        except Exception:
            pass

    def _mark_loop_start(self):
        a = self._arr_time_ms
        _, b = self.track_editor.currentLoop()
        if b is None:
            b = a
        self.track_editor.setLoopPoints(a, b)

    def _mark_loop_end(self):
        a, _ = self.track_editor.currentLoop()
        b = self._arr_time_ms
        if a is None:
            a = b
        self.track_editor.setLoopPoints(a, b)

    def _apply_editor_change_coalesced(self):
        self._recompute_total_and_overlays()
        if self._transport_playing or self._arr_time_ms > 0:
            cur = self._arr_time_ms
            self._ensure_mixdown_loaded(rebuild=True)
            self.on_seek_requested(cur)

    def _on_cell_drag_started(self):
        self._editor_dragging = True
        self._rebuild_timer.stop()

    def _on_cell_drag_finished(self):
        self._editor_dragging = False
        self._rebuild_timer.start(10)

    def _do_rebuild_mixdown(self):
        cur = self._arr_time_ms

        self._rebuilding = True
        self._transport_timer.stop()
        try:
            if self.player.is_playing():
                self.player.pause()
        except AttributeError:
            self.player.stop()

        self._mix_sig = ""
        self._ensure_mixdown_loaded(rebuild=True)

        tgt = min(cur, self._arr_total_ms)
        self.timeline.setPosition(tgt)
        self.track_editor.setPlayhead(tgt)
        self.pos_label.setText(f"{fmt_ms(tgt)} / {fmt_ms(self._arr_total_ms)}")

    def _get_segment_cached(self, src_path: str):
        from pydub import AudioSegment
        seg = self._segment_cache.get(src_path)
        if seg is None:
            seg = AudioSegment.from_file(src_path)
            self._segment_cache[src_path] = seg
            self._segment_cache_order.append(src_path)
            if len(self._segment_cache_order) > self._segment_cache_max:
                old = self._segment_cache_order.pop(0)
                self._segment_cache.pop(old, None)
        else:
            try:
                self._segment_cache_order.remove(src_path)
            except ValueError:
                pass
            self._segment_cache_order.append(src_path)
        return seg

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
                    self._segment_cache.pop(p, None)
                    try:
                        self._segment_cache_order.remove(p)
                    except ValueError:
                        pass
        except Exception:
            pass
        self._tempo_cache.clear()

    def _find_track_for_dance_baseline(self, dance_name: str, *, use_ui_filters: bool = True,
                                       used_ids: set[str] | None = None):
        """
        Původní jednoduchý výběr: podle názvu a u waltz/viennese podle BPM.
        Vrací (path, duration_ms) nebo None.
        """
        import re, random, os
        import numpy as _np
        try:
            import librosa
        except Exception:
            librosa = None

        target = (dance_name or "").strip().lower()
        if not target:
            return None

        syn = {
            "samba": [r"\bsamba\b"],
            "cha cha": [r"\bcha\b.*\bcha\b", r"\bchacha\b", r"\bcha-cha\b", r"\bcha\s*cha\b"],
            "rumba": [r"\brumba\b", r"\brhumba\b"],
            "paso doble": [r"\bpaso\b.*\bdoble\b", r"\bpasodoble\b", r"\bpaso\b"],
            "jive": [r"\bjive\b"],
            "waltz": [r"\bwaltz\b", r"\bwalzer\b", r"\bval(č|c)i(k|k)\b", r"\bvalse\b", r"\bviennese\b"],
            "viennese waltz": [r"\bwaltz\b", r"\bwalzer\b", r"\bval(č|c)i(k|k)\b", r"\bvalse\b", r"\bviennese\b"],
            "tango": [r"\btango\b"],
            "slowfox": [r"\bslow\s*fox\b", r"\bslowfox\b", r"\bfoxtrot\b"],
            "quickstep": [r"\bquick\s*step\b", r"\bquickstep\b"],
        }
        pats = [re.compile(p, re.I) for p in syn.get(target, [re.escape(target)])]

        query = (self.search_edit.text() or "").strip().lower() if use_ui_filters else ""
        fav_only = self._favorites_only if use_ui_filters else False

        cands = []
        try:
            tracks = self.library.list_tracks()
            for t in tracks:
                title = (t.title or "")
                tl = title.lower()

                if use_ui_filters and query and query not in tl:
                    continue
                if use_ui_filters and fav_only and not self._is_favorite(t.id):
                    continue
                if used_ids and t.id in used_ids:
                    continue

                if not any(p.search(tl) for p in pats):
                    continue

                path = self.library.get_track_path(t.id)
                if not path or not os.path.isfile(path):
                    continue

                dur = int(getattr(t, "duration_ms", 0)) or probe_duration_ms(path)

                if target in ("waltz", "viennese waltz") and librosa is not None:
                    try:
                        y, sr = librosa.load(path, sr=22050, mono=True, duration=45.0)
                        tempi = librosa.beat.tempo(y=y, sr=sr, aggregate=None)
                        bpm = float(_np.median(tempi)) if tempi is not None and len(tempi) else 0.0
                        if target == "waltz" and bpm >= 55.0:
                            continue
                        if target == "viennese waltz" and bpm <= 35.0:
                            continue
                    except Exception:
                        pass

                cands.append((path, dur))
        except Exception:
            pass

        if not cands:
            return None
        return random.choice(cands)

    def _theme_store_path(self) -> str:
        try:
            lib_dir, _ = self.library.locations()
            if lib_dir:
                return os.path.join(lib_dir, "_theme.json")
        except Exception:
            pass
        return os.path.join(tempfile.gettempdir(), "pm_theme.json")


    def _load_theme_or_default(self) -> Theme:
        default = Theme(mode="dark", accent=QColor("#D4AF37"))
        p = self._theme_store_path()
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Theme(
                    mode=data.get("mode", "dark"),
                    accent=QColor(data.get("accent", "#D4AF37"))
                )
        except Exception:
            pass
        return default

    def _save_theme(self) -> None:
        p = self._theme_store_path()
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"mode": self._theme.mode, "accent": self._theme.accent.name()}, f, indent=2)
        except Exception:
            pass

    def _apply_theme(self, th: Theme) -> None:
        app = QApplication.instance()
        css = qss_for_theme(th)
        if app:
            app.setStyleSheet(css)
        else:
            self.setStyleSheet(css)

        # role tlačítek
        self.practice_btn.setProperty("pmRole", "primary")
        self.playpause_btn.setProperty("pmRole", "primary")
        self.delete_btn.setProperty("pmRole", "danger")

        # custom paint widgets
        try:
            self.timeline.applyTheme(th)
        except Exception:
            pass
        try:
            self.track_editor.applyTheme(th)
        except Exception:
            pass

        # overlays jsou QColor -> vždy přepočítat
        try:
            self._recompute_total_and_overlays()
        except Exception:
            pass

        for w in (self.practice_btn, self.playpause_btn, self.delete_btn):
            try:
                w.style().unpolish(w)
                w.style().polish(w)
            except Exception:
                pass
            w.update()


    def _install_theme_menu(self) -> None:
        m = self.menuBar().addMenu("Vzhled")

        act_dark = QAction("Pozadí: Dark", self, checkable=True)
        act_light = QAction("Pozadí: Light", self, checkable=True)

        def sync_checks():
            act_dark.setChecked(self._theme.mode == "dark")
            act_light.setChecked(self._theme.mode == "light")
        sync_checks()

        def set_mode(mode: str):
            self._theme.mode = mode
            self._apply_theme(self._theme)
            self._save_theme()
            sync_checks()

        act_dark.triggered.connect(lambda: set_mode("dark"))
        act_light.triggered.connect(lambda: set_mode("light"))

        m.addAction(act_dark)
        m.addAction(act_light)
        m.addSeparator()

        def set_accent(hex_color: str):
            self._theme.accent = QColor(hex_color)
            self._apply_theme(self._theme)
            self._save_theme()

        def set_dynamic_accent(self):
            """Dynamický akcent - mění se v QTimeru"""
            import colorsys, math
            self._theme.accent = QColor.fromRgbF(
                0.5 + 0.5 * math.sin(self.time_counter * 2),  # R pulzuje rychle
                0.3 + 0.4 * math.cos(self.time_counter * 1.5),  # G pomaleji
                0.7 + 0.3 * math.sin(self.time_counter * 3),  # B chaos
                1.0
            )
            self.time_counter += 0.01
            self.apply_theme(self._theme)

        m.addAction(QAction("Accent Gold", self, triggered=lambda: self.setaccent("#D4AF37")))
        m.addAction(QAction("Accent Red", self, triggered=lambda: self.setaccent("#FF2A2A")))
        m.addAction(QAction("Accent Purple", self, triggered=lambda: self.setaccent("#8A2BE2")))
        m.addAction(QAction("Accent Blue", self, triggered=lambda: self.setaccent("#0575E5")))



        m.addAction(QAction("Accent Vlastní", self, triggered=self.pickcustom))


        # Dynamic na konci
        self.act_dynamic = QAction("Dynamic RGB", self, checkable=True)
        self.act_dynamic.triggered.connect(self.toggle_dynamic_accent)
        m.addAction(self.act_dynamic)

    def _install_export_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("Soubor")

        act_export = QAction("Exportovat mix…", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_mixdown)
        m.addAction(act_export)

    def export_mixdown(self) -> None:
        """
        Exportuje aktuální aranž (mixdown) do WAV/MP3/FLAC.
        Respektuje CUT (src_in_ms) + efekty (fade/gain/echo) = co máš v renderu.
        """
        # vynutit nový mix (ať je to vždy aktuální)
        try:
            self._ensure_mixdown_loaded(rebuild=True)
        except Exception:
            # fallback: když to spadne, aspoň se pokusíme vyrenderovat přímo
            try:
                self._render_arrangement_to_temp_wav()
            except Exception as e:
                QMessageBox.critical(self, "Export", f"Nepodařilo se vytvořit mix:\n{e}")
                return

        src = self._mixdown_tmp_path
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "Export", "Mixdown není k dispozici (nejdřív něco poskládej).")
            return

        default_name = "PracticeMaster_Mix.wav"
        out_path, flt = QFileDialog.getSaveFileName(
            self,
            "Export mixu",
            default_name,
            "WAV (*.wav);;MP3 (*.mp3);;FLAC (*.flac)"
        )
        if not out_path:
            return

        ext = os.path.splitext(out_path)[1].lower().strip()
        if ext not in (".wav", ".mp3", ".flac"):
            # když uživatel nenapíše příponu, doplň podle filtru
            if "MP3" in flt:
                out_path += ".mp3"
                ext = ".mp3"
            elif "FLAC" in flt:
                out_path += ".flac"
                ext = ".flac"
            else:
                out_path += ".wav"
                ext = ".wav"

        try:
            if ext == ".wav":
                shutil.copyfile(src, out_path)

            else:
                # MP3/FLAC přes pydub (FFmpeg musí být v PATH)
                from pydub import AudioSegment
                seg = AudioSegment.from_file(src)

                if ext == ".mp3":
                    seg.export(out_path, format="mp3", bitrate="192k")
                elif ext == ".flac":
                    seg.export(out_path, format="flac")

            QMessageBox.information(self, "Export hotový", f"Uloženo:\n{out_path}")

        except Exception as e:
            QMessageBox.critical(
                self,
                "Export selhal",
                "Export se nepodařil.\n"
                "Zkontroluj, že máš FFmpeg v PATH (pro MP3/FLAC).\n\n"
                f"Chyba: {e}"
            )

    def toggle_dynamic_accent(self):
        self.dynamic_mode = not self.dynamic_mode
        print(f"DYNAMIC Toggle -> {self.dynamic_mode}")

        if self.dynamic_mode:
            self.time_counter = 0.0
            if not self.accent_timer.isActive():
                self.accent_timer.start(50)
                print("DYNAMIC Timer started!")
        else:
            if self.accent_timer.isActive():
                self.accent_timer.stop()
                print("DYNAMIC Timer stopped!")

    def updatedynamicaccent(self):
        import math

        # inkrement času, ne reset
        self.time_counter += 0.1

        hue = math.sin(self.time_counter)  # whatever, to je ti asi jedno

        r = max(0.2, min(1.0, 0.5 + 0.5 * math.sin(self.time_counter * 3)))
        g = max(0.2, min(1.0, 0.5 + 0.5 * math.cos(self.time_counter * 2)))
        b = max(0.2, min(1.0, 0.7 + 0.3 * math.sin(self.time_counter)))

        newcolor = QColor.fromRgbF(r, g, b, 1.0)
        self._theme.accent = newcolor

        # Tohle je klíčové – znovu aplikovat theme
        self._apply_theme(self._theme)

        # Volitelně repolish některé widgety, pokud applytheme nedělá global stylesheet:
        for w in (
        self.practice_btn, self.playpause_btn, self.delete_btn, self.tempo_slider, self.timeline, self.list_widget):
            try:
                w.style().unpolish(w)
                w.style().polish(w)
                w.update()
            except Exception:
                pass

    def setaccent(self, color_or_hex, dynamic=False):
        """Nastaví accent a vypne dynamic mode."""
        if isinstance(color_or_hex, str):
            self._theme.accent = QColor(color_or_hex)
        else:
            self._theme.accent = color_or_hex

        # VŽDY vypni dynamic při ručním nastavení
        self.dynamic_mode = False
        if self.accent_timer.isActive():
            self.accent_timer.stop()
            print("DYNAMIC Auto-stopped by manual accent!")

        # Sync checkbox (pokud existuje)
        try:
            self.act_dynamic.setChecked(False)
        except AttributeError:
            pass

        self._apply_theme(self._theme)
        print(f"Accent set to {self._theme.accent.name()} (dynamic={dynamic})")
    # Vlastní...
    def pickcustom(self):
        col = QColorDialog.getColor(self._theme.accent, self, "Vyber accent barvu")
        if col.isValid():
            self.setaccent(col)