"""The app's one icon: a rounded clay square with the wordmark's tray glyph.

Drawn in code so the tray, every window's title bar, the taskbar and the installer
icon (`icons/make.py` calls `paint` too) are the same picture at every size — a
shipped PNG would drift from the tray the first time somebody redrew one of them.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

CLAY = QColor("#c0603d")
CREAM = QColor("#f6f1e9")
SIZES = (16, 32, 48, 64, 128, 256)


def paint(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    p.fillPath(tile, CLAY)
    # the mark: an open tray with a thing dropping into it
    pen = QPen(CREAM, max(1.0, s * 0.085), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    tray = QPainterPath()
    tray.moveTo(s * 0.24, s * 0.52)
    tray.lineTo(s * 0.24, s * 0.74)
    tray.lineTo(s * 0.76, s * 0.74)
    tray.lineTo(s * 0.76, s * 0.52)
    p.drawPath(tray)
    p.drawLine(QPointF(s * 0.5, s * 0.24), QPointF(s * 0.5, s * 0.58))
    p.drawLine(QPointF(s * 0.38, s * 0.47), QPointF(s * 0.5, s * 0.59))
    p.drawLine(QPointF(s * 0.62, s * 0.47), QPointF(s * 0.5, s * 0.59))
    p.end()
    return image


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(QPixmap.fromImage(paint(size)))
    return result
