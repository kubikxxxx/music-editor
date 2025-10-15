# src/ui/widgets/play_pause_button.py
from PyQt6.QtCore import Qt, QSize, QPoint
from PyQt6.QtGui import QPainter, QPaintEvent, QBrush, QColor, QPolygon
from PyQt6.QtWidgets import QPushButton

class PlayPauseButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bg = QColor("#222")
        self._fg = QColor("#fff")
        self._radius = 8
        self.setMinimumSize(44, 36)
        self.setMaximumHeight(36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Play/Pause (Space)")

    def sizeHint(self) -> QSize:
        return QSize(52, 36)

    def paintEvent(self, e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, self._radius, self._radius)
        p.setBrush(QBrush(self._fg)); p.setPen(Qt.PenStyle.NoPen)
        w = rect.width(); h = rect.height()
        cx = rect.x() + w // 2; cy = rect.y() + h // 2
        if not self.isChecked():
            side = int(min(w, h) * 0.3); x0 = cx - side // 2
            poly = QPolygon([QPoint(x0, cy - side), QPoint(x0, cy + side), QPoint(x0 + int(side * 1.15), cy)])
            p.drawConvexPolygon(poly)
        else:
            bar_w = max(3, int(w * 0.08)); bar_h = int(h * 0.52); gap = int(w * 0.07)
            x1 = cx - gap - bar_w; x2 = cx + gap; y = cy - bar_h // 2
            for x in (x1, x2): p.drawRoundedRect(x, y, bar_w, bar_h, 2, 2)
        p.end()
