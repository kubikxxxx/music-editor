# ui/theme.py
from __future__ import annotations

from dataclasses import dataclass
from PyQt6.QtGui import QColor


@dataclass
class Theme:
    mode: str            # "dark" | "light"
    accent: QColor       # accent barva

    def colors(self) -> dict[str, QColor]:
        if self.mode == "light":
            bg = QColor("#F5F5F7")
            panel = QColor("#FFFFFF")
            border = QColor("#D2D2D7")
            axis = QColor("#B8B8BD")
            text = QColor("#0A0A0C")
            muted = QColor("#5A5A66")
            neutral = QColor("#141418")   # playhead / handle / ghost (neutrální)
        else:
            bg = QColor("#0B0B0D")
            panel = QColor("#0F0F11")
            border = QColor("#2A2A2E")
            axis = QColor("#2D2D34")
            text = QColor("#EDEDED")
            muted = QColor("#AAAAAF")
            neutral = QColor("#EBEBEB")   # playhead / handle / ghost (neutrální)

        acc = QColor(self.accent)
        acc_hi = QColor(
            min(255, acc.red() + 40),
            min(255, acc.green() + 40),
            min(255, acc.blue() + 40),
        )

        return dict(
            bg=bg, panel=panel, border=border, axis=axis,
            text=text, muted=muted,
            accent=acc, accent_hi=acc_hi,
            neutral=neutral,
        )


def qss_for_theme(th: Theme) -> str:
    c = th.colors()

    bg = c["bg"].name()
    panel = c["panel"].name()
    border = c["border"].name()
    text = c["text"].name()
    accent = c["accent"].name()

    acc_soft = f"rgba({c['accent'].red()},{c['accent'].green()},{c['accent'].blue()},80)"

    return f"""
    QMainWindow, QWidget {{
        background-color: {bg};
        color: {text};
        font-size: 13px;
    }}

    QLabel {{ color: {text}; }}

    QLineEdit {{
        background: {panel};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {accent};
        selection-color: #000;
    }}
    QLineEdit:focus {{ border: 1px solid {accent}; }}

    QListWidget {{
        background: {panel};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 4px;
        outline: 0;
    }}
    QListWidget::item {{
        padding: 7px 10px;
        margin: 1px 2px;
        border-radius: 8px;
    }}
    QListWidget::item:hover {{
        background: {acc_soft};
        color: {text};
    }}
    QListWidget::item:selected {{
        background: {accent};
        color: #000;
    }}

    QPushButton, QToolButton {{
        background-color: {panel};
        color: {text};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 8px 12px;
    }}
    QPushButton:hover, QToolButton:hover {{
        border: 1px solid {accent};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background-color: {bg};
    }}

    QPushButton[pmRole="primary"], QToolButton[pmRole="primary"] {{
        background-color: {accent};
        color: #000;
        border: 1px solid {accent};
        font-weight: 600;
    }}

    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 4px;
        border: 1px solid {border};
        background: {panel};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
    }}

    QSlider::groove:horizontal {{
        height: 6px;
        background: {border};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {accent};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        margin: -6px 0px;
        border-radius: 7px;
        background: {accent};
        border: 1px solid #000;
    }}

    QMenu {{
        background: {panel};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 16px;
        border-radius: 8px;
    }}
    QMenu::item:selected {{
        background: {accent};
        color: #000;
    }}
    QMessageBox {{
        background: {panel};
        color: {text};
    }}

    /* Qt interně používá tyhle objectName */
    QMessageBox QLabel#qt_msgbox_label {{
        color: {text};
        font-size: 13px;
        font-weight: 600;
    }}
    QMessageBox QLabel#qt_msgbox_informative_label {{
        color: {accent};
        font-size: 13px;
    }}

    /* fallback pro ostatní labely v messageboxu (ikona apod.) */
    QMessageBox QLabel {{
        color: {text};
    }}

    QMessageBox QPushButton {{
        min-width: 90px;
        padding: 8px 12px;
        border-radius: 8px;
        background-color: {panel};
        color: {text};
        border: 1px solid {border};
    }}
    QMessageBox QPushButton:hover {{
        border: 1px solid {accent};
    }}
    QMessageBox QPushButton:pressed {{
        background-color: {bg};
    }}

    QSplitter::handle {{ background: {border}; }}
    QSplitter::handle:hover {{ background: {accent}; }}
    """
