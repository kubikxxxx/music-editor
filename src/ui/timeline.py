from __future__ import annotations

from typing import Iterable, Optional, Tuple

from PyQt6.QtCore import Qt, QSize, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QMouseEvent, QPaintEvent, QPolygonF
from PyQt6.QtWidgets import QWidget


class TimelineWidget(QWidget):
    """
    Waveform timeline se smyčkami A/B, přehrávacím kurzorem a pravoklik výběrem.

    Levé tlačítko: scrub (ghost), seek až při uvolnění.
    Pravé tlačítko: kreslení výběru, po uvolnění vyvolá selectionFinalized(a,b,globalX,globalY).
    """

    seekRequested = pyqtSignal(int)
    scrubbed = pyqtSignal(int)
    loopAChanged = pyqtSignal(int)
    loopBChanged = pyqtSignal(int)
    clearLoopRequested = pyqtSignal()

    selectionChanged = pyqtSignal(int, int)
    selectionFinalized = pyqtSignal(int, int, int, int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(84)
        self.setMouseTracking(True)

        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._loop_a_ms: Optional[int] = None
        self._loop_b_ms: Optional[int] = None
        self._waveform: Optional[list[float]] = None

        self._preview_ms: Optional[int] = None

        self._sel_a_ms: Optional[int] = None
        self._sel_b_ms: Optional[int] = None
        self._sel_dragging: bool = False

        self._bg = QColor(30, 30, 30)
        self._axis = QColor(60, 60, 60)
        self._wave = QColor(160, 160, 160)
        self._fill = QColor(110, 110, 110)
        self._playhead = QColor(195, 160, 255)
        self._ghost = QColor(255, 210, 120)
        self._loop_col = QColor(120, 200, 255, 70)
        self._handle_col = QColor(200, 220, 255)
        self._sel_fill = QColor(255, 200, 120, 64)
        self._sel_outline = QColor(255, 200, 120)
        self._pad_left = 8
        self._pad_right = 8
        self._pad_top = 10
        self._pad_bottom = 10

        self._drag_mode: Optional[str] = None  # "scrub" | "A" | "B"
        self._last_hover: Optional[str] = None

    # ---------- public API ----------
    def setDuration(self, ms: int) -> None:
        self._duration_ms = max(0, int(ms or 0))
        self._position_ms = min(self._position_ms, self._duration_ms)
        self.update()

    def duration(self) -> int:
        return self._duration_ms

    def setPosition(self, ms: int) -> None:
        self._position_ms = self._clamp_ms(ms)
        self.update()

    def position(self) -> int:
        return self._position_ms

    def setLoopPoints(self, a_ms: Optional[int], b_ms: Optional[int]) -> None:
        a = None if a_ms is None else self._clamp_ms(a_ms)
        b = None if b_ms is None else self._clamp_ms(b_ms)
        if a is not None and b is not None and a > b:
            a, b = b, a
        self._loop_a_ms, self._loop_b_ms = a, b
        self.update()

    def loopPoints(self) -> Tuple[Optional[int], Optional[int]]:
        return self._loop_a_ms, self._loop_b_ms

    def setWaveform(self, values: Optional[Iterable[float]]) -> None:
        if values is None:
            self._waveform = None
        else:
            arr = [float(v) if v is not None else 0.0 for v in values]
            if not arr:
                self._waveform = None
            else:
                m = max(arr)
                if m > 0:
                    arr = [max(0.0, min(1.0, v / m)) for v in arr]
                else:
                    arr = [0.0 for _ in arr]
                self._waveform = arr
        self.update()

    def selection(self) -> Tuple[Optional[int], Optional[int]]:
        return self._sel_a_ms, self._sel_b_ms

    def clearSelection(self) -> None:
        self._sel_a_ms = None
        self._sel_b_ms = None
        self.update()

    # ---------- helpers ----------
    def _content_rect(self):
        r = self.rect()
        return QRectF(
            r.left() + self._pad_left,
            r.top() + self._pad_top,
            max(1, r.width() - self._pad_left - self._pad_right),
            max(1, r.height() - self._pad_top - self._pad_bottom),
        )

    def _clamp_ms(self, ms: int) -> int:
        return max(0, min(int(ms), self._duration_ms if self._duration_ms > 0 else int(ms)))

    def _ms_to_x(self, ms: int) -> float:
        cr = self._content_rect()
        if self._duration_ms <= 0:
            return cr.left()
        ratio = max(0.0, min(1.0, ms / float(self._duration_ms)))
        return cr.left() + ratio * cr.width()

    def _x_to_ms(self, x: float) -> int:
        cr = self._content_rect()
        if cr.width() <= 0:
            return 0
        ratio = max(0.0, min(1.0, (x - cr.left()) / cr.width()))
        return int(round(ratio * (self._duration_ms or 0)))

    # ---------- painting ----------
    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), self._bg)

        cr = self._content_rect()

        p.setPen(QPen(self._axis, 1))
        mid_y = cr.center().y()
        p.drawLine(QPointF(cr.left(), mid_y), QPointF(cr.right(), mid_y))

        if self._waveform:
            wf = self._waveform
            n = len(wf)
            if n >= 2:
                poly = QPolygonF()
                for i, v in enumerate(wf):
                    x = cr.left() + (i / (n - 1)) * cr.width()
                    amp = float(v)
                    y = mid_y - amp * (cr.height() * 0.48)
                    poly.append(QPointF(x, y))
                for i, v in reversed(list(enumerate(wf))):
                    x = cr.left() + (i / (n - 1)) * cr.width()
                    amp = float(v)
                    y = mid_y + amp * (cr.height() * 0.48)
                    poly.append(QPointF(x, y))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self._fill))
                p.drawPolygon(poly)
                p.setPen(QPen(self._wave, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(poly)

        if self._loop_a_ms is not None and self._loop_b_ms is not None:
            xa = self._ms_to_x(self._loop_a_ms)
            xb = self._ms_to_x(self._loop_b_ms)
            if xb < xa: xa, xb = xb, xa
            p.fillRect(QRectF(xa, cr.top(), xb - xa, cr.height()), self._loop_col)

        # selection overlay
        if self._sel_a_ms is not None and self._sel_b_ms is not None:
            xa = self._ms_to_x(self._sel_a_ms)
            xb = self._ms_to_x(self._sel_b_ms)
            if xb < xa: xa, xb = xb, xa
            p.fillRect(QRectF(xa, cr.top(), xb - xa, cr.height()), self._sel_fill)
            p.setPen(QPen(self._sel_outline, 1, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(xa, cr.top(), xb - xa, cr.height()))

        # loop handles
        p.setPen(QPen(self._handle_col, 2))
        if self._loop_a_ms is not None:
            x = self._ms_to_x(self._loop_a_ms)
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
        if self._loop_b_ms is not None:
            x = self._ms_to_x(self._loop_b_ms)
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))

        # playhead
        p.setPen(QPen(self._playhead, 2.2))
        x = self._ms_to_x(self._position_ms)
        p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))

        # ghost scrub
        if self._drag_mode == "scrub" and self._preview_ms is not None:
            pen = QPen(self._ghost, 1.6, Qt.PenStyle.DashLine)
            p.setPen(pen)
            gx = self._ms_to_x(self._preview_ms)
            p.drawLine(QPointF(gx, cr.top()), QPointF(gx, cr.bottom()))

    # ---------- mouse ----------
    def _hit_handle(self, x: float) -> Optional[str]:
        tol = 6.0
        if self._loop_a_ms is not None and abs(self._ms_to_x(self._loop_a_ms) - x) <= tol:
            return "A"
        if self._loop_b_ms is not None and abs(self._ms_to_x(self._loop_b_ms) - x) <= tol:
            return "B"
        return None

    def mousePressEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()

        if e.button() == Qt.MouseButton.RightButton:
            ms = self._x_to_ms(x)
            self._sel_a_ms = self._sel_b_ms = ms
            self._sel_dragging = True
            self.selectionChanged.emit(self._sel_a_ms, self._sel_b_ms)
            self.update()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        handle = self._hit_handle(x)
        if handle == "A":
            self._drag_mode = "A"; return
        if handle == "B":
            self._drag_mode = "B"; return

        self._drag_mode = "scrub"
        self._preview_ms = self._x_to_ms(x)
        self.scrubbed.emit(self._preview_ms)
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()
        if self._sel_dragging:
            self._sel_b_ms = self._x_to_ms(x)
            self.selectionChanged.emit(min(self._sel_a_ms, self._sel_b_ms),
                                       max(self._sel_a_ms, self._sel_b_ms))
            self.update()
            return

        if self._drag_mode == "scrub":
            self._preview_ms = self._x_to_ms(x)
            self.scrubbed.emit(self._preview_ms)
            self.update()
        elif self._drag_mode == "A":
            ms = self._x_to_ms(x)
            if self._loop_b_ms is not None:
                ms = min(ms, self._loop_b_ms)
            self._loop_a_ms = ms
            self.loopAChanged.emit(ms)
            self.update()
        elif self._drag_mode == "B":
            ms = self._x_to_ms(x)
            if self._loop_a_ms is not None:
                ms = max(ms, self._loop_a_ms)
            self._loop_b_ms = ms
            self.loopBChanged.emit(ms)
            self.update()
        else:
            hover = self._hit_handle(x)
            if hover != self._last_hover:
                self._last_hover = hover
                if hover:
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.unsetCursor()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.RightButton and self._sel_dragging:
            self._sel_dragging = False
            if self._sel_a_ms is not None and self._sel_b_ms is not None:
                a = min(self._sel_a_ms, self._sel_b_ms)
                b = max(self._sel_a_ms, self._sel_b_ms)
                gp = e.globalPosition()
                self.selectionFinalized.emit(a, b, int(gp.x()), int(gp.y()))
            self.update()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self._drag_mode == "scrub":
            if self._preview_ms is not None:
                self.seekRequested.emit(self._preview_ms)
            self._preview_ms = None
            self.update()
        self._drag_mode = None

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._loop_a_ms = None
            self._loop_b_ms = None
            self.clearLoopRequested.emit()
        else:
            self.clearSelection()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(600, 96)
