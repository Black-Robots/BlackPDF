"""Visor 3D interactivo con OpenGL.

Dibuja una :class:`Scene` con iluminación de estudio, varios modos de
presentación, plano de sección y selección de piezas. La cámara gira
alrededor de un centro (como una plataforma giratoria), se desplaza y se
acerca hacia el cursor.

Se usa GLSL 1.20 para funcionar también con controladores antiguos y con
el OpenGL por software.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QOffscreenSurface,
    QOpenGLContext,
    QFont,
    QLinearGradient,
    QMatrix4x4,
    QMouseEvent,
    QPainter,
    QPen,
    QQuaternion,
    QSurfaceFormat,
    QVector3D,
    QVector4D,
    QWheelEvent,
)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..core.model3d import Scene
from . import theme

GL_POINTS = 0x0000
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_DEPTH_BUFFER_BIT = 0x0100
GL_COLOR_BUFFER_BIT = 0x4000
GL_LEQUAL = 0x0203
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_CULL_FACE = 0x0B44
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SCISSOR_TEST = 0x0C11
GL_STENCIL_TEST = 0x0B90
GL_FLOAT = 0x1406
GL_POLYGON_OFFSET_FILL = 0x8037
GL_MULTISAMPLE = 0x809D
GL_PROGRAM_POINT_SIZE = 0x8642

_GL_AVAILABLE: bool | None = None


def opengl_available() -> bool:
    """True si se puede crear un contexto OpenGL 2.0 o superior para dibujar en ventanas."""
    global _GL_AVAILABLE
    if _GL_AVAILABLE is None:
        ok = QGuiApplication.platformName() not in ("offscreen", "minimal", "vnc")
        if ok:
            context = QOpenGLContext()
            ok = context.create()
            if ok:
                surface = QOffscreenSurface()
                surface.create()
                ok = surface.isValid() and context.makeCurrent(surface)
                if ok:
                    ok = context.format().majorVersion() >= 2
                    context.doneCurrent()
        _GL_AVAILABLE = ok
    return _GL_AVAILABLE


RENDER_MODES = ("solid", "outline", "solid-wire", "wireframe", "transparent")
LIGHTING_MODES = ("studio", "headlamp", "flat")
STANDARD_VIEWS = ("front", "back", "left", "right", "top", "bottom", "iso")

ORBIT_SPEED = 0.35  # grados por píxel
WHEEL_STEP = 0.86

VERTEX_SHADER = """
#version 120
attribute vec3 a_position;
attribute vec3 a_normal;
attribute vec4 a_color;
uniform mat4 u_model;
uniform mat4 u_view;
uniform mat4 u_projection;
uniform mat4 u_normal_matrix;
uniform float u_point_size;
varying vec3 v_normal;
varying vec3 v_position;
varying vec4 v_color;
varying vec3 v_world;
void main() {
    vec4 world = u_model * vec4(a_position, 1.0);
    vec4 eye = u_view * world;
    v_world = world.xyz;
    v_position = eye.xyz;
    v_normal = mat3(u_normal_matrix) * a_normal;
    v_color = a_color;
    gl_PointSize = u_point_size;
    gl_Position = u_projection * eye;
}
"""

FRAGMENT_SHADER = """
#version 120
varying vec3 v_normal;
varying vec3 v_position;
varying vec4 v_color;
varying vec3 v_world;
uniform vec3 u_diffuse;
uniform vec3 u_specular;
uniform vec3 u_emissive;
uniform float u_shininess;
uniform float u_opacity;
uniform bool u_vertex_color;
uniform bool u_lighting;
uniform int u_light_mode;
uniform bool u_clip;
uniform vec4 u_clip_plane;
uniform vec3 u_highlight;
uniform float u_highlight_amount;
uniform bool u_depth_out;

vec3 pack_depth(float depth) {
    // Profundidad en 24 bits repartida en RGB: así se lee con la imagen del pase de selección.
    float value = floor(depth * 16777215.0 + 0.5);
    float r = floor(value / 65536.0);
    value -= r * 65536.0;
    float g = floor(value / 256.0);
    return vec3(r, g, value - g * 256.0) / 255.0;
}

vec3 light(vec3 n, vec3 v, vec3 dir, vec3 color, vec3 base, float exponent) {
    vec3 l = normalize(dir);
    float diffuse = max(dot(n, l), 0.0);
    vec3 h = normalize(l + v);
    float specular = diffuse > 0.0 ? pow(max(dot(n, h), 0.0), exponent) : 0.0;
    return color * (base * diffuse + u_specular * specular);
}

