"""Ventana del visor 3D.

Muestra un modelo U3D o PRC, ya venga de un PDF o de un archivo suelto. El
modelo se decodifica en un subproceso mientras la ventana enseña el avance.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.model3d import Scene
from ..core.model3d import measure3d as core_measure3d
from . import errors, formatting, icons, measure3d, tasks, theme
from .viewer3d import Viewer3D, opengl_available
from .widgets import Button, FloatingPanel, IconButton, SegmentedControl, label, separator

MODES = ("solid", "outline", "wireframe", "transparent")
VIEW_NAMES = {
    "front": QT_TRANSLATE_NOOP("Model3DWindow", "Frente"),
    "back": QT_TRANSLATE_NOOP("Model3DWindow", "Detrás"),
    "left": QT_TRANSLATE_NOOP("Model3DWindow", "Izquierda"),
    "right": QT_TRANSLATE_NOOP("Model3DWindow", "Derecha"),
    "top": QT_TRANSLATE_NOOP("Model3DWindow", "Arriba"),
    "bottom": QT_TRANSLATE_NOOP("Model3DWindow", "Abajo"),
    "iso": QT_TRANSLATE_NOOP("Model3DWindow", "Isométrica"),
}


class _Overlay(FloatingPanel):
    """Tarjeta centrada para la carga, los errores y los avisos."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, inner=(24, 20, 24, 20), radius=14)
        layout = QVBoxLayout(self)
        self.apply_margins(layout)
        layout.setSpacing(theme.space(3))
        self.icon = QLabel(self)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label("", "cardtitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message = label("", "muted", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(260)
        self.button = Button("", "ghost")
        layout.addWidget(self.icon)
        layout.addWidget(self.title)
        layout.addWidget(self.message)
        layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setFixedWidth(360)

    def show_state(self, icon_name: str, title: str, message: str, busy: bool, button: str = "",
                   role: str = icons.ROLE_DEFAULT) -> None:
        self.icon.setPixmap(icons.pixmap(icon_name, 32, role, self.devicePixelRatioF()))
        self.title.setText(title)
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.progress.setVisible(busy)
        self.button.setText(button)
        self.button.setVisible(bool(button))
        self.adjustSize()
        self.show()
        self.raise_()
        self.reposition()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, (parent.height() - self.height()) // 2)


class _SectionBar(FloatingPanel):
    """Controles del plano de sección."""

    changed = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent, inner=(8, 6, 8, 6))
        layout = QHBoxLayout(self)
        self.apply_margins(layout)
        layout.setSpacing(theme.space(2))
        icon = QLabel(self)
        icon.setPixmap(icons.pixmap("slice", 18, dpr=self.devicePixelRatioF()))
        layout.addWidget(icon)
        layout.addWidget(label(self.tr("Sección"), "cardtitle"))
        self.axis = SegmentedControl([("0", "X"), ("1", "Y"), ("2", "Z")], self)
        self.axis.changed.connect(lambda _value: self.changed.emit())
        layout.addWidget(self.axis)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setFixedWidth(220)
        self.slider.valueChanged.connect(lambda _value: self.changed.emit())
        layout.addWidget(self.slider)
        self.flip = IconButton("flip-horizontal-2", self.tr("Invertir el lado cortado"), self, checkable=True)
        self.flip.toggled.connect(lambda _on: self.changed.emit())
        layout.addWidget(self.flip)
        close = IconButton("x", self.tr("Quitar la sección"), self)
        close.clicked.connect(self.closed.emit)
        layout.addWidget(close)

    def values(self) -> tuple[int, float, bool]:
        return int(self.axis.value()), self.slider.value() / 1000.0, self.flip.isChecked()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.adjustSize()
            self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - theme.space(2))


