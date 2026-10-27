"""Geometría de las piezas para medir como un CAD."""

from __future__ import annotations

import math

import pytest

from blackpdf.core.model3d import measure3d as m3
from blackpdf.core.model3d import meshes, topology

from .test_measure3d import _cube


def _cylinder(radius=5.0, height=10.0, sides=48, center=(0.0, 0.0), sweep=360.0, name="Cilindro"):
    """Cilindro (o un trozo, con ``sweep`` < 360) con sus tapas, como lo daría un programa de CAD."""
    part = meshes.MeshPart(name)
    cx, cy = center
    closed = sweep >= 360.0
    steps = sides if closed else max(2, int(sides * sweep / 360))
    ring = [(cx + radius * math.cos(math.radians(sweep * i / (sides if closed else steps))),
             cy + radius * math.sin(math.radians(sweep * i / (sides if closed else steps))))
            for i in range(steps if closed else steps + 1)]
    count = len(ring)
    part.positions = [(x, y, 0.0) for x, y in ring] + [(x, y, height) for x, y in ring]
    part.positions += [(cx, cy, 0.0), (cx, cy, height)]
    bottom, top = 2 * count, 2 * count + 1
    for i in range(count if closed else count - 1):
        j = (i + 1) % count
        part.add_polygon([i, j, count + j, count + i])
        part.triangles.append((bottom, j, i))
        part.triangles.append((top, count + i, count + j))
    if not closed:
        part.add_polygon([count - 1, 0, count, 2 * count - 1])
        part.triangles.append((bottom, 0, count - 1))
        part.triangles.append((top, 2 * count - 1, count))
    return part


def test_topology_of_a_cylinder():
    topo = topology.PartTopology(meshes.parts_scene([_cylinder(5.0, 10.0)]), 0)
    kinds = sorted(face.kind for face in topo.faces)
    assert kinds == ["cylinder", "plane", "plane"]
    side = next(face for face in topo.faces if face.kind == "cylinder")
    assert math.isclose(side.radius, 5.0, rel_tol=1e-4) and math.isclose(abs(side.axis[2]), 1.0, rel_tol=1e-6)
    assert math.isclose(side.point[0], 0, abs_tol=1e-4) and math.isclose(side.point[1], 0, abs_tol=1e-4)
    rims = [curve for curve in topo.curves if curve.kind == "circle"]
    assert len(rims) == 2 and all(math.isclose(c.radius, 5.0, rel_tol=1e-6) for c in rims)
    assert sorted(round(c.center[2], 6) for c in rims) == [0.0, 10.0]


def test_topology_of_a_box_and_an_arc():
    topo = topology.PartTopology(meshes.parts_scene([_cube(10.0)]), 0)
    assert [face.kind for face in topo.faces] == ["plane"] * 6
    assert all(math.isclose(face.area, 100.0) for face in topo.faces)
    assert sorted(round(c.length, 6) for c in topo.curves) == [10.0] * 12
    assert all(c.kind == "line" for c in topo.curves)

    half = topology.PartTopology(meshes.parts_scene([_cylinder(4.0, 6.0, sides=64, sweep=180.0)]), 0)
    arcs = [c for c in half.curves if c.kind == "arc"]
    assert len(arcs) == 2 and all(math.isclose(c.radius, 4.0, rel_tol=1e-6) for c in arcs)
    assert all(math.isclose(c.sweep, 180.0, abs_tol=0.01) for c in arcs)
    lines = sorted(round(c.length, 6) for c in half.curves if c.kind == "line")
    assert lines == [6.0, 6.0, 8.0, 8.0], "el diámetro de las tapas y las dos generatrices"


def test_face_at_and_curve_for_segment():
    scene = meshes.parts_scene([_cylinder(5.0, 10.0)])
    topo = topology.PartTopology(scene, 0)
    side = topo.face_at((5.0, 0.0, 5.0))
    assert topo.faces[side].kind == "cylinder"
    assert topo.faces[topo.face_at((1.0, 1.0, 10.0))].kind == "plane"
    assert topo.face_at((50.0, 50.0, 50.0)) is None
    rim = next(c for c in topo.curves if c.kind == "circle")
    index = topo.curve_for_segment(rim.points[0], rim.points[1])
    assert topo.curves[index] is rim


def test_too_big_parts_are_not_rebuilt(monkeypatch):
    monkeypatch.setattr(topology, "MAX_TRIANGLES", 10)
    topo = topology.PartTopology(meshes.parts_scene([_cylinder()]), 0)
    assert not topo.complete and not topo.faces and topo.curves, "las aristas sí; las caras, no"


