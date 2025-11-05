# ui/track_editor.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QWheelEvent, QMouseEvent, QPaintEvent,
    QFont, QDragEnterEvent, QDropEvent, QPolygonF
)
from PyQt6.QtWidgets import QAbstractScrollArea


@dataclass
class Cell:
    lane: int
    offset_ms: int
    duration_ms: int
    path: str
    title: str = ""
    waveform: Optional[List[float]] = None  # 0..1 obálka pro tuhle buňku


class TrackEditorWidget(QAbstractScrollArea):
    """
    6 stop – bloky (buňky) se dají posouvat horizontálně i vertikálně.
    Ctrl + kolečko = zoom; kolečko = horizontální scroll.
    DnD: soubory z OS / položky z knihovny.

    Signály:
      arrangementChanged()
      clipOffsetChanged(int)              – offset aktuální (první) buňky (pro historickou kompatibilitu)
      externalFileDropped(path, lane, offset_ms)
      libraryTrackDropped(track_id, lane, offset_ms)
      currentCellChanged(index)
    """

    arrangementChanged = pyqtSignal()
    clipOffsetChanged = pyqtSignal(int)
    externalFileDropped = pyqtSignal(str, int, int)
    libraryTrackDropped = pyqtSignal(str, int, int)
    currentCellChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # vzhled
        self._bg = QColor(30, 30, 30)
        self._grid_major = QColor(70, 70, 70)
        self._grid_minor = QColor(50, 50, 50)
        self._label_col = QColor(0, 0, 0)
        self._cell_fill = QColor(60, 200, 140, 110)
        self._cell_border = QColor(80, 220, 170, 200)
        self._wf_area = QColor(90, 170, 150, 120)
        self._wf_line = QColor(160, 230, 210, 180)
        self._playhead_col = QColor(195, 160, 255)

        # layout
        self._pad_left = 60
        self._pad_right = 12
        self._pad_top = 24
        self._pad_bottom = 12
        self._lane_h = 64
        self._lane_gap = 10
        self._lane_count = 6

        # časová osa
        self._canvas_ms = 0  # dříve 240_000 (4 min) – nyní dynamické podle aranže
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

        # výchozí zdroj (když nic jiného)
        self._source_duration_ms: int = 0
        self._waveform: Optional[List[float]] = None

        # playhead
        self._playhead_ms: int = 0

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

        # DnD
        self.setAcceptDrops(True)

        self._update_scrollbars()

    # ---------- veřejné API ----------
    def setWaveform(self, wf: Optional[List[float]], duration_ms: int) -> None:
        self._waveform = wf[:] if wf else None
        self._source_duration_ms = max(0, int(duration_ms or 0))
        # první buňka (pokud žádná není) – ať má i waveform
        self._ensure_initial_cell()
        if self._cells and self._cells[0].waveform is None:
            self._cells[0].waveform = self._waveform
            self._cells[0].duration_ms = self._source_duration_ms
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
        if self._cells:
            idx = self._current_cell_index if self._current_cell_index is not None else 0
            idx = max(0, min(idx, len(self._cells) - 1))
            return int(self._cells[idx].offset_ms)
        return 0

    # ---- nové helpery pro MainWindow ----
    def currentCell(self) -> Optional[Cell]:
        if self._cells and self._current_cell_index is not None:
            i = max(0, min(self._current_cell_index, len(self._cells) - 1))
            return self._cells[i]
        return None

    def currentCellOffset(self) -> int:
        c = self.currentCell()
        return 0 if c is None else int(c.offset_ms)

    def currentCellDuration(self) -> int:
        c = self.currentCell()
        return 0 if c is None else int(c.duration_ms)

    def currentCellPath(self) -> str:
        c = self.currentCell()
        return "" if c is None else (c.path or "")

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

    def addCellWithWaveform(self,
                            path: str,
                            title: Optional[str],
                            lane: int,
                            offset_ms: int,
                            duration_ms: int,
                            waveform: Optional[List[float]]) -> None:
        """Vytvoří novou buňku (má vlastní délku i obálku)."""
        if not title or not title.strip():
            title = os.path.splitext(os.path.basename(path))[0]
        lane = max(0, min(self._lane_count - 1, int(lane)))
        cell = Cell(
            lane=lane,
            offset_ms=max(0, int(offset_ms)),
            duration_ms=max(1, int(duration_ms)),
            path=path,
            title=title,
            waveform=(waveform[:] if waveform else None),
        )
        self._cells.append(cell)
        self._current_cell_index = len(self._cells) - 1
        self.arrangementChanged.emit()
        self.currentCellChanged.emit(self._current_cell_index)
        self.viewport().update()

    def totalDurationMs(self) -> int:
        total = 0
        for c in self._cells:
            total = max(total, c.offset_ms + max(0, c.duration_ms))
        return total  # čistá délka aranže (bez příměsi canvas_ms)

    def exportMixdownWaveform(self, buckets: int = 3000) -> Optional[List[float]]:
        total_ms = self.totalDurationMs()
        if total_ms <= 0:
            return None
        N = max(300, min(8000, int(buckets)))
        out = [0.0] * N

        # mix = maximum přes všechny buňky v dané pozici (jednoduché a čitelné)
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

    # ---------- interní util ----------
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
            ))
            self._current_cell_index = 0
            self._pending_label_for_next_cell = None
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
        # malý „polštář“ na konci, ať není poslední buňka úplně u okraje
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
        if   minor >= 2000: minor = 2000
        elif minor >= 1000: minor = 1000
        elif minor >= 500:  minor = 500
        elif minor >= 200:  minor = 200
        self._tick_ms_minor = minor

    # ---------- kreslení ----------
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
        font = QFont(p.font()); font.setPointSizeF(font.pointSizeF() * 0.9)
        p.setFont(font)
        while True:
            x = self._ms_to_x(t)
            if x > cr.right():
                break
            if x >= cr.left():
                p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
                label = self._fmt_time(t)
                p.setPen(QPen(QColor(200, 200, 200)))
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
        p.setPen(QPen(QColor(80, 80, 80), 1))
        for lane in range(self._lane_count):
            lr = self._lane_rect(lane)
            p.drawLine(QPointF(cr.left(), lr.bottom()), QPointF(cr.right(), lr.bottom()))

        # cells (všechny řádky)
        for c in self._cells:
            r = self._cell_rect(c)
            if r.right() < cr.left() or r.left() > cr.right():
                continue

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(self._cell_fill))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QPen(self._cell_border, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 6, 6)

            p.setPen(QPen(self._label_col))
            p.drawText(
                r.adjusted(6, 4, -6, -4),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                c.title or ""
            )

            wf = c.waveform
            if wf and c.duration_ms > 0:
                self._draw_smooth_waveform(p, r, wf)

        # playhead
        phx = self._ms_to_x(self._playhead_ms)
        p.setPen(QPen(self._playhead_col, 2.0))
        p.drawLine(QPointF(phx, cr.top()), QPointF(phx, cr.bottom()))
        p.end()

    def _draw_smooth_waveform(self, p: QPainter, r: QRectF, wf: List[float]) -> None:
        n = len(wf)
        if n < 2 or r.width() < 2:
            return

        target_pts = max(100, min(20000, int(r.width() * 1.25)))

        top = QPolygonF(); bot = QPolygonF()
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

    # ---------- myš ----------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        hit = self._hit_cell(pos.x(), pos.y())
        if hit is not None:
            self._current_cell_index = hit
            self.currentCellChanged.emit(hit)
            self._drag_cell_index = hit
            self._dragging = True
            self._drag_x_start = pos.x()
            self._drag_y_start = pos.y()
            self._drag_cell_offset_orig = self._cells[hit].offset_ms
            self._drag_cell_lane_orig = self._cells[hit].lane
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self._current_cell_index = None
        self.viewport().update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        pos = e.position()
        if self._dragging and self._drag_cell_index is not None:
            dx = pos.x() - self._drag_x_start
            dms = int(round(dx / max(1e-6, self._px_per_ms)))
            new_off = max(0, self._drag_cell_offset_orig + dms)

            lane = self._lane_from_y(pos.y())
            c = self._cells[self._drag_cell_index]
            changed = False
            if new_off != c.offset_ms:
                c.offset_ms = new_off
                changed = True
            if lane != c.lane:
                c.lane = lane
                changed = True

            if changed:
                if self._drag_cell_index == 0:
                    self.clipOffsetChanged.emit(self.clipOffset())
                self.arrangementChanged.emit()
                self.viewport().update()
        else:
            if self._hit_cell(pos.x(), pos.y()) is not None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().unsetCursor()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_cell_index = None
            self.viewport().unsetCursor()

    # ---------- kolečko ----------
    def wheelEvent(self, e: QWheelEvent) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle = e.angleDelta().y()
            factor = 1.0 + (0.20 if angle > 0 else -0.20)  # svižnější zoom
            old_ppm = self._px_per_ms
            new_ppm = min(self._max_px_per_ms, max(self._min_px_per_ms, self._px_per_ms * factor))
            if abs(new_ppm - old_ppm) > 1e-6:
                cursor_x = e.position().x()
                cr = self._content_rect()
                # pokud je kurzor mimo obsah, ukotvi na střed
                if not (cr.left() <= cursor_x <= cr.right()):
                    cursor_x = (cr.left() + cr.right()) * 0.5
                cursor_ms = self._x_to_ms(cursor_x)
                self._px_per_ms = new_ppm
                self._choose_ticks()
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

    # ---------- pomocné ----------
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
        """Vymaže celou aranži a připraví widget na novou hlavní skladbu."""
        self._cells.clear()
        self._current_cell_index = None
        self._pending_label_for_next_cell = title  # první buňka získá titulek až při vytvoření
        self._waveform = None
        self._source_duration_ms = 0
        self._playhead_ms = 0
        self._canvas_ms = 0
        self.arrangementChanged.emit()
        self._update_scrollbars()
        self.viewport().update()
