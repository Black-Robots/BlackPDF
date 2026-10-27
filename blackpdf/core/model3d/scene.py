"""Escena 3D lista para dibujar, común a U3D y PRC.

Las geometrías se guardan como bytes de float32 para que la escena viaje
barata entre el proceso que decodifica y la interfaz.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field

Matrix = tuple[float, ...]
"""Matriz 4x4 por columnas (16 valores), como en OpenGL."""

IDENTITY: Matrix = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)

FEATURE_ANGLE = 35.0
"""Ángulo entre caras a partir del cual una arista se dibuja como contorno."""

SMOOTH_ANGLE = 40.0
"""Ángulo máximo entre caras para suavizar normales calculadas."""


@dataclass
class Material:
    diffuse: tuple[float, float, float] = (0.72, 0.72, 0.74)
    ambient: tuple[float, float, float] = (0.0, 0.0, 0.0)
    specular: tuple[float, float, float] = (0.25, 0.25, 0.25)
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0)
    shininess: float = 0.3
    opacity: float = 1.0
    lighting: bool = True
    vertex_colors: bool = True


@dataclass
class Primitive:
    """Lote de dibujo con un único material."""

    kind: str  # "triangles", "lines" o "points"
    positions: bytes  # float32 xyz por vértice
    normals: bytes = b""  # float32 xyz por vértice (solo triángulos)
    colors: bytes = b""  # float32 rgba por vértice
    material: int = 0
    wire: bytes = b""  # float32 xyz por extremo: todas las aristas
    edges: bytes = b""  # float32 xyz por extremo: aristas vivas y bordes

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 12

    @property
    def triangle_count(self) -> int:
        return self.vertex_count // 3 if self.kind == "triangles" else 0


@dataclass
class Part:
    """Pieza visible del modelo: unos lotes colocados con una matriz."""

    name: str
    matrix: Matrix
    primitives: list[int]
    visible: bool = True
    opacity: float = 1.0


@dataclass
class View3D:
    """Vista guardada: cámara en coordenadas del mundo."""

    name: str
    camera: Matrix | None = None  # cámara a mundo, mira hacia -Z con Y arriba
    orbit: float | None = None  # distancia de la cámara al centro de giro
    projection: str = "perspective"  # o "orthographic"
    fov: float = 30.0  # grados, vertical
    ortho_height: float | None = None
    background: tuple[float, float, float] | None = None
    render_mode: str | None = None
    lighting: str | None = None
    hidden: list[str] = field(default_factory=list)


@dataclass
class Scene:
    format: str
    primitives: list[Primitive] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    views: list[View3D] = field(default_factory=list)
    default_view: int | None = None
    background: tuple[float, float, float] | None = None
    warnings: list[str] = field(default_factory=list)
    unit_mm: float | None = None
    """Milímetros por unidad del modelo, si el archivo lo dice (para medir)."""

    @property
    def triangle_count(self) -> int:
        total = 0
        for part in self.parts:
            for index in part.primitives:
                total += self.primitives[index].triangle_count
        return total

    def bounds(self, visible_only: bool = False):
        """Caja que envuelve la escena en coordenadas del mundo, o None."""
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        cache: dict[int, tuple] = {}
        for part in self.parts:
            if visible_only and not part.visible:
                continue
            for index in part.primitives:
                box = cache.get(index)
                if box is None:
                    box = cache[index] = local_bounds(self.primitives[index].positions)
                if box is None:
                    continue
                for corner in _corners(box):
                    point = transform_point(part.matrix, corner)
                    for axis in range(3):
                        lo[axis] = min(lo[axis], point[axis])
                        hi[axis] = max(hi[axis], point[axis])
        if lo[0] == math.inf:
            return None
        return tuple(lo), tuple(hi)

    def is_empty(self) -> bool:
        return not any(self.primitives[i].vertex_count for part in self.parts for i in part.primitives)

    def dark_fraction(self) -> float:
        """Parte de la geometría visible dibujada en colores casi negros (0 a 1).

        Los modelos pensados para fondo blanco (ejes y rótulos negros de
        LaTeX) no se leen sobre un fondo oscuro.
        """
        dark = total = 0.0
        for part in self.parts:
            if not part.visible:
                continue
            for index in part.primitives:
                primitive = self.primitives[index]
                if not 0 <= primitive.material < len(self.materials):
                    continue
                material = self.materials[primitive.material]
                if material.vertex_colors and primitive.colors:
                    continue
                weight = primitive.triangle_count if primitive.kind == "triangles" else primitive.vertex_count / 2
                r, g, b = material.diffuse
                total += weight
                if 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.15:
                    dark += weight
        return dark / total if total else 0.0


# -- piezas ---------------------------------------------------------------------


def merge_parts(parts: list[Part]) -> list[Part]:
    """Une piezas seguidas con el mismo nombre, posición y visibilidad.

    Programas como Asymptote guardan cada parche como un elemento propio: sin
    unirlos el panel de piezas tendría cientos de entradas iguales.
    """
    merged: list[Part] = []
    for part in parts:
        last = merged[-1] if merged else None
        if (last is not None and last.name == part.name and last.matrix == part.matrix
                and last.visible == part.visible and last.opacity == part.opacity):
            last.primitives.extend(part.primitives)
        else:
            merged.append(Part(part.name, part.matrix, list(part.primitives), part.visible, part.opacity))
    return merged


def merge_scenes(base: Scene, extra: Scene) -> None:
    """Añade a ``base`` las piezas de ``extra`` (con sus lotes y materiales)."""
    material_offset = len(base.materials)
    primitive_offset = len(base.primitives)
    base.materials.extend(extra.materials)
    for primitive in extra.primitives:
        primitive.material += material_offset
        base.primitives.append(primitive)
    for part in extra.parts:
        part.primitives = [index + primitive_offset for index in part.primitives]
        base.parts.append(part)
    base.warnings.extend(extra.warnings)


def unique_part_names(parts: list[Part]) -> None:
    """Nombres únicos para el panel de piezas: «Tornillo», «Tornillo (2)»..."""
    totals: dict[str, int] = {}
    for part in parts:
        totals[part.name] = totals.get(part.name, 0) + 1
    seen: dict[str, int] = {}
    for index, part in enumerate(parts, 1):
        base = part.name
        if not base:
            part.name = f"Pieza {index}"
            continue
        if totals[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            if seen[base] > 1:
                part.name = f"{base} ({seen[base]})"


# -- matrices -------------------------------------------------------------------


def multiply(a: Matrix, b: Matrix) -> Matrix:
    """Producto a·b de matrices por columnas."""
    result = [0.0] * 16
    for col in range(4):
        b0, b1, b2, b3 = b[col * 4:col * 4 + 4]
        for row in range(4):
            result[col * 4 + row] = a[row] * b0 + a[4 + row] * b1 + a[8 + row] * b2 + a[12 + row] * b3
    return tuple(result)


def transform_point(m: Matrix, p) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def invert(m: Matrix) -> Matrix:
    """Inversa de una matriz afín (rotación, escala y traslación)."""
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-300:
        return IDENTITY
    inv = 1.0 / det
    r00 = (e * i - f * h) * inv
    r01 = (c * h - b * i) * inv
    r02 = (b * f - c * e) * inv
    r10 = (f * g - d * i) * inv
    r11 = (a * i - c * g) * inv
    r12 = (c * d - a * f) * inv
    r20 = (d * h - e * g) * inv
    r21 = (b * g - a * h) * inv
    r22 = (a * e - b * d) * inv
    tx, ty, tz = m[12], m[13], m[14]
    return (
        r00, r10, r20, 0.0,
        r01, r11, r21, 0.0,
        r02, r12, r22, 0.0,
        -(r00 * tx + r01 * ty + r02 * tz),
        -(r10 * tx + r11 * ty + r12 * tz),
        -(r20 * tx + r21 * ty + r22 * tz),
        1.0,
    )


def is_finite_matrix(m) -> bool:
    return len(m) == 16 and all(math.isfinite(v) for v in m)


# -- geometría ------------------------------------------------------------------


def local_bounds(positions: bytes):
    values = array("f")
    values.frombytes(positions)
    if not values:
        return None
    xs, ys, zs = values[0::3], values[1::3], values[2::3]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _corners(box):
    (x0, y0, z0), (x1, y1, z1) = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _normalize(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-30 or not math.isfinite(length):
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def face_normal(a, b, c):
    return _normalize(_cross(_sub(b, a), _sub(c, a)))


def build_triangles(
    positions: list,
    triangles: list[tuple[int, int, int]],
    corner_normals: list | None = None,
    corner_colors: list | None = None,
    oriented: bool = True,
) -> Primitive:
    """Crea un lote de triángulos a partir de posiciones indexadas.

    ``corner_normals`` y ``corner_colors`` dan, si existen, un valor por
    esquina (tres por triángulo). Sin normales se calculan suavizando entre
    caras parecidas. Además se generan las aristas para el modo alámbrico y
    las aristas vivas para el contorno. Con ``oriented=False`` los triángulos
    vecinos pueden tener el giro invertido y se comparan sin signo.
    """
    normals_by_face = []
    for a, b, c in triangles:
        normals_by_face.append(face_normal(positions[a], positions[b], positions[c]))

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for index, (a, b, c) in enumerate(triangles):
        for p, q in ((a, b), (b, c), (c, a)):
            key = (p, q) if p < q else (q, p)
            faces = edge_faces.get(key)
            if faces is None:
                edge_faces[key] = [index]
            else:
                faces.append(index)

    if corner_normals is None:
        corner_normals = _smooth_normals(positions, triangles, normals_by_face, oriented)

    pos = array("f")
    nor = array("f")
    col = array("f")
    for index, (a, b, c) in enumerate(triangles):
        fallback = normals_by_face[index] or (0.0, 0.0, 1.0)
        for corner, vertex in enumerate((a, b, c)):
            pos.extend(positions[vertex])
            normal = corner_normals[index * 3 + corner]
            nor.extend(normal if normal is not None else fallback)
            if corner_colors is not None:
                col.extend(corner_colors[index * 3 + corner])

    wire = array("f")
    edges = array("f")
    limit = math.cos(math.radians(FEATURE_ANGLE))
    for (p, q), faces in edge_faces.items():
        segment = (*positions[p], *positions[q])
        wire.extend(segment)
        if len(faces) != 2:
            edges.extend(segment)
            continue
        n1, n2 = normals_by_face[faces[0]], normals_by_face[faces[1]]
        if n1 is None or n2 is None:
            continue
        dot = n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]
        if (dot if oriented else abs(dot)) < limit:
            edges.extend(segment)

    return Primitive(
        kind="triangles",
        positions=pos.tobytes(),
        normals=nor.tobytes(),
        colors=col.tobytes() if corner_colors is not None else b"",
        wire=wire.tobytes(),
        edges=edges.tobytes(),
    )


def _smooth_normals(positions, triangles, normals_by_face, oriented=True):
    """Normal por esquina: media de las caras vecinas con ángulo pequeño."""
    incident: dict[int, list[int]] = {}
    for index, tri in enumerate(triangles):
        if normals_by_face[index] is None:
            continue
        for vertex in tri:
            faces = incident.get(vertex)
            if faces is None:
                incident[vertex] = [index]
            else:
                faces.append(index)
    limit = math.cos(math.radians(SMOOTH_ANGLE))
    result = []
    for index, tri in enumerate(triangles):
        own = normals_by_face[index]
        for vertex in tri:
            if own is None:
                result.append(None)
                continue
            sx = sy = sz = 0.0
            for other in incident.get(vertex, ()):
                n = normals_by_face[other]
                dot = own[0] * n[0] + own[1] * n[1] + own[2] * n[2]
                sign = 1.0
                if not oriented and dot < 0:
                    dot, sign = -dot, -1.0
                if dot >= limit:
                    sx += sign * n[0]
                    sy += sign * n[1]
                    sz += sign * n[2]
            result.append(_normalize((sx, sy, sz)) or own)
    return result


def build_lines(positions: list, segments: list[tuple[int, int]], colors: list | None = None) -> Primitive:
    pos = array("f")
    col = array("f")
    for index, (p, q) in enumerate(segments):
        pos.extend(positions[p])
        pos.extend(positions[q])
        if colors is not None:
            col.extend(colors[index * 2])
            col.extend(colors[index * 2 + 1])
    return Primitive(kind="lines", positions=pos.tobytes(), colors=col.tobytes() if colors is not None else b"")


def build_points(positions: list, colors: list | None = None) -> Primitive:
    pos = array("f")
    col = array("f")
    for index, point in enumerate(positions):
        pos.extend(point)
        if colors is not None:
            col.extend(colors[index])
    return Primitive(kind="points", positions=pos.tobytes(), colors=col.tobytes() if colors is not None else b"")
