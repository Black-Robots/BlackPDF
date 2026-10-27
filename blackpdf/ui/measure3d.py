"""Medir en el visor 3D como en un programa de CAD.

Al pasar el ratón se resalta lo que hay debajo (una esquina, una arista o una cara). Con un
clic se ven sus datos: longitud, diámetro, radio, área... Con dos, lo que hay entre ellas:
la distancia, entre centros si son agujeros o ejes, el espesor entre caras paralelas, el
ángulo y lo que avanza en cada eje.
"""

from __future__ import annotations

import queue
import threading
import time
from array import array
from concurrent.futures import Future

from PySide6.QtCore import QCoreApplication, QLocale, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF, QVector3D

from ..core.model3d import measure3d as core
from ..core.model3d import topology as core_topology
from . import theme

SNAP_PIXELS = 7.0
"""Píxeles a los que el cursor se pega a una esquina o una arista (como la mira de un CAD)."""
FILL_LIMIT = 6000
"""Triángulos como mucho para rellenar una cara resaltada; con más, solo su contorno."""


def _tr(text: str) -> str:
    return QCoreApplication.translate("Measure3D", text)


def kind_names() -> dict[str, str]:
    return {
        "vertex": _tr("Esquina"), "point": _tr("Punto de la superficie"), "line": _tr("Arista recta"),
        "circle": _tr("Circunferencia"), "arc": _tr("Arco"), "curve": _tr("Arista"), "plane": _tr("Cara plana"),
        "cylinder": _tr("Cara cilíndrica"), "face": _tr("Cara"),
    }


def _number(value: float, decimals: int = 2) -> str:
    return QLocale().toString(float(value), "f", decimals)


class _Worker:
    """Un hilo que analiza las piezas una tras otra, sin parar la ventana (solo Python: ni PyMuPDF ni Qt)."""

    def __init__(self):
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def submit(self, work) -> Future:
        future: Future = Future()
        self._jobs.put((future, work))
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="BlackPDF-medir-3D", daemon=True)
            self._thread.start()
        return future

    def _run(self) -> None:
        while True:
            future, work = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(work())
            except Exception as error:
                future.set_exception(error)


