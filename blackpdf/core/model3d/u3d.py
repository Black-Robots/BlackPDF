"""Decodificador de archivos U3D (ECMA-363, 4.ª edición).

Lee la jerarquía de nodos, los materiales y las mallas CLOD, incluidas las
actualizaciones progresivas, y devuelve una :class:`Scene` lista para
dibujar. El orden de lectura de la malla progresiva sigue al pie de la letra
la implementación de referencia, porque cualquier desviación desincroniza el
decodificador aritmético.

Animaciones, subdivisión y referencias a archivos externos se ignoran: se
muestra la pose estática del modelo.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from ..progress import ProgressCallback, report
from .bitstream import STATIC_FULL, BitReader, BitStreamError
from .scene import (
    IDENTITY,
    Material,
    Matrix,
    Part,
    Scene,
    View3D,
    build_lines,
    build_points,
    build_triangles,
    is_finite_matrix,
    multiply,
)

# Tipos de bloque.
HEADER = 0x00443355
FILE_REFERENCE = 0xFFFFFF12
MODIFIER_CHAIN = 0xFFFFFF14
PRIORITY_UPDATE = 0xFFFFFF15
NEW_OBJECT_TYPE = 0xFFFFFF16
GROUP_NODE = 0xFFFFFF21
MODEL_NODE = 0xFFFFFF22
LIGHT_NODE = 0xFFFFFF23
VIEW_NODE = 0xFFFFFF24
CLOD_DECLARATION = 0xFFFFFF31
POINT_SET_DECLARATION = 0xFFFFFF36
LINE_SET_DECLARATION = 0xFFFFFF37
CLOD_BASE = 0xFFFFFF3B
CLOD_PROGRESSIVE = 0xFFFFFF3C
POINT_SET_CONTINUATION = 0xFFFFFF3E
LINE_SET_CONTINUATION = 0xFFFFFF3F
SHADING_MODIFIER = 0xFFFFFF45
LIGHT_RESOURCE = 0xFFFFFF51
VIEW_RESOURCE = 0xFFFFFF52
LIT_TEXTURE_SHADER = 0xFFFFFF53
MATERIAL_RESOURCE = 0xFFFFFF54

# Contextos del decodificador aritmético (IFXACContext.h).
CTX_BASE_SHADING = 1
CTX_NUM_NEW_FACES = 1
CTX_ORIENTATION = 2
CTX_THIRD_INDEX_TYPE = 3
CTX_LOCAL_THIRD = 4
CTX_STAY_MOVE = 15
CTX_POSITION_SIGNS = 20
CTX_POSITION_X = 21
CTX_POSITION_Y = 22
CTX_POSITION_Z = 23
CTX_TEX_SPLIT_TYPE = 29
CTX_TEX_DIFF_SIGNS = 32
CTX_TEX_DIFF_U = 33
CTX_TEX_DIFF_V = 34
CTX_TEX_DIFF_S = 35
CTX_TEX_DIFF_T = 36
CTX_TEX_DUP = 39
CTX_NUM_LOCAL_NORMALS = 40
CTX_NORMAL_SIGNS = 41
CTX_NORMAL_X = 42
CTX_NORMAL_Y = 43
CTX_NORMAL_Z = 44
CTX_NORMAL_LOCAL = 45
CTX_COLOR_SPLIT_TYPE = 55
CTX_COLOR_DUP = 56
CTX_COLOR_DIFF_R = 60
CTX_COLOR_DIFF_G = 61
CTX_COLOR_DIFF_B = 62
CTX_COLOR_DIFF_A = 63
CTX_SHADING = 65
CTX_NEW_DIFFUSE = 99
CTX_DIFFUSE_SIGN = 100
CTX_NEW_SPECULAR = 101
CTX_SPECULAR_SIGN = 102
CTX_TEX_SIGN = 103
CTX_DIFFUSE_CHANGE = (104, 105, 106, 107, 108)
CTX_SPECULAR_CHANGE = (109, 110, 111, 112, 113)
CTX_TEX_CHANGE = (114, 115, 116, 117, 118)
CTX_COLOR_LOCAL = 119
CTX_COLOR_GLOBAL = 120
CTX_TEX_LOCAL = 121
CTX_TEX_GLOBAL = 122
CTX_NEW_TEXCOORDS = 123

LEFT, RIGHT = 1, 2
LOCAL, GLOBAL = 1, 2
CHANGE = 1
UPDATE_NEW, UPDATE_LOCAL = 1, 2
MOVE = 1
PREDICT_MOVE, PREDICT_STAY, PREDICT_MOVE2, PREDICT_STAY2 = 1, 2, 3, 4
LEN_EPSILON = 1e-7
SLERP_DELTA = 1e-6
NODE_LIMIT = 100_000


class U3DError(Exception):
    """El archivo no es U3D o está dañado."""


# -- estructuras del archivo ------------------------------------------------------


@dataclass
class _Node:
    kind: int
    parents: list[tuple[str, Matrix]]
    resource: str = ""
    visibility: int = 3
    fov: float | None = None
    ortho_height: float | None = None


@dataclass
class _Shader:
    material: str
    use_vertex_color: bool
    lighting: bool


@dataclass
class _Shading:
    diffuse: bool
    specular: bool
    layers: int


@dataclass
class _ClodMesh:
    """Estado de reconstrucción de una malla CLOD."""

    name: str
    exclude_normals: bool
    shadings: list[_Shading]
    max_positions: int
    quant: tuple[float, float, float, float, float]  # posición, normal, uv, difuso, especular
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    diffuse: list = field(default_factory=list)  # BGRA, como la referencia
    specular: list = field(default_factory=list)
    texcoords: list = field(default_factory=list)
    face_shading: list = field(default_factory=list)
    face_pos: list = field(default_factory=list)
    face_norm: list = field(default_factory=list)
    face_diff: list = field(default_factory=list)
    face_spec: list = field(default_factory=list)
    face_tex: list = field(default_factory=list)  # lista de capas por cara
    adjacency: dict = field(default_factory=dict)  # posición -> set de caras

    def faces_at(self, position: int) -> list[int]:
        return sorted(self.adjacency.get(position, ()), reverse=True)

    def attribute_set(self, attribute: str, position: int, layer: int = 0) -> list[int]:
        """Índices de color o UV que usan las caras en una posición (descendente)."""
        result = set()
        corners = getattr(self, "face_" + attribute)
        for face in self.adjacency.get(position, ()):
            shading = self.shadings[self.face_shading[face]]
            if attribute == "diff" and not shading.diffuse:
                continue
            if attribute == "spec" and not shading.specular:
                continue
            if attribute == "tex" and shading.layers <= layer:
                continue
            corner = _corner_of(self.face_pos[face], position)
            if corner is None:
                continue
            values = corners[face][layer] if attribute == "tex" else corners[face]
            result.add(values[corner])
        return sorted(result, reverse=True)

    def add_face(self, face: int) -> None:
        for position in self.face_pos[face]:
            self.adjacency.setdefault(position, set()).add(face)


@dataclass
class _PointLineSet:
    kind: str
    positions: list = field(default_factory=list)
    diffuse: list = field(default_factory=list)
    items: list = field(default_factory=list)  # (shading, índices de posición, índices de color)
    shadings: list[_Shading] = field(default_factory=list)
    quant: float = 0.0
    color_quant: float = 0.0
    specular_quant: float = 0.0
    has_texcoords: bool = False
    specular: list = field(default_factory=list)
    links: dict = field(default_factory=dict)  # posición -> [(línea, extremo)]


def _corner_of(face, position):
    if face[0] == position:
        return 0
    if face[1] == position:
        return 1
    if face[2] == position:
        return 2
    return None


def _pick(members: list[int], index: int) -> int:
    if index >= len(members):
        raise U3DError("índice local fuera de rango")
    return members[index]


# -- lectura de bloques -----------------------------------------------------------


def _blocks(data: bytes, offset: int, end: int):
    while offset + 12 <= end:
        kind, data_size, meta_size = struct.unpack_from("<III", data, offset)
        start = offset + 12
        stop = start + data_size
        if stop > end:
            raise U3DError("bloque truncado")
        yield kind, data[start:stop]
        offset = start + ((data_size + 3) & ~3) + ((meta_size + 3) & ~3)


def _matrix(reader: BitReader) -> Matrix:
    return tuple(reader.f32s(16))


def _parents(reader: BitReader) -> list[tuple[str, Matrix]]:
    count = reader.u32()
    if count > NODE_LIMIT:
        raise U3DError("demasiados padres")
    return [(reader.string(), _matrix(reader)) for _ in range(count)]


class U3DFile:
    def __init__(self, data: bytes, progress: ProgressCallback | None = None):
        if len(data) < 12 or struct.unpack_from("<I", data)[0] != HEADER:
            raise U3DError("no es un archivo U3D")
        self.data = data
        self.progress = progress
        self.no_compression = False
        self.units = 1.0
        self.nodes: dict[str, _Node] = {}
        self.node_order: list[str] = []
        self.meshes: dict[str, _ClodMesh] = {}
        self.sets: dict[str, _PointLineSet] = {}
        self.shaders: dict[str, _Shader] = {}
        self.materials: dict[str, Material] = {}
        self.node_shading: dict[str, list[list[str]]] = {}
        self.resource_shading: dict[str, list[list[str]]] = {}
        self.warnings: list[str] = []
        self._parse()

    # -- recorrido ---------------------------------------------------------------

    def _reader(self, payload: bytes) -> BitReader:
        return BitReader(payload, self.no_compression)

    def _parse(self) -> None:
        total = len(self.data)
        for kind, payload in _blocks(self.data, 0, total):
            self._block(kind, payload, chain=None)
            if self.progress is not None:
                report(self.progress, 0, 1)

    def _block(self, kind: int, payload: bytes, chain: tuple[str, int] | None) -> None:
        try:
            handler = _HANDLERS.get(kind)
            if handler is not None:
                handler(self, payload, chain)
        except BitStreamError as error:
            raise U3DError(f"datos dañados ({error})") from error
        except (IndexError, ValueError, ZeroDivisionError, OverflowError, struct.error) as error:
            raise U3DError(f"datos no válidos ({error})") from error

    def _header(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        reader.i16()
        reader.i16()
        profile = reader.u32()
        reader.u32()
        reader.u64()
        reader.u32()
        self.no_compression = bool(profile & 0x4)
        if profile & 0x8:
            units = reader.f64()
            if math.isfinite(units) and units > 0:
                self.units = units

    def _modifier_chain(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        chain_type = reader.u32()
        attributes = reader.u32()
        if attributes & 1:
            reader.f32s(4)
        if attributes & 2:
            reader.f32s(6)
        offset = (reader.byte_position + 3) & ~3
        if offset + 4 > len(payload):
            return
        count = struct.unpack_from("<I", payload, offset)[0]
        seen = 0
        for kind, inner in _blocks(payload, offset + 4, len(payload)):
            self._block(kind, inner, chain=(name, chain_type))
            seen += 1
            if seen >= count:
                break

    # -- nodos ---------------------------------------------------------------------

    def _add_node(self, name: str, node: _Node) -> None:
        if name not in self.nodes:
            self.node_order.append(name)
        self.nodes[name] = node

    def _group_node(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        self._add_node(name, _Node(GROUP_NODE, _parents(reader)))

    def _model_node(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        parents = _parents(reader)
        resource = reader.string()
        visibility = reader.u32()
        self._add_node(name, _Node(MODEL_NODE, parents, resource, visibility))

    def _light_node(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        self._add_node(name, _Node(LIGHT_NODE, _parents(reader), reader.string()))

    def _view_node(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        parents = _parents(reader)
        resource = reader.string()
        attributes = reader.u32()
        reader.f32()  # plano cercano
        reader.f32()  # plano lejano
        node = _Node(VIEW_NODE, parents, resource)
        mode = attributes & 0x6
        if mode == 0:
            node.fov = reader.f32()
        elif mode == 0x2:
            node.ortho_height = reader.f32()
        self._add_node(name, node)

    # -- recursos ------------------------------------------------------------------

    def _shading_modifier(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        reader.u32()  # índice en la cadena
        reader.u32()  # atributos
        lists = []
        for _ in range(min(reader.u32(), NODE_LIMIT)):
            lists.append([reader.string() for _ in range(min(reader.u32(), 64))])
        if chain is not None and chain[1] == 1:
            self.resource_shading[name] = lists
        else:
            self.node_shading[name] = lists

    def _lit_texture_shader(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        attributes = reader.u32()
        reader.f32()  # referencia de alfa
        reader.u32()  # función de alfa
        reader.u32()  # mezcla de color
        reader.u32()  # pasadas
        reader.u32()  # canales
        reader.u32()  # canales con alfa
        material = reader.string()
        self.shaders[name] = _Shader(material, bool(attributes & 0x4), bool(attributes & 0x1))

    def _material(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        reader.u32()
        ambient = tuple(reader.f32s(3))
        diffuse = tuple(reader.f32s(3))
        specular = tuple(reader.f32s(3))
        emissive = tuple(reader.f32s(3))
        reflectivity = reader.f32()
        opacity = reader.f32()
        self.materials[name] = Material(
            diffuse=_clamp_color(diffuse),
            ambient=_clamp_color(ambient),
            specular=_clamp_color(specular),
            emissive=_clamp_color(emissive),
            shininess=_clamp(reflectivity),
            opacity=_clamp(opacity) if math.isfinite(opacity) else 1.0,
        )

    # -- malla CLOD ------------------------------------------------------------

    def _clod_declaration(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        reader.u32()  # índice en la cadena
        exclude_normals = bool(reader.u32() & 1)
        for _ in range(6):  # caras, posiciones, normales, difusos, especulares, uv
            reader.u32()
        shadings = []
        for _ in range(min(reader.u32(), 4096)):
            attributes = reader.u32()
            layers = reader.u32()
            if layers > 8:
                raise U3DError("demasiadas capas de textura")
            for _ in range(layers):
                reader.u32()
            reader.u32()  # identificador original
            shadings.append(_Shading(bool(attributes & 1), bool(attributes & 2), layers))
        reader.u32()  # resolución mínima
        max_positions = reader.u32()
        for _ in range(3):
            reader.u32()
        quant = tuple(reader.f32s(5))
        self.meshes[name] = _ClodMesh(name, exclude_normals, shadings or [_Shading(False, False, 0)], max_positions, quant)

    def _clod_base(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        mesh = self.meshes.get(name)
        if mesh is None:
            return
        reader.u32()
        faces, positions, normals, diffuse, specular, texcoords = (reader.u32() for _ in range(6))
        if positions > 50_000_000 or faces > 50_000_000:
            raise U3DError("malla demasiado grande")
        mesh.positions = [tuple(reader.f32s(3)) for _ in range(positions)]
        mesh.normals = [tuple(reader.f32s(3)) for _ in range(normals)]
        mesh.diffuse = [_bgra(reader.f32s(4)) for _ in range(diffuse)]
        mesh.specular = [_bgra(reader.f32s(4)) for _ in range(specular)]
        mesh.texcoords = [tuple(reader.f32s(4)) for _ in range(texcoords)]
        shadings = mesh.shadings
        exclude = mesh.exclude_normals
        cu32 = reader.cu32
        p_ctx = STATIC_FULL + positions
        n_ctx = STATIC_FULL + normals
        d_ctx = STATIC_FULL + diffuse
        s_ctx = STATIC_FULL + specular
        t_ctx = STATIC_FULL + texcoords
        for index in range(faces):
            shading_id = cu32(CTX_BASE_SHADING)
            if shading_id >= len(shadings):
                raise U3DError("sombreado fuera de rango")
            shading = shadings[shading_id]
            pos = [0, 0, 0]
            nor = [0, 0, 0]
            dif = [0, 0, 0]
            spe = [0, 0, 0]
            tex = [[0, 0, 0] for _ in range(shading.layers)]
            for corner in range(3):
                pos[corner] = cu32(p_ctx)
                if not exclude:
                    nor[corner] = cu32(n_ctx)
                if shading.diffuse:
                    dif[corner] = cu32(d_ctx)
                if shading.specular:
                    spe[corner] = cu32(s_ctx)
                for layer in tex:
                    layer[corner] = cu32(t_ctx)
            mesh.face_shading.append(shading_id)
            mesh.face_pos.append(pos)
            mesh.face_norm.append(nor)
            mesh.face_diff.append(dif)
            mesh.face_spec.append(spe)
            mesh.face_tex.append(tex)
            mesh.add_face(index)
            if self.progress is not None and index % 4096 == 0:
                report(self.progress, 0, 1)

    def _clod_progressive(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        mesh = self.meshes.get(name)
        if mesh is None:
            return
        reader.u32()
        start = reader.u32()
        end = reader.u32()
        if start != len(mesh.positions):
            self.warnings.append("progressive-order")
            return
        if end > max(mesh.max_positions, start) or end - start > 50_000_000:
            raise U3DError("resolución fuera de rango")
        prev = {"diff": [0, 0, 0], "spec": [0, 0, 0], "tex": [0, 0, 0]}
        for resolution in range(start, end):
            _resolution_update(reader, mesh, resolution, prev)
            if self.progress is not None and resolution % 512 == 0:
                report(self.progress, 0, 1)

    # -- puntos y líneas -----------------------------------------------------------

    def _set_declaration(self, payload: bytes, chain, kind: str) -> None:
        reader = self._reader(payload)
        name = reader.string()
        reader.u32()  # índice en la cadena
        reader.u32()  # reservado
        counts = [reader.u32() for _ in range(6)]  # elementos, posiciones, normales, difusos, especulares, uv
        shadings = []
        for _ in range(min(reader.u32(), 4096)):
            attributes = reader.u32()
            layers = reader.u32()
            if layers > 8:
                raise U3DError("demasiadas capas de textura")
            for _ in range(layers):
                reader.u32()
            reader.u32()
            shadings.append(_Shading(bool(attributes & 1), bool(attributes & 2), layers))
        for _ in range(3):
            reader.u32()
        quant = reader.f32s(5)
        self.sets[name] = _PointLineSet(
            kind,
            shadings=shadings or [_Shading(False, False, 0)],
            quant=quant[0],
            color_quant=quant[3],
            specular_quant=quant[4],
            has_texcoords=counts[5] > 0,
        )

    def _point_set_declaration(self, payload: bytes, chain) -> None:
        self._set_declaration(payload, chain, "points")

    def _line_set_declaration(self, payload: bytes, chain) -> None:
        self._set_declaration(payload, chain, "lines")

    def _set_continuation(self, payload: bytes, chain) -> None:
        reader = self._reader(payload)
        name = reader.string()
        item = self.sets.get(name)
        if item is None:
            return
        reader.u32()
        start = reader.u32()
        end = reader.u32()
        if start != len(item.positions) or end - start > 50_000_000:
            self.warnings.append("progressive-order")
            return
        _decode_point_line_set(reader, item, start, end)

    # -- montaje de la escena --------------------------------------------------

    def world_matrices(self, name: str, depth: int = 0) -> list[Matrix]:
        if name == "":
            return [IDENTITY]
        node = self.nodes.get(name)
        if node is None or depth > 64:
            return []
        result = []
        for parent, matrix in node.parents:
            if not is_finite_matrix(matrix):
                continue
            for world in self.world_matrices(parent, depth + 1):
                result.append(multiply(world, matrix))
                if len(result) > 4096:
                    return result
        if not node.parents:
            result.append(IDENTITY)
        return result

    def scene(self) -> Scene:
        scene = Scene(format="U3D", warnings=list(self.warnings))
        # El factor de escala dice cuántos metros mide una unidad; 1 es el valor por defecto
        # y casi nadie lo cambia, así que solo se fía de él si es otro.
        if self.units != 1.0:
            scene.unit_mm = self.units * 1000.0
        material_index: dict[tuple, int] = {}
        cache: dict[tuple, list[int]] = {}

        def material_for(shader_name: str | None) -> tuple[int, bool]:
            shader = self.shaders.get(shader_name) if shader_name else None
            material = self.materials.get(shader.material) if shader else None
            if material is None:
                material = Material()
            elif max(material.diffuse) < 0.02 and max(material.ambient) > 0.02:
                material = Material(**{**material.__dict__, "diffuse": material.ambient})
            vertex_colors = shader.use_vertex_color if shader else True
            key = (tuple(material.__dict__.values()), vertex_colors)
            index = material_index.get(key)
            if index is None:
                index = material_index[key] = len(scene.materials)
                scene.materials.append(Material(**{**material.__dict__, "vertex_colors": vertex_colors}))
            return index, vertex_colors

        for name in self.node_order:
            node = self.nodes[name]
            if node.kind != MODEL_NODE or node.visibility == 0:
                continue
            shading = self.node_shading.get(name) or self.resource_shading.get(node.resource) or []
            key = (node.resource, tuple(tuple(item) for item in shading))
            primitives = cache.get(key)
            if primitives is None:
                primitives = cache[key] = self._primitives(scene, node.resource, shading, material_for)
            if not primitives:
                continue
            matrices = self.world_matrices(name)
            for number, matrix in enumerate(matrices):
                label = name if len(matrices) == 1 else f"{name} ({number + 1})"
                scene.parts.append(Part(label or "Modelo", matrix, list(primitives)))

        for name in self.node_order:
            node = self.nodes[name]
            if node.kind != VIEW_NODE:
                continue
            matrices = self.world_matrices(name)
            if not matrices:
                continue
            view = View3D(name=name, camera=matrices[0])
            if node.ortho_height:
                view.projection = "orthographic"
                view.ortho_height = abs(node.ortho_height)
            elif node.fov and 1.0 < node.fov < 179.0:
                view.fov = node.fov
            scene.views.append(view)
        return scene

    def _primitives(self, scene: Scene, resource: str, shading_lists, material_for) -> list[int]:
        mesh = self.meshes.get(resource)
        if mesh is not None:
            return self._mesh_primitives(scene, mesh, shading_lists, material_for)
        item = self.sets.get(resource)
        if item is not None:
            return self._set_primitives(scene, item, shading_lists, material_for)
        return []

    def _mesh_primitives(self, scene: Scene, mesh: _ClodMesh, shading_lists, material_for) -> list[int]:
        groups: dict[int, list[int]] = {}
        for face, shading_id in enumerate(mesh.face_shading):
            groups.setdefault(shading_id, []).append(face)
        result = []
        positions = mesh.positions
        count = len(positions)
        for shading_id, faces in sorted(groups.items()):
            shader = shading_lists[shading_id][0] if shading_id < len(shading_lists) and shading_lists[shading_id] else None
            material, vertex_colors = material_for(shader)
            shading = mesh.shadings[shading_id]
            triangles = []
            normals = None if mesh.exclude_normals or not mesh.normals else []
            colors = [] if vertex_colors and shading.diffuse and mesh.diffuse else None
            for face in faces:
                tri = mesh.face_pos[face]
                if tri[0] >= count or tri[1] >= count or tri[2] >= count:
                    continue
                triangles.append(tuple(tri))
                if normals is not None:
                    for index in mesh.face_norm[face]:
                        normals.append(_unit(mesh.normals[index]) if index < len(mesh.normals) else None)
                if colors is not None:
                    for index in mesh.face_diff[face]:
                        colors.append(_rgba(mesh.diffuse[index]) if index < len(mesh.diffuse) else (1.0, 1.0, 1.0, 1.0))
            if not triangles:
                continue
            primitive = build_triangles(positions, triangles, normals, colors)
            primitive.material = material
            result.append(len(scene.primitives))
            scene.primitives.append(primitive)
        return result

    def _set_primitives(self, scene: Scene, item: _PointLineSet, shading_lists, material_for) -> list[int]:
        groups: dict[int, list] = {}
        for shading_id, indices, colors in item.items:
            groups.setdefault(shading_id, []).append((indices, colors))
        result = []
        count = len(item.positions)
        for shading_id, entries in sorted(groups.items()):
            shader = shading_lists[shading_id][0] if shading_id < len(shading_lists) and shading_lists[shading_id] else None
            material, vertex_colors = material_for(shader)
            shading = item.shadings[shading_id] if shading_id < len(item.shadings) else _Shading(False, False, 0)
            use_colors = vertex_colors and shading.diffuse and bool(item.diffuse)
            colors = [] if use_colors else None
            if item.kind == "lines":
                segments = []
                for indices, color_indices in entries:
                    if max(indices) >= count:
                        continue
                    segments.append(indices)
                    if colors is not None:
                        colors.extend(_rgba(item.diffuse[i]) if i < len(item.diffuse) else (1.0, 1.0, 1.0, 1.0) for i in color_indices)
                if not segments:
                    continue
                primitive = build_lines(item.positions, segments, colors)
            else:
                points = []
                for indices, color_indices in entries:
                    if indices[0] >= count:
                        continue
                    points.append(item.positions[indices[0]])
                    if colors is not None:
                        i = color_indices[0]
                        colors.append(_rgba(item.diffuse[i]) if i < len(item.diffuse) else (1.0, 1.0, 1.0, 1.0))
                if not points:
                    continue
                primitive = build_points(points, colors)
            primitive.material = material
            result.append(len(scene.primitives))
            scene.primitives.append(primitive)
        return result


_HANDLERS = {
    HEADER: U3DFile._header,
    MODIFIER_CHAIN: U3DFile._modifier_chain,
    GROUP_NODE: U3DFile._group_node,
    MODEL_NODE: U3DFile._model_node,
    LIGHT_NODE: U3DFile._light_node,
    VIEW_NODE: U3DFile._view_node,
    SHADING_MODIFIER: U3DFile._shading_modifier,
    LIT_TEXTURE_SHADER: U3DFile._lit_texture_shader,
    MATERIAL_RESOURCE: U3DFile._material,
    CLOD_DECLARATION: U3DFile._clod_declaration,
    CLOD_BASE: U3DFile._clod_base,
    CLOD_PROGRESSIVE: U3DFile._clod_progressive,
    POINT_SET_DECLARATION: U3DFile._point_set_declaration,
    LINE_SET_DECLARATION: U3DFile._line_set_declaration,
    POINT_SET_CONTINUATION: U3DFile._set_continuation,
    LINE_SET_CONTINUATION: U3DFile._set_continuation,
}


# -- utilidades numéricas ------------------------------------------------------


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def _clamp_color(color) -> tuple[float, float, float]:
    return tuple(_clamp(c) for c in color)


def _bgra(values) -> list[float]:
    """Color leído (R, G, B, A) guardado como la referencia: B, G, R, A."""
    r, g, b, a = values
    return [b, g, r, a]


def _rgba(bgra) -> tuple[float, float, float, float]:
    b, g, r, a = bgra
    return (_clamp(r), _clamp(g), _clamp(b), _clamp(a))


def _unit(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if not math.isfinite(length) or length < 1e-12:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _face_normal(mesh: _ClodMesh, face: int):
    a, b, c = (mesh.positions[i] for i in mesh.face_pos[face])
    d1 = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    d2 = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    if max(abs(d1[0]), abs(d1[1]), abs(d1[2])) < LEN_EPSILON or max(abs(d2[0]), abs(d2[1]), abs(d2[2])) < LEN_EPSILON:
        return None
    d1 = _unit(d1)
    d2 = _unit(d2)
    if d1 is None or d2 is None:
        return None
    n = (d1[1] * d2[2] - d1[2] * d2[1], d1[2] * d2[0] - d1[0] * d2[2], d1[0] * d2[1] - d1[1] * d2[0])
    if max(abs(n[0]), abs(n[1]), abs(n[2])) < LEN_EPSILON:
        return None
    return _unit(n)


def _slerp(t: float, a, b):
    """Interpola dos normales como cuaterniones puros (IFXQuaternion)."""
    cos = _dot(a, b)
    if cos < 0.0:
        cos = -cos
        b = (-b[0], -b[1], -b[2])
    if 1.0 - cos > SLERP_DELTA:
        omega = math.acos(min(1.0, cos))
        sin = math.sin(omega)
        s0 = math.sin((1.0 - t) * omega) / sin
        s1 = math.sin(t * omega) / sin
    else:
        s0 = 1.0 - t
        s1 = t
    return (s0 * a[0] + s1 * b[0], s0 * a[1] + s1 * b[1], s0 * a[2] + s1 * b[2])


def _signed(signs: int, bit: int, value: float) -> float:
    return -value if signs & bit else value


# -- actualización progresiva -----------------------------------------------------


def _read_new_colors(reader, mesh, attribute, split, count, quant, sign_ctx, mag_ctx):
    """Colores o coordenadas nuevos, predichos desde la posición dividida."""
    store = {"diff": mesh.diffuse, "spec": mesh.specular, "tex": mesh.texcoords}[attribute]
    members = mesh.attribute_set(attribute, split)
    acc = [0.0, 0.0, 0.0, 0.0]
    for index in members:
        value = store[index] if index < len(store) else (0.0, 0.0, 0.0, 0.0)
        for k in range(4):
            acc[k] += value[k]
    size = len(members)
    predicted = [v / size for v in acc] if size else [0.0, 0.0, 0.0, 0.0]
    for _ in range(count):
        signs = reader.cu8(sign_ctx)
        deltas = [reader.cu32(ctx) for ctx in mag_ctx]
        store.append([
            predicted[k] + _signed(signs, 1 << k, quant * deltas[k]) for k in range(4)
        ])


def _change(reader, mesh, attribute, contexts, base_count, split, layer=0):
    """Lee si una cara que se mueve cambia de color o UV; devuelve el índice nuevo."""
    keep_ctx, type_ctx, new_ctx, local_ctx, global_ctx = contexts
    if reader.cu8(keep_ctx) != CHANGE:
        return None
    kind = reader.cu8(type_ctx)
    if kind == UPDATE_NEW:
        return reader.cu32(new_ctx) + base_count
    if kind == UPDATE_LOCAL:
        index = reader.cu32(local_ctx)
        return _pick(mesh.attribute_set(attribute, split, layer), index)
    return reader.cu32(global_ctx)


def _corner_indices(reader, split_set, third_set, prev, dup_ctx, type_ctx, local_ctx, global_ctx, specular_bug=False):
    """Índices de color o UV en las tres esquinas de una cara nueva."""
    flags = reader.cu8(dup_ctx)
    values = []
    for bit, members, previous in ((1, split_set, prev[0]), (2, split_set, prev[1]), (4, third_set, prev[2])):
        if flags & bit:
            values.append(previous)
            continue
        kind = reader.cu8(type_ctx)
        if kind == UPDATE_LOCAL:
            if specular_bug and bit == 2:
                # La referencia toma el primer miembro antes de leer el índice.
                value = _pick(members, 0)
                reader.cu32(local_ctx)
            else:
                value = _pick(members, reader.cu32(local_ctx))
        else:
            value = reader.cu32(global_ctx)
        values.append(value)
    return values


def _resolution_update(reader: BitReader, mesh: _ClodMesh, current: int, prev: dict) -> None:
    split = reader.cu32(STATIC_FULL + current)
    if split >= max(current, 1):
        raise U3DError("posición fuera de rango")
    split_faces = mesh.faces_at(split)
    position_set = set()
    for face in split_faces:
        position_set.update(mesh.face_pos[face])
    position_set.discard(split)

    base_diffuse = len(mesh.diffuse)
    base_specular = len(mesh.specular)
    base_texcoords = len(mesh.texcoords)
    q_pos, q_normal, q_tex, q_diffuse, q_specular = mesh.quant

    count = reader.cu16(CTX_NEW_DIFFUSE)
    if count:
        _read_new_colors(reader, mesh, "diff", split, count, q_diffuse, CTX_DIFFUSE_SIGN,
                         (CTX_COLOR_DIFF_R, CTX_COLOR_DIFF_G, CTX_COLOR_DIFF_B, CTX_COLOR_DIFF_A))
    count = reader.cu16(CTX_NEW_SPECULAR)
    if count:
        _read_new_colors(reader, mesh, "spec", split, count, q_specular, CTX_SPECULAR_SIGN,
                         (CTX_COLOR_DIFF_R, CTX_COLOR_DIFF_G, CTX_COLOR_DIFF_B, CTX_COLOR_DIFF_A))
    count = reader.cu16(CTX_NEW_TEXCOORDS)
    if count:
        _read_new_colors(reader, mesh, "tex", split, count, q_tex, CTX_TEX_SIGN,
                         (CTX_TEX_DIFF_U, CTX_TEX_DIFF_V, CTX_TEX_DIFF_S, CTX_TEX_DIFF_T))

    # Caras nuevas.
    new_count = reader.cu32(CTX_NUM_NEW_FACES)
    if new_count > 100_000:
        raise U3DError("demasiadas caras nuevas")
    first_new = len(mesh.face_pos)
    left_third = set()
    right_third = set()
    shadings = mesh.shadings
    for _ in range(new_count):
        shading_id = reader.cu32(CTX_SHADING)
        if shading_id >= len(shadings):
            raise U3DError("sombreado fuera de rango")
        orientation = reader.cu8(CTX_ORIENTATION)
        if reader.cu8(CTX_THIRD_INDEX_TYPE) == LOCAL:
            index = reader.cu32(CTX_LOCAL_THIRD)
            third = _pick(sorted(position_set, reverse=True), index)
        else:
            third = reader.cu32(STATIC_FULL + current)
            if third >= max(current, 1):
                raise U3DError("posición fuera de rango")
            position_set.add(third)
        if orientation == LEFT:
            left_third.add(third)
            face = [split, current, third]
        elif orientation == RIGHT:
            right_third.add(third)
            face = [current, split, third]
        else:
            raise U3DError("orientación no válida")
        layers = shadings[shading_id].layers
        mesh.face_shading.append(shading_id)
        mesh.face_pos.append(face)
        mesh.face_norm.append([0, 0, 0])
        mesh.face_diff.append([0, 0, 0])
        mesh.face_spec.append([0, 0, 0])
        mesh.face_tex.append([[0, 0, 0] for _ in range(layers)])

    # Caras que se quedan o se mueven a la posición nueva.
    move_faces = []
    move_positions = set()
    stay_positions = set()
    for face in split_faces:
        a, b, c = mesh.face_pos[face]
        if split == a:
            x, y = b, c
        elif split == b:
            x, y = c, a
        elif split == c:
            x, y = a, b
        else:
            x = y = None
        prediction = 0
        if x is not None:
            if x in right_third:
                prediction = PREDICT_MOVE
            elif y in right_third:
                prediction = PREDICT_STAY
            elif x in left_third:
                prediction = PREDICT_STAY
            elif y in left_third:
                prediction = PREDICT_MOVE
        if not prediction:
            if a in move_positions or b in move_positions or c in move_positions:
                prediction = PREDICT_MOVE2
            elif a in stay_positions or b in stay_positions or c in stay_positions:
                prediction = PREDICT_STAY2
        if reader.cu8(CTX_STAY_MOVE + prediction) == MOVE:
            move_faces.append(face)
            move_positions.update((a, b, c))
            move_positions.discard(split)
        else:
            stay_positions.update((a, b, c))
            stay_positions.discard(split)

    updates = []
    for face in sorted(move_faces, reverse=True):
        corner = _corner_of(mesh.face_pos[face], split)
        updates.append((mesh.face_pos[face], corner, current))
        shading = shadings[mesh.face_shading[face]]
        if shading.diffuse:
            value = _change(reader, mesh, "diff", CTX_DIFFUSE_CHANGE, base_diffuse, split)
            if value is not None:
                updates.append((mesh.face_diff[face], corner, value))
        if shading.specular:
            value = _change(reader, mesh, "spec", CTX_SPECULAR_CHANGE, base_specular, split)
            if value is not None:
                updates.append((mesh.face_spec[face], corner, value))
        for layer in range(shading.layers):
            value = _change(reader, mesh, "tex", CTX_TEX_CHANGE, base_texcoords, split, layer)
            if value is not None:
                updates.append((mesh.face_tex[face][layer], corner, value))

    # Colores y UV de las caras nuevas.
    for face in range(first_new, first_new + new_count):
        pos = mesh.face_pos[face]
        if pos[0] == current:
            new_corner, split_corner, third_corner = (0, 1, 2) if pos[1] == split else (0, 2, 1)
        elif pos[1] == current:
            new_corner, split_corner, third_corner = (1, 0, 2) if pos[0] == split else (1, 2, 0)
        else:
            new_corner, split_corner, third_corner = (2, 1, 0) if pos[1] == split else (2, 0, 1)
        third = pos[third_corner]
        shading = shadings[mesh.face_shading[face]]
        if shading.diffuse:
            values = _corner_indices(
                reader, mesh.attribute_set("diff", split), mesh.attribute_set("diff", third), prev["diff"],
                CTX_COLOR_DUP, CTX_COLOR_SPLIT_TYPE, CTX_COLOR_LOCAL, CTX_COLOR_GLOBAL,
            )
            _assign(mesh.face_diff[face], split_corner, new_corner, third_corner, values)
            prev["diff"] = values
        if shading.specular:
            values = _corner_indices(
                reader, mesh.attribute_set("spec", split), mesh.attribute_set("spec", third), prev["spec"],
                CTX_COLOR_DUP, CTX_COLOR_SPLIT_TYPE, CTX_COLOR_LOCAL, CTX_COLOR_GLOBAL, specular_bug=True,
            )
            _assign(mesh.face_spec[face], split_corner, new_corner, third_corner, values)
            prev["spec"] = values
        for layer in range(shading.layers):
            values = _corner_indices(
                reader, mesh.attribute_set("tex", split, layer), mesh.attribute_set("tex", third, layer), prev["tex"],
                CTX_TEX_DUP, CTX_TEX_SPLIT_TYPE, CTX_TEX_LOCAL, CTX_TEX_GLOBAL,
            )
            _assign(mesh.face_tex[face][layer], split_corner, new_corner, third_corner, values)
            prev["tex"] = values

    # Adyacencia: las caras movidas pasan a la posición nueva y se añaden las nuevas.
    adjacency = mesh.adjacency
    for face in move_faces:
        faces = adjacency.get(split)
        if faces is not None:
            faces.discard(face)
        adjacency.setdefault(current, set()).add(face)
    for face in range(first_new, first_new + new_count):
        mesh.add_face(face)

    # Posiciones que tocan la nueva (antes de aplicar los cambios de esquina).
    new_position_set = set()
    for face in adjacency.get(current, ()):
        new_position_set.update(mesh.face_pos[face])

    predicted = mesh.positions[split] if current > 0 else (0.0, 0.0, 0.0)
    signs = reader.cu8(CTX_POSITION_SIGNS)
    dx = reader.cu32(CTX_POSITION_X)
    dy = reader.cu32(CTX_POSITION_Y)
    dz = reader.cu32(CTX_POSITION_Z)
    mesh.positions.append((
        predicted[0] + _signed(signs, 1, q_pos * dx),
        predicted[1] + _signed(signs, 2, q_pos * dy),
        predicted[2] + _signed(signs, 4, q_pos * dz),
    ))

    for target, corner, value in updates:
        if corner is not None:
            target[corner] = value

    if not mesh.exclude_normals:
        _read_normals(reader, mesh, sorted(new_position_set, reverse=True), q_normal)


def _assign(target, split_corner, new_corner, third_corner, values) -> None:
    target[split_corner] = values[0]
    target[new_corner] = values[1]
    target[third_corner] = values[2]


def _read_normals(reader: BitReader, mesh: _ClodMesh, positions: list[int], quant: float) -> None:
    base = len(mesh.normals)
    added: list = []
    for position in positions:
        faces = mesh.faces_at(position)
        local_count = reader.cu32(CTX_NUM_LOCAL_NORMALS)
        if local_count > 4096:
            raise U3DError("demasiadas normales")
        face_normals = []
        for face in faces:
            normal = _face_normal(mesh, face)
            if normal is not None:
                face_normals.append(normal)
        predicted = []
        if face_normals:
            predicted.append(face_normals[0])
        while len(predicted) < local_count:
            if not face_normals:
                predicted.append((0.0, 0.0, 0.0))
                continue
            farthest_distance = 1.0
            farthest = 0
            for k, normal in enumerate(face_normals):
                minimum = -2.0
                for other in predicted:
                    distance = _dot(normal, other)
                    if distance > minimum:
                        minimum = distance
                if minimum < farthest_distance:
                    farthest_distance = minimum
                    farthest = k
            predicted.append(face_normals[farthest])
        contributions = [0] * len(predicted)
        for normal in face_normals:
            best = -2.0
            closest = 0
            for k, other in enumerate(predicted):
                distance = _dot(normal, other)
                if distance > best:
                    best = distance
                    closest = k
            predicted[closest] = _slerp(1.0 / (contributions[closest] + 1), predicted[closest], normal)
            contributions[closest] += 1

        local = []
        for k in range(local_count):
            signs = reader.cu8(CTX_NORMAL_SIGNS)
            x = _signed(signs, 2, quant * reader.cu32(CTX_NORMAL_X))
            y = _signed(signs, 4, quant * reader.cu32(CTX_NORMAL_Y))
            z = _signed(signs, 8, quant * reader.cu32(CTX_NORMAL_Z))
            square = min(1.0, x * x + y * y + z * z)
            w = _signed(signs, 1, math.sqrt(1.0 - square))
            px, py, pz = predicted[k]
            # Producto (0, p) * (w, x, y, z), parte vectorial.
            local.append((px * w + py * z - pz * y, py * w + pz * x - px * z, pz * w + px * y - py * x))

        for face in faces:
            index = reader.cu32(CTX_NORMAL_LOCAL)
            corner = _corner_of(mesh.face_pos[face], position)
            if corner is not None:
                mesh.face_norm[face][corner] = base + len(added) + index
        added.extend(local)
    mesh.normals.extend(added)


# -- puntos y líneas progresivos ---------------------------------------------------


def _decode_point_line_set(reader: BitReader, item: _PointLineSet, start: int, end: int) -> None:
    """Lee las actualizaciones de un conjunto de puntos o líneas (CIFXPoint/LineSetDecoder)."""
    is_lines = item.kind == "lines"
    for current in range(start, end):
        split = reader.cu32(STATIC_FULL + (current if current > 1 else 1))
        if current and split >= current:
            raise U3DError("posición fuera de rango")
        predicted = item.positions[split] if current else (0.0, 0.0, 0.0)
        signs = reader.cu8(CTX_POSITION_SIGNS)
        dx = reader.cu32(CTX_POSITION_X)
        dy = reader.cu32(CTX_POSITION_Y)
        dz = reader.cu32(CTX_POSITION_Z)
        item.positions.append((
            predicted[0] + _signed(signs, 1, item.quant * dx),
            predicted[1] + _signed(signs, 2, item.quant * dy),
            predicted[2] + _signed(signs, 4, item.quant * dz),
        ))
        for _ in range(min(reader.cu32(CTX_NUM_LOCAL_NORMALS), 4096)):
            reader.cu8(CTX_NORMAL_SIGNS)
            reader.cu32(CTX_NORMAL_X)
            reader.cu32(CTX_NORMAL_Y)
            reader.cu32(CTX_NORMAL_Z)
        count = reader.cu32(CTX_NUM_NEW_FACES)
        if count > 100_000:
            raise U3DError("demasiados elementos nuevos")
        for _ in range(count):
            shading_id = reader.cu32(CTX_BASE_SHADING)
            shading = item.shadings[shading_id] if shading_id < len(item.shadings) else _Shading(False, False, 0)
            if is_lines:
                first = reader.cu32(STATIC_FULL + current)
                indices = (first, current)
            else:
                indices = (current,)
            colors = []
            for _corner in indices:
                reader.cu32(CTX_NORMAL_LOCAL)
                if shading.diffuse:
                    colors.append(_set_color(reader, item, split, current, item.diffuse))
                if shading.specular:
                    _set_color(reader, item, split, current, item.specular)
                if item.has_texcoords:
                    for _layer in range(shading.layers):
                        if reader.cu8(CTX_TEX_DUP) == 0:
                            reader.cu8(CTX_TEX_DIFF_SIGNS)
                            for ctx in (CTX_TEX_DIFF_U, CTX_TEX_DIFF_V, CTX_TEX_DIFF_S, CTX_TEX_DIFF_T):
                                reader.cu32(ctx)
            item.items.append((shading_id, indices, colors or [0] * len(indices)))
            if is_lines:
                item.links.setdefault(indices[0], []).append((len(item.items) - 1, 0))
                item.links.setdefault(indices[1], []).append((len(item.items) - 1, 1))


def _set_color(reader: BitReader, item: _PointLineSet, split: int, current: int, store: list) -> int:
    """Color de un extremo; los nuevos se predicen como la referencia."""
    if reader.cu8(CTX_COLOR_DUP) != 0:
        return max(len(store) - 1, 0)
    signs = reader.cu8(CTX_NORMAL_SIGNS)
    deltas = [reader.cu32(ctx) for ctx in (CTX_COLOR_DIFF_R, CTX_COLOR_DIFF_G, CTX_COLOR_DIFF_B, CTX_COLOR_DIFF_A)]
    quant = item.color_quant if store is item.diffuse else item.specular_quant
    predicted = [0.0, 0.0, 0.0, 0.0]
    if current and store and store is item.diffuse:
        if item.kind == "lines":
            used = 0
            for number, end in item.links.get(split, ()):
                _shading, indices, colors = item.items[number]
                if indices[1 - end] < current and colors[end] < len(store):
                    color = store[colors[end]]
                    predicted = [predicted[k] + color[k] for k in range(4)]
                    used += 1
            predicted = [value * used for value in predicted]
        elif split < len(item.items):
            colors = item.items[split][2]
            if colors and colors[0] < len(store):
                r, g, b = store[colors[0]][:3]
                length = math.sqrt(r * r + g * g + b * b)
                if length > 0:
                    predicted = [r / length, g / length, b / length, store[colors[0]][3]]
    store.append([predicted[k] + _signed(signs, 1 << k, quant * deltas[k]) for k in range(4)])
    return len(store) - 1


def decode(data: bytes, progress: ProgressCallback | None = None) -> Scene:
    """Decodifica un archivo U3D completo."""
    return U3DFile(data, progress).scene()
