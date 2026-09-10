"""The app's one icon: the growing-memory mark on a rounded orange square.

Drawn in code so the tray, every window's title bar, the taskbar and the installer
icon (`icons/make.py` calls `paint` too) are the same picture at every size — a
shipped PNG would drift from the tray the first time somebody redrew one of them.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

ORANGE = QColor("#bd450c")
WHITE = QColor("#ffffff")
SIZES = (16, 32, 48, 64, 128, 256)


def paint(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    p.fillPath(tile, ORANGE)
    # One stem becomes two connected leaves: knowledge growing from a shared root.
    pen = QPen(WHITE, max(1.0, s * 0.085), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    mark = QPainterPath()
    mark.moveTo(s * 0.5, s * 0.79)
    mark.lineTo(s * 0.5, s * 0.48)
    mark.cubicTo(s * 0.5, s * 0.30, s * 0.34, s * 0.20, s * 0.21, s * 0.23)
    mark.cubicTo(s * 0.18, s * 0.39, s * 0.31, s * 0.51, s * 0.5, s * 0.48)
    mark.cubicTo(s * 0.5, s * 0.30, s * 0.66, s * 0.20, s * 0.79, s * 0.23)
    mark.cubicTo(s * 0.82, s * 0.39, s * 0.69, s * 0.51, s * 0.5, s * 0.48)
    p.drawPath(mark)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(WHITE)
    p.drawEllipse(QPointF(s * 0.5, s * 0.79), s * 0.07, s * 0.07)
    p.end()
    return image


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(QPixmap.fromImage(paint(size)))
    return result
