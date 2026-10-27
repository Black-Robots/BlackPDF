"""Medir en el visor 3D."""

from __future__ import annotations

import math
import zipfile

import pytest

from blackpdf.core.errors import InvalidOptionError
from blackpdf.core.model3d import measure3d as m3
from blackpdf.core.model3d import meshes
from blackpdf.core.model3d.scene import build_lines


def _cube(size: float = 10.0, offset=(0.0, 0.0, 0.0), name: str = "Cubo") -> meshes.MeshPart:
    part = meshes.MeshPart(name)
    ox, oy, oz = offset
    part.positions = [(ox + x * size, oy + y * size, oz + z * size) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    for face in ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)):
        part.add_polygon(list(face))
    return part


def test_distance_angle_and_circle():
    assert m3.distance((0, 0, 0), (3, 4, 12)) == 13
    assert m3.deltas((5, 1, 0), (2, 4, 0)) == (3, 3, 0)
    assert math.isclose(m3.angle((1, 0, 0), (0, 0, 0), (0, 5, 0)), 90)
    assert math.isclose(m3.angle((1, 1, 0), (0, 0, 0), (2, 2, 0)), 0, abs_tol=1e-6)
    # Circunferencia de radio 5 centrada en (1, 2, 3) en un plano inclinado.
    u = (1 / math.sqrt(2), 0, 1 / math.sqrt(2))
    v = (0, 1, 0)
    points = [tuple(c + 5 * (math.cos(t) * u[i] + math.sin(t) * v[i]) for i, c in enumerate((1, 2, 3)))
              for t in (0.3, 1.9, 4.0)]
    center, radius, normal = m3.circle_through(*points)
    assert math.isclose(radius, 5, rel_tol=1e-9) and all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(center, (1, 2, 3)))
    assert math.isclose(abs(m3._dot(normal, (-u[2], 0, u[0]))), 1, rel_tol=1e-9)
    with pytest.raises(InvalidOptionError):
        m3.circle_through((0, 0, 0), (1, 1, 1), (2, 2, 2))
    with pytest.raises(InvalidOptionError):
        m3.angle((0, 0, 0), (0, 0, 0), (1, 0, 0))


def test_measure_value_and_units():
    assert m3.measure_value("distance", [(0, 0, 0), (0, 0, 7)]) == 7
    assert math.isclose(m3.measure_value("angle", [(1, 0, 0), (0, 0, 0), (0, 0, 1)]), 90)
    assert math.isclose(m3.measure_value("radius", [(2, 0, 0), (0, 2, 0), (-2, 0, 0)]), 2)
    with pytest.raises(InvalidOptionError):
        m3.measure_value("distance", [(0, 0, 0)])
    with pytest.raises(InvalidOptionError):
        m3.measure_value("volume", [(0, 0, 0), (1, 1, 1)])
    assert m3.convert(25.4, 1.0, "in") == 1 and m3.convert(2.5, 1000.0, "cm") == 250
    assert m3.unit_name(25.4) == "in" and m3.unit_name(1.0) == "mm" and m3.unit_name(None) is None
    assert m3.unit_name(3.0) is None
    with pytest.raises(InvalidOptionError):
        m3.convert(1, 1, "leguas")


def test_snap_to_corners_edges_and_surface():
    scene = meshes.parts_scene([_cube(), _cube(offset=(30, 0, 0), name="Escondido")])
    index = m3.SnapIndex(scene, hidden={1})
    assert index.segment_count >= 12, "las aristas vivas del cubo"
    assert m3.SnapIndex(scene).segment_count > index.segment_count
    point, kind = index.snap((10.3, 9.8, 10.2), 0.6)
    assert kind == "vertex" and point == (10, 10, 10)
    point, kind = index.snap((5.0, 0.2, 10.1), 0.6)
    assert kind == "edge" and all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(point, (5, 0, 10)))
    point, kind = index.snap((5.0, 5.0, 10.0), 0.6)
    assert kind == "surface" and point == (5.0, 5.0, 10.0)
    point, kind = index.snap((30.2, 0.1, 0.1), 0.6)
    assert kind == "surface", "las piezas ocultas no atraen el cursor"


