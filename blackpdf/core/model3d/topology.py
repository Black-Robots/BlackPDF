"""Geometría de una pieza para medir como un programa de CAD: caras y aristas con su forma.

Las mallas solo traen triángulos. Aquí se reconstruye lo que eran: los triángulos que se
tocan sin una arista viva entre ellos forman una cara (plana, cilíndrica u otra), y las
aristas vivas encadenadas forman curvas (rectas, circunferencias, arcos u otras). Los
vértices de una malla de CAD están sobre la superficie exacta, así que los diámetros y
las distancias que salen de aquí son los del diseño.
"""

from __future__ import annotations

import heapq
import math
from array import array
from dataclasses import dataclass, field

from .scene import IDENTITY, Scene, transform_point

Vec = tuple[float, float, float]

MAX_TRIANGLES = 300_000
"""Por encima, la pieza solo se mide por esquinas y aristas sueltas: reconstruirla tardaría demasiado."""
SPLIT_ANGLE = 50.0
"""Giro a partir del cual una cadena de aristas se parte en dos curvas (las esquinas de un contorno)."""
LEAF_SIZE = 4
"""Elementos en cada hoja del árbol de cajas."""
MAX_WORK = 120_000
"""Tope de trabajo al buscar la distancia mínima entre dos selecciones, en pasos sencillos (abrir
una pareja de nodos o descartar una de elementos); comparar dos elementos a fondo cuenta diez."""
_LOW = (1 << 32) - 1
"""Las aristas se guardan como un número: el vértice mayor en los 32 bits bajos."""


def _sub(a: Vec, b: Vec) -> Vec:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _add(a: Vec, b: Vec) -> Vec:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _scale(a: Vec, k: float) -> Vec:
    return a[0] * k, a[1] * k, a[2] * k


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _length(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec) -> Vec | None:
    length = _length(a)
    return None if length < 1e-30 else (a[0] / length, a[1] / length, a[2] / length)


def _dist(a: Vec, b: Vec) -> float:
    return _length(_sub(a, b))


def circumcircle(a: Vec, b: Vec, c: Vec) -> tuple[Vec, float, Vec] | None:
    """Centro, radio y normal de la circunferencia por tres puntos; None si están en línea."""
    ab, ac = _sub(b, a), _sub(c, a)
    normal = _cross(ab, ac)
    n2 = _dot(normal, normal)
    scale = max(_dot(ab, ab), _dot(ac, ac), 1e-30)
    if n2 <= 1e-12 * scale * scale:
        return None
    t1, t2 = _cross(normal, ab), _cross(ac, normal)
    k1, k2 = _dot(ac, ac), _dot(ab, ab)
    center = tuple(a[i] + (k1 * t1[i] + k2 * t2[i]) / (2 * n2) for i in range(3))
    length = math.sqrt(n2)
    return center, _dist(center, a), (normal[0] / length, normal[1] / length, normal[2] / length)


def _spread_three(points: list[Vec]) -> tuple[Vec, Vec, Vec]:
    """Tres puntos bien repartidos (para que la circunferencia que pasa por ellos sea precisa)."""
    a = points[0]
    b = max(points, key=lambda p: _dist(p, a))
    c = max(points, key=lambda p: _length(_cross(_sub(p, a), _sub(b, a))))
    return a, b, c


def fit_circle(points: list[Vec], tolerance: float) -> tuple[Vec, float, Vec] | None:
    """La circunferencia en la que están todos los puntos (con ``tolerance``), o None."""
    unique = list(dict.fromkeys(points))
    if len(unique) < 4:
        return None
    found = circumcircle(*_spread_three(unique))
    if found is None:
        return None
    center, radius, normal = found
    limit = max(tolerance, radius * 2e-3)
    for p in unique:
        if abs(_dist(p, center) - radius) > limit or abs(_dot(_sub(p, center), normal)) > limit:
            return None
    return center, radius, normal


