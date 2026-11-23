"""The app's one icon: the growing-memory mark on a rounded orange square.

Drawn in code so the tray, every window's title bar, the taskbar and the installer
icon (`icons/make.py` calls `paint` too) are the same picture at every size — a
shipped PNG would drift from the tray the first time somebody redrew one of them.

The geometry is the website's favicon (mindkeep-home `assets/favicon.svg`: the
`logo.svg` path on a 96×96 tile with rx 20), copied in its own 96-unit coordinates —
change it there first, then here, number for number.
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
    p.scale(size / 96, size / 96)  # from here on, the SVG's viewBox
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, 96, 96), 20, 20)
    p.fillPath(tile, ORANGE)
    # One stem becomes two connected leaves: knowledge growing from a shared root.
    pen = QPen(WHITE, 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    mark = QPainterPath()
    mark.moveTo(48, 79)
    mark.lineTo(48, 48)
    mark.cubicTo(48, 30, 32, 20, 19, 23)
    mark.cubicTo(16, 39, 29, 52, 48, 48)
    mark.moveTo(48, 48)
    mark.cubicTo(48, 30, 64, 20, 77, 23)
    mark.cubicTo(80, 39, 67, 52, 48, 48)
    p.drawPath(mark)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(WHITE)
    p.drawEllipse(QPointF(48, 79), 7, 7)
    p.end()
    return image


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(QPixmap.fromImage(paint(size)))
    return result