def test_snap_to_lines():
    scene = meshes.parts_scene([])
    scene.primitives.append(build_lines([(0, 0, 0), (100, 0, 0)], [(0, 1)]))
    from blackpdf.core.model3d.scene import IDENTITY, Part

    scene.parts.append(Part("Eje", IDENTITY, [0]))
    point, kind = m3.SnapIndex(scene).snap((40, 0.5, 0), 1.0)
    assert kind == "edge" and math.isclose(point[0], 40)


def test_units_by_file(tmp_path):
    assert meshes.unit_mm("pieza.step") == 1.0 and meshes.unit_mm("pieza.IGS") == 1.0
    assert meshes.unit_mm("pieza.glb") == 1000.0
    assert meshes.unit_mm("pieza.stl") is None and meshes.unit_mm("pieza.obj") is None
    inch = tmp_path / "pieza.3mf"
    with zipfile.ZipFile(inch, "w") as archive:
        archive.writestr("3D/3dmodel.model", '<?xml version="1.0"?><model unit="inch" xmlns="x"><resources/></model>')
    assert meshes.unit_mm(str(inch)) == 25.4
    plain = tmp_path / "otra.3mf"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("3D/3dmodel.model", '<model xmlns="x"><resources/></model>')
    assert meshes.unit_mm(str(plain)) == 1.0


def test_snap_ray_in_screen_terms():
    index = m3.SnapIndex(meshes.parts_scene([_cube()]))
    down = (0.0, 0.0, -1.0)
    # Junto a la silueta, sin tocar la pieza: se pega a la arista de arriba.
    point, kind, _segment = index.snap_ray((10.3, 5.0, 100.0), down, 0.6, 0.0, None)
    assert kind == "edge" and all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(point, (10, 5, 10)))
    # Cerca de una esquina, mirando a la cara de arriba (a 90 del origen).
    point, kind, _segment = index.snap_ray((0.2, 0.3, 100.0), down, 0.6, 0.0, 90.0)
    assert kind == "vertex" and point == (0, 0, 10)
    # Si algo la tapa (la superficie que se ve está más cerca), esa esquina no cuenta.
    assert index.snap_ray((0.2, 0.3, 100.0), down, 0.6, 0.0, 50.0) is None
    # En perspectiva la tolerancia crece con la distancia.
    assert index.snap_ray((11.0, 5.0, 100.0), down, 0.0, 0.005, None) is None
    assert index.snap_ray((11.0, 5.0, 100.0), down, 0.0, 0.02, None).kind == "edge"
    assert index.snap_ray((50.0, 50.0, 100.0), down, 0.6, 0.0, None) is None, "lejos de la pieza, nada"


def test_snap_ray_stays_fast_with_the_part_far_away():
    import time

    index = m3.SnapIndex(meshes.parts_scene([_cube()]))
    down = (0.0, 0.0, -1.0)
    for origin, growth in (((10.4, 5.0, 1000.0), 0.0005), ((10.4, 5.0, 300.0), 0.002), ((5.0, 5.0, 1e5), 1.0)):
        started = time.perf_counter()
        result = index.snap_ray(origin, down, 0.0, growth, None)
        assert time.perf_counter() - started < 0.5, origin
        assert result is not None, origin


def test_snap_ray_ignores_what_the_section_cuts():
    index = m3.SnapIndex(meshes.parts_scene([_cube()]))
    down = (0.0, 0.0, -1.0)
    assert index.snap_ray((0.2, 0.3, 100.0), down, 0.6, 0.0, None).point == (0, 0, 10)
    cut_top = (0.0, 0.0, 1.0, -5.0)  # todo lo que está por encima de z = 5, recortado
    point, kind, _segment = index.snap_ray((0.2, 0.3, 100.0), down, 0.6, 0.0, None, cut_top)
    assert kind == "vertex" and point == (0, 0, 0)


def test_snap_index_has_a_limit(monkeypatch):
    monkeypatch.setattr(m3, "MAX_SEGMENTS", 5)
    index = m3.SnapIndex(meshes.parts_scene([_cube()]))
    assert index.truncated and index.segment_count <= 5