@dataclass
class Curve:
    points: list[Vec]
    """En orden; en las cerradas, el último punto no repite el primero."""
    closed: bool
    kind: str = "curve"
    """«line», «circle», «arc» o «curve»."""
    length: float = 0.0
    center: Vec | None = None
    normal: Vec | None = None
    radius: float | None = None
    sweep: float | None = None
    """Grados que abarca un arco."""
    _tree: BoxTree | None = field(default=None, init=False, repr=False, compare=False)

    def segments(self) -> list[tuple[Vec, Vec]]:
        ring = self.points + [self.points[0]] if self.closed else self.points
        return list(zip(ring, ring[1:]))

    def tree(self) -> BoxTree:
        if self._tree is None:
            self._tree = BoxTree(self.segments() or [(self.points[0],)])
        return self._tree


@dataclass
class Face:
    triangles: list[tuple[Vec, Vec, Vec]]
    kind: str = "face"
    """«plane», «cylinder» o «face»."""
    area: float = 0.0
    normal: Vec | None = None
    point: Vec | None = None
    """Un punto del plano, o del eje del cilindro."""
    axis: Vec | None = None
    radius: float | None = None
    outline: list[tuple[Vec, Vec]] = field(default_factory=list)
    """Aristas que la rodean, para resaltarla."""
    _tree: BoxTree | None = field(default=None, init=False, repr=False, compare=False)

    def tree(self) -> BoxTree:
        if self._tree is None:
            self._tree = BoxTree(self.triangles)
        return self._tree


def smallest_eigenvector(m: list[list[float]]) -> Vec:
    """Vector propio del menor valor propio de una matriz simétrica de 3x3 (método de Jacobi)."""
    a = [row[:] for row in m]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _sweep in range(50):
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        if off < 1e-15:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) < 1e-30:
                continue
            theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
            t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
            c = 1 / math.sqrt(t * t + 1)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    smallest = min(range(3), key=lambda i: a[i][i])
    return v[0][smallest], v[1][smallest], v[2][smallest]


