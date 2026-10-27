"""Piezas 3D de archivos de malla y de CAD: STL, OBJ, 3MF, glTF/GLB, STEP e IGES.

Cada formato se convierte en una lista de :class:`MeshPart` (triángulos indexados con
nombre y color). STEP e IGES son superficies exactas: gmsh (con OpenCASCADE) las
convierte en triángulos. glTF usa Y hacia arriba y se gira para dejar Z arriba, como
el resto de formatos de CAD.
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from ..errors import CadUnavailableError, FileOpenError, Model3DEmptyError, Model3DError
from .scene import IDENTITY, Material, Part, Scene, build_triangles, multiply, transform_point

MESH_EXTENSIONS = (".stl", ".obj", ".3mf", ".glb", ".gltf")
CAD_EXTENSIONS = (".step", ".stp", ".iges", ".igs")
DEFAULT_COLOR = (0.72, 0.72, 0.75)

Vector = tuple[float, float, float]


@dataclass
class MeshPart:
    name: str
    positions: list[Vector] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    color: Vector | None = None

    def add_polygon(self, indices: list[int]) -> None:
        """Añade un polígono convexo (o casi) como abanico de triángulos."""
        for k in range(1, len(indices) - 1):
            a, b, c = indices[0], indices[k], indices[k + 1]
            if a != b and b != c and a != c:
                self.triangles.append((a, b, c))


def cad_available() -> bool:
    try:
        import gmsh  # noqa: F401
    except Exception:
        return False
    return True


# -- utilidades ---------------------------------------------------------------------


class _Welder:
    """Une vértices repetidos (STL guarda cada triángulo con sus tres vértices)."""

    def __init__(self, part: MeshPart):
        self.part = part
        self.index: dict[Vector, int] = {}

    def __call__(self, point: Vector) -> int:
        key = (round(point[0], 6), round(point[1], 6), round(point[2], 6))
        found = self.index.get(key)
        if found is None:
            found = self.index[key] = len(self.part.positions)
            self.part.positions.append(point)
        return found


def _hex_color(text: str | None) -> Vector | None:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", (text or "").strip())
    if not match:
        return None
    value = match.group(1)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _clean(parts: list[MeshPart], path: str) -> list[MeshPart]:
    parts = [part for part in parts if part.triangles]
    if not parts:
        raise Model3DEmptyError(path=path)
    names: dict[str, int] = {}
    for part in parts:
        base = part.name or Path(path).stem
        names[base] = names.get(base, 0) + 1
        part.name = base if names[base] == 1 else f"{base} ({names[base]})"
    return parts


def _transformed(part: MeshPart, matrix) -> MeshPart:
    if matrix == IDENTITY:
        return part
    return MeshPart(part.name, [transform_point(matrix, p) for p in part.positions], list(part.triangles), part.color)


# -- STL ----------------------------------------------------------------------------


def read_stl(path: str) -> list[MeshPart]:
    data = Path(path).read_bytes()
    stem = Path(path).stem
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            part = MeshPart(stem)
            weld = _Welder(part)
            for offset in range(84, 84 + count * 50, 50):
                values = struct.unpack_from("<12f", data, offset)
                part.add_polygon([weld(values[3:6]), weld(values[6:9]), weld(values[9:12])])
            return [part]
    text = data.decode("utf-8", "replace")
    if not text.lstrip().lower().startswith("solid"):
        raise Model3DError("STL no válido", path=path)
    parts: list[MeshPart] = []
    part = weld = None
    corners: list[int] = []
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        keyword = words[0].lower()
        if keyword == "solid":
            part = MeshPart(" ".join(words[1:]) or stem)
            weld = _Welder(part)
            parts.append(part)
        elif keyword == "vertex" and part is not None and len(words) >= 4:
            corners.append(weld((float(words[1]), float(words[2]), float(words[3]))))
        elif keyword == "endloop" and part is not None:
            part.add_polygon(corners)
            corners = []
    return parts


# -- OBJ ----------------------------------------------------------------------------


def _read_mtl(path: Path) -> dict[str, Vector]:
    colors: dict[str, Vector] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return colors
    current = None
    for line in lines:
        words = line.split()
        if not words:
            continue
        if words[0] == "newmtl" and len(words) > 1:
            current = " ".join(words[1:])
        elif words[0] == "Kd" and current is not None and len(words) >= 4:
            colors[current] = (float(words[1]), float(words[2]), float(words[3]))
    return colors


def read_obj(path: str) -> list[MeshPart]:
    source = Path(path)
    vertices: list[Vector] = []
    materials: dict[str, Vector] = {}
    parts: dict[tuple[str, str], MeshPart] = {}
    group, material = source.stem, ""
    remap: dict[tuple[str, str], dict[int, int]] = {}
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        keyword = words[0]
        if keyword == "v" and len(words) >= 4:
            vertices.append((float(words[1]), float(words[2]), float(words[3])))
        elif keyword in ("o", "g") and len(words) > 1:
            group = " ".join(words[1:])
        elif keyword == "usemtl" and len(words) > 1:
            material = " ".join(words[1:])
        elif keyword == "mtllib" and len(words) > 1:
            materials.update(_read_mtl(source.parent / " ".join(words[1:])))
        elif keyword == "f" and len(words) >= 4:
            key = (group, material)
            part = parts.get(key)
            if part is None:
                part = parts[key] = MeshPart(group, color=materials.get(material))
                remap[key] = {}
            local = remap[key]
            indices = []
            for token in words[1:]:
                number = int(token.split("/")[0])
                vertex = number - 1 if number > 0 else len(vertices) + number
                if vertex not in local:
                    local[vertex] = len(part.positions)
                    part.positions.append(vertices[vertex])
                indices.append(local[vertex])
            part.add_polygon(indices)
    for (_, material_name), part in parts.items():
        if part.color is None and material_name in materials:
            part.color = materials[material_name]
    return list(parts.values())


# -- 3MF ----------------------------------------------------------------------------

_NS_3MF = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02",
           "mat": "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"}


def _matrix_3mf(text: str | None):
    if not text:
        return IDENTITY
    v = [float(x) for x in text.split()]
    if len(v) != 12:
        return IDENTITY
    # 3MF: m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32 (filas; traslación al final).
    return (v[0], v[1], v[2], 0.0, v[3], v[4], v[5], 0.0, v[6], v[7], v[8], 0.0, v[9], v[10], v[11], 1.0)


def read_3mf(path: str) -> list[MeshPart]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".model")]
            main = next((n for n in names if n.lower() == "3d/3dmodel.model"), names[0] if names else None)
            if main is None:
                raise Model3DError("3MF sin modelo", path=path)
            root = ElementTree.fromstring(archive.read(main))
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise Model3DError(str(exc), path=path) from exc
    ns = _NS_3MF
    palettes: dict[str, list[Vector | None]] = {}
    for group in root.iterfind(".//m:basematerials", ns):
        palettes[group.get("id")] = [_hex_color(base.get("displaycolor")) for base in group.iterfind("m:base", ns)]
    for group in root.iterfind(".//mat:colorgroup", ns):
        palettes[group.get("id")] = [_hex_color(color.get("color")) for color in group.iterfind("mat:color", ns)]
    objects = {obj.get("id"): obj for obj in root.iterfind(".//m:resources/m:object", ns)}

    def mesh_parts(obj) -> list[MeshPart]:
        mesh = obj.find("m:mesh", ns)
        if mesh is None:
            return []
        points = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                  for v in mesh.iterfind("m:vertices/m:vertex", ns)]
        default = palettes.get(obj.get("pid"), [])
        default_color = default[int(obj.get("pindex", 0))] if default and int(obj.get("pindex", 0)) < len(default) else None
        by_color: dict[Vector | None, MeshPart] = {}
        remaps: dict[Vector | None, dict[int, int]] = {}
        for tri in mesh.iterfind("m:triangles/m:triangle", ns):
            color = default_color
            palette = palettes.get(tri.get("pid") or obj.get("pid"), [])
            if tri.get("p1") is not None and palette and int(tri.get("p1")) < len(palette):
                color = palette[int(tri.get("p1"))]
            part = by_color.get(color)
            if part is None:
                part = by_color[color] = MeshPart(obj.get("name") or f"Objeto {obj.get('id')}", color=color)
                remaps[color] = {}
            local = remaps[color]
            corners = []
            for key in ("v1", "v2", "v3"):
                vertex = int(tri.get(key))
                if vertex not in local:
                    local[vertex] = len(part.positions)
                    part.positions.append(points[vertex])
                corners.append(local[vertex])
            part.add_polygon(corners)
        return list(by_color.values())

    def expand(object_id: str, matrix, depth: int = 0) -> list[MeshPart]:
        obj = objects.get(object_id)
        if obj is None or depth > 16:
            return []
        result = [_transformed(part, matrix) for part in mesh_parts(obj)]
        for component in obj.iterfind("m:components/m:component", ns):
            result += expand(component.get("objectid"), multiply(matrix, _matrix_3mf(component.get("transform"))),
                             depth + 1)
        return result

    parts: list[MeshPart] = []
    items = list(root.iterfind("m:build/m:item", ns))
    for item in items or [ElementTree.Element("item", objectid=k) for k in objects]:
        parts += expand(item.get("objectid"), _matrix_3mf(item.get("transform")))
    return parts


# -- glTF / GLB ---------------------------------------------------------------------

_COMPONENTS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_Y_UP_TO_Z_UP = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _node_matrix(node: dict):
    if "matrix" in node and len(node["matrix"]) == 16:
        return tuple(float(v) for v in node["matrix"])
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    qx, qy, qz, qw = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    rotation = (
        1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw), 0.0,
        2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw), 0.0,
        2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy), 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    scale = (sx, 0.0, 0.0, 0.0, 0.0, sy, 0.0, 0.0, 0.0, 0.0, sz, 0.0, 0.0, 0.0, 0.0, 1.0)
    translate = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, tx, ty, tz, 1.0)
    return multiply(translate, multiply(rotation, scale))


def read_gltf(path: str) -> list[MeshPart]:
    source = Path(path)
    raw = source.read_bytes()
    binary = b""
    try:
        if raw[:4] == b"glTF":
            length = struct.unpack_from("<I", raw, 8)[0]
            offset, document = 12, None
            while offset + 8 <= min(length, len(raw)):
                chunk_length, chunk_type = struct.unpack_from("<II", raw, offset)
                chunk = raw[offset + 8:offset + 8 + chunk_length]
                if chunk_type == 0x4E4F534A:
                    document = json.loads(chunk)
                elif chunk_type == 0x004E4942:
                    binary = chunk
                offset += 8 + chunk_length
            if document is None:
                raise ValueError("GLB sin JSON")
        else:
            document = json.loads(raw)
    except (ValueError, struct.error) as exc:
        raise Model3DError(str(exc), path=path) from exc

    buffers = []
    for buffer in document.get("buffers", []):
        uri = buffer.get("uri")
        if uri is None:
            buffers.append(binary)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            try:
                buffers.append((source.parent / uri).read_bytes())
            except OSError as exc:
                raise FileOpenError(str(exc), path=str(source.parent / uri)) from exc

    def accessor(index: int) -> list:
        acc = document["accessors"][index]
        view = document["bufferViews"][acc["bufferView"]]
        data = buffers[view["buffer"]]
        fmt, size = _COMPONENTS[acc["componentType"]]
        width = _SIZES[acc["type"]]
        stride = view.get("byteStride") or size * width
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        unpack = struct.Struct("<" + fmt * width).unpack_from
        values = [unpack(data, base + i * stride) for i in range(acc["count"])]
        return values if width > 1 else [v[0] for v in values]

    def material_color(index) -> Vector | None:
        if index is None:
            return None
        pbr = document.get("materials", [])[index].get("pbrMetallicRoughness", {})
        factor = pbr.get("baseColorFactor")
        return tuple(float(c) for c in factor[:3]) if factor else None

    parts: list[MeshPart] = []

    def visit(node_index: int, parent, depth: int = 0) -> None:
        if depth > 64:
            return
        node = document["nodes"][node_index]
        matrix = multiply(parent, _node_matrix(node))
        if "mesh" in node:
            mesh = document["meshes"][node["mesh"]]
            for primitive in mesh.get("primitives", []):
                mode = primitive.get("mode", 4)
                if mode not in (4, 5, 6) or "POSITION" not in primitive.get("attributes", {}):
                    continue
                points = accessor(primitive["attributes"]["POSITION"])
                indices = accessor(primitive["indices"]) if "indices" in primitive else list(range(len(points)))
                part = MeshPart(node.get("name") or mesh.get("name") or f"Pieza {len(parts) + 1}",
                                [transform_point(matrix, p) for p in points], color=material_color(primitive.get("material")))
                if mode == 4:
                    for k in range(0, len(indices) - 2, 3):
                        part.add_polygon([indices[k], indices[k + 1], indices[k + 2]])
                elif mode == 5:
                    for k in range(len(indices) - 2):
                        a, b, c = indices[k], indices[k + 1], indices[k + 2]
                        part.add_polygon([a, b, c] if k % 2 == 0 else [b, a, c])
                else:
                    part.add_polygon(list(indices))
                parts.append(part)
        for child in node.get("children", []):
            visit(child, matrix, depth + 1)

    try:
        scenes = document.get("scenes") or [{"nodes": list(range(len(document.get("nodes", []))))}]
        roots = scenes[document.get("scene", 0)].get("nodes", [])
        for root in roots:
            visit(root, _Y_UP_TO_Z_UP)
    except (KeyError, IndexError, struct.error, TypeError) as exc:
        raise Model3DError(str(exc), path=path) from exc
    return parts


# -- STEP / IGES (gmsh) -------------------------------------------------------------


def read_cad(path: str, progress=None) -> list[MeshPart]:
    """Convierte las superficies de un STEP o IGES en triángulos, una pieza por sólido."""
    try:
        import gmsh
    except Exception as exc:
        raise CadUnavailableError("falta gmsh", path=path) from exc
    if progress:
        progress(0, 4)
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.Verbosity", 0)
        gmsh.option.setNumber("Geometry.OCCImportLabels", 1)
        try:
            gmsh.open(path)
        except Exception as exc:
            raise Model3DError(str(exc), path=path) from exc
        if progress:
            progress(1, 4)
        lo, hi = gmsh.model.getBoundingBox(-1, -1)[:3], gmsh.model.getBoundingBox(-1, -1)[3:]
        diagonal = math.dist(lo, hi) or 1.0
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 24)
        gmsh.option.setNumber("Mesh.MeshSizeMax", diagonal / 30)
        gmsh.option.setNumber("Mesh.MeshSizeMin", diagonal / 4000)
        gmsh.model.mesh.generate(2)
        if progress:
            progress(2, 4)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        point_of = {int(tag): (coords[3 * i], coords[3 * i + 1], coords[3 * i + 2]) for i, tag in enumerate(tags)}

        def color_of(dim: int, tag: int) -> Vector | None:
            r, g, b, a = gmsh.model.getColor(dim, tag)
            return None if a == 0 else (r / 255, g / 255, b / 255)

        def label(dim: int, tag: int, fallback: str) -> str:
            name = gmsh.model.getEntityName(dim, tag) or ""
            return name.split("/")[-1].strip() or fallback

        groups: list[tuple[str, list[int], Vector | None]] = []
        claimed: set[int] = set()
        for _, volume in gmsh.model.getEntities(3):
            faces = [abs(t) for d, t in gmsh.model.getBoundary([(3, volume)], oriented=False) if d == 2]
            claimed.update(faces)
            groups.append((label(3, volume, f"Sólido {volume}"), faces, color_of(3, volume)))
        loose = [t for _, t in gmsh.model.getEntities(2) if t not in claimed]
        if loose:
            groups.append((Path(path).stem, loose, None))

        parts = []
        for name, faces, color in groups:
            part = MeshPart(name, color=color)
            local: dict[int, int] = {}
            for face in faces:
                face_color = color_of(2, face)
                if part.color is None and face_color is not None:
                    part.color = face_color
                types, _, node_lists = gmsh.model.mesh.getElements(2, face)
                for element_type, nodes in zip(types, node_lists):
                    corners = {2: 3, 3: 4}.get(int(element_type))
                    if corners is None:
                        continue
                    for k in range(0, len(nodes), corners):
                        indices = []
                        for node in nodes[k:k + corners]:
                            node = int(node)
                            if node not in local:
                                local[node] = len(part.positions)
                                part.positions.append(point_of[node])
                            indices.append(local[node])
                        part.add_polygon(indices)
            parts.append(part)
        if progress:
            progress(3, 4)
        return parts
    finally:
        gmsh.finalize()


# -- entrada común ------------------------------------------------------------------


def read_parts(path: str, progress=None) -> list[MeshPart]:
    """Piezas de cualquier archivo de malla o de CAD admitido."""
    extension = Path(path).suffix.lower()
    if not Path(path).is_file():
        raise FileOpenError("no existe", path=path)
    readers = {".stl": read_stl, ".obj": read_obj, ".3mf": read_3mf, ".glb": read_gltf, ".gltf": read_gltf}
    try:
        if extension in CAD_EXTENSIONS:
            parts = read_cad(path, progress)
        elif extension in readers:
            parts = readers[extension](path)
        else:
            raise Model3DError("formato no admitido", path=path)
    except (Model3DError, FileOpenError, CadUnavailableError):
        raise
    except OSError as exc:
        raise FileOpenError(str(exc), path=path) from exc
    except (ValueError, IndexError, KeyError, struct.error, zipfile.BadZipFile) as exc:
        raise Model3DError(str(exc), path=path) from exc
    return _clean(parts, path)


def parts_scene(parts: list[MeshPart], fmt: str = "MESH") -> Scene:
    """Escena del visor 3D con las piezas (una por pieza, cada una con su color)."""
    scene = Scene(format=fmt)
    for part in parts:
        scene.materials.append(Material(diffuse=part.color or DEFAULT_COLOR, vertex_colors=False))
        primitive = build_triangles(part.positions, part.triangles, oriented=False)
        primitive.material = len(scene.materials) - 1
        scene.primitives.append(primitive)
        scene.parts.append(Part(part.name, IDENTITY, [len(scene.primitives) - 1]))
    return scene


UNIT_MM_BY_EXTENSION = {".step": 1.0, ".stp": 1.0, ".iges": 1.0, ".igs": 1.0, ".gltf": 1000.0, ".glb": 1000.0,
                        ".3mf": 1.0}
"""Unidades que fija cada formato: OpenCASCADE da milímetros, glTF va en metros y 3MF, salvo que diga otra
cosa, en milímetros. STL y OBJ no lo dicen."""


_3MF_UNITS = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def unit_mm(path: str) -> float | None:
    """Milímetros por unidad del modelo de un archivo de piezas, o None si el formato no lo dice."""
    extension = Path(path).suffix.lower()
    if extension == ".3mf":
        try:
            with zipfile.ZipFile(path) as archive:
                name = next((n for n in archive.namelist() if n.lower().endswith(".model")), None)
                head = archive.read(name)[:4096].decode("utf-8", "replace") if name else ""
        except (OSError, zipfile.BadZipFile):
            return 1.0
        found = re.search(r"<model\b[^>]*\bunit=\"(\w+)\"", head)
        return _3MF_UNITS.get(found.group(1).lower(), 1.0) if found else 1.0
    return UNIT_MM_BY_EXTENSION.get(extension)


def load_mesh_file(path: str, progress=None) -> Scene:
    scene = parts_scene(read_parts(path, progress), Path(path).suffix.lstrip(".").upper())
    scene.unit_mm = unit_mm(path)
    return scene
