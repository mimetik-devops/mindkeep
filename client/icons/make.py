"""Draws the app icon and writes every file Briefcase asks for.

    python icons/make.py        (from client/, with PySide6 installed)

A rounded clay square with the wordmark's tray glyph in white. The PNGs are the Linux
sizes, `mindstash.ico` is Windows, `mindstash.icns` is macOS — the last two are just
containers around the same PNGs, so no image library is needed beyond Qt. Checked in
so a build never depends on this script; rerun it when the mark changes.
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

HERE = Path(__file__).parent
CLAY = QColor("#c0603d")
CREAM = QColor("#f6f1e9")
SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)


def draw(size: int) -> bytes:
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
    return _png(image)


def _png(image: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def ico(pngs: dict[int, bytes]) -> bytes:
    """An .ico is a directory of images; Vista and later accept PNG-compressed entries."""
    sizes = [s for s in (16, 32, 48, 64, 128, 256) if s in pngs]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, blobs = b"", b""
    for s in sizes:
        data = pngs[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def icns(pngs: dict[int, bytes]) -> bytes:
    """An .icns is typed chunks; these types carry PNG data as is."""
    types = {
        16: b"icp4",
        32: b"icp5",
        64: b"icp6",
        128: b"ic07",
        256: b"ic08",
        512: b"ic09",
        1024: b"ic10",
    }
    body = b"".join(
        kind + struct.pack(">I", 8 + len(pngs[s])) + pngs[s]
        for s, kind in types.items()
        if s in pngs
    )
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def main() -> None:
    QGuiApplication(sys.argv)
    pngs = {s: draw(s) for s in SIZES}
    for s, data in pngs.items():
        (HERE / f"mindstash-{s}.png").write_bytes(data)
    (HERE / "mindstash.png").write_bytes(pngs[512])
    (HERE / "mindstash.ico").write_bytes(ico(pngs))
    (HERE / "mindstash.icns").write_bytes(icns(pngs))
    print("wrote", len(SIZES) + 3, "files to", HERE)


if __name__ == "__main__":
    main()