def test_smallest_eigenvector():
    v = topology.smallest_eigenvector([[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 1.0]])
    assert math.isclose(abs(v[2]), 1.0)
    u = (1 / math.sqrt(2), 1 / math.sqrt(2), 0.0)
    m = [[(1.0 if i == j else 0.0) - 0.9 * u[i] * u[j] for j in range(3)] for i in range(3)]
    v = topology.smallest_eigenvector(m)
    assert math.isclose(abs(v[0] * u[0] + v[1] * u[1]), 1.0, rel_tol=1e-9)


def _topology(parts):
    return topology.PartTopology(meshes.parts_scene(parts), 0)


def test_relations_like_a_cad():
    scene = meshes.parts_scene([_cylinder(5.0, 10.0, center=(0, 0), name="A"), _cylinder(3.0, 10.0, center=(30, 40), name="B")])
    a, b = topology.PartTopology(scene, 0), topology.PartTopology(scene, 1)
    side_a = next(f for f in a.faces if f.kind == "cylinder")
    side_b = next(f for f in b.faces if f.kind == "cylinder")
    relation = m3.relate(m3.entity_from_face(side_a, (5, 0, 5)), m3.entity_from_face(side_b, (27, 40, 5)))
    assert math.isclose(relation.center_distance, 50.0, rel_tol=1e-4), "entre ejes paralelos"
    assert math.isclose(relation.distance, 42.0, rel_tol=1e-3), "y la mínima, entre las superficies"
    assert relation.angle == pytest.approx(0.0, abs=0.01)

    rim_a = next(c for c in a.curves if c.kind == "circle" and c.center[2] > 5)
    rim_b = next(c for c in b.curves if c.kind == "circle" and c.center[2] < 5)
    relation = m3.relate(m3.entity_from_curve(rim_a, rim_a.points[0]), m3.entity_from_curve(rim_b, rim_b.points[0]))
    assert math.isclose(relation.center_distance, math.sqrt(50 ** 2 + 10 ** 2), rel_tol=1e-6)
    assert [round(v, 6) for v in relation.delta] == [30.0, 40.0, 10.0]
    assert m3.describe(m3.entity_from_curve(rim_a, rim_a.points[0]))["diameter"] == pytest.approx(10.0, rel=1e-6)

    top, bottom = sorted((f for f in a.faces if f.kind == "plane"), key=lambda f: -f.triangles[0][0][2])
    relation = m3.relate(m3.entity_from_face(top, (1, 1, 10)), m3.entity_from_face(bottom, (-2, 1, 0)))
    assert relation.parallel and math.isclose(relation.distance, 10.0), "espesor entre caras paralelas"
    corner = m3.Entity("vertex", (3.0, 4.0, 7.0))
    relation = m3.relate(corner, m3.entity_from_face(bottom, (-2, 1, 0)))
    assert math.isclose(relation.distance, 7.0) and relation.anchors[1] == pytest.approx((3.0, 4.0, 0.0))
    relation = m3.relate(corner, m3.entity_from_curve(rim_a, rim_a.points[0]))
    assert math.isclose(relation.center_distance, math.sqrt(9 + 16 + 9))


def test_relations_between_lines_and_faces():
    box = _topology([_cube(10.0)])
    lines = box.curves
    x_line = next(c for c in lines if abs(c.points[0][0] - c.points[-1][0]) > 5)
    z_line = next(c for c in lines if abs(c.points[0][2] - c.points[-1][2]) > 5)
    relation = m3.relate(m3.entity_from_curve(x_line, x_line.points[0]), m3.entity_from_curve(z_line, z_line.points[0]))
    assert relation.angle == pytest.approx(90.0)
    side = next(f for f in box.faces if abs(f.normal[0]) > 0.9)
    relation = m3.relate(m3.entity_from_curve(z_line, z_line.points[0]), m3.entity_from_face(side, side.triangles[0][0]))
    assert relation.angle == pytest.approx(0.0, abs=1e-9), "una recta paralela a una cara forma 0° con ella"
    described = m3.describe(m3.entity_from_face(side, side.triangles[0][0]))
    assert described == {"area": pytest.approx(100.0)}
    assert m3.describe(m3.Entity("vertex", (1.0, 2.0, 3.0))) == {"position": (1.0, 2.0, 3.0)}


@pytest.mark.skipif(not meshes.cad_available(), reason="gmsh no está instalado")
def test_the_sample_plate():
    from pathlib import Path

    step = Path(__file__).resolve().parent.parent / "blackpdf" / "resources" / "model3d" / "placa.step"
    topo = topology.PartTopology(meshes.load_mesh_file(str(step)), 0)
    hole = next(f for f in topo.faces if f.kind == "cylinder")
    assert hole.radius == pytest.approx(4.0, abs=1e-4)
    assert sorted(round(c.length, 4) for c in topo.curves if c.kind == "line") == [4.0] * 4 + [20.0] * 4 + [30.0] * 4


