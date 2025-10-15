# src/ui/track_editor.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QSize
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QMouseEvent, QPaintEvent
from PyQt6.QtWidgets import QWidget


@dataclass
class ClipModel:
    offset_ms: int = 0
    duration_ms: int = 0
    color: QColor = field(default_factory=lambda: QColor(60, 200, 140, 110))


class TrackEditorWidget(QWidget):
    arrangementChanged = pyqtSignal()
    offsetChanged = pyqtSignal(int)
    canvasDurationChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

        self._waveform: Optional[list[float]] = None
        self._source_len_ms: int = 0

        self._clip = ClipModel(0, 0)

        self._grid_ms = 1000
        self._canvas_ms = 60_000

        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_offset_ms = 0

        self._bg = QColor(24, 24, 24)
        self._lane_fill = QColor(35, 35, 35)
        self._grid_col = QColor(60, 60, 60, 110)
        self._border = QColor(130, 130, 130)

    # ---------- API ----------
    def setWaveform(self, wf: Optional[list[float]], source_len_ms: int):
        self._waveform = wf
        self._source_len_ms = max(0, int(source_len_ms or 0))
        self._clip.duration_ms = self._source_len_ms
        self._ensure_canvas_for_clip()
        self.update()
        self.arrangementChanged.emit()

    def currentOffsetMs(self) -> int:
        return int(self._clip.offset_ms)

    # ---------- helpers ----------
    def _ms_to_x(self, ms: int) -> float:
        if self._canvas_ms <= 0:
            return 0.0
        r = self.rect().adjusted(16, 16, -16, -16)
        ratio = max(0.0, min(1.0, ms / float(self._canvas_ms)))
        return r.left() + ratio * r.width()

    def _x_to_ms(self, x: float) -> int:
        r = self.rect().adjusted(16, 16, -16, -16)
        if r.width() <= 0:
            return 0
        ratio = max(0.0, min(1.0, (x - r.left()) / r.width()))
        return int(round(ratio * self._canvas_ms))

    def _ensure_canvas_for_clip(self):
        end_ms = self._clip.offset_ms + self._clip.duration_ms
        if end_ms > self._canvas_ms:
            self._canvas_ms = int(end_ms * 1.1)
            self.canvasDurationChanged.emit(self._canvas_ms)

    # ---------- painting ----------
    def sizeHint(self) -> QSize:
        return QSize(800, 220)

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self._bg)

        lane = self.rect().adjusted(8, 48, -8, -8)
        p.fillRect(lane, self._lane_fill)
        p.setPen(QPen(self._border, 1))
        p.drawRect(lane.adjusted(0, 0, -1, -1))

        # grid
        p.setPen(QPen(self._grid_col, 1))
        ms = 0
        while ms <= self._canvas_ms:
            x = self._ms_to_x(ms)
            p.drawLine(int(x), lane.top(), int(x), lane.bottom())
            ms += self._grid_ms

        # clip rect
        if self._source_len_ms > 0:
            x1 = self._ms_to_x(self._clip.offset_ms)
            x2 = self._ms_to_x(self._clip.offset_ms + self._clip.duration_ms)
            rect = QRectF(x1, lane.top() + 6, x2 - x1, lane.height() - 12)
            p.setBrush(QBrush(self._clip.color))
            p.setPen(QPen(QColor(200, 255, 200), 1.2))
            p.drawRoundedRect(rect, 8, 8)

            # waveform uvnitř klipu (pokud máme)
            if self._waveform:
                wf = self._waveform
                n = len(wf)
                if n >= 2:
                    inner = rect.adjusted(8, 8, -8, -8)
                    mid = inner.center().y()
                    p.setPen(QPen(QColor(230, 255, 240), 1))
                    last_x = inner.left()
                    last_y = mid
                    for i, v in enumerate(wf):
                        x = inner.left() + (i / (n - 1)) * inner.width()
                        amp = float(v)
                        y = mid - amp * (inner.height() * 0.45)
                        p.drawLine(QPointF(last_x, last_y), QPointF(x, y))
                        last_x, last_y = x, y

        p.end()

    # ---------- mouse ----------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        lane = self.rect().adjusted(8, 48, -8, -8)
        x = e.position().x()
        y = e.position().y()
        # click pouze pokud klik do klipu
        x1 = self._ms_to_x(self._clip.offset_ms)
        x2 = self._ms_to_x(self._clip.offset_ms + self._clip.duration_ms)
        clip_rect = QRectF(x1, lane.top() + 6, x2 - x1, lane.height() - 12)
        if clip_rect.contains(x, y):
            self._dragging = True
            self._drag_start_x = x
            self._drag_start_offset_ms = self._clip.offset_ms
            self.setCursor(Qt.CursorShape.SizeHorCursor)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        x = e.position().x()
        if self._dragging:
            dx = x - self._drag_start_x
            lane = self.rect().adjusted(16, 16, -16, -16)
            px_per_ms = lane.width() / max(1, self._canvas_ms)
            delta_ms = int(round(dx / px_per_ms))
            new_off = max(0, self._drag_start_offset_ms + delta_ms)
            # snap na grid
            if self._grid_ms > 0:
                new_off = int(round(new_off / self._grid_ms) * self._grid_ms)
            self._clip.offset_ms = new_off
            self._ensure_canvas_for_clip()
            self.offsetChanged.emit(self._clip.offset_ms)
            self.arrangementChanged.emit()
            self.update()
        else:
            # hover cursor jen nad klipem
            lane = self.rect().adjusted(8, 48, -8, -8)
            x1 = self._ms_to_x(self._clip.offset_ms)
            x2 = self._ms_to_x(self._clip.offset_ms + self._clip.duration_ms)
            clip_rect = QRectF(x1, lane.top() + 6, x2 - x1, lane.height() - 12)
            if clip_rect.contains(x, e.position().y()):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.unsetCursor()  # <- důležité: vrátit kurzor

    def leaveEvent(self, _) -> None:
        # kdyby myš odešla během dragu/hoveru
        if not self._dragging:
            self.unsetCursor()

