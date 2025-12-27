# ui/track_editor.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QWheelEvent, QMouseEvent, QPaintEvent,
    QFont, QDragEnterEvent, QDropEvent, QPolygonF, QAction
)
from PyQt6.QtWidgets import QDialog, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QAbstractScrollArea, QMenu, QLabel
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QTimer

from ui.theme import Theme


@dataclass
class Cell:
    lane: int
    offset_ms: int
    duration_ms: int                 # délka po tempu (aranž)
    path: str                        # zdrojový soubor ("" = původní track v MainWindow)
    title: str = ""
    waveform: Optional[List[float]] = None

    # čas v původním zdroji (pro CUT/trim)
    src_in_ms: int = 0               # odkud ve zdroji začít hrát (v "natural" ms)

    # tempo model
    natural_ms: int = 0              # délka v původním čase (bez tempa)
    tempo: float = 1.0               # 1.0 = bez změny

    # jednoduché efekty na buňku
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    gain_db: float = 0.0

    echo_enabled: bool = False
    echo_delay_ms: int = 180        # zpoždění jedné ozvěny
    echo_decay_db: float = 6.0      # útlum každé další ozvěny (dB)
    echo_repeats: int = 3           # počet opakování


class TrackEditorWidget(QAbstractScrollArea):
    """
    6 stop – bloky (buňky) se dají posouvat horizontálně i vertikálně.
    Ctrl + kolečko = zoom; kolečko = horizontální scroll.
    DnD: soubory z OS / položky z knihovny.

    Nově:
      - pravý klik na buňku: Cut / Delete / Efekty…
    """

    arrangementChanged = pyqtSignal()
    clipOffsetChanged = pyqtSignal(int)
    externalFileDropped = pyqtSignal(str, int, int)
    libraryTrackDropped = pyqtSignal(str, int, int)
    currentCellChanged = pyqtSignal(int)
    loopRangeChanged = pyqtSignal(int, int)
    cellDragStarted = pyqtSignal()
    cellDragFinished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # default theme (přebije applyTheme)
        self._bg = QColor("#0B0B0D")
        self._grid_major = QColor("#2A2A2E")
        self._grid_minor = QColor(30, 30, 36)
        self._label_col = QColor(0, 0, 0)

        self._cell_fill = QColor(212, 175, 55, 120)
        self._cell_border = QColor(212, 175, 55, 210)
        self._wf_area = QColor(212, 175, 55, 35)
        self._wf_line = QColor(255, 225, 140, 170)

        # playhead neutrální
        self._playhead_col = QColor(235, 235, 235, 220)

        # layout
        self._pad_left = 60
        self._pad_right = 12
        self._pad_top = 24
        self._pad_bottom = 12
        self._lane_h = 64
        self._lane_gap = 10
        self._lane_count = 6

        # časová osa
        self._canvas_ms = 0
        self._px_per_ms = 0.10
        self._min_px_per_ms = 0.001
        self._max_px_per_ms = 1.50
        self._tick_ms_major = 10_000
        self._tick_ms_minor = 2_000
        self._choose_ticks()

        # data
        self._cells: List[Cell] = []
        self._current_cell_index: Optional[int] = 0
        self._pending_label_for_next_cell: Optional[str] = None

        # výchozí zdroj
        self._source_duration_ms: int = 0
        self._waveform: Optional[List[float]] = None

        # playhead
        self._playhead_ms: int = 0

        # paint perf
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_StaticContents, True)
        self.setAutoFillBackground(False)

        # debouncing signálů během dragování
        self._arrange_debounce = QTimer(self)
        self._arrange_debounce.setSingleShot(True)
        self._arrange_debounce.timeout.connect(self.arrangementChanged)

        self._clip_debounce = QTimer(self)
        self._clip_debounce.setSingleShot(True)
        self._clip_debounce.timeout.connect(lambda: self.clipOffsetChanged.emit(self.clipOffset()))

        # waveform raster cache
        self._wf_cache = {}  # klíč: (id(cell), w, h, ppmQ, wfId)

        # interakce
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._dragging: bool = False
        self._drag_cell_index: Optional[int] = None
        self._drag_x_start: float = 0.0
        self._drag_y_start: float = 0.0
        self._drag_cell_offset_orig: int = 0
        self._drag_cell_lane_orig: int = 0

        self.setAcceptDrops(True)

        self._update_scrollbars()

        # loop
        self._loop_a_ms: Optional[int] = None
        self._loop_b_ms: Optional[int] = None
        self._loop_dragging: Optional[str] = None
        self._loop_drag_anchor_ms: int = 0

        # pamatujeme si poslední kontext klik (kvůli "Cut here")
        self._last_context_ms: Optional[int] = None

    # ---------- THEME ----------
    def applyTheme(self, theme: Theme) -> None:
        c = theme.colors()

        self._bg = c["bg"]
        self._grid_major = c["border"]
        self._grid_minor = QColor(c["border"].red(), c["border"].green(), c["border"].blue(), 180)

        acc = c["accent"]
        acc_hi = c["accent_hi"]

        self._cell_fill = QColor(acc.red(), acc.green(), acc.blue(), 120)
        self._cell_border = QColor(acc.red(), acc.green(), acc.blue(), 210)
        self._wf_area = QColor(acc.red(), acc.green(), acc.blue(), 35)
        self._wf_line = QColor(acc_hi.red(), acc_hi.green(), acc_hi.blue(), 170)

        base = c["neutral"]
        self._playhead_col = QColor(base.red(), base.green(), base.blue(), 220)

        self._invalidate_all_rasters()
        self.viewport().update()

    # ---------- public API ----------
    def setWaveform(self, wf: Optional[List[float]], duration_ms: int) -> None:
        self._invalidate_all_rasters()
        self._waveform = wf[:] if wf else None
        self._source_duration_ms = max(0, int(duration_ms or 0))
        self._ensure_initial_cell()
        if self._cells and self._cells[0].waveform is None:
            self._cells[0].waveform = self._waveform
            self._cells[0].duration_ms = self._source_duration_ms
            self._cells[0].natural_ms = self._source_duration_ms
            self._cells[0].tempo = 1.0
            self._cells[0].src_in_ms = 0
        self.update()

    def setSourceDuration(self, ms: int) -> None:
        self._source_duration_ms = max(0, int(ms or 0))
        if self._cells and self._cells[0].duration_ms <= 0:
            self._cells[0].duration_ms = self._source_duration_ms
        self._ensure_initial_cell()
        self.update()

    def setPlayhead(self, ms: int) -> None:
        self._playhead_ms = max(0, int(ms or 0))
        self._ensure_playhead_visible()
        self.viewport().update()

    def clipOffset(self) -> int:
        c = self.currentCell()
        return 0 if c is None else int(c.offset_ms)

    def currentCell(self) -> Optional[Cell]:
        if self._cells and self._current_cell_index is not None:
            i = max(0, min(self._current_cell_index, len(self._cells) - 1))
            return self._cells[i]
        return None

    def currentCellTempo(self) -> float:
        c = self.currentCell()
        return 1.0 if c is None else float(c.tempo or 1.0)

    def setCurrentCellTempo(self, tempo: float) -> None:
        c = self.currentCell()
        if not c:
            return
        tempo = max(0.25, min(4.0, float(tempo)))
        c.tempo = tempo
        base = max(1, int(c.natural_ms or c.duration_ms or 1))
        c.duration_ms = max(1, int(round(base / max(1e-6, tempo))))
        self.arrangementChanged.emit()
        self.viewport().update()

    def setClipLabel(self, title: str) -> None:
        if not title:
            return
        if self._cells:
            idx = self._current_cell_index if self._current_cell_index is not None else 0
            idx = max(0, min(idx, len(self._cells) - 1))
            self._cells[idx].title = title
            self.arrangementChanged.emit()
            self.viewport().update()
        else:
            self._pending_label_for_next_cell = title
            self._ensure_initial_cell()

    def addCellWithWaveform(self, path: str, title: Optional[str], lane: int,
                            offset_ms: int, duration_ms: int, waveform: Optional[List[float]]) -> None:
        if not title or not title.strip():
            title = os.path.splitext(os.path.basename(path))[0]
        lane = max(0, min(self._lane_count - 1, int(lane)))
        base_ms = max(1, int(duration_ms))
        cell = Cell(
            lane=lane,
            offset_ms=max(0, int(offset_ms)),
            duration_ms=base_ms,
            path=path,
            title=title,
            waveform=(waveform[:] if waveform else None),
            src_in_ms=0,
            natural_ms=base_ms,
            tempo=1.0,
            fade_in_ms=0,
            fade_out_ms=0,
            gain_db=0.0,
        )
        self._cells.append(cell)
        self._current_cell_index = len(self._cells) - 1
        self._invalidate_all_rasters()
        self._update_scrollbars()
        self.arrangementChanged.emit()
        self.currentCellChanged.emit(self._current_cell_index)
        self.viewport().update()

    def totalDurationMs(self) -> int:
        total = 0
        for c in self._cells:
            total = max(total, c.offset_ms + max(0, c.duration_ms))
        return total

    def exportMixdownWaveform(self, buckets: int = 3000) -> Optional[List[float]]:
        total_ms = self.totalDurationMs()
        if total_ms <= 0:
            return None
        N = max(300, min(8000, int(buckets)))
        out = [0.0] * N

        for c in self._cells:
            if not c.waveform or c.duration_ms <= 0:
                continue
            wf = c.waveform
            n = len(wf)
            for i in range(N):
                t_ms = (i / (N - 1)) * total_ms
                if not (c.offset_ms <= t_ms <= c.offset_ms + c.duration_ms):
                    continue
                s = (t_ms - c.offset_ms) / c.duration_ms
                x = s * (n - 1)
                j = int(x)
                frac = x - j
                v0 = wf[j]
                v1 = wf[j + 1] if j + 1 < n else wf[j]
                v = (1 - frac) * v0 + frac * v1
                if v > out[i]:
                    out[i] = v

        m = max(out) if out else 0.0
        if m > 0:
            out = [min(1.0, max(0.0, v / m)) for v in out]
        return out

    # ---------- CUT / DELETE / EFFECTS ----------
    def splitCurrentCellAtMs(self, cut_arr_ms: int) -> bool:
        """
        Rozdělí aktuální buňku v absolutním čase aranže.
        cut_arr_ms musí ležet uvnitř buňky (ne na hraně).
        """
        c = self.currentCell()
        if not c:
            return False

        cut_arr_ms = int(cut_arr_ms)
        rel = cut_arr_ms - int(c.offset_ms)
        if rel <= 50 or rel >= int(c.duration_ms) - 50:
            return False

        dur = max(1, int(c.duration_ms))
        tempo = float(c.tempo or 1.0)
        natural_total = int(c.natural_ms or max(1, int(round(dur * tempo))))
        natural_total = max(2, natural_total)

        # převod rel (arr time) -> natural time
        left_natural = int(round(rel * (natural_total / dur)))
        left_natural = max(1, min(left_natural, natural_total - 1))
        right_natural = natural_total - left_natural

        left_dur = max(1, int(round(left_natural / max(1e-6, tempo))))
        right_dur = max(1, int(round(right_natural / max(1e-6, tempo))))

        # split waveform (jen vizuálně)
        left_wf, right_wf = self._split_waveform(c.waveform, left_natural, natural_total)

        # levá buňka = uprav původní
        c.duration_ms = left_dur
        c.natural_ms = left_natural
        c.waveform = left_wf

        # pravá buňka = kopie + posuny
        right = Cell(
            lane=c.lane,
            offset_ms=c.offset_ms + rel,
            duration_ms=right_dur,
            path=c.path,
            title=c.title,
            waveform=right_wf,
            src_in_ms=int(getattr(c, "src_in_ms", 0) or 0) + left_natural,
            natural_ms=right_natural,
            tempo=tempo,
            fade_in_ms=int(getattr(c, "fade_in_ms", 0) or 0),
            fade_out_ms=int(getattr(c, "fade_out_ms", 0) or 0),
            gain_db=float(getattr(c, "gain_db", 0.0) or 0.0),
        )

        idx = self._current_cell_index if self._current_cell_index is not None else 0
        idx = max(0, min(idx, len(self._cells) - 1))
        self._cells.insert(idx + 1, right)

        # vyber pravou (často užitečné)
        self._current_cell_index = idx + 1

        self._invalidate_all_rasters()
        self._update_scrollbars()
        self.arrangementChanged.emit()
        self.currentCellChanged.emit(self._current_cell_index)
        self.viewport().update()
        return True

    def deleteCurrentCell(self) -> bool:
        c = self.currentCell()
        if not c:
            return False

        idx = self._current_cell_index if self._current_cell_index is not None else 0
        idx = max(0, min(idx, len(self._cells) - 1))
        self._cells.pop(idx)

        if not self._cells:
            self._current_cell_index = None
        else:
            self._current_cell_index = min(idx, len(self._cells) - 1)

        self._invalidate_all_rasters()
        self._update_scrollbars()
        self.arrangementChanged.emit()
        if self._current_cell_index is not None:
            self.currentCellChanged.emit(self._current_cell_index)
        self.viewport().update()
        return True

    def editCurrentCellEffects(self) -> bool:
        c = self.currentCell()
        if not c:
            return False

        dlg = QDialog(self)
        dlg.setWindowTitle("Efekty buňky")
        layout = QFormLayout(dlg)

        # --- Gain ---
        gain_chk = QCheckBox("Zapnout gain (± dB)", dlg)
        gain_spin = QDoubleSpinBox(dlg)
        gain_spin.setRange(-30.0, 30.0)
        gain_spin.setSingleStep(0.5)
        gain_spin.setDecimals(1)
        gain_spin.setValue(float(getattr(c, "gain_db", 0.0) or 0.0))
        gain_chk.setChecked(abs(gain_spin.value()) > 1e-6)

        # --- Fade in/out ---
        fi_chk = QCheckBox("Fade-in", dlg)
        fi_spin = QDoubleSpinBox(dlg)
        fi_spin.setRange(0.0, 30.0)
        fi_spin.setSingleStep(0.05)
        fi_spin.setDecimals(2)
        fi_spin.setValue(float((getattr(c, "fade_in_ms", 0) or 0) / 1000.0))
        fi_chk.setChecked(fi_spin.value() > 0.0)

        fo_chk = QCheckBox("Fade-out", dlg)
        fo_spin = QDoubleSpinBox(dlg)
        fo_spin.setRange(0.0, 30.0)
        fo_spin.setSingleStep(0.05)
        fo_spin.setDecimals(2)
        fo_spin.setValue(float((getattr(c, "fade_out_ms", 0) or 0) / 1000.0))
        fo_chk.setChecked(fo_spin.value() > 0.0)

        # --- Echo ---
        echo_chk = QCheckBox("Echo", dlg)
        echo_chk.setChecked(bool(getattr(c, "echo_enabled", False)))

        echo_delay = QSpinBox(dlg)
        echo_delay.setRange(20, 2000)
        echo_delay.setSingleStep(10)
        echo_delay.setValue(int(getattr(c, "echo_delay_ms", 180) or 180))

        echo_decay = QDoubleSpinBox(dlg)
        echo_decay.setRange(1.0, 24.0)
        echo_decay.setSingleStep(0.5)
        echo_decay.setDecimals(1)
        echo_decay.setValue(float(getattr(c, "echo_decay_db", 6.0) or 6.0))

        echo_rep = QSpinBox(dlg)
        echo_rep.setRange(1, 12)
        echo_rep.setValue(int(getattr(c, "echo_repeats", 3) or 3))

        # layout
        layout.addRow(gain_chk, gain_spin)
        layout.addRow(fi_chk, fi_spin)
        layout.addRow(fo_chk, fo_spin)
        layout.addRow(echo_chk, QLabel("Zapnout / vypnout", dlg))
        layout.addRow(QLabel("Echo delay (ms):", dlg), echo_delay)
        layout.addRow(QLabel("Echo decay (dB/echo):", dlg), echo_decay)
        layout.addRow(QLabel("Echo repeats:", dlg), echo_rep)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        layout.addRow(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

        # --- apply to cell ---
        c.gain_db = float(gain_spin.value()) if gain_chk.isChecked() else 0.0
        c.fade_in_ms = int(round(fi_spin.value() * 1000)) if fi_chk.isChecked() else 0
        c.fade_out_ms = int(round(fo_spin.value() * 1000)) if fo_chk.isChecked() else 0

        c.echo_enabled = bool(echo_chk.isChecked())
        c.echo_delay_ms = int(echo_delay.value())
        c.echo_decay_db = float(echo_decay.value())
        c.echo_repeats = int(echo_rep.value())

        self.arrangementChanged.emit()
        self.viewport().update()
        return True

    @staticmethod
    def _split_waveform(wf: Optional[List[float]], left_natural: int, natural_total: int
                        ) -> Tuple[Optional[List[float]], Optional[List[float]]]:
        if not wf or natural_total <= 1:
            return wf, None

        n = len(wf)
        if n < 4:
            # moc málo bodů – necháme stejné (vizuál se neřeší)
            return wf, wf

        idx = int(round((left_natural / max(1, natural_total)) * (n - 1)))
        idx = max(1, min(idx, n - 2))

        left = wf[:idx + 1]
        right = wf[idx:]

        # zaruč aspoň 2 body
        if len(left) < 2:
            left = wf[:2]
        if len(right) < 2:
            right = wf[-2:]
        return left, right

    # ---------- internals ----------
    def _ensure_initial_cell(self) -> None:
        if not self._cells and self._source_duration_ms > 0:
            title = self._pending_label_for_next_cell or "Untitled"
            self._cells.append(Cell(
                lane=0,
                offset_ms=0,
                duration_ms=self._source_duration_ms,
                path="",
                title=title,
                waveform=(self._waveform[:] if self._waveform else None),
                src_in_ms=0,
                natural_ms=self._source_duration_ms,
                tempo=1.0,
                fade_in_ms=0,
                fade_out_ms=0,
                gain_db=0.0,
            ))
            self._current_cell_index = 0
            self._pending_label_for_next_cell = None
            self._invalidate_all_rasters()
            self._update_scrollbars()
            self.arrangementChanged.emit()
            self.currentCellChanged.emit(0)

    def _content_rect(self) -> QRectF:
        vr = self.viewport().rect()
        return QRectF(
            vr.left() + self._pad_left,
            vr.top() + self._pad_top,
            max(1, vr.width() - (self._pad_left + self._pad_right)),
            max(1, vr.height() - (self._pad_top + self._pad_bottom)),
        )

    def _ms_to_x(self, ms: int) -> float:
        return self._content_rect().left() + ms * self._px_per_ms - self.horizontalScrollBar().value()

    def _x_to_ms(self, x: float) -> int:
        cr = self._content_rect()
        ms = (x - cr.left() + self.horizontalScrollBar().value()) / max(1e-6, self._px_per_ms)
        return max(0, int(round(ms)))

    def _lane_rect(self, lane: int) -> QRectF:
        cr = self._content_rect()
        y = cr.top() + lane * (self._lane_h + self._lane_gap)
        return QRectF(cr.left(), y, cr.width(), self._lane_h)

    def _cell_rect(self, c: Cell) -> QRectF:
        lr = self._lane_rect(c.lane)
        x = self._ms_to_x(c.offset_ms)
        w = max(4.0, c.duration_ms * self._px_per_ms)
        return QRectF(x, lr.top(), w, lr.height())

    def _update_scrollbars(self) -> None:
        cr = self._content_rect()
        total_ms = max(0, self.totalDurationMs())
        self._canvas_ms = int(total_ms * 1.02)

        full_w = max(1, self._canvas_ms) * self._px_per_ms
        page = int(max(1, cr.width()))
        self.horizontalScrollBar().setPageStep(int(page))
        self.horizontalScrollBar().setRange(0, max(0, int(full_w - page)))
        self.horizontalScrollBar().setSingleStep(int(page / 8))

        total_h = self._pad_top + self._pad_bottom + self._lane_count * self._lane_h + (self._lane_count - 1) * self._lane_gap
        self.setMinimumHeight(int(total_h + self.horizontalScrollBar().sizeHint().height()) + 4)
        self.viewport().update()

    def _choose_ticks(self) -> None:
        px_per_s = self._px_per_ms * 1000.0
        cand = [1, 2, 5, 10, 15, 20, 30, 60, 120]
        target_px = 100.0
        best = 10
        best_err = 1e9
        for s in cand:
            px = s * px_per_s
            err = abs(px - target_px)
            if err < best_err:
                best_err = err
                best = s
        self._tick_ms_major = best * 1000
        minor = max(1, int(self._tick_ms_major / 5))
        if minor >= 2000:
            minor = 2000
        elif minor >= 1000:
            minor = 1000
        elif minor >= 500:
            minor = 500
        elif minor >= 200:
            minor = 200
        self._tick_ms_minor = minor

    # ---------- painting ----------
    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        vr = self.viewport().rect()
        p.fillRect(vr, self._bg)
        cr = self._content_rect()

        # minor grid
        minor = self._tick_ms_minor
        first_minor = (self.horizontalScrollBar().value() / max(1e-6, self._px_per_ms)) // minor * minor
        t = int(first_minor)
        p.setPen(QPen(self._grid_minor, 1))
        while True:
            x = self._ms_to_x(t)
            if x > cr.right():
                break
            if x >= cr.left():
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            t += minor
            if t > self._canvas_ms + 10 * minor:
                break

        # major grid + labels
        major = self._tick_ms_major
        first_major = (self.horizontalScrollBar().value() / max(1e-6, self._px_per_ms)) // major * major
        t = int(first_major)
        p.setPen(QPen(self._grid_major, 1.2))
        font = QFont(p.font())
        font.setPointSizeF(font.pointSizeF() * 0.9)
        p.setFont(font)
        while True:
            x = self._ms_to_x(t)
            if x > cr.right():
                break
            if x >= cr.left():
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                label = self._fmt_time(t)
                p.setPen(QPen(QColor(220, 220, 225)))
                p.drawText(
                    QRectF(x + 3, vr.top() + 2, 80, self._pad_top - 2),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    label
                )
                p.setPen(QPen(self._grid_major, 1.2))
            t += major
            if t > self._canvas_ms + 10 * major:
                break

        # lane separators
        p.setPen(QPen(QColor(32, 32, 38), 1))
        for lane in range(self._lane_count):
            lr = self._lane_rect(lane)
            p.drawLine(QPointF(cr.left(), lr.bottom()), QPointF(cr.right(), lr.bottom()))

        # cells
        for i, c in enumerate(self._cells):
            r = self._cell_rect(c)
            if r.right() < cr.left() or r.left() > cr.right():
                continue

            selected = (self._current_cell_index is not None and i == self._current_cell_index)

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self._cell_fill))
            p.drawRoundedRect(r, 8, 8)

            if selected:
                glow = QRectF(r).adjusted(-2, -2, 2, 2)
                p.setPen(QPen(QColor(self._wf_line.red(), self._wf_line.green(), self._wf_line.blue(), 120), 3.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(glow, 10, 10)

            p.setPen(QPen(self._cell_border, 1.8 if selected else 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 8, 8)

            # title
            p.setPen(QPen(self._label_col))
            p.drawText(
                r.adjusted(8, 6, -8, -6),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                c.title or ""
            )

            # effects line (malý text dole)
            fx = []
            fi = int(getattr(c, "fade_in_ms", 0) or 0)
            fo = int(getattr(c, "fade_out_ms", 0) or 0)
            gd = float(getattr(c, "gain_db", 0.0) or 0.0)
            if fi > 0:
                fx.append(f"FI {fi/1000:.2f}s")
            if fo > 0:
                fx.append(f"FO {fo/1000:.2f}s")
            if abs(gd) > 1e-6:
                fx.append(f"{gd:+.1f}dB")
            if fx:
                p.setPen(QPen(QColor(0, 0, 0, 160)))
                p.drawText(
                    r.adjusted(8, 6, -8, -6),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                    "  ".join(fx)
                )

            # waveform
            wf = c.waveform
            if wf and c.duration_ms > 0:
                raster = self._ensure_waveform_raster(c, r)
                if raster is not None:
                    p.drawImage(QPointF(r.left(), r.top()), raster)
                else:
                    self._draw_smooth_waveform(p, r, wf)

        self._paint_loop(p, cr)

        # playhead
        phx = self._ms_to_x(self._playhead_ms)
        p.setPen(QPen(self._playhead_col, 2.2))
        p.drawLine(QPointF(phx, cr.top()), QPointF(phx, cr.bottom()))
        p.end()

    def _draw_smooth_waveform(self, p: QPainter, r: QRectF, wf: List[float]) -> None:
        n = len(wf)
        if n < 2 or r.width() < 2:
            return
        target_pts = max(100, min(20000, int(r.width() * 1.25)))

        top = QPolygonF()
        bot = QPolygonF()
        mid_y = r.center().y()
        half_h = r.height() * 0.42

        for i in range(target_pts):
            t = i / (target_pts - 1)
            x = r.left() + t * r.width()
            xf = t * (n - 1)
            j = int(xf)
            frac = xf - j
            v0 = wf[j]
            v1 = wf[j + 1] if j + 1 < n else wf[j]
            v = (1 - frac) * v0 + frac * v1
            y_top = mid_y - float(v) * half_h
            y_bot = mid_y + float(v) * half_h
            top.append(QPointF(x, y_top))
            bot.append(QPointF(x, y_bot))

        poly = QPolygonF(top)
        for k in range(bot.count() - 1, -1, -1):
            poly.append(bot[k])

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._wf_area))
        p.drawPolygon(poly)

        p.setPen(QPen(self._wf_line, 1.2))
        p.drawPolyline(top)
        p.drawPolyline(bot)

    # ---------- mouse ----------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        # loop handling má prioritu
        if self._handle_loop_mousePress(e):
            return

        pos = e.position()

        # pravý klik: kontextové menu buňky
        if e.button() == Qt.MouseButton.RightButton:
            hit = self._hit_cell(pos.x(), pos.y())
            if hit is None:
                return
            self._current_cell_index = hit
            self.currentCellChanged.emit(hit)
            self._last_context_ms = self._x_to_ms(pos.x())
            self.viewport().update()
            self._show_cell_context_menu(e)
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        hit = self._hit_cell(pos.x(), pos.y())
        if hit is not None:
            self._current_cell_index = hit
            self.currentCellChanged.emit(hit)
            self._drag_cell_index = hit
            self._dragging = True
            self.cellDragStarted.emit()
            self._drag_x_start = pos.x()
            self._drag_y_start = pos.y()
            self._drag_cell_offset_orig = self._cells[hit].offset_ms
            self._drag_cell_lane_orig = self._cells[hit].lane
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._current_cell_index = None
        self.viewport().update()

    def _show_cell_context_menu(self, e: QMouseEvent) -> None:
        menu = QMenu(self)

        act_cut_here = QAction("Cut here", self)
        act_cut_playhead = QAction("Cut at playhead", self)
        menu.addAction(act_cut_here)
        menu.addAction(act_cut_playhead)
        menu.addSeparator()

        act_fx = QAction("Efekty…", self)
        act_fx_clear = QAction("Vymazat efekty", self)
        menu.addAction(act_fx)
        menu.addAction(act_fx_clear)
        menu.addSeparator()

        act_del = QAction("Smazat buňku", self)
        menu.addAction(act_del)

        chosen = menu.exec(self.viewport().mapToGlobal(e.position().toPoint()))
        if not chosen:
            return

        if chosen == act_cut_here:
            ms = self._last_context_ms if self._last_context_ms is not None else self._playhead_ms
            self.splitCurrentCellAtMs(ms)
        elif chosen == act_cut_playhead:
            self.splitCurrentCellAtMs(self._playhead_ms)
        elif chosen == act_fx:
            self.editCurrentCellEffects()
        elif chosen == act_fx_clear:
            c = self.currentCell()
            if c:
                c.fade_in_ms = 0
                c.fade_out_ms = 0
                c.gain_db = 0.0
                self.arrangementChanged.emit()
                self.viewport().update()
        elif chosen == act_del:
            self.deleteCurrentCell()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._handle_loop_mouseMove(e):
            return
        pos = e.position()
        if self._dragging and self._drag_cell_index is not None:
            dx = pos.x() - self._drag_x_start
            dms = int(round(dx / max(1e-6, self._px_per_ms)))
            new_off = max(0, self._drag_cell_offset_orig + dms)

            lane = self._lane_from_y(pos.y())
            c = self._cells[self._drag_cell_index]

            old_rect = self._cell_rect(c)
            changed = False
            if new_off != c.offset_ms:
                c.offset_ms = new_off
                changed = True
            if lane != c.lane:
                c.lane = lane
                changed = True

            if changed:
                new_rect = self._cell_rect(c)
                ur = old_rect.united(new_rect).adjusted(-2, -2, 2, 2).toRect()
                self.viewport().update(ur)

                if self._drag_cell_index == 0:
                    self._clip_debounce.start(30)
                self._arrange_debounce.start(30)
        else:
            if self._hit_cell(pos.x(), pos.y()) is not None or self._hit_loop_handle(pos) is not None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().unsetCursor()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._handle_loop_mouseRelease(e):
            return
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False

            self._arrange_debounce.stop()
            self._clip_debounce.stop()
            idx0 = self._drag_cell_index

            self.cellDragFinished.emit()

            if idx0 == 0:
                self.clipOffsetChanged.emit(self.clipOffset())
            self.arrangementChanged.emit()

            self._drag_cell_index = None
            self.viewport().unsetCursor()

    # ---------- wheel ----------
    def wheelEvent(self, e: QWheelEvent) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle = e.angleDelta().y()
            factor = 1.0 + (0.20 if angle > 0 else -0.20)
            old_ppm = self._px_per_ms
            new_ppm = min(self._max_px_per_ms, max(self._min_px_per_ms, self._px_per_ms * factor))
            if abs(new_ppm - old_ppm) > 1e-6:
                cursor_x = e.position().x()
                cr = self._content_rect()
                if not (cr.left() <= cursor_x <= cr.right()):
                    cursor_x = (cr.left() + cr.right()) * 0.5
                cursor_ms = self._x_to_ms(cursor_x)
                self._px_per_ms = new_ppm
                self._choose_ticks()
                self._invalidate_all_rasters()
                self._update_scrollbars()
                new_x = self._ms_to_x(cursor_ms)
                delta_px = int(new_x - cursor_x)
                sb = self.horizontalScrollBar()
                sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() + delta_px)))
                self.viewport().update()
            e.accept()
            return

        sb = self.horizontalScrollBar()
        step = int(sb.pageStep() / 8)
        delta = -e.angleDelta().y()
        sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() + (step if delta > 0 else -step))))
        e.accept()

    # ---------- DnD ----------
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        md = e.mimeData()
        if md.hasUrls() or md.hasFormat("application/x-library-track-id"):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        md = e.mimeData()
        lane = self._lane_from_y(e.position().y())
        offset_ms = self._x_to_ms(e.position().x())
        if md.hasFormat("application/x-library-track-id"):
            ba = md.data("application/x-library-track-id")
            try:
                track_id = bytes(ba).decode("utf-8").strip()
            except Exception:
                track_id = str(ba, errors="ignore").strip()
            if track_id:
                self.libraryTrackDropped.emit(track_id, lane, offset_ms)
                e.acceptProposedAction()
                return
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if os.path.isfile(path):
                        self.externalFileDropped.emit(path, lane, offset_ms)
                        e.acceptProposedAction()
                        return
        e.ignore()

    # ---------- misc ----------
    def _lane_from_y(self, y: float) -> int:
        cr = self._content_rect()
        rel = y - cr.top()
        lane_block = self._lane_h + self._lane_gap
        if rel < 0:
            return 0
        idx = int(rel // lane_block)
        return max(0, min(self._lane_count - 1, idx))

    def _hit_cell(self, x: float, y: float) -> Optional[int]:
        for i, c in enumerate(self._cells):
            if self._cell_rect(c).contains(QPointF(x, y)):
                return i
        return None

    def _ensure_playhead_visible(self) -> None:
        cr = self._content_rect()
        x = self._ms_to_x(self._playhead_ms)
        sb = self.horizontalScrollBar()
        left = cr.left()
        right = cr.right()
        if x < left + 40:
            sb.setValue(max(sb.minimum(), sb.value() - int((left + 40 - x))))
        elif x > right - 40:
            sb.setValue(min(sb.maximum(), sb.value() + int((x - (right - 40)))))

    @staticmethod
    def _fmt_time(ms: int) -> str:
        s = max(0, int(ms // 1000))
        m, s = divmod(s, 60)
        return f"{m:02d}:{s:02d}"

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._update_scrollbars()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._update_scrollbars()

    def resetForNewSource(self, title: Optional[str] = None) -> None:
        self._invalidate_all_rasters()
        self._cells.clear()
        self._current_cell_index = None
        self._pending_label_for_next_cell = title
        self._waveform = None
        self._source_duration_ms = 0
        self._playhead_ms = 0
        self._canvas_ms = 0
        self.arrangementChanged.emit()
        self._update_scrollbars()
        self.viewport().update()

    # ---------- loop ----------
    def setLoopPoints(self, a_ms: Optional[int], b_ms: Optional[int]) -> None:
        if a_ms is None or b_ms is None or a_ms < 0 or b_ms < 0:
            self._loop_a_ms = None
            self._loop_b_ms = None
            self.viewport().update()
            self.loopRangeChanged.emit(-1, -1)
            return

        a = max(0, int(a_ms))
        b = max(0, int(b_ms))
        if b < a:
            a, b = b, a

        total = max(0, int(self.totalDurationMs()))
        if total > 0:
            a = min(a, total)
            b = min(b, total)

        changed = (a != self._loop_a_ms) or (b != self._loop_b_ms)
        self._loop_a_ms, self._loop_b_ms = a, b
        if changed:
            self.viewport().update()
            self.loopRangeChanged.emit(int(self._loop_a_ms), int(self._loop_b_ms))

    def clearLoop(self) -> None:
        if self._loop_a_ms is None and self._loop_b_ms is None:
            return
        self._loop_a_ms = None
        self._loop_b_ms = None
        self.viewport().update()
        self.loopRangeChanged.emit(-1, -1)

    def currentLoop(self) -> Tuple[Optional[int], Optional[int]]:
        return self._loop_a_ms, self._loop_b_ms

    def _paint_loop(self, p: QPainter, cr: QRectF) -> None:
        if self._loop_a_ms is None or self._loop_b_ms is None:
            return
        a = int(self._loop_a_ms)
        b = int(self._loop_b_ms)
        if b < a:
            a, b = b, a
        xa = self._ms_to_x(a)
        xb = self._ms_to_x(b)

        r = QRectF(max(cr.left(), xa), cr.top(),
                   max(1.0, min(cr.right(), xb) - max(cr.left(), xa)),
                   cr.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(self._cell_fill.red(), self._cell_fill.green(), self._cell_fill.blue(), 55)))
        p.drawRect(r)

        col = QColor(self._wf_line.red(), self._wf_line.green(), self._wf_line.blue(), 230)
        p.setPen(QPen(col, 2.2))
        p.drawLine(QPointF(xa, cr.top()), QPointF(xa, cr.bottom()))
        p.drawLine(QPointF(xb, cr.top()), QPointF(xb, cr.bottom()))

        h = 10.0
        w = 8.0
        for x in (xa, xb):
            tri = QPolygonF([
                QPointF(x, cr.top()),
                QPointF(x - w * 0.6, cr.top() + h),
                QPointF(x + w * 0.6, cr.top() + h),
            ])
            p.setPen(QPen(col, 1.2))
            p.setBrush(QBrush(QColor(self._cell_border.red(), self._cell_border.green(), self._cell_border.blue(), 200)))
            p.drawPolygon(tri)

    def _hit_loop_handle(self, pos: QPointF, tol_px: float = 6.0) -> Optional[str]:
        if self._loop_a_ms is None or self._loop_b_ms is None:
            return None
        xa = self._ms_to_x(int(self._loop_a_ms))
        xb = self._ms_to_x(int(self._loop_b_ms))
        x = pos.x()
        if abs(x - xa) <= tol_px:
            return 'A'
        if abs(x - xb) <= tol_px:
            return 'B'
        return None

    def _handle_loop_mousePress(self, e: QMouseEvent) -> bool:
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        pos = e.position()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            ms = self._x_to_ms(pos.x())
            self._loop_dragging = 'region'
            self._loop_drag_anchor_ms = ms
            self.setLoopPoints(ms, ms)
            return True
        hit = self._hit_loop_handle(pos)
        if hit:
            self._loop_dragging = hit
            return True
        return False

    def _handle_loop_mouseMove(self, e: QMouseEvent) -> bool:
        if not self._loop_dragging:
            return False
        ms = self._x_to_ms(e.position().x())
        if self._loop_dragging == 'A':
            b = self._loop_b_ms if self._loop_b_ms is not None else ms
            self.setLoopPoints(ms, b)
        elif self._loop_dragging == 'B':
            a = self._loop_a_ms if self._loop_a_ms is not None else ms
            self.setLoopPoints(a, ms)
        else:
            self.setLoopPoints(self._loop_drag_anchor_ms, ms)
        return True

    def _handle_loop_mouseRelease(self, e: QMouseEvent) -> bool:
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        if not self._loop_dragging:
            return False
        self._loop_dragging = None
        return True

    # ---------- waveform raster cache ----------
    def _wf_key(self, c: Cell, r: QRectF) -> tuple:
        return (id(c), int(r.width()), int(r.height()), int(round(self._px_per_ms * 1000)), id(c.waveform))

    def _invalidate_all_rasters(self) -> None:
        self._wf_cache.clear()

    def _ensure_waveform_raster(self, c: Cell, r: QRectF) -> Optional[QImage]:
        wf = c.waveform
        if not wf or c.duration_ms <= 0 or r.width() < 2 or r.height() < 2:
            return None
        key = self._wf_key(c, r)
        img = self._wf_cache.get(key)
        if img is not None:
            return img

        w = max(2, int(r.width()))
        h = max(2, int(r.height()))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)

        qp = QPainter(img)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        mid_y = h / 2.0
        half_h = h * 0.42
        n = len(wf)

        pen = QPen(self._wf_line, 1)
        qp.setPen(pen)

        for x in range(w):
            t = x / max(1, (w - 1))
            xf = t * (n - 1)
            j = int(xf)
            frac = xf - j
            v0 = wf[j]
            v1 = wf[j + 1] if j + 1 < n else wf[j]
            v = (1 - frac) * v0 + frac * v1
            y_top = mid_y - float(v) * half_h
            y_bot = mid_y + float(v) * half_h
            qp.drawLine(x, int(y_top), x, int(y_bot))

        qp.end()
        self._wf_cache[key] = img
        return img