def _brute_force(xs, ys):
    """La distancia mínima comparando cada pareja: lo que el árbol debe dar sin mirarlo todo."""
    return min(math.dist(*topology._closest_items(x, y)) for x in xs for y in ys)


def test_closest_between_matches_brute_force():
    import random

    rng = random.Random(7)

    def soup(count, offset):
        items = []
        for _ in range(count):
            base = [rng.uniform(-5, 5) + o for o in offset]
            items.append(tuple(tuple(c + rng.uniform(-1, 1) for c in base) for _ in range(3)))
        return items

    for offset in ((12.0, 0.0, 0.0), (3.0, 2.0, 1.0), (0.0, 0.0, 0.0)):
        xs, ys = soup(60, (0.0, 0.0, 0.0)), soup(70, offset)
        found = topology.closest_between(topology.BoxTree(xs), topology.BoxTree(ys))
        assert found[0] == pytest.approx(_brute_force(xs, ys), abs=1e-4)
        assert math.dist(found[1], found[2]) == pytest.approx(found[0])
        segments = [t[:2] for t in ys]
        found = topology.closest_between(topology.BoxTree(xs), topology.BoxTree(segments))
        assert found[0] == pytest.approx(_brute_force(xs, segments), abs=1e-4)


def test_closest_between_crossing_and_touching():
    flat = [((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0))]
    through = [((2.0, 2.0, -1.0), (2.0, 2.0, 1.0), (3.0, 3.0, 1.0))]
    assert topology.closest_between(topology.BoxTree(flat), topology.BoxTree(through))[0] == 0.0, "se cortan"
    above = [((1.0, 1.0, 3.0), (5.0, 1.0, 3.0), (1.0, 5.0, 3.0))]
    assert topology.closest_between(topology.BoxTree(flat), topology.BoxTree(above))[0] == pytest.approx(3.0)
    point = topology.BoxTree([((20.0, 0.0, 0.0),)])
    assert topology.closest_between(topology.BoxTree(flat), point)[0] == pytest.approx(10.0)
    tree = topology.BoxTree(flat * 3 + above)
    index, gap, at = tree.nearest((2.0, 2.0, 2.5))
    assert index == 3 and gap == pytest.approx(0.5) and at == pytest.approx((2.0, 2.0, 3.0))
    assert tree.nearest((50.0, 0.0, 0.0), limit=1.0) is None


def test_minimum_distance_between_faces_of_a_tube():
    """Dos superficies a la misma distancia en todas partes: no se compara todo con todo, y sale exacta."""
    part = meshes.MeshPart("Tubo")
    around, along = 96, 12
    for radius in (5.0, 4.0):
        base = len(part.positions)
        part.positions += [(radius * math.cos(2 * math.pi * i / around), radius * math.sin(2 * math.pi * i / around), 2.0 * j)
                           for j in range(along + 1) for i in range(around)]
        for j in range(along):
            for i in range(around):
                a, b = base + j * around + i, base + j * around + (i + 1) % around
                part.triangles += [(a, b, b + around), (a, b + around, a + around)]
    topo = _topology([part])
    outer, inner = (topo.faces[topo.face_at((r, 0.0, 10.0))] for r in (5.0, 4.0))
    assert outer.kind == inner.kind == "cylinder" and outer is not inner
    relation = m3.relate(m3.entity_from_face(outer, (5.0, 0.0, 10.0)), m3.entity_from_face(inner, (4.0, 0.0, 10.0)))
    wall = math.cos(math.pi / around)
    assert relation.distance == pytest.approx(wall, abs=1e-6), "la pared, entre los polígonos de la malla"
    assert relation.center_distance == pytest.approx(0.0, abs=1e-6) and relation.parallel


def test_angle_of_a_circle_is_that_of_its_plane():
    topo = _topology([_cylinder(5.0, 10.0)])
    rim = next(c for c in topo.curves if c.kind == "circle")
    top = next(f for f in topo.faces if f.kind == "plane")
    side = next(f for f in topo.faces if f.kind == "cylinder")
    circle = m3.entity_from_curve(rim, rim.points[0])
    assert m3.relate(circle, m3.entity_from_face(top, top.triangles[0][0])).angle == pytest.approx(0.0, abs=1e-6)
    assert m3.relate(circle, m3.entity_from_face(side, (5.0, 0.0, 5.0))).angle == pytest.approx(90.0, abs=1e-6)
    upright = m3.entity_from_curve(topology.Curve([(9.0, 0.0, 0.0), (9.0, 0.0, 4.0)], False, "line", 4.0), (9.0, 0.0, 0.0))
    assert m3.relate(circle, upright).angle == pytest.approx(90.0, abs=1e-6), "una recta a lo largo del eje"
