"""Medir en el visor 3D: distancias, ángulos y radios sobre la escena.

Los puntos llegan en coordenadas del mundo (las que da el visor al leer la profundidad
bajo el ratón). Aquí está la geometría, el índice que permite pegarse a las esquinas y
aristas del modelo y el paso de las unidades del modelo a las que se muestran.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Iterable

from ..errors import InvalidOptionError
from .scene import Scene, transform_point

Vec = tuple[float, float, float]

UNITS_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
KINDS = ("distance", "angle", "radius")
POINTS_NEEDED = {"distance": 2, "angle": 3, "radius": 3}
MAX_SEGMENTS = 200_000
MAX_VERTICES = 200_000
"""Aristas y esquinas que se indexan como mucho; en modelos enormes el resto se mide sobre la superficie."""
LEVELS = (1, 4, 16, 64)
"""Tamaños de las rejillas, en celdas de la más fina (que mide 1/64 de la diagonal de la escena)."""


# ---------- geometría ----------
def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _length(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def distance(a: Vec, b: Vec) -> float:
    return _length(_sub(b, a))


def deltas(a: Vec, b: Vec) -> Vec:
    """Lo que avanza la medida en cada eje (siempre positivo)."""
    return abs(b[0] - a[0]), abs(b[1] - a[1]), abs(b[2] - a[2])


def angle(a: Vec, vertex: Vec, c: Vec) -> float:
    """Ángulo en grados entre los segmentos vertex→a y vertex→c."""
    u, v = _sub(a, vertex), _sub(c, vertex)
    lu, lv = _length(u), _length(v)
    if lu == 0 or lv == 0:
        raise InvalidOptionError("puntos repetidos", option="points")
    # atan2 es estable también cerca de 0° y 180°, donde acos pierde precisión.
    return math.degrees(math.atan2(_length(_cross(u, v)), _dot(u, v)))


def circle_through(a: Vec, b: Vec, c: Vec) -> tuple[Vec, float, Vec]:
    """Centro, radio y normal de la circunferencia que pasa por tres puntos."""
    ab, ac = _sub(b, a), _sub(c, a)
    normal = _cross(ab, ac)
    n2 = _dot(normal, normal)
    scale = max(_dot(ab, ab), _dot(ac, ac), 1e-30)
    if n2 <= 1e-12 * scale * scale:
        raise InvalidOptionError("los tres puntos están en línea", option="points")
    # Fórmula del circuncentro: a + ((|ac|² (ab×ac)×ab) + (|ab|² ac×(ab×ac))) / (2 |ab×ac|²)
    t1 = _cross(normal, ab)
    t2 = _cross(ac, normal)
    k1, k2 = _dot(ac, ac), _dot(ab, ab)
    center = tuple(a[i] + (k1 * t1[i] + k2 * t2[i]) / (2 * n2) for i in range(3))
    length = math.sqrt(n2)
    return center, distance(center, a), (normal[0] / length, normal[1] / length, normal[2] / length)


def measure_value(kind: str, points: list[Vec]) -> float:
    """Valor en unidades del modelo (grados para el ángulo; el radio, para «radius»)."""
    if kind not in KINDS:
        raise InvalidOptionError(kind, option="kind")
    if len(points) < POINTS_NEEDED[kind]:
        raise InvalidOptionError("faltan puntos", option="points")
    if kind == "distance":
        return distance(points[0], points[1])
    if kind == "angle":
        return angle(points[0], points[1], points[2])
    return circle_through(points[0], points[1], points[2])[1]


# ---------- unidades ----------
def unit_name(unit_mm: float | None) -> str | None:
    """La unidad con nombre que corresponde a «milímetros por unidad del modelo», si la hay."""
    if not unit_mm:
        return None
    for name, value in UNITS_MM.items():
        if abs(value - unit_mm) <= value * 1e-6:
            return name
    return None


def convert(value: float, model_unit_mm: float, unit: str) -> float:
    """De unidades del modelo a ``unit`` (mm, cm, m, in, ft)."""
    if unit not in UNITS_MM:
        raise InvalidOptionError(unit, option="unit")
    return value * model_unit_mm / UNITS_MM[unit]


# ---------- ajuste a esquinas y aristas ----------
def _floats(data: bytes) -> array:
    values = array("f")
    values.frombytes(data)
    return values


class SnapIndex:
    """Esquinas y aristas visibles de la escena, en rejillas, para pegar el cursor a ellas.

    Las aristas son las vivas y los bordes de cada malla (las que el visor dibuja como
    contorno), las líneas y los puntos sueltos. Las esquinas son sus extremos. Hay rejillas
    de varios tamaños: con la pieza pequeña en pantalla, unos píxeles abarcan mucho del
    modelo y se busca en la rejilla gruesa, así cada búsqueda mira pocas celdas.
    """

    def __init__(self, scene: Scene, hidden: Iterable[int] = ()):
        bounds = scene.bounds(visible_only=True)
        hidden = set(hidden)
        if bounds is None:
            self.cell = 1.0
            self.bounds = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        else:
            lo, hi = bounds
            self.cell = (math.dist(lo, hi) or 1.0) / 64
            self.bounds = bounds
        self.vertices: dict[tuple[int, int, int], list[Vec]] = {}
        self.segments: dict[tuple[int, int, int], list[int]] = {}
        self.lines: list[tuple[Vec, Vec]] = []
        self.truncated = False
        """El modelo tenía más esquinas o aristas de las que se indexan: el resto se mide sobre la superficie."""
        self._vertex_count = 0
        self._levels: dict[int, tuple[dict, dict]] = {1: (self.vertices, self.segments)}
        seen_vertices: set[Vec] = set()
        for index, part in enumerate(scene.parts):
            if index in hidden or not part.visible:
                continue
            for prim_index in part.primitives:
                primitive = scene.primitives[prim_index]
                pairs = primitive.kind in ("triangles", "lines")
                values = _floats(primitive.edges if primitive.kind == "triangles" else primitive.positions)
                # El tope se mira antes de transformar: en modelos enormes, construir el índice no congela la ventana.
                room = (MAX_SEGMENTS - len(self.lines)) * 6 if pairs else (MAX_VERTICES - self._vertex_count) * 3
                if room <= 0:
                    self.truncated = self.truncated or len(values) > 0
                    continue
                if len(values) > room:
                    self.truncated = True
                    values = values[:room]
                points = [transform_point(part.matrix, (values[i], values[i + 1], values[i + 2]))
                          for i in range(0, len(values) - 2, 3)]
                if pairs:
                    for start in range(0, len(points) - 1, 2):
                        self._add_segment(points[start], points[start + 1], seen_vertices)
                else:
                    for point in points:
                        self._add_vertex(point, seen_vertices)

    def _key(self, p: Vec, level: int = 1) -> tuple[int, int, int]:
        h = self.cell * level
        return int(math.floor(p[0] / h)), int(math.floor(p[1] / h)), int(math.floor(p[2] / h))

    def _add_vertex(self, p: Vec, seen: set[Vec]) -> None:
        rounded = (round(p[0], 9), round(p[1], 9), round(p[2], 9))
        if rounded in seen or self._vertex_count >= MAX_VERTICES:
            return
        seen.add(rounded)
        self._vertex_count += 1
        self.vertices.setdefault(self._key(p), []).append(p)

    def _add_segment(self, a: Vec, b: Vec, seen: set[Vec]) -> None:
        if a == b:
            self._add_vertex(a, seen)
            return
        index = len(self.lines)
        self.lines.append((a, b))
        self._add_vertex(a, seen)
        self._add_vertex(b, seen)
        steps = max(1, int(math.ceil(distance(a, b) / (self.cell * 0.5))))
        keys = set()
        for step in range(steps + 1):
            t = step / steps
            keys.add(self._key((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)))
        for key in keys:
            self.segments.setdefault(key, []).append(index)

    @property
    def segment_count(self) -> int:
        return len(self.lines)

    def _level(self, level: int) -> tuple[dict, dict]:
        """Rejilla de celdas ``level`` veces más grandes, hecha juntando las finas."""
        found = self._levels.get(level)
        if found is None:
            vertices: dict = {}
            segments: dict = {}
            for (x, y, z), items in self.vertices.items():
                vertices.setdefault((x // level, y // level, z // level), []).extend(items)
            for (x, y, z), items in self.segments.items():
                segments.setdefault((x // level, y // level, z // level), set()).update(items)
            found = self._levels[level] = (vertices, segments)
        return found

    def _cells(self, p: Vec, radius: float) -> list[tuple[int, tuple[int, int, int]]]:
        """Celdas (con su nivel) que cubren la esfera de ``radius`` alrededor de ``p``: como mucho 27."""
        level = next((size for size in LEVELS if radius <= self.cell * size), None)
        if level is None:
            # Más grande que toda la escena: se mira todo, que en la rejilla más gruesa son pocas celdas.
            return [(LEVELS[-1], key) for key in self._level(LEVELS[-1])[0].keys() | self._level(LEVELS[-1])[1].keys()]
        cx, cy, cz = self._key(p, level)
        return [(level, (cx + dx, cy + dy, cz + dz)) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    def _candidates(self, cells) -> tuple[list[Vec], set[int]]:
        vertices: list[Vec] = []
        segments: set[int] = set()
        for level, key in cells:
            level_vertices, level_segments = self._level(level)
            vertices.extend(level_vertices.get(key, ()))
            segments.update(level_segments.get(key, ()))
        return vertices, segments

    def snap(self, point: Vec, radius: float) -> tuple[Vec, str]:
        """El punto donde pegar el cursor: ("vertex", esquina), ("edge", sobre una arista) o ("surface", el mismo)."""
        vertices, segments = self._candidates(self._cells(point, radius))
        best, best_distance = None, radius
        for vertex in vertices:
            d = distance(point, vertex)
            if d <= best_distance:
                best, best_distance = vertex, d
        if best is not None:
            return best, "vertex"
        best_distance = radius
        for index in segments:
            a, b = self.lines[index]
            ab = _sub(b, a)
            t = max(0.0, min(1.0, _dot(_sub(point, a), ab) / max(_dot(ab, ab), 1e-30)))
            closest = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
            d = distance(point, closest)
            if d <= best_distance:
                best, best_distance = closest, d
        if best is not None:
            return best, "edge"
        return point, "surface"

    def snap_ray(self, origin: Vec, direction: Vec, near: float, growth: float,
                 hit: float | None = None, clip: tuple[float, float, float, float] | None = None
                 ) -> tuple[Vec, str] | None:
        """Esquina o arista que se ve junto al rayo que sale del cursor, o None.

        Se mide en pantalla: la tolerancia a una distancia ``t`` del origen es ``near + growth * t``
        (unos píxeles, convertidos a unidades del modelo a esa profundidad). ``hit`` es la distancia
        a la superficie que se ve bajo el cursor: lo que esté detrás no cuenta. Así se pegan también
        los bordes de un agujero aunque el cursor vea la pared del fondo. ``clip`` es el plano de
        sección (a, b, c, d): lo que queda del lado positivo está recortado y no cuenta.
        """
        length = _length(direction)
        if length == 0:
            return None
        d = (direction[0] / length, direction[1] / length, direction[2] / length)
        (lo, hi) = self.bounds
        center = tuple((lo[i] + hi[i]) / 2 for i in range(3))
        reach = distance(origin, center) + distance(lo, hi)
        # La caja se agranda con la tolerancia: el cursor puede estar junto a la silueta, fuera de la pieza.
        span = self._ray_span(origin, d, self.cell + near + growth * reach)
        if span is None:
            return None
        start, end = span
        if hit is not None:
            end = min(end, hit + self.cell)
        cells: set = set()
        t = start
        while True:
            radius = near + growth * max(t, 0.0)
            point = (origin[0] + d[0] * t, origin[1] + d[1] * t, origin[2] + d[2] * t)
            found = self._cells(point, radius)
            cells.update(found)
            if t > end:
                break
            # El paso va con el tamaño de la celda en que se busca: con la pieza lejos, pocos pasos.
            t += self.cell * found[0][0] * 0.5 if found else self.cell * 0.5
        vertices, segments = self._candidates(cells)
        # Lo que esté detrás de la superficie que se ve no cuenta; con margen, porque una esquina
        # de esa misma cara, si está inclinada, queda algo más honda que el punto bajo el cursor.
        limit = hit + max(self.cell * 0.02, 3 * (near + growth * hit)) if hit is not None else math.inf

        def usable(p: Vec) -> bool:
            return clip is None or p[0] * clip[0] + p[1] * clip[1] + p[2] * clip[2] + clip[3] <= 0

        def rank(p: Vec) -> tuple[int, float] | None:
            v = _sub(p, origin)
            along = _dot(v, d)
            if along < 0 or along > limit or not usable(p):
                return None
            off = distance(p, (origin[0] + d[0] * along, origin[1] + d[1] * along, origin[2] + d[2] * along))
            # A igual distancia en pantalla (de 5 % en 5 % de la tolerancia), gana lo que está
            # más cerca de la cámara: lo de detrás, en la misma línea de visión, no se ve.
            score = off / max(near + growth * along, 1e-30)
            return (int(score * 20), along) if score <= 1.0 else None

        best, best_rank = None, None
        for vertex in vertices:
            current = rank(vertex)
            if current is not None and (best_rank is None or current < best_rank):
                best, best_rank = vertex, current
        if best is not None:
            return best, "vertex"
        for index in segments:
            closest = self._segment_near_ray(self.lines[index], origin, d)
            current = rank(closest)
            if current is not None and (best_rank is None or current < best_rank):
                best, best_rank = closest, current
        return (best, "edge") if best is not None else None

    def _ray_span(self, origin: Vec, d: Vec, margin: float) -> tuple[float, float] | None:
        """Tramo del rayo dentro de la caja de la escena agrandada en ``margin``."""
        (lo, hi) = self.bounds
        start, end = 0.0, math.inf
        for axis in range(3):
            a, b = lo[axis] - margin, hi[axis] + margin
            if abs(d[axis]) < 1e-15:
                if not a <= origin[axis] <= b:
                    return None
                continue
            t1, t2 = (a - origin[axis]) / d[axis], (b - origin[axis]) / d[axis]
            start, end = max(start, min(t1, t2)), min(end, max(t1, t2))
        return (start, end) if start <= end else None

    @staticmethod
    def _segment_near_ray(segment: tuple[Vec, Vec], origin: Vec, d: Vec) -> Vec:
        """Punto del segmento más cercano al rayo."""
        a, b = segment
        u = _sub(b, a)
        w = _sub(a, origin)
        uu, ud = _dot(u, u), _dot(u, d)
        uw, dw = _dot(u, w), _dot(d, w)
        denominator = uu - ud * ud
        s = (ud * dw - uw) / denominator if denominator > 1e-20 else 0.0
        s = max(0.0, min(1.0, s))
        return a[0] + u[0] * s, a[1] + u[1] * s, a[2] + u[2] * s