class PartsPanel(QFrame):
    """Lista de piezas con visibilidad y selección."""

    visibilityChanged = Signal(int, bool)
    selected = Signal(int)
    focusRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PartsPanel")
        self.setFixedWidth(260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.space(3), theme.space(3), theme.space(3), theme.space(3))
        layout.setSpacing(theme.space(2))
        header = QHBoxLayout()
        self.title = label(self.tr("Piezas"), "cardtitle")
        header.addWidget(self.title)
        header.addStretch(1)
        self.count = label("", "caption")
        header.addWidget(self.count)
        layout.addLayout(header)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(self.tr("Buscar pieza"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = QListWidget(self)
        self.list.setUniformItemSizes(True)
        # Nombres largos: se recortan por el centro para que se vea el final («(2)»).
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.list.itemChanged.connect(self._item_changed)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemDoubleClicked.connect(lambda item: self.focusRequested.emit(self.list.row(item)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.show_all = Button(self.tr("Mostrar todas"), "ghost")
        self.show_all.clicked.connect(lambda: self.set_all_visible(True))
        self.isolate = Button(self.tr("Aislar"), "ghost")
        self.isolate.clicked.connect(self._isolate_current)
        buttons.addWidget(self.show_all)
        buttons.addWidget(self.isolate)
        layout.addLayout(buttons)
        self._updating = False

    def set_scene(self, scene: Scene, hidden: set[int]) -> None:
        self._updating = True
        self.list.clear()
        for index, part in enumerate(scene.parts):
            item = QListWidgetItem(part.name or self.tr("Pieza {0}").format(index + 1))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if index in hidden else Qt.CheckState.Checked)
            item.setToolTip(item.text())
            self.list.addItem(item)
        self.count.setText(formatting.number(len(scene.parts)))
        self._updating = False

    def sync_hidden(self, hidden: set[int]) -> None:
        self._updating = True
        for row in range(self.list.count()):
            self.list.item(row).setCheckState(Qt.CheckState.Unchecked if row in hidden else Qt.CheckState.Checked)
        self._updating = False

    def select(self, index: int) -> None:
        self.list.blockSignals(True)
        if index < 0:
            self.list.clearSelection()
            self.list.setCurrentRow(-1)
        else:
            self.list.setCurrentRow(index)
            self.list.scrollToItem(self.list.item(index))
        self.list.blockSignals(False)

    def set_all_visible(self, visible: bool, keep: int = -1) -> None:
        for row in range(self.list.count()):
            want = visible or row == keep
            self.list.item(row).setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)

    def _isolate_current(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.set_all_visible(False, keep=row)

    def _item_changed(self, item: QListWidgetItem) -> None:
        if not self._updating:
            self.visibilityChanged.emit(self.list.row(item), item.checkState() == Qt.CheckState.Checked)

    def _row_changed(self, row: int) -> None:
        self.selected.emit(row)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _menu(self, pos: QPoint) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        row = self.list.row(item)
        menu = QMenu(self)
        visible = item.checkState() == Qt.CheckState.Checked
        toggle = menu.addAction(icons.icon("eye-off" if visible else "eye"), self.tr("Ocultar") if visible else self.tr("Mostrar"))
        isolate = menu.addAction(icons.icon("scan-eye"), self.tr("Aislar"))
        focus = menu.addAction(icons.icon("focus"), self.tr("Encuadrar"))
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is toggle:
            item.setCheckState(Qt.CheckState.Unchecked if visible else Qt.CheckState.Checked)
        elif chosen is isolate:
            self.set_all_visible(False, keep=row)
        elif chosen is focus:
            self.focusRequested.emit(row)


class _MeasureBar(FloatingPanel):
    """Controles de medir: qué se mide, en qué unidad y el resultado."""

    modeChanged = Signal(str)
    unitChanged = Signal(str)
    clearRequested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent, inner=(8, 6, 8, 6))
        layout = QHBoxLayout(self)
        self.apply_margins(layout)
        layout.setSpacing(theme.space(2))
        icon = QLabel(self)
        icon.setPixmap(icons.pixmap("ruler", 18, dpr=self.devicePixelRatioF()))
        layout.addWidget(icon)
        self.modes = SegmentedControl(list(measure3d.mode_names().items()), self)
        self.modes.changed.connect(self.modeChanged)
        layout.addWidget(self.modes)
        self.unit = QComboBox(self)
        for name in core_measure3d.UNITS_MM:
            self.unit.addItem(name, name)
        self.unit.currentIndexChanged.connect(lambda _row: self.unitChanged.emit(self.unit.currentData()))
        layout.addWidget(self.unit)
        self.readout = label("", "")
        self.readout.setMinimumWidth(theme.space(80))
        layout.addWidget(self.readout, 1)
        self.clear_button = IconButton("trash-2", self.tr("Borrar las medidas"), self)
        self.clear_button.clicked.connect(self.clearRequested)
        layout.addWidget(self.clear_button)
        close = IconButton("x", self.tr("Dejar de medir"), self)
        close.clicked.connect(self.closed)
        layout.addWidget(close)

    def set_unit(self, unit: str, known: bool) -> None:
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(max(0, self.unit.findData(unit)))
        self.unit.blockSignals(False)
        self.unit.setToolTip(self.tr("Unidad en la que se muestran las medidas") if known else self.tr(
            "El archivo no dice en qué unidades está el modelo: elige la suya"))

    def show_text(self, text: str) -> None:
        self.readout.setText(text)
        self.adjustSize()

    def reposition(self, above: QWidget | None = None) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.adjustSize()
            bottom = parent.height() - theme.space(2)
            if above is not None and above.isVisible():
                bottom = above.y() - theme.space(1)
            self.move((parent.width() - self.width()) // 2, bottom - self.height())


class Model3DWindow(QWidget):
    """Ventana independiente con el visor 3D y sus herramientas."""

    closed = Signal()

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("Window")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(self.tr("{0} - Visor 3D - BlackPDF").format(title))
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 760)
        self._title = title
        self._subtitle = subtitle
        self._task = None
        self.scene: Scene | None = None

        self.stage = QWidget(self)
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = Viewer3D(self.stage)
        stage_layout.addWidget(self.viewer)
        self.parts = PartsPanel(self)
        self.parts.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        self.parts_button.toggled.connect(self.parts.setVisible)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.stage, 1)
        body.addWidget(self.parts)
        root.addLayout(body, 1)
        root.addWidget(self._build_status())

        self.overlay = _Overlay(self.stage)
        self.overlay.button.clicked.connect(self._overlay_button)
        self.section_bar = _SectionBar(self.stage)
        self.section_bar.hide()
        self.section_bar.changed.connect(self._apply_section)
        self.section_bar.closed.connect(lambda: self.section_button.setChecked(False))
        self.measure = measure3d.Measure3D(self.viewer, self)
        self.measure.changed.connect(self._measure_changed)
        self.measure_bar = _MeasureBar(self.stage)
        self.measure_bar.hide()
        self.measure_bar.modeChanged.connect(self.measure.set_mode)
        self.measure_bar.unitChanged.connect(self.measure.set_unit)
        self.measure_bar.clearRequested.connect(self.measure.clear)
        self.measure_bar.closed.connect(lambda: self.measure_button.setChecked(False))
        self.stage.installEventFilter(self)

        self.viewer.partClicked.connect(self._part_clicked)
        self.viewer.glUnavailable.connect(self._gl_failed)
        self.parts.visibilityChanged.connect(self.viewer.set_part_visible)
        self.parts.selected.connect(self._list_selected)
        self.parts.focusRequested.connect(self.viewer.fit_part)
        self._set_tools_enabled(False)
        self._shortcuts()

    # ======================================================================
    # Construcción
    # ======================================================================
    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("DocToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.space(3), theme.space(2), theme.space(3), theme.space(2))
        layout.setSpacing(theme.space(1))

        icon = QLabel(bar)
        icon.setPixmap(icons.pixmap("box", 20, icons.ROLE_ACCENT, self.devicePixelRatioF()))
        layout.addWidget(icon)
        layout.addSpacing(theme.space(1))
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name_label = label(self._title, "cardtitle")
        self.name_label.setMaximumWidth(260)
        names.addWidget(self.name_label)
        self.source_label = label(self._subtitle, "caption")
        self.source_label.setVisible(bool(self._subtitle))
        names.addWidget(self.source_label)
        layout.addLayout(names)
        layout.addSpacing(theme.space(3))
        layout.addWidget(separator(vertical=True))

        self.views_box = QComboBox(bar)
        self.views_box.setMinimumWidth(170)
        self.views_box.setToolTip(self.tr("Vistas guardadas y estándar"))
        self.views_box.activated.connect(self._view_chosen)
        layout.addWidget(self.views_box)
        self.home_button = IconButton("house", self.tr("Vista inicial (Inicio)"), bar)
        self.home_button.clicked.connect(self._reset)
        layout.addWidget(self.home_button)
        self.fit_button = IconButton("scan", self.tr("Encuadrar todo (F)"), bar)
        self.fit_button.clicked.connect(lambda: self.viewer.fit())
        layout.addWidget(self.fit_button)
        layout.addWidget(separator(vertical=True))

        self.modes = SegmentedControl(
            [(mode, "") for mode in MODES],
            bar,
            icons_map={"solid": "box", "outline": "frame", "wireframe": "grid-3x3", "transparent": "blend"},
            tooltips={
                "solid": self.tr("Sólido"),
                "outline": self.tr("Sólido con contornos"),
                "wireframe": self.tr("Alámbrico"),
                "transparent": self.tr("Transparente"),
            },
        )
        self.modes.changed.connect(self.viewer.set_render_mode)
        layout.addWidget(self.modes)
        layout.addWidget(separator(vertical=True))

        self.projection_button = IconButton("view", self.tr("Proyección ortográfica (O)"), bar, checkable=True)
        self.projection_button.toggled.connect(self.viewer.set_orthographic)
        layout.addWidget(self.projection_button)
        self.section_button = IconButton("slice", self.tr("Plano de sección (S)"), bar, checkable=True)
        self.section_button.toggled.connect(self._toggle_section)
        layout.addWidget(self.section_button)
        self.measure_button = IconButton("ruler", self.tr("Medir distancias, ángulos y radios (M)"), bar, checkable=True)
        self.measure_button.toggled.connect(self._toggle_measure)
        layout.addWidget(self.measure_button)
        self.spin_button = IconButton("rotate-3d", self.tr("Girar automáticamente (Espacio)"), bar, checkable=True)
        self.spin_button.toggled.connect(self.viewer.set_spinning)
        layout.addWidget(self.spin_button)
        self.light_button = IconButton("sun-medium", self.tr("Iluminación y fondo"), bar)
        self.light_button.setPopupMode(IconButton.ToolButtonPopupMode.InstantPopup)
        self.light_button.setMenu(self._appearance_menu())
        layout.addWidget(self.light_button)

        layout.addStretch(1)
        self.export_button = IconButton("image-down", self.tr("Guardar imagen (Ctrl+S)"), bar)
        self.export_button.clicked.connect(self._export_image)
        layout.addWidget(self.export_button)
        self.parts_button = IconButton("layers", self.tr("Piezas (P)"), bar, checkable=True)
        layout.addWidget(self.parts_button)
        self.full_button = IconButton("expand", self.tr("Pantalla completa (F11)"), bar)
        self.full_button.clicked.connect(self._toggle_fullscreen)
        layout.addWidget(self.full_button)
        return bar

    def _appearance_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addSection(self.tr("Iluminación"))
        self._light_group = QActionGroup(menu)
        for key, text in (("studio", self.tr("Estudio")), ("headlamp", self.tr("Linterna")), ("flat", self.tr("Sin sombras"))):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key == "studio")
            action.setData(key)
            self._light_group.addAction(action)
        self._light_group.triggered.connect(lambda action: self.viewer.set_lighting(action.data()))
        menu.addSection(self.tr("Fondo"))
        self._background_group = QActionGroup(menu)
        for key, text in (
            ("theme", self.tr("Degradado")),
            ("document", self.tr("El del documento")),
            ("white", self.tr("Blanco")),
            ("dark", self.tr("Oscuro")),
        ):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setData(key)
            self._background_group.addAction(action)
        self._background_group.triggered.connect(lambda action: self.viewer.set_background(action.data()))
        return menu

    def _build_status(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("StatusBar3D")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.space(4), theme.space(1), theme.space(4), theme.space(1))
        self.info_label = label("", "caption")
        layout.addWidget(self.info_label)
        layout.addStretch(1)
        self.selection_label = label("", "caption")
        layout.addWidget(self.selection_label)
        layout.addSpacing(theme.space(4))
        hint = label(self.tr("Arrastrar: girar · Mayús o botón derecho: desplazar · Rueda: acercar · Doble clic: encuadrar"), "caption")
        layout.addWidget(hint)
        return bar

    def _shortcuts(self) -> None:
        bindings = (
            ("F", lambda: self.viewer.fit()),
            ("Home", self._reset),
            ("H", self._reset),
            ("O", self.projection_button.toggle),
            ("S", self.section_button.toggle),
            ("M", self.measure_button.toggle),
            ("P", self.parts_button.toggle),
            ("Space", self.spin_button.toggle),
            ("F11", self._toggle_fullscreen),
            ("Ctrl+S", self._export_image),
            ("Ctrl+W", self.close),
            ("Escape", self._escape),
        )
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
        for number, mode in enumerate(MODES, 1):
            shortcut = QShortcut(QKeySequence(str(number)), self)
            shortcut.activated.connect(lambda m=mode: self.modes.set_value(m))

    # ======================================================================
    # Carga
    # ======================================================================
    def load(self, target: str, *args) -> None:
        """Decodifica en segundo plano con ``target`` y muestra el resultado."""
        if not opengl_available():
            self._gl_failed("no disponible")
            return
        self.overlay.show_state("box", self.tr("Cargando el modelo 3D"), self.tr("Esto puede tardar con modelos grandes."),
                                True, self.tr("Cancelar"))
        self.overlay.button.setProperty("action", "cancel")
        task = tasks.start(target, *args)
        self._task = task
        task.signals.progress.connect(self._progress)
        task.signals.succeeded.connect(self._loaded)
        task.signals.failed.connect(self._failed)
        task.signals.crashed.connect(self._crashed)
        task.signals.cancelled.connect(self.close)

    def _progress(self, done: int, total: int) -> None:
        if total > 1:
            self.overlay.progress.setRange(0, total)
            self.overlay.progress.setValue(done)

    def _loaded(self, scene: Scene) -> None:
        self._task = None
        self.set_scene(scene)

    def _failed(self, exc) -> None:
        self._task = None
        title, message = errors.user_message(exc)
        self._show_error(title, message)

    def _crashed(self, details: str) -> None:
        self._task = None
        errors.log.error("Visor 3D\n%s", details)
        title, message = errors.user_message(RuntimeError(details))
        self._show_error(title, message)

    def _show_error(self, title: str, message: str) -> None:
        self.overlay.show_state("circle-alert", title, message, False, self.tr("Cerrar"), icons.ROLE_DANGER)
        self.overlay.button.setProperty("action", "close")

    def _gl_failed(self, reason: str) -> None:
        errors.log.warning("OpenGL no disponible: %s", reason)
        self._set_tools_enabled(False)
        self._show_error(
            self.tr("No se puede mostrar en 3D"),
            self.tr("Este equipo no ofrece OpenGL 2.1, que hace falta para dibujar modelos 3D. "
                    "Actualizar el controlador de la tarjeta gráfica suele resolverlo."),
        )

    def _overlay_button(self) -> None:
        if self.overlay.button.property("action") == "cancel" and self._task is not None:
            self._task.cancel()
        else:
            self.close()

    def set_scene(self, scene: Scene) -> None:
        self.scene = scene
        self.overlay.hide()
        self.viewer.set_scene(scene)
        self.parts.set_scene(scene, self.viewer.hidden)
        self.modes.set_value(self.viewer.render_mode if self.viewer.render_mode in MODES else "solid", emit=False)
        if self.viewer.render_mode not in MODES:
            self.viewer.set_render_mode("outline" if self.viewer.render_mode == "solid-wire" else "solid")
            self.modes.set_value(self.viewer.render_mode, emit=False)
        self.projection_button.blockSignals(True)
        self.projection_button.setChecked(self.viewer.camera.orthographic)
        self.projection_button.blockSignals(False)
        if scene.background is not None:
            background = "document"
        elif theme.current().is_dark and scene.dark_fraction() > 0.25:
            # Hecho para fondo blanco (ejes y rótulos negros): como en Acrobat.
            background = "white"
        else:
            background = "theme"
        self.viewer.set_background(background)
        for action in self._background_group.actions():
            action.setChecked(action.data() == background)
            if action.data() == "document":
                action.setEnabled(scene.background is not None)
        self._fill_views()
        self.measure.clear()
        self.measure.invalidate()
        unit = core_measure3d.unit_name(scene.unit_mm) or "mm"
        self.measure.unit = unit
        self.measure_bar.set_unit(unit, scene.unit_mm is not None)
        triangles = scene.triangle_count
        parts = len(scene.parts)
        info = [scene.format]
        info.append(self.tr("{0} triángulos").format(formatting.number(triangles)))
        info.append(self.tr("{0} piezas").format(formatting.number(parts)) if parts != 1 else self.tr("1 pieza"))
        self.info_label.setText("  ·  ".join(info))
        self.parts_button.setChecked(parts > 1 and self.width() > 900)
        self._set_tools_enabled(True)
        self.viewer.setFocus()

    def _fill_views(self) -> None:
        box = self.views_box
        box.clear()
        if self.scene is not None:
            for index, view in enumerate(self.scene.views):
                if view.camera is not None:
                    box.addItem(icons.icon("camera"), view.name or self.tr("Vista {0}").format(index + 1), ("saved", index))
        if box.count():
            box.insertSeparator(box.count())
        for key, text in VIEW_NAMES.items():
            box.addItem(icons.icon("axis-3d"), self.tr(text), ("standard", key))
        default = self.scene.default_view if self.scene is not None else None
        if default is not None:
            for row in range(box.count()):
                if box.itemData(row) == ("saved", default):
                    box.setCurrentIndex(row)
                    break
        else:
            for row in range(box.count()):
                if box.itemData(row) == ("standard", "iso"):
                    box.setCurrentIndex(row)

    def _set_tools_enabled(self, enabled: bool) -> None:
        for widget in (self.views_box, self.home_button, self.fit_button, self.modes, self.projection_button,
                       self.section_button, self.measure_button, self.spin_button, self.light_button,
                       self.export_button, self.parts_button):
            widget.setEnabled(enabled)

    # ======================================================================
    # Acciones
    # ======================================================================
    def _view_chosen(self, row: int) -> None:
        data = self.views_box.itemData(row)
        if not data or self.scene is None:
            return
        kind, value = data
        if kind == "saved":
            self.viewer.apply_view(self.scene.views[value])
            self.parts.sync_hidden(self.viewer.hidden)
        else:
            self.viewer.standard_view(value)
        self.viewer.setFocus()

    def _reset(self) -> None:
        self.viewer.reset()
        self.parts.sync_hidden(self.viewer.hidden)
        self.projection_button.blockSignals(True)
        self.projection_button.setChecked(self.viewer.home_camera.orthographic)
        self.projection_button.blockSignals(False)

    def _toggle_section(self, enabled: bool) -> None:
        self.section_bar.setVisible(enabled)
        if enabled:
            self.section_bar.reposition()
            self.section_bar.raise_()
            self._apply_section()
        else:
            self.viewer.set_section(None)
        if self.measure_bar.isVisible():
            self.measure_bar.reposition(self.section_bar)

    def _toggle_measure(self, enabled: bool) -> None:
        self.measure_bar.setVisible(enabled)
        self.viewer.measure_tool = self.measure if enabled else None
        if enabled:
            self.viewer.setCursor(Qt.CursorShape.CrossCursor)
            self._measure_changed()
            self.measure_bar.reposition(self.section_bar)
            self.measure_bar.raise_()
        else:
            self.measure.cancel()
            self.measure.stop()
            self.viewer.unsetCursor()
        self.viewer.update()

    def _measure_changed(self) -> None:
        if self.measure.notice:
            text = self.measure.notice
        elif self.measure.results and not self.measure.points:
            text = self.measure.detail(self.measure.results[-1])
        else:
            text = measure3d.mode_hints()[self.measure.mode]
        self.measure_bar.clear_button.setEnabled(bool(self.measure.results))
        self.measure_bar.show_text(text)
        if self.measure_bar.isVisible():
            self.measure_bar.reposition(self.section_bar)

    def _apply_section(self) -> None:
        axis, offset, flip = self.section_bar.values()
        self.viewer.set_section(axis, offset, flip)

    def _part_clicked(self, index: int) -> None:
        self.viewer.set_selected(index)
        self.parts.select(index)
        self._show_selection(index)

    def _list_selected(self, index: int) -> None:
        self.viewer.set_selected(index)
        self._show_selection(index)

    def _show_selection(self, index: int) -> None:
        if self.scene is not None and 0 <= index < len(self.scene.parts):
            self.selection_label.setText(self.tr("Seleccionada: {0}").format(self.scene.parts[index].name))
        else:
            self.selection_label.setText("")

    def _export_image(self) -> None:
        if self.scene is None:
            return
        base = Path(self._title).stem or self.tr("modelo")
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Guardar imagen"), str(Path.home() / f"{base}.png"), self.tr("Imagen PNG (*.png)")
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        image = self.viewer.grab_image()
        if not image.save(path, "PNG"):
            errors.show_error(self, OSError(path))

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.full_button.set_icon_name("expand")
        else:
            self.showFullScreen()
            self.full_button.set_icon_name("shrink")

    def _escape(self) -> None:
        if self.isFullScreen():
            self._toggle_fullscreen()
        elif self.measure_button.isChecked():
            # Primero se descarta la medida a medias; con otro Esc, se deja de medir.
            if not self.measure.cancel():
                self.measure_button.setChecked(False)
        elif self.section_button.isChecked():
            self.section_button.setChecked(False)
        elif self.viewer.selected >= 0:
            self._part_clicked(-1)

    # ======================================================================
    # Eventos
    # ======================================================================
    def eventFilter(self, watched, event) -> bool:
        if watched is self.stage and event.type() == QEvent.Type.Resize:
            if self.overlay.isVisible():
                self.overlay.reposition()
            if self.section_bar.isVisible():
                self.section_bar.reposition()
            if self.measure_bar.isVisible():
                self.measure_bar.reposition(self.section_bar)
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.viewer.set_spinning(False)
        self.closed.emit()
        super().closeEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(1180, 760)


def show_window(window: Model3DWindow) -> None:
    """Muestra la ventana centrada sobre la ventana activa."""
    active = QApplication.activeWindow()
    window.show()
    if active is not None and active is not window:
        center = active.frameGeometry().center()
        geometry = window.frameGeometry()
        geometry.moveCenter(center)
        window.move(geometry.topLeft())
    QTimer.singleShot(0, window.raise_)
    QTimer.singleShot(0, window.activateWindow)