void main() {
    if (u_clip && dot(vec4(v_world, 1.0), u_clip_plane) > 0.0) discard;
    if (u_depth_out) {
        gl_FragColor = vec4(pack_depth(gl_FragCoord.z), 1.0);
        return;
    }
    vec3 base = u_vertex_color ? v_color.rgb : u_diffuse;
    float alpha = u_opacity * (u_vertex_color ? v_color.a : 1.0);
    vec3 color = base;
    if (u_lighting && u_light_mode != 2) {
        vec3 n = normalize(v_normal);
        vec3 v = normalize(-v_position);
        if (dot(n, v) < 0.0) n = -n;
        float exponent = 4.0 + 120.0 * clamp(u_shininess, 0.0, 1.0);
        if (u_light_mode == 1) {
            color = 0.22 * base + light(n, v, vec3(0.0, 0.0, 1.0), vec3(0.9), base, exponent);
        } else {
            float sky = 0.5 + 0.5 * n.y;
            color = mix(vec3(0.16), vec3(0.30), sky) * base;
            color += light(n, v, vec3(-0.45, 0.55, 0.70), vec3(0.78), base, exponent);
            color += light(n, v, vec3(0.65, 0.15, 0.55), vec3(0.32), base, exponent);
            color += light(n, v, vec3(0.10, -0.60, -0.40), vec3(0.18), base, exponent);
        }
        color += u_emissive * 0.5;
    }
    color = mix(color, u_highlight, u_highlight_amount);
    gl_FragColor = vec4(color, alpha);
}
"""


class _GpuPrimitive:
    """Buffers de un lote de la escena."""

    def __init__(self, primitive):
        self.kind = primitive.kind
        self.count = primitive.vertex_count
        self.positions = _buffer(primitive.positions)
        self.normals = _buffer(primitive.normals) if primitive.normals else None
        self.colors = _buffer(primitive.colors) if primitive.colors else None
        self.wire = _buffer(primitive.wire) if primitive.wire else None
        self.wire_count = len(primitive.wire) // 12
        self.edges = _buffer(primitive.edges) if primitive.edges else None
        self.edge_count = len(primitive.edges) // 12

    def destroy(self) -> None:
        for buffer in (self.positions, self.normals, self.colors, self.wire, self.edges):
            if buffer is not None:
                buffer.destroy()


def _buffer(data: bytes) -> QOpenGLBuffer:
    buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
    buffer.create()
    buffer.bind()
    buffer.allocate(data, len(data))
    buffer.release()
    return buffer


def _vec(values) -> QVector3D:
    return QVector3D(float(values[0]), float(values[1]), float(values[2]))


def _matrix(values) -> QMatrix4x4:
    """Matriz por columnas (16 valores) a QMatrix4x4."""
    m = [float(v) for v in values]
    return QMatrix4x4(
        m[0], m[4], m[8], m[12],
        m[1], m[5], m[9], m[13],
        m[2], m[6], m[10], m[14],
        m[3], m[7], m[11], m[15],
    )


class Camera:
    """Cámara orbital: centro, distancia y orientación."""

    def __init__(self):
        self.target = QVector3D(0, 0, 0)
        self.distance = 10.0
        self.rotation = QQuaternion()
        self.fov = 30.0
        self.orthographic = False
        self.ortho_height = 10.0

    def copy(self) -> "Camera":
        other = Camera()
        other.target = QVector3D(self.target)
        other.distance = self.distance
        other.rotation = QQuaternion(self.rotation)
        other.fov = self.fov
        other.orthographic = self.orthographic
        other.ortho_height = self.ortho_height
        return other

    def right(self) -> QVector3D:
        return self.rotation.rotatedVector(QVector3D(1, 0, 0))

    def up(self) -> QVector3D:
        return self.rotation.rotatedVector(QVector3D(0, 1, 0))

    def back(self) -> QVector3D:
        return self.rotation.rotatedVector(QVector3D(0, 0, 1))

    def eye(self) -> QVector3D:
        return self.target + self.back() * self.distance

    def view_matrix(self) -> QMatrix4x4:
        m = QMatrix4x4()
        m.lookAt(self.eye(), self.target, self.up())
        return m

    def vertical_fov(self, aspect: float) -> float:
        """El ángulo de vista se aplica a la dimensión menor, como en los PDF."""
        if aspect >= 1.0:
            return self.fov
        half = math.radians(self.fov) / 2
        return math.degrees(2 * math.atan(math.tan(half) / max(aspect, 1e-3)))

    def view_height(self, aspect: float) -> float:
        """Alto visible en el plano del centro, en unidades del mundo."""
        if self.orthographic:
            return self.ortho_height
        return 2 * self.distance * math.tan(math.radians(self.vertical_fov(aspect)) / 2)

    def set_orthographic(self, enabled: bool, aspect: float) -> None:
        if enabled == self.orthographic:
            return
        if enabled:
            self.ortho_height = self.view_height(aspect)
        else:
            half = math.radians(self.vertical_fov(aspect)) / 2
            self.distance = max(self.ortho_height / (2 * math.tan(half)), 1e-6)
        self.orthographic = enabled

    def fit(self, center: QVector3D, radius: float, aspect: float) -> None:
        radius = max(radius, 1e-6)
        vertical = math.radians(self.vertical_fov(aspect))
        horizontal = 2 * math.atan(math.tan(vertical / 2) * aspect)
        half = min(vertical, horizontal) / 2
        self.target = QVector3D(center)
        self.distance = radius / math.sin(min(max(half, 0.01), math.radians(80))) * 1.06
        self.ortho_height = radius * 2.12 / min(aspect, 1.0)

    def orbit(self, dx: float, dy: float, up: QVector3D) -> None:
        yaw = QQuaternion.fromAxisAndAngle(up, -dx * ORBIT_SPEED)
        pitch = QQuaternion.fromAxisAndAngle(self.right(), -dy * ORBIT_SPEED)
        self.rotation = (yaw * pitch * self.rotation).normalized()

    def roll(self, angle: float) -> None:
        self.rotation = (QQuaternion.fromAxisAndAngle(self.back(), angle) * self.rotation).normalized()


class Viewer3D(QOpenGLWidget):
    """Lienzo OpenGL que dibuja una escena y gestiona la cámara."""

    partClicked = Signal(int)  # -1 si se hace clic en el vacío
    glUnavailable = Signal(str)
    cameraChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(QSize(240, 180))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self.scene: Scene | None = None
        self.camera = Camera()
        self.home_camera = Camera()
        self.render_mode = "solid"
        self.lighting = "studio"
        self.background_mode = "theme"  # "theme", "document", "white", "dark"
        self.selected = -1
        self.hidden: set[int] = set()
        self.section_axis: int | None = None
        self.section_offset = 0.5
        self.section_flip = False
        self.up_axis = QVector3D(0, 0, 1)
        self.gl_ok = True
        self.gl_error = ""

        self._program: QOpenGLShaderProgram | None = None
        self._gpu: dict[int, _GpuPrimitive] = {}
        self._world_buffers: dict[int, tuple[bytes, QOpenGLBuffer]] = {}
        """Lo resaltado al medir, ya en la gráfica: se sube una vez y no en cada fotograma."""
        self._uploaded: Scene | None = None
        self._center = QVector3D(0, 0, 0)
        self._radius = 1.0
        self._bounds = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        self._drag_button = None
        self._drag_origin = QPointF()
        self._last = QPointF()
        self._moved = False
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._spin_step)
        self._camera_anim = QTimer(self)
        self._camera_anim.setInterval(16)
        self._camera_anim.timeout.connect(self._camera_step)
        self._anim_from: Camera | None = None
        self._anim_to: Camera | None = None
        self._anim_t = 0.0
        self.measure_tool = None
        """Herramienta de medir activa (ui/measure3d.py): recibe los clics y dibuja encima."""
        self._depth_fbo: QOpenGLFramebufferObject | None = None
        theme.manager().changed.connect(self.update)

    # ======================================================================
    # Escena
    # ======================================================================
    def set_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.selected = -1
        self.hidden = {i for i, part in enumerate(scene.parts) if not part.visible}
        bounds = scene.bounds() or ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
        self._bounds = bounds
        lo, hi = bounds
        self._center = QVector3D((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        self._radius = max(math.dist(lo, hi) / 2, 1e-6)
        self.up_axis = self._guess_up(scene)
        if scene.default_view is not None and scene.default_view < len(scene.views):
            self.apply_view(scene.views[scene.default_view], animate=False)
            if scene.views[scene.default_view].render_mode in RENDER_MODES:
                self.render_mode = scene.views[scene.default_view].render_mode
        else:
            self.standard_view("iso", animate=False)
        self.home_camera = self.camera.copy()
        self.update()

    def _guess_up(self, scene: Scene) -> QVector3D:
        view = None
        if scene.default_view is not None and scene.default_view < len(scene.views):
            view = scene.views[scene.default_view]
        if view is not None and view.camera is not None:
            up = _vec(view.camera[4:7])
            axes = [abs(up.x()), abs(up.y()), abs(up.z())]
            axis = axes.index(max(axes))
            sign = 1.0 if (up.x(), up.y(), up.z())[axis] >= 0 else -1.0
            return QVector3D(*(sign if i == axis else 0.0 for i in range(3)))
        return QVector3D(0, 0, 1) if scene.format == "PRC" else QVector3D(0, 1, 0)

    def aspect(self) -> float:
        return max(self.width(), 1) / max(self.height(), 1)

    def _view_camera(self, view) -> Camera | None:
        if view.camera is None:
            return None
        m = view.camera
        right = _vec(m[0:3]).normalized()
        up = _vec(m[4:7]).normalized()
        back = _vec(m[8:11]).normalized()
        if back.length() < 0.5 or up.length() < 0.5:
            return None
        right = QVector3D.crossProduct(up, back).normalized()
        up = QVector3D.crossProduct(back, right).normalized()
        eye = _vec(m[12:15])
        camera = Camera()
        camera.rotation = QQuaternion.fromAxes(right, up, back).normalized()
        camera.fov = view.fov
        forward = -back
        if view.orbit:
            distance = view.orbit
        else:
            distance = QVector3D.dotProduct(self._center - eye, forward)
            if distance <= self._radius * 0.05:
                distance = self._radius * 2.5
        camera.distance = max(distance, 1e-6)
        camera.target = eye + forward * camera.distance
        if view.projection == "orthographic":
            camera.orthographic = True
            camera.ortho_height = view.ortho_height or self._radius * 2.2
        return camera

    def apply_view(self, view, animate: bool = True) -> None:
        camera = self._view_camera(view)
        if camera is None:
            self.fit(animate=animate)
            return
        if self.scene is not None and view.hidden:
            names = set(view.hidden)
            self.hidden = {i for i, part in enumerate(self.scene.parts) if part.name in names or not part.visible}
        self._go(camera, animate)

    def standard_view(self, name: str, animate: bool = True) -> None:
        up = self.up_axis
        # Un eje "frontal" perpendicular al de arriba.
        if abs(up.z()) > 0.5:
            front = QVector3D(0, -1, 0)
        elif abs(up.y()) > 0.5:
            front = QVector3D(0, 0, 1)
        else:
            front = QVector3D(0, 0, 1)
        side = QVector3D.crossProduct(up, front).normalized() * -1  # derecha vista desde el frente
        directions = {
            "front": (front, up),
            "back": (-front, up),
            "right": (side, up),
            "left": (-side, up),
            "top": (up, -front),
            "bottom": (-up, front),
            "iso": ((front + side + up * 0.9).normalized(), up),
        }
        back, up_vector = directions.get(name, directions["iso"])
        right = QVector3D.crossProduct(up_vector, back).normalized()
        up_vector = QVector3D.crossProduct(back, right).normalized()
        camera = self.camera.copy()
        camera.rotation = QQuaternion.fromAxes(right, up_vector, back).normalized()
        camera.fit(self._center, self._radius, self.aspect())
        self._go(camera, animate)

    def fit(self, animate: bool = True) -> None:
        camera = self.camera.copy()
        camera.fit(self._center, self._radius, self.aspect())
        self._go(camera, animate)

    def fit_part(self, index: int, animate: bool = True) -> None:
        if self.scene is None or not 0 <= index < len(self.scene.parts):
            return
        part = self.scene.parts[index]
        sub = Scene(format=self.scene.format, primitives=self.scene.primitives, parts=[part])
        bounds = sub.bounds()
        if bounds is None:
            return
        lo, hi = bounds
        center = QVector3D((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
        camera = self.camera.copy()
        camera.fit(center, max(math.dist(lo, hi) / 2, self._radius * 0.01), self.aspect())
        self._go(camera, animate)

    def reset(self) -> None:
        self._go(self.home_camera.copy(), True)
        if self.scene is not None:
            self.hidden = {i for i, part in enumerate(self.scene.parts) if not part.visible}

    def _go(self, camera: Camera, animate: bool) -> None:
        if not animate or not self.isVisible():
            self._camera_anim.stop()
            self.camera = camera
            self.update()
            self.cameraChanged.emit()
            return
        self._anim_from = self.camera.copy()
        self._anim_to = camera
        self._anim_t = 0.0
        self._camera_anim.start()

    def _camera_step(self) -> None:
        self._anim_t = min(1.0, self._anim_t + 16 / theme.ANIMATION_MS / 1.6)
        t = 1 - (1 - self._anim_t) ** 3
        a, b = self._anim_from, self._anim_to
        camera = b.copy()
        camera.target = a.target + (b.target - a.target) * t
        camera.distance = a.distance + (b.distance - a.distance) * t
        camera.ortho_height = a.ortho_height + (b.ortho_height - a.ortho_height) * t
        camera.rotation = QQuaternion.slerp(a.rotation, b.rotation, t)
        self.camera = camera
        if self._anim_t >= 1.0:
            self._camera_anim.stop()
            self.camera = b
            self.cameraChanged.emit()
        self.update()

    # ======================================================================
    # Opciones de presentación
    # ======================================================================
    def set_render_mode(self, mode: str) -> None:
        if mode in RENDER_MODES:
            self.render_mode = mode
            self.update()

    def set_lighting(self, mode: str) -> None:
        if mode in LIGHTING_MODES:
            self.lighting = mode
            self.update()

    def set_background(self, mode: str) -> None:
        self.background_mode = mode
        self.update()

    def set_orthographic(self, enabled: bool) -> None:
        self.camera.set_orthographic(enabled, self.aspect())
        self.update()
        self.cameraChanged.emit()

    def set_section(self, axis: int | None, offset: float = 0.5, flip: bool = False) -> None:
        self.section_axis = axis
        self.section_offset = min(1.0, max(0.0, offset))
        self.section_flip = flip
        self.update()

    def set_part_visible(self, index: int, visible: bool) -> None:
        if visible:
            self.hidden.discard(index)
        else:
            self.hidden.add(index)
        self.update()

    def set_selected(self, index: int) -> None:
        self.selected = index
        self.update()

    def set_spinning(self, enabled: bool) -> None:
        if enabled:
            self._spin.start()
        else:
            self._spin.stop()

    def is_spinning(self) -> bool:
        return self._spin.isActive()

    def _spin_step(self) -> None:
        self.camera.orbit(0.3 / ORBIT_SPEED, 0.0, self.up_axis)
        self.update()

    # ======================================================================
    # OpenGL
    # ======================================================================
    def initializeGL(self) -> None:
        context = self.context()
        if context is None or not context.isValid():
            self._fail("context")
            return
        program = QOpenGLShaderProgram(self)
        ok = program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER)
        ok = ok and program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER)
        program.bindAttributeLocation("a_position", 0)
        program.bindAttributeLocation("a_normal", 1)
        program.bindAttributeLocation("a_color", 2)
        ok = ok and program.link()
        if not ok:
            self._fail(program.log())
            return
        self._program = program
        context.aboutToBeDestroyed.connect(self._cleanup)

    def _fail(self, reason: str) -> None:
        self.gl_ok = False
        self.gl_error = reason or "OpenGL"
        QTimer.singleShot(0, lambda: self.glUnavailable.emit(self.gl_error))

    def _cleanup(self) -> None:
        if self.context() is None:
            return
        self.makeCurrent()
        for gpu in self._gpu.values():
            gpu.destroy()
        self._gpu.clear()
        self._drop_world_buffers(set())
        self._uploaded = None
        self._program = None
        self._depth_fbo = None
        self.doneCurrent()

    def _upload(self) -> None:
        if self._uploaded is self.scene:
            return
        for gpu in self._gpu.values():
            gpu.destroy()
        self._gpu.clear()
        if self.scene is not None:
            for index, primitive in enumerate(self.scene.primitives):
                if primitive.vertex_count:
                    self._gpu[index] = _GpuPrimitive(primitive)
        self._uploaded = self.scene

    def paintGL(self) -> None:
        painter = QPainter(self)
        self._paint_background(painter)
        if self.gl_ok and self._program is not None and self.scene is not None:
            painter.beginNativePainting()
            try:
                gl = self.context().functions()
                self._render(gl, pick=False)
                # Lo que se dibuja encima (ejes, medidas) no debe quedar tapado por la pieza.
                gl.glDisable(GL_DEPTH_TEST)
                gl.glClear(GL_DEPTH_BUFFER_BIT)
            finally:
                painter.endNativePainting()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_axes(painter)
            if self.measure_tool is not None:
                self.measure_tool.paint(painter)
        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        rect = QRectF(self.rect())
        if self.background_mode == "document" and self.scene is not None and self.scene.background is not None:
            r, g, b = self.scene.background
            painter.fillRect(rect, QColor.fromRgbF(r, g, b))
            return
        if self.background_mode == "white":
            painter.fillRect(rect, QColor(255, 255, 255))
            return
        p = theme.current() if self.background_mode != "dark" else theme.DARK
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, p.qcolor("scene_top"))
        gradient.setColorAt(1.0, p.qcolor("scene_bottom"))
        painter.fillRect(rect, gradient)

    def background_is_dark(self) -> bool:
        if self.background_mode == "white":
            return False
        if self.background_mode == "document" and self.scene is not None and self.scene.background is not None:
            r, g, b = self.scene.background
            return 0.299 * r + 0.587 * g + 0.114 * b < 0.5
        return self.background_mode == "dark" or theme.current().is_dark

    def _projection(self, aspect: float) -> QMatrix4x4:
        camera = self.camera
        eye = camera.eye()
        forward = -camera.back()
        depth = QVector3D.dotProduct(self._center - eye, forward)
        radius = self._radius * 1.05
        m = QMatrix4x4()
        if camera.orthographic:
            half = camera.ortho_height / 2
            m.ortho(-half * aspect, half * aspect, -half, half, depth - radius, depth + radius)
        else:
            far = max(depth + radius, radius * 0.01)
            near = max(depth - radius, far * 0.0005)
            m.perspective(camera.vertical_fov(aspect), aspect, near, far)
        return m

    def _clip_plane(self) -> QVector4D | None:
        if self.section_axis is None:
            return None
        lo, hi = self._bounds
        axis = self.section_axis
        span = hi[axis] - lo[axis]
        position = lo[axis] - span * 0.001 + (span * 1.002) * self.section_offset
        normal = [0.0, 0.0, 0.0]
        normal[axis] = -1.0 if self.section_flip else 1.0
        return QVector4D(normal[0], normal[1], normal[2], -position * normal[axis])

    def _render(self, gl, pick: bool, projection: QMatrix4x4 | None = None, size: QSize | None = None,
                depth: bool = False) -> None:
        self._upload()
        program = self._program
        scene = self.scene
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        if size is not None:
            gl.glViewport(0, 0, size.width(), size.height())
        else:
            gl.glViewport(0, 0, width, height)
        gl.glDisable(GL_SCISSOR_TEST)
        gl.glDisable(GL_STENCIL_TEST)
        gl.glDisable(GL_CULL_FACE)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LEQUAL)
        gl.glDepthMask(True)
        gl.glClear(GL_DEPTH_BUFFER_BIT)
        gl.glEnable(GL_PROGRAM_POINT_SIZE)
        if pick:
            gl.glDisable(GL_BLEND)
            gl.glDisable(GL_MULTISAMPLE)
        else:
            gl.glEnable(GL_MULTISAMPLE)
            gl.glEnable(GL_BLEND)
            gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        view = self.camera.view_matrix()
        if projection is None:
            projection = self._projection(width / height)
        program.bind()
        program.setUniformValue1i("u_depth_out", 1 if depth else 0)
        program.setUniformValue("u_view", view)
        program.setUniformValue("u_projection", projection)
        program.setUniformValue1i("u_light_mode", LIGHTING_MODES.index(self.lighting))
        program.setUniformValue1f("u_point_size", 3.0 * ratio)
        plane = self._clip_plane()
        program.setUniformValue1i("u_clip", 1 if plane is not None else 0)
        if plane is not None:
            program.setUniformValue("u_clip_plane", plane)
        program.setUniformValue1f("u_highlight_amount", 0.0)

        mode = self.render_mode
        transparent_mode = mode == "transparent"
        edge_color = theme.current().qcolor("scene_edge")
        accent = theme.current().qcolor("accent")

        opaque = []
        blended = []
        for index, part in enumerate(scene.parts):
            if index in self.hidden:
                continue
            alpha = part.opacity
            for prim_index in part.primitives:
                gpu = self._gpu.get(prim_index)
                if gpu is None:
                    continue
                material = scene.materials[scene.primitives[prim_index].material] if scene.materials else None
                opacity = (material.opacity if material else 1.0) * alpha
                item = (index, part, prim_index, gpu, material, opacity)
                if not pick and (transparent_mode or opacity < 0.995) and gpu.kind == "triangles":
                    blended.append(item)
                else:
                    opaque.append(item)

        draw_faces = mode != "wireframe" or pick
        if draw_faces:
            # Al leer la profundidad, sin desplazar las caras: el punto queda justo en la superficie.
            if not depth:
                gl.glEnable(GL_POLYGON_OFFSET_FILL)
                gl.glPolygonOffset(1.0, 1.0)
            for item in opaque:
                self._draw(gl, item, pick, wire=False)
            gl.glDisable(GL_POLYGON_OFFSET_FILL)

        if not pick and mode in ("outline", "solid-wire", "wireframe"):
            for index, part, prim_index, gpu, material, opacity in opaque + blended:
                if gpu.kind != "triangles":
                    continue
                if mode == "wireframe":
                    color = self._wire_color(material, index)
                    self._draw_lines(gl, part, gpu.wire, gpu.wire_count, color)
                elif mode == "solid-wire":
                    self._draw_lines(gl, part, gpu.wire, gpu.wire_count, _with_alpha(edge_color, 0.45))
                else:
                    color = accent if index == self.selected else edge_color
                    self._draw_lines(gl, part, gpu.edges, gpu.edge_count, color)
        elif not pick and self.selected >= 0 and mode != "wireframe":
            for index, part, prim_index, gpu, material, opacity in opaque + blended:
                if index == self.selected and gpu.kind == "triangles":
                    self._draw_lines(gl, part, gpu.edges, gpu.edge_count, accent)

        if blended and draw_faces:
            eye = self.camera.eye()
            blended.sort(key=lambda item: -(_part_center(item[1]) - eye).length())
            gl.glDepthMask(False)
            for item in blended:
                index, part, prim_index, gpu, material, opacity = item
                if transparent_mode:
                    item = (index, part, prim_index, gpu, material, min(opacity, 0.32))
                self._draw(gl, item, pick, wire=False)
            gl.glDepthMask(True)
            if transparent_mode:
                for index, part, prim_index, gpu, material, opacity in blended:
                    self._draw_lines(gl, part, gpu.edges, gpu.edge_count, _with_alpha(edge_color, 0.55))
        if not pick:
            # Lo que se resalta al medir, con la profundidad: solo se ve la parte visible de cada cara.
            drawn = set()
            for kind, data, color in self.measure_tool.gl_highlights() if self.measure_tool is not None else ():
                self._draw_world(gl, kind, data, color)
                drawn.add(id(data))
            self._drop_world_buffers(drawn)
        program.release()
        gl.glDisable(GL_PROGRAM_POINT_SIZE)

    def _draw_world(self, gl, kind: str, data: bytes, color: QColor) -> None:
        """Triángulos o líneas ya en coordenadas del mundo (float32 xyz), de un solo color."""
        if not data:
            return
        program = self._program
        identity = QMatrix4x4()
        program.setUniformValue("u_model", identity)
        program.setUniformValue("u_normal_matrix", identity)
        program.setUniformValue1i("u_lighting", 0)
        program.setUniformValue1i("u_vertex_color", 0)
        program.setUniformValue("u_diffuse", _vec(_rgb(color)))
        program.setUniformValue1f("u_opacity", color.alphaF())
        program.setUniformValue1f("u_highlight_amount", 0.0)
        cached = self._world_buffers.get(id(data))
        if cached is None or cached[0] is not data:
            if cached is not None:
                cached[1].destroy()
            cached = self._world_buffers[id(data)] = (data, _buffer(data))
        buffer = cached[1]
        buffer.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3)
        program.disableAttributeArray(1)
        program.disableAttributeArray(2)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        if kind == "triangles":
            gl.glDepthMask(False)
            gl.glEnable(GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(-1.0, -1.0)
            gl.glDrawArrays(GL_TRIANGLES, 0, len(data) // 12)
            gl.glDisable(GL_POLYGON_OFFSET_FILL)
            gl.glDepthMask(True)
        else:
            gl.glDrawArrays(GL_LINES, 0, len(data) // 12)
        program.disableAttributeArray(0)
        buffer.release()

    def _drop_world_buffers(self, keep: set[int]) -> None:
        for key in [key for key in self._world_buffers if key not in keep]:
            self._world_buffers.pop(key)[1].destroy()

    def _wire_color(self, material, index: int) -> QColor:
        if index == self.selected:
            return theme.current().qcolor("accent")
        if material is None:
            return QColor(200, 200, 208)
        r, g, b = material.diffuse
        color = QColor.fromRgbF(r, g, b)
        if not self.background_is_dark() and color.lightnessF() > 0.75:
            color = color.darker(160)
        return color

    def _set_part(self, part) -> None:
        program = self._program
        model = _matrix(part.matrix)
        program.setUniformValue("u_model", model)
        normal = (self.camera.view_matrix() * model).inverted()[0].transposed()
        program.setUniformValue("u_normal_matrix", normal)

    def _draw(self, gl, item, pick: bool, wire: bool) -> None:
        index, part, prim_index, gpu, material, opacity = item
        program = self._program
        self._set_part(part)
        if pick:
            program.setUniformValue1i("u_lighting", 0)
            program.setUniformValue1i("u_vertex_color", 0)
            program.setUniformValue("u_diffuse", _id_color(index))
            program.setUniformValue1f("u_opacity", 1.0)
            program.setUniformValue1f("u_highlight_amount", 0.0)
        else:
            if material is not None:
                program.setUniformValue("u_diffuse", _vec(material.diffuse))
                program.setUniformValue("u_specular", _vec(material.specular) * 0.6 + QVector3D(0.08, 0.08, 0.08))
                program.setUniformValue("u_emissive", _vec(material.emissive))
                program.setUniformValue1f("u_shininess", float(material.shininess))
                lighting = material.lighting and gpu.kind == "triangles" and gpu.normals is not None
                use_colors = material.vertex_colors and gpu.colors is not None
            else:
                program.setUniformValue("u_diffuse", QVector3D(0.72, 0.72, 0.74))
                program.setUniformValue("u_specular", QVector3D(0.2, 0.2, 0.2))
                program.setUniformValue("u_emissive", QVector3D(0, 0, 0))
                program.setUniformValue1f("u_shininess", 0.3)
                lighting = gpu.kind == "triangles" and gpu.normals is not None
                use_colors = gpu.colors is not None
            program.setUniformValue1i("u_lighting", 1 if lighting else 0)
            program.setUniformValue1i("u_vertex_color", 1 if use_colors else 0)
            program.setUniformValue1f("u_opacity", float(opacity))
            if index == self.selected:
                program.setUniformValue("u_highlight", _vec(_rgb(theme.current().qcolor("accent"))))
                program.setUniformValue1f("u_highlight_amount", 0.28)
            else:
                program.setUniformValue1f("u_highlight_amount", 0.0)

        gpu.positions.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3)
        if gpu.normals is not None and not pick:
            gpu.normals.bind()
            program.enableAttributeArray(1)
            program.setAttributeBuffer(1, GL_FLOAT, 0, 3)
        else:
            program.disableAttributeArray(1)
        if gpu.colors is not None and not pick:
            gpu.colors.bind()
            program.enableAttributeArray(2)
            program.setAttributeBuffer(2, GL_FLOAT, 0, 4)
        else:
            program.disableAttributeArray(2)
        mode = {"triangles": GL_TRIANGLES, "lines": GL_LINES, "points": GL_POINTS}[gpu.kind]
        gl.glDrawArrays(mode, 0, gpu.count)
        program.disableAttributeArray(0)
        program.disableAttributeArray(1)
        program.disableAttributeArray(2)
        gpu.positions.release()

    def _draw_lines(self, gl, part, buffer, count: int, color: QColor) -> None:
        if buffer is None or not count:
            return
        program = self._program
        self._set_part(part)
        program.setUniformValue1i("u_lighting", 0)
        program.setUniformValue1i("u_vertex_color", 0)
        program.setUniformValue("u_diffuse", _vec(_rgb(color)))
        program.setUniformValue1f("u_opacity", color.alphaF())
        program.setUniformValue1f("u_highlight_amount", 0.0)
        buffer.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3)
        program.disableAttributeArray(1)
        program.disableAttributeArray(2)
        gl.glDrawArrays(GL_LINES, 0, count)
        program.disableAttributeArray(0)
        buffer.release()

    def pick(self, pos: QPointF) -> int:
        """Índice de la pieza bajo ``pos`` (coordenadas del widget) o -1."""
        if not self.gl_ok or self._program is None or self.scene is None:
            return -1
        ratio = self.devicePixelRatioF()
        size = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        self.makeCurrent()
        try:
            fbo = QOpenGLFramebufferObject(size, QOpenGLFramebufferObject.Attachment.Depth)
            fbo.bind()
            gl = self.context().functions()
            gl.glClearColor(1.0, 1.0, 1.0, 1.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._render(gl, pick=True)
            fbo.release()
            image = fbo.toImage()
        finally:
            self.doneCurrent()
        x = int(pos.x() * ratio)
        y = int(pos.y() * ratio)
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return -1
        color = image.pixelColor(x, y)
        value = color.red() | (color.green() << 8) | (color.blue() << 16)
        if value == 0xFFFFFF:
            return -1
        return value - 1 if value - 1 < len(self.scene.parts) else -1

    def _pick_pixel(self, pos: QPointF, depth: bool):
        """Color del píxel bajo ``pos`` en un pase de selección de 1x1 (profundidad o pieza), o None."""
        if not self.gl_ok or self._program is None or self.scene is None:
            return None
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        x = pos.x() * ratio
        y = height - pos.y() * ratio
        if not (0 <= x < width and 0 <= y < height):
            return None
        projection = self._projection(width / height)
        # Como gluPickMatrix: el píxel bajo el ratón ocupa toda la imagen de 1x1.
        region = QMatrix4x4()
        region.translate(width - 2 * x, height - 2 * y, 0)
        region.scale(width, height, 1.0)
        self.makeCurrent()
        try:
            # Se reutiliza: al medir se lee en cada movimiento del ratón.
            if self._depth_fbo is None:
                self._depth_fbo = QOpenGLFramebufferObject(QSize(1, 1), QOpenGLFramebufferObject.Attachment.Depth)
            fbo = self._depth_fbo
            fbo.bind()
            gl = self.context().functions()
            gl.glClearColor(1.0, 1.0, 1.0, 1.0)
            gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._render(gl, pick=True, projection=region * projection, size=QSize(1, 1), depth=depth)
            fbo.release()
            color = fbo.toImage().pixelColor(0, 0)
        finally:
            self.doneCurrent()
        return color, x, y, width, height, projection

    def pick_point(self, pos: QPointF) -> QVector3D | None:
        """Punto de la superficie visible bajo ``pos`` (coordenadas del widget), en el mundo, o None."""
        found = self._pick_pixel(pos, depth=True)
        if found is None:
            return None
        color, x, y, width, height, projection = found
        value = (color.red() << 16) | (color.green() << 8) | color.blue()
        if value >= 0xFFFFFF:
            return None
        ndc = QVector4D(2 * x / width - 1, 2 * y / height - 1, 2 * value / 0xFFFFFF - 1, 1.0)
        inverse, ok = (projection * self.camera.view_matrix()).inverted()
        if not ok:
            return None
        world = inverse.map(ndc)
        if abs(world.w()) < 1e-12:
            return None
        return world.toVector3D() / world.w()

    def pick_part(self, pos: QPointF) -> int:
        """Pieza bajo ``pos`` (como ``pick``, pero leyendo un solo píxel), o -1."""
        found = self._pick_pixel(pos, depth=False)
        if found is None:
            return -1
        color = found[0]
        value = color.red() | (color.green() << 8) | (color.blue() << 16)
        if value == 0xFFFFFF or value - 1 >= len(self.scene.parts):
            return -1
        return value - 1

    def projector(self):
        """Función que lleva puntos del mundo a la pantalla, con la cámara de ahora (para dibujar muchos)."""
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        m = self._projection(width / height) * self.camera.view_matrix()
        r = [m.row(i) for i in range(4)]
        w_px, h_px = self.width(), self.height()

        def project(p) -> QPointF | None:
            x, y, z = p
            cw = r[3].x() * x + r[3].y() * y + r[3].z() * z + r[3].w()
            if cw <= 1e-12:
                return None
            cx = (r[0].x() * x + r[0].y() * y + r[0].z() * z + r[0].w()) / cw
            cy = (r[1].x() * x + r[1].y() * y + r[1].z() * z + r[1].w()) / cw
            return QPointF((cx + 1) / 2 * w_px, (1 - cy) / 2 * h_px)

        return project

    def grab_image(self) -> QImage:
        """Imagen de lo que se ve, con las medidas encima (grabFramebuffer las taparía con la pieza)."""
        tool = self.measure_tool
        self.measure_tool = None
        try:
            image = self.grabFramebuffer()
        finally:
            self.measure_tool = tool
        if tool is not None:
            image.setDevicePixelRatio(self.devicePixelRatioF())
            painter = QPainter(image)
            tool.paint(painter, hover=False)
            painter.end()
        return image

    def project(self, point: QVector3D) -> QPointF | None:
        """Dónde se ve un punto del mundo, en coordenadas del widget; None si queda detrás de la cámara."""
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        clip = (self._projection(width / height) * self.camera.view_matrix()).map(QVector4D(point, 1.0))
        if clip.w() <= 1e-12:
            return None
        x, y = clip.x() / clip.w(), clip.y() / clip.w()
        return QPointF((x + 1) / 2 * self.width(), (1 - y) / 2 * self.height())

    def clip_values(self) -> tuple[float, float, float, float] | None:
        """Plano de sección activo (a, b, c, d): lo que da positivo está recortado."""
        plane = self._clip_plane()
        return None if plane is None else (plane.x(), plane.y(), plane.z(), plane.w())

    def pick_ray(self, pos: QPointF) -> tuple[QVector3D, QVector3D] | None:
        """Rayo que sale del cursor hacia la escena: origen (en el plano cercano) y dirección unitaria."""
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        inverse, ok = (self._projection(width / height) * self.camera.view_matrix()).inverted()
        if not ok:
            return None
        x = 2 * pos.x() / max(self.width(), 1) - 1
        y = 1 - 2 * pos.y() / max(self.height(), 1)
        near = inverse.map(QVector4D(x, y, -1.0, 1.0))
        far = inverse.map(QVector4D(x, y, 1.0, 1.0))
        if abs(near.w()) < 1e-12 or abs(far.w()) < 1e-12:
            return None
        origin = near.toVector3D() / near.w()
        direction = far.toVector3D() / far.w() - origin
        if direction.length() == 0:
            return None
        return origin, direction.normalized()

    def snap_tolerance(self, origin: QVector3D, direction: QVector3D, pixels: float) -> tuple[float, float]:
        """``pixels`` de pantalla en unidades del modelo a lo largo del rayo: ``near + growth * t``."""
        camera = self.camera
        if camera.orthographic:
            return pixels * camera.ortho_height / max(self.height(), 1), 0.0
        per_depth = pixels * 2 * math.tan(math.radians(camera.vertical_fov(self.aspect())) / 2) / max(self.height(), 1)
        forward = -camera.back()
        return (per_depth * max(QVector3D.dotProduct(origin - camera.eye(), forward), 0.0),
                per_depth * QVector3D.dotProduct(direction, forward))

    # ======================================================================
    # Superposiciones
    # ======================================================================
    def _paint_axes(self, painter: QPainter) -> None:
        p = theme.current()
        center = QPointF(36, self.height() - 36)
        length = 22.0
        right = self.camera.right()
        up = self.camera.up()
        back = self.camera.back()
        axes = []
        for label, vector, color in (
            ("X", QVector3D(1, 0, 0), p.qcolor("axis_x")),
            ("Y", QVector3D(0, 1, 0), p.qcolor("axis_y")),
            ("Z", QVector3D(0, 0, 1), p.qcolor("axis_z")),
        ):
            x = QVector3D.dotProduct(vector, right)
            y = QVector3D.dotProduct(vector, up)
            depth = QVector3D.dotProduct(vector, back)
            axes.append((depth, label, QPointF(x, -y), color))
        axes.sort(key=lambda item: item[0])
        font = QFont(painter.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        for depth, label, direction, color in axes:
            end = center + direction * length
            faded = QColor(color)
            faded.setAlphaF(0.55 + 0.45 * (depth + 1) / 2)
            painter.setPen(QPen(faded, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, end)
            painter.setPen(faded)
            painter.drawText(QRectF(end.x() - 8 + direction.x() * 7, end.y() - 8 + direction.y() * 7, 16, 16),
                             Qt.AlignmentFlag.AlignCenter, label)

    # ======================================================================
    # Ratón y teclado
    # ======================================================================
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._camera_anim.stop()
        self._drag_button = event.button()
        self._drag_origin = event.position()
        self._last = event.position()
        self._moved = False
        if self._is_pan(event):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _is_pan(self, event) -> bool:
        return self._drag_button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_button is None:
            if self.measure_tool is not None:
                self.measure_tool.hover_at(event.position())
            return
        delta = event.position() - self._last
        self._last = event.position()
        if (event.position() - self._drag_origin).manhattanLength() > 3:
            self._moved = True
            if self.measure_tool is not None:
                # Al girar, el marcador del cursor quedaría en un punto que ya no está bajo el ratón.
                self.measure_tool.hover = None
        if not self._moved:
            return
        if self._is_pan(event):
            self._pan(delta)
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.camera.roll(delta.x() * ORBIT_SPEED)
        else:
            self.camera.orbit(delta.x(), delta.y(), self.up_axis)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        button = self._drag_button
        self._drag_button = None
        self.unsetCursor()
        if not self._moved and button == Qt.MouseButton.LeftButton:
            if self.measure_tool is not None:
                self.measure_tool.click(event.position())
            else:
                self.partClicked.emit(self.pick(event.position()))
        elif self._moved:
            self.cameraChanged.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.measure_tool is not None:
            # Midiendo, el segundo clic de un doble clic es otro punto, no «encuadrar».
            self.mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.pick(event.position())
            if index >= 0:
                self.fit_part(index)
            else:
                self.fit()

    def _pan(self, delta: QPointF) -> None:
        per_pixel = self.camera.view_height(self.aspect()) / max(self.height(), 1)
        shift = self.camera.right() * (-delta.x() * per_pixel) + self.camera.up() * (delta.y() * per_pixel)
        self.camera.target = self.camera.target + shift

    def wheelEvent(self, event: QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        self._camera_anim.stop()
        factor = WHEEL_STEP ** steps
        pos = event.position()
        aspect = self.aspect()
        height = self.camera.view_height(aspect)
        ox = (pos.x() / max(self.width(), 1) - 0.5) * height * aspect
        oy = (0.5 - pos.y() / max(self.height(), 1)) * height
        point = self.camera.target + self.camera.right() * ox + self.camera.up() * oy
        self.camera.target = point + (self.camera.target - point) * factor
        if self.camera.orthographic:
            self.camera.ortho_height = max(self.camera.ortho_height * factor, self._radius * 1e-5)
        else:
            self.camera.distance = max(self.camera.distance * factor, self._radius * 1e-4)
        self.update()
        self.cameraChanged.emit()
        event.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        step = 6.0 / ORBIT_SPEED
        if key == Qt.Key.Key_Left:
            self.camera.orbit(-step, 0, self.up_axis)
        elif key == Qt.Key.Key_Right:
            self.camera.orbit(step, 0, self.up_axis)
        elif key == Qt.Key.Key_Up:
            self.camera.orbit(0, -step, self.up_axis)
        elif key == Qt.Key.Key_Down:
            self.camera.orbit(0, step, self.up_axis)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_center(WHEEL_STEP)
        elif key == Qt.Key.Key_Minus:
            self._zoom_center(1 / WHEEL_STEP)
        else:
            super().keyPressEvent(event)
            return
        self.update()

    def _zoom_center(self, factor: float) -> None:
        if self.camera.orthographic:
            self.camera.ortho_height *= factor
        else:
            self.camera.distance = max(self.camera.distance * factor, self._radius * 1e-4)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update()


def _rgb(color: QColor) -> tuple[float, float, float]:
    return color.redF(), color.greenF(), color.blueF()


def _with_alpha(color: QColor, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


def _id_color(index: int) -> QVector3D:
    value = index + 1
    return QVector3D((value & 0xFF) / 255.0, ((value >> 8) & 0xFF) / 255.0, ((value >> 16) & 0xFF) / 255.0)


def _part_center(part) -> QVector3D:
    m = part.matrix
    return QVector3D(m[12], m[13], m[14])
