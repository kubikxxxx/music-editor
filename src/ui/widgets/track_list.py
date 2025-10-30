# src/ui/widgets/track_list.py
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QDrag

class TrackListWidget(QListWidget):
    """
    QListWidget, který při dragu přibalí `track_id` do mime
    v custom formátu application/x-practicemaster-track-id.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        md = QMimeData()
        # zachovej text (kvůli vizuálnímu dragu)
        md.setText(item.text())
        # přibal náš id z UserRole
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid:
            md.setData("application/x-practicemaster-track-id", str(tid).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(supportedActions)
