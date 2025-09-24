from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QFileDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QMessageBox
)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PracticeMaster – Alpha")
        self.player = None
        self._duration = 0
        self._multimedia_ok = False

        # UI
        self.open_btn = QPushButton("Open…")
        self.play_btn = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")

        self.pos_slider = QSlider(Qt.Orientation.Horizontal)
        self.pos_slider.setRange(0, 1000)

        self.set_a_btn = QPushButton("Set A")
        self.set_b_btn = QPushButton("Set B")
        self.clear_loop_btn = QPushButton("Clear Loop")

        self.tempo_label = QLabel("Tempo: 1.00x")
        self.tempo_slider = QSlider(Qt.Orientation.Horizontal)
        self.tempo_slider.setRange(50, 200)
        self.tempo_slider.setValue(100)

        root = QWidget()
        v = QVBoxLayout(root)
        row1 = QHBoxLayout(); row2 = QHBoxLayout(); row3 = QHBoxLayout()
        row1.addWidget(self.open_btn); row1.addWidget(self.play_btn)
        row1.addWidget(self.pause_btn); row1.addWidget(self.stop_btn)
        row2.addWidget(QLabel("Position")); row2.addWidget(self.pos_slider)
        row3.addWidget(self.set_a_btn); row3.addWidget(self.set_b_btn)
        row3.addWidget(self.clear_loop_btn)
        v.addLayout(row1); v.addLayout(row2); v.addLayout(row3)
        v.addWidget(self.tempo_label); v.addWidget(self.tempo_slider)
        self.setCentralWidget(root)

        # Události UI (před připojením na player)
        self.open_btn.clicked.connect(self.open_file)
        self.play_btn.clicked.connect(lambda: self._safe(lambda: self.player.play()))
        self.pause_btn.clicked.connect(lambda: self._safe(lambda: self.player.pause()))
        self.stop_btn.clicked.connect(lambda: self._safe(lambda: self.player.stop()))
        self.pos_slider.sliderMoved.connect(self.on_seek)
        self.set_a_btn.clicked.connect(lambda: self._safe(lambda: self.player.set_loop_point('A')))
        self.set_b_btn.clicked.connect(lambda: self._safe(lambda: self.player.set_loop_point('B')))
        self.clear_loop_btn.clicked.connect(lambda: self._safe(lambda: self.player.clear_loop()))
        self.tempo_slider.valueChanged.connect(self.on_tempo_change)

        # Zkusíme načíst audio subsystém
        try:
            from audio.player import AudioPlayer
            self.player = AudioPlayer()
            self.player.position_changed.connect(self.on_position)
            self.player.duration_changed.connect(self.on_duration)
            self._multimedia_ok = True
        except Exception as e:
            self._multimedia_ok = False
            QMessageBox.critical(self, "Audio backend error",
                                 f"QtMultimedia / audio backend nelze inicializovat:\n{e}\n\n"
                                 "Aplikace běží, ale bez přehrávání. Zkontroluj instalaci PyQt6/QtMultimedia a Windows Media Foundation.")

    def _safe(self, fn):
        if not self._multimedia_ok or self.player is None:
            QMessageBox.warning(self, "Audio disabled", "Audio backend není dostupný.")
            return
        fn()

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open audio", "", "Audio (*.mp3 *.wav *.flac)")
        if path and self._multimedia_ok and self.player:
            self.player.load(path)

    def on_seek(self, value):
        if self._multimedia_ok and self.player and self._duration:
            pos_ms = int((value / 1000.0) * self._duration)
            self.player.seek(pos_ms)

    def on_position(self, pos_ms: int):
        if self._duration:
            self.pos_slider.blockSignals(True)
            self.pos_slider.setValue(int(1000.0 * pos_ms / self._duration))
            self.pos_slider.blockSignals(False)

    def on_duration(self, dur_ms: int):
        self._duration = dur_ms

    def on_tempo_change(self, slider_val: int):
        factor = slider_val / 100.0
        self.tempo_label.setText(f"Tempo: {factor:.2f}x")
        if not (self._multimedia_ok and self.player and self.player.current_source):
            return
        try:
            from audio.processing import render_tempo_variant
            src = self.player.current_source
            path = render_tempo_variant(src, tempo_factor=factor)
            self.player.load(path, autostart=True)
        except Exception as e:
            QMessageBox.critical(self, "Tempo render error", str(e))
