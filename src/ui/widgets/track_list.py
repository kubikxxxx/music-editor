# src/ui/widgets/track_list.py
from PyQt6.QtWidgets import QListWidget, QAbstractItemView, QListWidgetItem
from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtGui import QDrag, QPixmap, QPainter, QColor, QFontMetrics

MIME_PRIMARY = "application/x-library-track-id"
MIME_COMPAT  = "application/x-practicemaster-track-id"

class TrackListWidget(QListWidget):
    """
    QListWidget, který umožní přetahovat skladby do editoru.
    Do MIME přibalí track_id v klíči, který TrackEditorWidget očekává.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    # Pozn.: v PyQt6 nepoužívej Qt.DropActions v anotaci – neexistuje.
    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if not item:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        if not track_id:
            return

        mime = QMimeData()
        mime.setText(item.text() or str(track_id))
        payload = str(track_id).encode("utf-8")
        mime.setData(MIME_PRIMARY, payload)
        mime.setData(MIME_COMPAT, payload)

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setHotSpot(QPoint(10, 10))
        drag.setPixmap(self._make_drag_pixmap(item))
        drag.exec(Qt.DropAction.CopyAction)

    def _make_drag_pixmap(self, item: QListWidgetItem) -> QPixmap:
        text = item.text() or "Track"
        fm = QFontMetrics(self.font())
        w = min(360, max(120, fm.horizontalAdvance(text) + 24))
        h = fm.height() + 12
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QColor(90, 90, 90, 220))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, 8, 8)
        p.setPen(QColor(255, 255, 255))
        p.drawText(12, h - fm.descent() - 2, text)
        p.end()
        return pm