class Measure3D(QObject):
    """Estado de la herramienta: el visor le pasa el ratón y le pide que se dibuje."""

    changed = Signal()
    _topology_built = Signal()

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.unit = "mm"
        self.hover: core.Entity | None = None
        """Lo que hay bajo el cursor."""
        self.selection: list[core.Entity] = []
        self.relation: core.Relation | None = None
        self.results: list[tuple[core.Entity, core.Entity, core.Relation]] = []
        self._index: core.SnapIndex | None = None
        self._index_key = None
        self._topologies: dict[int, Future] = {}
        """La geometría de cada pieza, que se analiza en otro hilo la primera vez que se pasa por ella."""
        self._topology_scene = None
        self._worker = _Worker()
        self._topology_built.connect(self._on_topology_built)
        self._pending: QPointF | None = None
        self._gl_cache: dict[tuple, tuple[bytes, bytes]] = {}
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

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.changed.emit()
        self.viewer.update()

    def cancel(self) -> bool:
        """Quita la selección a medias; True si había algo que quitar."""
        if not self.selection:
            return False
        self.selection = []
        self.relation = None
        self.changed.emit()
        self.viewer.update()
        return True

    def clear(self) -> None:
        self.selection = []
        self.relation = None
        self.results = []
        self.changed.emit()
        self.viewer.update()

    def invalidate(self) -> None:
        """La escena ha cambiado: lo calculado de sus piezas se rehace al volver a medir."""
        self._index = None
        self._index_key = None
        self._drop_topologies()
        self._gl_cache = {}
        self.hover = None

    def stop(self) -> None:
        """Deja de seguir al ratón (la herramienta se ha desactivado)."""
        self._timer.stop()
        self._pending = None
        self.hover = None
        self.viewer.update()

    def _busy(self, work):
        self.viewer.setCursor(Qt.CursorShape.WaitCursor)
        try:
            return work()
        finally:
            self.viewer.setCursor(Qt.CursorShape.CrossCursor)

    def _snap_index(self) -> core.SnapIndex | None:
        scene = self.viewer.scene
        if scene is None:
            return None
        key = (id(scene), frozenset(self.viewer.hidden))
        if self._index is None or self._index_key != key:
            self._index = self._busy(lambda: core.SnapIndex(scene, self.viewer.hidden))
            self._index_key = key
        return self._index

    def _drop_topologies(self) -> None:
        for future in self._topologies.values():
            future.cancel()
        self._topologies = {}
        self._topology_scene = None

    def _topology(self, part: int, wait: bool = False) -> core_topology.PartTopology | None:
        """Caras y curvas de la pieza. Mientras se analizan, None (o, con ``wait``, se espera)."""
        scene = self.viewer.scene
        if scene is None or not 0 <= part < len(scene.parts):
            return None
        if self._topology_scene is not scene:
            self._drop_topologies()
            self._topology_scene = scene
        future = self._topologies.get(part)
        if future is None:
            future = self._topologies[part] = self._worker.submit(lambda: core_topology.PartTopology(scene, part))
            future.add_done_callback(self._built)
        if not future.done():
            if not wait:
                return None
            if future.cancel():
                # Aún esperaba turno detrás de otras piezas: se analiza aquí mismo.
                future = self._topologies[part] = Future()
                future.set_result(self._busy(lambda: core_topology.PartTopology(scene, part)))
            else:
                self._busy(future.exception)
        if future.cancelled() or future.exception() is not None:
            return None
        return future.result()

    def _built(self, _future: Future) -> None:
        # Llega desde el hilo que analiza: la señal lo pasa al de la ventana.
        try:
            self._topology_built.emit()
        except RuntimeError:
            pass  # La ventana ya se ha cerrado.

    def _on_topology_built(self) -> None:
        # Lo que hay bajo el cursor puede ser ahora una circunferencia o una cara entera.
        if self._pending is not None and self.viewer.measure_tool is self and not self._timer.isActive():
            self._timer.start()

    # ---------- ratón ----------
    def hover_at(self, pos: QPointF) -> None:
        self._pending = QPointF(pos)
        if not self._timer.isActive():
            self._timer.start()

    def _process_hover(self) -> None:
        if self._pending is None or self.viewer.measure_tool is not self:
            return
        started = time.perf_counter()
        self.hover = self._locate(self._pending)
        self.viewer.update()
        # Con modelos pesados, leer bajo el cursor tarda: se espera más entre lecturas para no atascar la ventana.
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._timer.setInterval(int(min(250, max(25, elapsed_ms * 2))))

    def _locate(self, pos: QPointF, wait: bool = False) -> core.Entity | None:
        """Lo que hay bajo el cursor: una esquina; si no, una arista; y si no, la cara.

        Si la pieza aún se está analizando, la arista es el tramo de la malla y la cara, un
        punto; con ``wait`` (al hacer clic) se espera a tener la curva o la cara entera.
        """
        world = self.viewer.pick_point(pos)
        index = self._snap_index()
        ray = self.viewer.pick_ray(pos)
        if index is not None and ray is not None:
            origin, direction = ray
            near, growth = self.viewer.snap_tolerance(origin, direction, SNAP_PIXELS)
            hit = QVector3D.dotProduct(world - origin, direction) if world is not None else None
            snap = index.snap_ray((origin.x(), origin.y(), origin.z()), (direction.x(), direction.y(), direction.z()),
                                  near, growth, hit, self.viewer.clip_values())
            if snap is not None:
                if snap.kind == "vertex":
                    return core.Entity("vertex", snap.point, key=("vertex", snap.point))
                part = index.owner[snap.segment]
                a, b = index.lines[snap.segment]
                topology = self._topology(part, wait)
                curve_index = topology.curve_for_segment(a, b) if topology is not None else None
                if curve_index is not None:
                    return core.entity_from_curve(topology.curves[curve_index], snap.point, (part, "curve", curve_index))
                single = core_topology.Curve([a, b], False, "line", core.distance(a, b))
                return core.entity_from_curve(single, snap.point, ("segment", snap.segment))
        if world is None:
            return None
        point = (world.x(), world.y(), world.z())
        part = self.viewer.pick_part(pos)
        topology = self._topology(part, wait) if part >= 0 else None
        if topology is not None and topology.complete:
            face_index = topology.face_at(point)
            if face_index is not None:
                return core.entity_from_face(topology.faces[face_index], point, (part, "face", face_index))
        return core.Entity("point", point, key=("point", point))

    def click(self, pos: QPointF) -> None:
        self._timer.stop()
        entity = self._locate(pos, wait=True)
        self.hover = entity
        if entity is None:
            return
        if len(self.selection) >= 2:
            self.selection, self.relation = [], None
        if self.selection and self.selection[0].key == entity.key:
            # Lo mismo otra vez: se quita, como en los programas de CAD.
            self.selection, self.relation = [], None
        else:
            self.selection.append(entity)
            if len(self.selection) == 2:
                self.relation = self._busy(lambda: core.relate(*self.selection))
                self.results.append((self.selection[0], self.selection[1], self.relation))
        self.changed.emit()
        self.viewer.update()

    # ---------- textos ----------
    def length_text(self, value: float) -> str:
        converted = core.convert(value, self.model_unit_mm, self.unit)
        decimals = {"mm": 2, "cm": 2, "m": 3, "in": 3, "ft": 3}.get(self.unit, 2)
        return f"{_number(converted, decimals)} {self.unit}"

    def area_text(self, value: float) -> str:
        factor = self.model_unit_mm / core.UNITS_MM[self.unit]
        decimals = {"mm": 2, "cm": 2, "m": 4, "in": 3, "ft": 4}.get(self.unit, 2)
        return f"{_number(value * factor * factor, decimals)} {self.unit}²"

    def point_text(self, point) -> str:
        factor = self.model_unit_mm / core.UNITS_MM[self.unit]
        return "  ·  ".join(_number(v * factor, 2) for v in point) + f" {self.unit}"

    def entity_rows(self, entity: core.Entity) -> list[tuple[str, str]]:
        rows = []
        for key, value in core.describe(entity).items():
            if key == "position":
                factor = self.model_unit_mm / core.UNITS_MM[self.unit]
                rows += [(axis, f"{_number(v * factor, 2)} {self.unit}") for axis, v in zip("XYZ", value)]
            elif key in ("length", "diameter", "radius"):
                label = {"length": _tr("Longitud"), "diameter": _tr("Diámetro"), "radius": _tr("Radio")}[key]
                rows.append((label, ("Ø " if key == "diameter" else "") + self.length_text(value)))
            elif key == "center":
                rows.append((_tr("Centro"), self.point_text(value)))
            elif key == "sweep":
                rows.append((_tr("Abarca"), f"{_number(value, 1)}°"))
            elif key == "area":
                rows.append((_tr("Área"), self.area_text(value)))
        return rows

    def relation_rows(self, relation: core.Relation) -> list[tuple[str, str]]:
        rows = []
        if relation.center_distance is not None:
            rows.append((_tr("Entre centros"), self.length_text(relation.center_distance)))
        both_planes = len(self.selection) == 2 and all(e.kind == "plane" for e in self.selection)
        label = _tr("Distancia entre caras") if relation.parallel and both_planes else _tr("Distancia mínima")
        rows.append((label, self.length_text(relation.distance)))
        if relation.angle is not None:
            rows.append((_tr("Ángulo"), f"{_number(relation.angle, 2)}°"))
        for axis, value in zip("XYZ", relation.delta):
            rows.append((f"Δ{axis}", self.length_text(value)))
        return rows

    def primary_text(self, relation: core.Relation) -> str:
        value = relation.center_distance if relation.center_distance is not None else relation.distance
        return self.length_text(value)

    # ---------- dibujo ----------
    def _face_buffers(self, entity: core.Entity) -> tuple[bytes, bytes]:
        found = self._gl_cache.get(entity.key)
        if found is None:
            triangles = array("f", [c for t in entity.face.triangles for p in t for c in p])
            outline = array("f", [c for segment in entity.face.outline for p in segment for c in p])
            found = (triangles.tobytes(), outline.tobytes())
            if len(self._gl_cache) > 64:
                self._gl_cache.clear()
            self._gl_cache[entity.key] = found
        return found

    def gl_highlights(self) -> list[tuple[str, bytes, QColor]]:
        """Caras resaltadas, para dibujarlas dentro de la escena (con lo que las tape delante)."""
        items = []
        selected = {entity.key for entity in self.selection}
        faces = [(entity, True) for entity in self.selection if entity.face is not None]
        if self.hover is not None and self.hover.face is not None and self.hover.key not in selected:
            faces.append((self.hover, False))
        accent = theme.color("accent")
        for entity, strong in faces:
            triangles, outline = self._face_buffers(entity)
            fill = QColor(accent)
            fill.setAlphaF(0.30 if strong else 0.18)
            edge = QColor(accent)
            edge.setAlphaF(1.0 if strong else 0.75)
            items.append(("triangles", triangles, fill))
            items.append(("lines", outline, edge))
        return items

    def paint(self, painter: QPainter, hover: bool = True) -> None:
        accent = theme.color("accent")
        project = self.viewer.projector()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected_keys = {entity.key for entity in self.selection}
        if hover and self.hover is not None and self.hover.key not in selected_keys:
            self._paint_entity(painter, project, self.hover, accent, strong=False)
        for entity in self.selection:
            self._paint_entity(painter, project, entity, accent, strong=True)
        for _first, _second, relation in self.results:
            self._paint_relation(painter, project, relation, accent)
        painter.restore()

    def _paint_relation(self, painter, project, relation: core.Relation, color: QColor) -> None:
        a, b = (project(p) for p in relation.anchors)
        if a is None or b is None:
            return
        painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(a, b)
        for point in (a, b):
            self._dot(painter, point, color)
        self._pill(painter, (a + b) / 2 + QPointF(10, -10), self.primary_text(relation))

    def _paint_entity(self, painter, project, entity: core.Entity, color: QColor, strong: bool) -> None:
        width = 3.2 if strong else 2.4
        tone = QColor(color)
        tone.setAlphaF(1.0 if strong else 0.7)
        if entity.face is not None:
            if self.viewer.gl_ok:
                return  # las caras se resaltan dentro de la escena (gl_highlights)
            face = entity.face
            if len(face.triangles) <= FILL_LIMIT:
                fill = QColor(color)
                fill.setAlphaF(0.22 if strong else 0.14)
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.WindingFill)
                for triangle in face.triangles:
                    corners = [project(p) for p in triangle]
                    if None not in corners:
                        path.addPolygon(QPolygonF(corners))
                        path.closeSubpath()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.fillPath(path, fill)
            painter.setPen(QPen(tone, width * 0.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for p, q in face.outline:
                a, b = project(p), project(q)
                if a is not None and b is not None:
                    painter.drawLine(a, b)
            return
        if entity.curve is not None:
            curve = entity.curve
            points = [project(p) for p in curve.points + ([curve.points[0]] if curve.closed else [])]
            painter.setPen(QPen(tone, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for a, b in zip(points, points[1:]):
                if a is not None and b is not None:
                    painter.drawLine(a, b)
            if curve.center is not None:
                center = project(curve.center)
                if center is not None:
                    painter.setPen(QPen(tone, 1.6))
                    painter.drawLine(center + QPointF(-6, 0), center + QPointF(6, 0))
                    painter.drawLine(center + QPointF(0, -6), center + QPointF(0, 6))
            return
        point = project(entity.point)
        if point is None:
            return
        painter.setPen(QPen(tone, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if entity.kind == "vertex":
            painter.drawRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))
        else:
            painter.drawEllipse(point, 4, 4)

    def _dot(self, painter: QPainter, point: QPointF, color: QColor) -> None:
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(theme.color("accent_text"))
        painter.drawEllipse(point, 3.5, 3.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)

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