def closest_on_triangle(p: Vec, a: Vec, b: Vec, c: Vec) -> Vec:
    """Punto del triángulo más cercano a ``p`` (Ericson, «Real-Time Collision Detection»)."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return _add(a, _scale(ab, d1 / (d1 - d3)))
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return _add(a, _scale(ac, d2 / (d2 - d6)))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _add(b, _scale(_sub(c, b), w))
    denominator = 1.0 / (va + vb + vc)
    v, w = vb * denominator, vc * denominator
    return _add(a, _add(_scale(ab, v), _scale(ac, w)))


def _closest_on_segment(p: Vec, a: Vec, b: Vec) -> Vec:
    ab = _sub(b, a)
    t = max(0.0, min(1.0, _dot(_sub(p, a), ab) / max(_dot(ab, ab), 1e-30)))
    return a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t


def closest_segments(p1: Vec, q1: Vec, p2: Vec, q2: Vec) -> tuple[Vec, Vec]:
    """Puntos más cercanos entre dos segmentos (Ericson)."""
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    if a <= 1e-30 and e <= 1e-30:
        return p1, p2
    if a <= 1e-30:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = _dot(d1, r)
        if e <= 1e-30:
            s, t = max(0.0, min(1.0, -c / a)), 0.0
        else:
            b = _dot(d1, d2)
            denominator = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / denominator)) if denominator > 1e-30 else 0.0
            t = (b * s + f) / e
            if t < 0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    return (p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s), (p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t)


def _segment_hits_triangle(p: Vec, q: Vec, a: Vec, b: Vec, c: Vec) -> Vec | None:
    """Punto donde el segmento atraviesa el triángulo, o None (Möller-Trumbore)."""
    d, e1, e2 = _sub(q, p), _sub(b, a), _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < 1e-30:
        return None
    s = _sub(p, a)
    u = _dot(s, h) / det
    if u < 0.0 or u > 1.0:
        return None
    k = _cross(s, e1)
    v = _dot(d, k) / det
    if v < 0.0 or u + v > 1.0:
        return None
    t = _dot(e2, k) / det
    return None if t < 0.0 or t > 1.0 else _add(p, _scale(d, t))


def _sides(t: tuple[Vec, ...]) -> tuple[tuple[Vec, Vec], ...]:
    return (t[0], t[1]), (t[1], t[2]), (t[2], t[0])


def _closest_items(x: tuple[Vec, ...], y: tuple[Vec, ...]) -> tuple[Vec, Vec]:
    """Puntos más cercanos entre dos elementos: un punto (1), un segmento (2) o un triángulo (3)."""
    if len(x) > len(y):
        q, p = _closest_items(y, x)
        return p, q
    if len(x) == 1:
        p = x[0]
        if len(y) == 1:
            return p, y[0]
        return p, _closest_on_segment(p, *y) if len(y) == 2 else closest_on_triangle(p, *y)
    # Dos elementos que no se cortan están más cerca entre una esquina y el otro, o entre dos lados.
    for p, q in _sides(x) if len(x) == 3 else (x,):
        hit = _segment_hits_triangle(p, q, *y) if len(y) == 3 else None
        if hit is not None:
            return hit, hit
    if len(x) == 2 and len(y) == 2:
        return closest_segments(*x, *y)
    pairs: list[tuple[Vec, Vec]] = []
    if len(x) == 3:
        for p, q in _sides(y):
            hit = _segment_hits_triangle(p, q, *x)
            if hit is not None:
                return hit, hit
        pairs += [(closest_on_triangle(p, *x), p) for p in y]
    pairs += [(p, closest_on_triangle(p, *y)) for p in x]
    pairs += [closest_segments(*s, *t) for s in (_sides(x) if len(x) == 3 else (x,)) for t in _sides(y)]
    return min(pairs, key=lambda pair: _dot(_sub(pair[0], pair[1]), _sub(pair[0], pair[1])))


def _plane_gap(triangle: tuple[Vec, ...], other: tuple[Vec, ...]) -> float:
    """Cota de la distancia entre un triángulo y otro elemento: lo que separa a este del plano de aquel."""
    a, b, c = triangle
    n = _cross(_sub(b, a), _sub(c, a))
    length = _length(n)
    if length < 1e-30:
        return 0.0
    heights = [_dot(n, _sub(p, a)) / length for p in other]
    if min(heights) > 0:
        return min(heights)
    return -max(heights) if max(heights) < 0 else 0.0


def _box_gap(box: array, o: int, other: array, k: int) -> float:
    """Distancia entre dos cajas guardadas como seis números (mínimos y máximos)."""
    dx = max(box[o] - other[k + 3], other[k] - box[o + 3], 0.0)
    dy = max(box[o + 1] - other[k + 4], other[k + 1] - box[o + 4], 0.0)
    dz = max(box[o + 2] - other[k + 5], other[k + 2] - box[o + 5], 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _point_gap(box: array, o: int, p: Vec) -> float:
    dx = max(box[o] - p[0], p[0] - box[o + 3], 0.0)
    dy = max(box[o + 1] - p[1], p[1] - box[o + 4], 0.0)
    dz = max(box[o + 2] - p[2], p[2] - box[o + 5], 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _spread_bits() -> list[int]:
    table = []
    for value in range(1024):
        spread = 0
        for bit in range(10):
            spread |= ((value >> bit) & 1) << (3 * bit)
        table.append(spread)
    return table


_SPREAD = _spread_bits()
"""Los 10 bits de un número repartidos cada tres: para ordenar por la curva de Morton."""


class BoxTree:
    """Árbol de cajas sobre puntos, segmentos o triángulos: busca lo más cercano sin mirarlo todo.

    Los elementos se ordenan por la curva de Morton de su centro (los cercanos en el espacio
    quedan juntos) y se agrupan de dos en dos hasta la raíz; cada nodo guarda la caja que
    envuelve lo que tiene debajo.
    """

    def __init__(self, items: list[tuple[Vec, ...]]):
        self.items = items
        boxes = array("d")
        for item in items:
            xs, ys, zs = zip(*item)
            boxes.extend((min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)))
        self.item_boxes = boxes
        count = len(items)
        order = list(range(count))
        if count > LEAF_SIZE:
            lo = [min(boxes[k::6]) for k in range(3)]
            hi = [max(boxes[k + 3::6]) for k in range(3)]
            scale = [511.5 / (h - low) if h > low else 0.0 for low, h in zip(lo, hi)]
            spread = _SPREAD
            codes = [spread[int((boxes[o] + boxes[o + 3] - 2 * lo[0]) * scale[0])]
                     | spread[int((boxes[o + 1] + boxes[o + 4] - 2 * lo[1]) * scale[1])] << 1
                     | spread[int((boxes[o + 2] + boxes[o + 5] - 2 * lo[2]) * scale[2])] << 2
                     for o in range(0, 6 * count, 6)]
            order.sort(key=codes.__getitem__)
        self.order = order
        spans = [(0, count)]
        children: list[int] = []
        """Primer hijo de cada nodo (el segundo va detrás), o -1 en las hojas."""
        node = 0
        while node < len(spans):
            start, end = spans[node]
            if end - start > LEAF_SIZE:
                middle = (start + end) // 2
                children.append(len(spans))
                spans += [(start, middle), (middle, end)]
            else:
                children.append(-1)
            node += 1
        box = [0.0] * (6 * len(spans))
        for node in range(len(spans) - 1, -1, -1):
            o, child = 6 * node, children[node]
            if child < 0:
                start, end = spans[node]
                if start == end:
                    continue
                offsets = [6 * order[k] for k in range(start, end)]
                for k in range(3):
                    box[o + k] = min(boxes[j + k] for j in offsets)
                    box[o + 3 + k] = max(boxes[j + 3 + k] for j in offsets)
            else:
                a, b = 6 * child, 6 * child + 6
                for k in range(3):
                    box[o + k] = min(box[a + k], box[b + k])
                    box[o + 3 + k] = max(box[a + 3 + k], box[b + 3 + k])
        self.box = array("d", box)
        self.children = children
        self.spans = spans

    def nearest(self, point: Vec, limit: float = math.inf) -> tuple[int, float, Vec] | None:
        """El elemento más cercano a ``point`` (a ``limit`` como mucho): índice, distancia y punto."""
        if not self.items:
            return None
        best, best_distance = None, limit
        box, children = self.box, self.children
        stack = [0]
        while stack:
            node = stack.pop()
            if _point_gap(box, 6 * node, point) > best_distance:
                continue
            child = children[node]
            if child >= 0:
                near, far = child, child + 1
                if _point_gap(box, 6 * far, point) < _point_gap(box, 6 * near, point):
                    near, far = far, near
                stack += [far, near]
                continue
            start, end = self.spans[node]
            for k in range(start, end):
                index = self.order[k]
                p, q = _closest_items((point,), self.items[index])
                d = _dist(p, q)
                if d <= best_distance:
                    best, best_distance = (index, q), d
        return None if best is None else (best[0], best_distance, best[1])


def _extent(tree: BoxTree, node: int) -> float:
    box, o = tree.box, 6 * node
    return box[o + 3] - box[o] + box[o + 4] - box[o + 1] + box[o + 5] - box[o + 2]


def closest_between(a: BoxTree, b: BoxTree, budget: int = MAX_WORK) -> tuple[float, Vec, Vec] | None:
    """Distancia mínima entre lo que guardan dos árboles y los puntos donde se da.

    Se recorren las parejas de nodos de la más cercana a la más lejana y se descartan las que
    no pueden mejorar lo encontrado en más de una cienmilésima del tamaño (muy por debajo de
    lo que se muestra): así dos superficies paralelas, a la misma distancia en todas partes,
    no obligan a compararlo todo. ``budget`` pone un tope al trabajo: lo primero que se mira
    es lo más prometedor, así que al llegar a él ya se tiene la distancia.
    """
    if not a.items or not b.items:
        return None
    size = max(_dist(a.box[0:3], a.box[3:6]), _dist(b.box[0:3], b.box[3:6]), 1e-30)
    slack = size * 1e-5
    best: tuple[float, Vec, Vec] | None = None
    best_distance = math.inf
    heap = [(_box_gap(a.box, 0, b.box, 0), 0, 0)]
    work = 0
    while heap and work < budget:
        gap, na, nb = heapq.heappop(heap)
        if gap >= best_distance - slack:
            break
        work += 1
        ca, cb = a.children[na], b.children[nb]
        if ca < 0 and cb < 0:
            for i in range(*a.spans[na]):
                ia = a.order[i]
                for j in range(*b.spans[nb]):
                    ib = b.order[j]
                    limit = best_distance - slack
                    work += 1
                    if _box_gap(a.item_boxes, 6 * ia, b.item_boxes, 6 * ib) >= limit:
                        continue
                    x, y = a.items[ia], b.items[ib]
                    if (len(x) == 3 and _plane_gap(x, y) >= limit) or (len(y) == 3 and _plane_gap(y, x) >= limit):
                        continue
                    work += 9
                    p, q = _closest_items(x, y)
                    d = _dist(p, q)
                    if d < best_distance:
                        best, best_distance = (d, p, q), d
            continue
        # Se abre el nodo más grande (o el que no es hoja).
        if cb < 0 or (ca >= 0 and _extent(a, na) >= _extent(b, nb)):
            pairs = [(ca, nb), (ca + 1, nb)]
        else:
            pairs = [(na, cb), (na, cb + 1)]
        for x, y in pairs:
            g = _box_gap(a.box, 6 * x, b.box, 6 * y)
            if g < best_distance - slack:
                heapq.heappush(heap, (g, x, y))
    return best


def _edge_key(i: int, j: int) -> int:
    return i << 32 | j if i < j else j << 32 | i


def _vertex_ids(values: array, ids: dict[Vec, int]) -> list[int]:
    """Número de cada vértice de la lista de coordenadas: los repetidos tienen el mismo."""
    add = ids.setdefault
    return [add(p, len(ids)) for p in zip(values[0::3], values[1::3], values[2::3])]


class PartTopology:
    """Caras y curvas de una pieza de la escena, en coordenadas del mundo."""

    def __init__(self, scene: Scene, part_index: int):
        part = scene.parts[part_index]
        ids: dict[Vec, int] = {}
        corners: list[int] = []
        """Tres vértices por triángulo."""
        ends: list[int] = []
        """Dos vértices por arista viva (las que el visor dibuja como contorno)."""
        self.complete = True
        for prim_index in part.primitives:
            primitive = scene.primitives[prim_index]
            if primitive.kind == "triangles":
                values = array("f", primitive.positions)
                values = values[:len(values) - len(values) % 9]
                if len(corners) // 3 + len(values) // 9 > MAX_TRIANGLES:
                    self.complete = False
                else:
                    corners += _vertex_ids(values, ids)
                edges = array("f", primitive.edges)
            elif primitive.kind == "lines":
                edges = array("f", primitive.positions)
            else:
                continue
            ends += _vertex_ids(edges[:len(edges) - len(edges) % 6], ids)
        points = list(ids)
        del ids
        if part.matrix != IDENTITY:
            points = [transform_point(part.matrix, p) for p in points]
        if points:
            columns = list(zip(*points))
            self.size = _dist(tuple(map(min, columns)), tuple(map(max, columns))) or 1.0
        else:
            self.size = 1.0
        self.tolerance = self.size * 1e-5
        self.curves: list[Curve] = []
        self.faces: list[Face] = []
        self._segment_curve: dict[tuple[Vec, Vec], int] = {}
        self._build_curves(points, ends)
        self._build_faces(points, corners, ends)

    # ---------- curvas ----------
    @staticmethod
    def _key(a: Vec, b: Vec) -> tuple[Vec, Vec]:
        return (a, b) if a <= b else (b, a)

    def _build_curves(self, points: list[Vec], ends: list[int]) -> None:
        unique = list(dict.fromkeys(_edge_key(i, j) for i, j in zip(ends[0::2], ends[1::2]) if i != j))
        neighbours: dict[int, list[int]] = {}
        for index, key in enumerate(unique):
            neighbours.setdefault(key >> 32, []).append(index)
            neighbours.setdefault(key & _LOW, []).append(index)
        used = [False] * len(unique)

        def walk(start: int, first: int) -> list[int]:
            chain = [start]
            current, edge = start, first
            while True:
                used[edge] = True
                key = unique[edge]
                current = key & _LOW if key >> 32 == current else key >> 32
                chain.append(current)
                following = [e for e in neighbours[current] if not used[e]]
                if len(neighbours[current]) != 2 or not following:
                    return chain
                edge = following[0]

        chains: list[tuple[list[int], bool]] = []
        for vertex, edges in neighbours.items():
            if len(edges) == 2:
                continue
            for edge in edges:
                if not used[edge]:
                    chains.append((walk(vertex, edge), False))
        for edge in range(len(unique)):
            if not used[edge]:
                loop = walk(unique[edge] >> 32, edge)
                closed = loop[0] == loop[-1]
                chains.append((loop[:-1] if closed else loop, closed))
        for chain, closed in chains:
            for piece, piece_closed in self._split_at_corners([points[i] for i in chain], closed):
                curve = self._classify_curve(piece, piece_closed)
                index = len(self.curves)
                self.curves.append(curve)
                for a, b in curve.segments():
                    self._segment_curve[self._key(a, b)] = index

    @staticmethod
    def _turn(a: Vec, b: Vec, c: Vec) -> float:
        u, v = _unit(_sub(b, a)), _unit(_sub(c, b))
        if u is None or v is None:
            return 0.0
        return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v)))))

    def _split_at_corners(self, points: list[Vec], closed: bool):
        """Parte la cadena por sus esquinas: cada lado de un contorno es una curva."""
        n = len(points)
        if n < 3:
            return [(points, closed and n > 2)]
        if closed:
            corners = [i for i in range(n) if self._turn(points[i - 1], points[i], points[(i + 1) % n]) > SPLIT_ANGLE]
            if not corners:
                return [(points, True)]
            start = corners[0]
            ring = points[start:] + points[:start] + [points[start]]
            corners = [(c - start) % n for c in corners] + [n]
        else:
            ring = points
            corners = [0] + [i for i in range(1, n - 1)
                             if self._turn(points[i - 1], points[i], points[i + 1]) > SPLIT_ANGLE] + [n - 1]
        pieces = []
        for a, b in zip(corners, corners[1:]):
            if b > a:
                pieces.append((ring[a:b + 1], False))
        return pieces

    def _classify_curve(self, points: list[Vec], closed: bool) -> Curve:
        ring = points + [points[0]] if closed else points
        length = sum(_dist(a, b) for a, b in zip(ring, ring[1:]))
        curve = Curve(points, closed, "curve", length)
        tol = max(self.tolerance, length * 1e-4)
        if not closed:
            direction = _unit(_sub(points[-1], points[0]))
            if direction is not None and all(
                    _length(_cross(_sub(p, points[0]), direction)) <= tol for p in points):
                curve.kind = "line"
                return curve
        found = fit_circle(points, self.tolerance)
        if found is not None:
            curve.center, curve.radius, curve.normal = found
            if closed:
                curve.kind = "circle"
            else:
                curve.kind = "arc"
                sweep = 0.0
                for a, b in zip(points, points[1:]):
                    u, v = _unit(_sub(a, curve.center)), _unit(_sub(b, curve.center))
                    if u is not None and v is not None:
                        sweep += math.degrees(math.atan2(_length(_cross(u, v)), _dot(u, v)))
                if sweep >= 359.0:
                    curve.kind = "circle"
                curve.sweep = min(sweep, 360.0)
        return curve

    def curve_for_segment(self, a: Vec, b: Vec) -> int | None:
        return self._segment_curve.get(self._key(a, b))

    # ---------- caras ----------
    def _build_faces(self, points: list[Vec], corners: list[int], ends: list[int]) -> None:
        triangles = [t for t in zip(corners[0::3], corners[1::3], corners[2::3])
                     if t[0] != t[1] and t[1] != t[2] and t[0] != t[2]]
        count = len(triangles)
        if not count:
            return
        barrier = {_edge_key(i, j) for i, j in zip(ends[0::2], ends[1::2])}
        # Cada arista con su primer triángulo y, si lo hay, el segundo; con más, la malla no es
        # una superficie normal ahí y la arista separa caras.
        first: dict[int, int] = {}
        second: dict[int, int] = {}
        crowded: set[int] = set()
        for index, (a, b, c) in enumerate(triangles):
            for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
                owner = first.setdefault(key, index)
                if owner != index:
                    if key in second:
                        crowded.add(key)
                    else:
                        second[key] = index
        parent = list(range(count))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for key, other in second.items():
            if key not in barrier and key not in crowded:
                ra, rb = find(first[key]), find(other)
                if ra != rb:
                    parent[ra] = rb
        members: list[list[int]] = []
        face_of = [0] * count
        roots: dict[int, int] = {}
        for index in range(count):
            root = find(index)
            face_index = roots.get(root)
            if face_index is None:
                face_index = roots[root] = len(members)
                members.append([])
            members[face_index].append(index)
            face_of[index] = face_index
        shapes = [(points[a], points[b], points[c]) for a, b, c in triangles]
        self.faces = [Face([shapes[t] for t in group]) for group in members]
        for key, owner in first.items():
            other = second.get(key)
            if other is not None and key not in barrier and key not in crowded:
                continue
            segment = (points[key >> 32], points[key & _LOW])
            self.faces[face_of[owner]].outline.append(segment)
            if other is not None and face_of[other] != face_of[owner]:
                self.faces[face_of[other]].outline.append(segment)
        for face, group in zip(self.faces, members):
            self._classify_face(face, [triangles[t] for t in group], points)
        self._tree = BoxTree(shapes)
        self._triangle_face = face_of

    def _classify_face(self, face: Face, triangles: list[tuple[int, int, int]], points: list[Vec]) -> None:
        normals: list[Vec] = []
        areas: list[float] = []
        for i, j, k in triangles:
            a, b, c = points[i], points[j], points[k]
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length >= 1e-30:
                normals.append((nx / length, ny / length, nz / length))
                areas.append(length / 2)
        face.area = sum(areas)
        if not normals:
            return
        rx, ry, rz = normals[0]
        sx = sy = sz = 0.0
        for (nx, ny, nz), area in zip(normals, areas):
            if nx * rx + ny * ry + nz * rz < 0:
                area = -area
            sx, sy, sz = sx + nx * area, sy + ny * area, sz + nz * area
        mean = _unit((sx, sy, sz))
        vertices = [points[i] for i in dict.fromkeys(i for t in triangles for i in t)]
        tol = max(self.tolerance, math.sqrt(face.area) * 1e-4)
        if mean is not None:
            mx, my, mz = mean
            flat = math.cos(math.radians(1.0))
            if all(abs(nx * mx + ny * my + nz * mz) >= flat for nx, ny, nz in normals):
                ox, oy, oz = origin = vertices[0]
                if all(abs((p[0] - ox) * mx + (p[1] - oy) * my + (p[2] - oz) * mz) <= tol for p in vertices):
                    face.kind, face.normal, face.point = "plane", mean, origin
                    return
        # Cilindro: el eje es la dirección a la que las normales son (casi) perpendiculares, y lo
        # que decide son los puntos, que están sobre la superficie exacta: todos a la misma
        # distancia del eje. Las normales de los triángulos se tuercen un poco si la malla va sesgada.
        xx = xy = xz = yy = yz = zz = 0.0
        for (nx, ny, nz), area in zip(normals, areas):
            xx, xy, xz = xx + nx * nx * area, xy + nx * ny * area, xz + nx * nz * area
            yy, yz, zz = yy + ny * ny * area, yz + ny * nz * area, zz + nz * nz * area
        axis = _unit(smallest_eigenvector([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]))
        if axis is None:
            return
        ax, ay, az = axis
        skew = math.sin(math.radians(25.0))
        if any(abs(nx * ax + ny * ay + nz * az) > skew for nx, ny, nz in normals):
            return
        base = vertices[0]
        flat_points = [_sub(p, _scale(axis, _dot(_sub(p, base), axis))) for p in vertices]
        unique = list(dict.fromkeys((round(p[0], 9), round(p[1], 9), round(p[2], 9)) for p in flat_points))
        if len(unique) < 3:
            return
        found = circumcircle(*_spread_three(unique))
        if found is None:
            return
        center, radius, _normal = found
        limit = max(tol, radius * 5e-3)
        if all(abs(_dist(p, center) - radius) <= limit for p in unique):
            face.kind, face.axis, face.point, face.radius = "cylinder", axis, center, radius

    def face_at(self, point: Vec, tolerance: float | None = None) -> int | None:
        """Cara en la que está ``point`` (un punto de la superficie, leído bajo el cursor)."""
        if not self.faces:
            return None
        found = self._tree.nearest(point, tolerance if tolerance is not None else self.size * 2e-3)
        return None if found is None else self._triangle_face[found[0]]
