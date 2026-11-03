"""胶囊内的小图标，全部用 QPainter 矢量绘制。"""
from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen


class IconKind(Enum):
    RECORD = "record"
    STOP = "stop"
    PLAY = "play"


def _prepare(p: QPainter, color: QColor, opacity: float) -> None:
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))


def draw_record(p: QPainter, center: QPointF, color: QColor,
                opacity: float = 1.0, scale: float = 1.0) -> None:
    _prepare(p, color, opacity)
    r = 5.2 * scale
    p.drawEllipse(center, r, r)
    p.restore()


def draw_stop(p: QPainter, center: QPointF, color: QColor,
              opacity: float = 1.0, scale: float = 1.0) -> None:
    _prepare(p, color, opacity)
    side = 9.6 * scale
    radius = 2.3 * scale
    rect = QRectF(center.x() - side / 2.0, center.y() - side / 2.0, side, side)
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


def draw_play(p: QPainter, center: QPointF, color: QColor,
              opacity: float = 1.0, scale: float = 1.0) -> None:
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    pen = QPen(color, max(0.8, 1.5 * scale))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(color))

    w = 9.6 * scale
    h = 11.2 * scale
    path = QPainterPath()
    path.moveTo(center.x() - w / 2.0 + 1.0 * scale, center.y() - h / 2.0)
    path.lineTo(center.x() + w / 2.0 + 0.9 * scale, center.y())
    path.lineTo(center.x() - w / 2.0 + 1.0 * scale, center.y() + h / 2.0)
    path.closeSubpath()
    p.drawPath(path)
    p.restore()


_DRAWERS = {
    IconKind.RECORD: draw_record,
    IconKind.STOP: draw_stop,
    IconKind.PLAY: draw_play,
}


def draw_icon(p: QPainter, kind: IconKind, center: QPointF, color: QColor,
              opacity: float = 1.0, scale: float = 1.0) -> None:
    _DRAWERS[kind](p, center, color, opacity, scale)
