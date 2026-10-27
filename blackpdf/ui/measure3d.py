"""Medir en el visor 3D: distancias, ángulos y radios con el cursor pegado a esquinas y aristas."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QVector3D

from ..core.errors import InvalidOptionError
from ..core.model3d import measure3d as core
from . import theme

SNAP_PIXELS = 10.0


def _tr(text: str) -> str:
    return QCoreApplication.translate("Measure3D", text)


def mode_names() -> dict[str, str]:
    return {"distance": _tr("Distancia"), "angle": _tr("Ángulo"), "radius": _tr("Radio")}


def mode_hints() -> dict[str, str]:
    return {
        "distance": _tr("Haz clic en dos puntos."),
        "angle": _tr("Haz clic en tres puntos: el segundo es el vértice del ángulo."),
        "radius": _tr("Haz clic en tres puntos del borde de un agujero o un cilindro."),
    }


def _number(value: float, decimals: int = 2) -> str:
    return QLocale().toString(float(value), "f", decimals)


@dataclass
class Result:
    kind: str
    points: list[core.Vec]
    value: float
    """En unidades del modelo (grados en los ángulos)."""


class Measure3D(QObject):
    """Estado de la herramienta: el visor le pasa el ratón y le pide que se dibuje."""

    changed = Signal()

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.mode = "distance"
        self.unit = "mm"
        self.points: list[core.Vec] = []
        self.results: list[Result] = []
        self.hover_point: core.Vec | None = None
        self.hover_kind = ""
        self.notice = ""
        """Aviso para la barra (puntos en línea...); se borra con el siguiente clic."""
        self._index: core.SnapIndex | None = None
        self._index_key = None
        self._pending: QPointF | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._process_hover)

    # ---------- estado ----------
    @property
    def model_unit_mm(self) -> float:
        """Milímetros por unidad del modelo: lo que diga el archivo o, si no dice nada, la unidad elegida."""
        scene = self.viewer.scene
        if scene is not None and scene.unit_mm:
            return scene.unit_mm
        return core.UNITS_MM[self.unit]

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.points = []
        self.changed.emit()
        self.viewer.update()

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.changed.emit()
        self.viewer.update()

    def cancel(self) -> bool:
        """Descarta la medida a medias; True si había algo que descartar."""
        if not self.points:
            return False
        self.points = []
        self.changed.emit()
        self.viewer.update()
        return True

    def clear(self) -> None:
        self.points = []
        self.results = []
        self.changed.emit()
        self.viewer.update()

    def invalidate(self) -> None:
        """La escena o las piezas visibles han cambiado: el índice de esquinas se rehace al volver a usarlo."""
        self._index = None
        self._index_key = None

    def _snap_index(self) -> core.SnapIndex | None:
        scene = self.viewer.scene
        if scene is None:
            return None
        key = (id(scene), frozenset(self.viewer.hidden))
        if self._index is None or self._index_key != key:
            self.viewer.setCursor(Qt.CursorShape.WaitCursor)
            try:
                self._index = core.SnapIndex(scene, self.viewer.hidden)
            finally:
                self.viewer.setCursor(Qt.CursorShape.CrossCursor)
            self._index_key = key
        return self._index

    # ---------- ratón ----------
    def hover(self, pos: QPointF) -> None:
        self._pending = QPointF(pos)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Deja de seguir al ratón (la herramienta se ha desactivado)."""
        self._timer.stop()
        self._pending = None
        self.hover_point = None

    def _process_hover(self) -> None:
        if self._pending is None or self.viewer.measure_tool is not self:
            return
        started = time.perf_counter()
        self._locate(self._pending)
        self.viewer.update()
        # Con modelos pesados, leer el punto tarda: se espera más entre lecturas para no atascar la ventana.
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._timer.setInterval(int(min(250, max(25, elapsed_ms * 2))))

    def _locate(self, pos: QPointF) -> None:
        world = self.viewer.pick_point(pos)
        index = self._snap_index()
        ray = self.viewer.pick_ray(pos)
        if index is not None and ray is not None:
            origin, direction = ray
            near, growth = self.viewer.snap_tolerance(origin, direction, SNAP_PIXELS)
            hit = QVector3D.dotProduct(world - origin, direction) if world is not None else None
            snapped = index.snap_ray((origin.x(), origin.y(), origin.z()), (direction.x(), direction.y(), direction.z()),
                                     near, growth, hit, self.viewer.clip_values())
            if snapped is not None:
                self.hover_point, self.hover_kind = snapped
                return
        if world is None:
            self.hover_point, self.hover_kind = None, ""
        else:
            self.hover_point, self.hover_kind = (world.x(), world.y(), world.z()), "surface"

    def click(self, pos: QPointF) -> None:
        self._timer.stop()
        self._locate(pos)
        if self.hover_point is None:
            return
        self.notice = ""
        self.points.append(self.hover_point)
        needed = core.POINTS_NEEDED[self.mode]
        if len(self.points) >= needed:
            try:
                value = core.measure_value(self.mode, self.points)
            except InvalidOptionError:
                # Puntos repetidos o en línea: se empieza de nuevo esta medida, avisando.
                self.points = []
                self.notice = (_tr("Los tres puntos están en línea: elige otros.") if self.mode == "radius"
                               else _tr("Hay puntos repetidos: elige otros."))
            else:
                self.results.append(Result(self.mode, list(self.points), value))
                self.points = []
        self.changed.emit()
        self.viewer.update()

    # ---------- textos ----------
    def length_text(self, value: float) -> str:
        converted = core.convert(value, self.model_unit_mm, self.unit)
        decimals = {"mm": 2, "cm": 2, "m": 3, "in": 3, "ft": 3}.get(self.unit, 2)
        return f"{_number(converted, decimals)} {self.unit}"

    def label(self, result: Result) -> str:
        if result.kind == "distance":
            return self.length_text(result.value)
        if result.kind == "angle":
            return f"{_number(result.value, 1)}°"
        return f"R {self.length_text(result.value)}"

    def detail(self, result: Result) -> str:
        """Lo que se cuenta en la barra de medir, más completo que la etiqueta."""
        if result.kind == "distance":
            dx, dy, dz = core.deltas(result.points[0], result.points[1])
            return _tr("Distancia {0}  ·  X {1}  ·  Y {2}  ·  Z {3}").format(
                self.length_text(result.value), self.length_text(dx), self.length_text(dy), self.length_text(dz))
        if result.kind == "angle":
            return _tr("Ángulo {0}°").format(_number(result.value, 2))
        return _tr("Radio {0}  ·  Diámetro {1}").format(self.length_text(result.value),
                                                        self.length_text(result.value * 2))

    # ---------- dibujo ----------
    def _screen(self, point: core.Vec) -> QPointF | None:
        return self.viewer.project(QVector3D(*point))

    def paint(self, painter: QPainter, hover: bool = True) -> None:
        accent = theme.color("accent")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for result in self.results:
            self._paint_result(painter, result, accent)
        if self.points:
            pts = [p for p in (self._screen(point) for point in self.points) if p is not None]
            hover = self._screen(self.hover_point) if hover and self.hover_point is not None else None
            painter.setPen(QPen(accent, 1.6, Qt.PenStyle.DashLine))
            path_points = pts + ([hover] if hover is not None else [])
            for a, b in zip(path_points, path_points[1:]):
                painter.drawLine(a, b)
            for point in pts:
                self._dot(painter, point, accent)
            if self.mode == "distance" and hover is not None and self.hover_point is not None:
                self._pill(painter, hover + QPointF(16, 16), self.length_text(core.distance(self.points[0], self.hover_point)))
        if hover and self.hover_point is not None:
            marker = self._screen(self.hover_point)
            if marker is not None:
                self._marker(painter, marker, self.hover_kind, accent)
        painter.restore()

    def _paint_result(self, painter: QPainter, result: Result, color: QColor) -> None:
        pts = [self._screen(point) for point in result.points]
        if any(p is None for p in pts):
            return
        painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if result.kind == "distance":
            painter.drawLine(pts[0], pts[1])
            anchor = (pts[0] + pts[1]) / 2
        elif result.kind == "angle":
            painter.drawLine(pts[1], pts[0])
            painter.drawLine(pts[1], pts[2])
            anchor = pts[1]
        else:
            center, radius, normal = core.circle_through(*result.points)
            self._paint_circle(painter, center, radius, normal, result.points[0])
            middle = self._screen(center)
            if middle is not None:
                painter.drawLine(middle + QPointF(-5, 0), middle + QPointF(5, 0))
                painter.drawLine(middle + QPointF(0, -5), middle + QPointF(0, 5))
                painter.setPen(QPen(color, 1.2, Qt.PenStyle.DashLine))
                painter.drawLine(middle, pts[0])
            anchor = middle if middle is not None else pts[0]
        for point in pts:
            self._dot(painter, point, color)
        self._pill(painter, anchor + QPointF(10, -10), self.label(result))

    def _paint_circle(self, painter: QPainter, center, radius: float, normal, start) -> None:
        e1 = [start[i] - center[i] for i in range(3)]
        length = math.sqrt(sum(v * v for v in e1)) or 1.0
        e1 = [v / length for v in e1]
        e2 = [normal[1] * e1[2] - normal[2] * e1[1], normal[2] * e1[0] - normal[0] * e1[2],
              normal[0] * e1[1] - normal[1] * e1[0]]
        path = QPainterPath()
        first = True
        for step in range(65):
            t = 2 * math.pi * step / 64
            point = tuple(center[i] + radius * (math.cos(t) * e1[i] + math.sin(t) * e2[i]) for i in range(3))
            screen = self._screen(point)
            if screen is None:
                first = True
                continue
            if first:
                path.moveTo(screen)
                first = False
            else:
                path.lineTo(screen)
        painter.drawPath(path)

    def _dot(self, painter: QPainter, point: QPointF, color: QColor) -> None:
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(theme.color("accent_text"))
        painter.drawEllipse(point, 3.5, 3.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _marker(self, painter: QPainter, point: QPointF, kind: str, color: QColor) -> None:
        painter.setPen(QPen(color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "vertex":
            painter.drawRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))
        elif kind == "edge":
            path = QPainterPath(point + QPointF(0, -6))
            for offset in (QPointF(6, 0), QPointF(0, 6), QPointF(-6, 0), QPointF(0, -6)):
                path.lineTo(point + offset)
            painter.drawPath(path)
        else:
            painter.drawEllipse(point, 3, 3)

    def _pill(self, painter: QPainter, anchor: QPointF, text: str) -> None:
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        box = QFontMetricsF(font).boundingRect(text).adjusted(-7, -4, 7, 4)
        box.moveTopLeft(anchor)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("surface"))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(theme.color("text"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.setBrush(Qt.BrushStyle.NoBrush)
