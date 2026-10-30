"""Herramientas de organización: unir, dividir, alternar, extraer y rotar."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QCheckBox, QSpinBox, QWidget

from ...core.document import needs_password, open_pdf
from ...core.errors import BlackPdfError, WrongPasswordError
from ...core.files import unique_path
from ...core.ranges import parse_page_ranges, parse_page_selection
from ..settings import settings
from ..widgets import AutoStack, FileListWidget, PageRangeEdit, SegmentedControl, label, pdf_filter
from .base import OutputPicker, SourcePicker, ToolDialog, ValidationError
from .common import ask_password


def _tr(text: str) -> str:
    return QCoreApplication.translate("Organize", text)


def _page_count_label(path: str) -> str:
    try:
        if needs_password(path):
            return _tr("(protegido)")
        with open_pdf(path) as doc:
            count = doc.page_count
    except BlackPdfError:
        return ""
    return _tr("1 pág.") if count == 1 else _tr("{0} págs.").format(count)


class MergeDialog(ToolDialog):
    icon_name = "combine"
    output_suffix = "unido"

    def title(self):
        return self.tr("Unir PDF")

    def description(self):
        return self.tr("Combina varios PDF en uno solo, en el orden que elijas. Arrastra para reordenar.")

    def action_text(self):
        return self.tr("Unir PDF")

    def build(self):
        self._passwords: dict[str, str] = {}
        self.files = FileListWidget((".pdf",), pdf_filter(), self, describe=_page_count_label)
        self.files.changed.connect(self._files_changed)
        self.add_field(self.tr("Archivos"), self.files)
        self.bookmarks = QCheckBox(self.tr("Crear un marcador por cada archivo"))
        self.bookmarks.setChecked(True)
        self.form.addWidget(self.bookmarks)
        self.output = OutputPicker("save", pdf_filter(), self)
        self.add_field(self.tr("Guardar como"), self.output)
        if self.context is not None and self.context.path:
            self.files.add_files([self.context.path])
        for path in self.preset.get("files", []):
            self.files.add_files([path])

    def _files_changed(self):
        files = self.files.files()
        if files:
            first = Path(files[0])
            self.output.suggest(str(unique_path(first.parent / f"{first.stem}_{self.output_suffix}.pdf")))
        self.refresh()

    def is_ready(self):
        return len(self.files.files()) >= 2

    def job(self):
        files = self.files.files()
        if len(files) < 2:
            raise ValidationError(self.tr("Añade al menos dos archivos."))
        output = self.require_output(self.output, files)
        passwords = {}
        for index, path in enumerate(files):
            if needs_password(path):
                password = self._passwords.get(path) or self._ask(path)
                if password is None:
                    raise ValidationError(self.tr("Falta la contraseña de «{0}».").format(Path(path).name))
                passwords[index] = password
        settings().last_directory = output
        return "blackpdf.core.merge:merge_pdfs", (files, output), {
            "add_bookmarks": self.bookmarks.isChecked(),
            "passwords": passwords,
        }

    def _ask(self, path: str) -> str | None:
        retry = False
        while True:
            password = ask_password(self, path, retry)
            if password is None:
                return None
            try:
                open_pdf(path, password).close()
            except WrongPasswordError:
                retry = True
                continue
            self._passwords[path] = password
            return password


class _SingleSourceDialog(ToolDialog):
    """Herramienta con un PDF de entrada y un archivo de salida."""

    def add_source(self, title: str = "") -> SourcePicker:
        self.source = SourcePicker(self.context, self, title)
        self.source.changed.connect(self._source_changed)
        self.add_field(self.tr("Documento"), self.source)
        return self.source

    def add_output(self, mode: str = "save", title: str = "") -> OutputPicker:
        self.output = OutputPicker(mode, pdf_filter(), self)
        self.add_field(title or (self.tr("Guardar como") if mode == "save" else self.tr("Carpeta de destino")), self.output)
        return self.output

    def _source_changed(self):
        if self.source.is_ready():
            if self.output.mode == "save":
                self.output.suggest(self.default_output(self.source))
            else:
                self.output.suggest(self.default_folder(self.source))
        self.on_source_changed()
        self.refresh()

    def on_source_changed(self):
        pass

    def finish_build(self):
        if self.source.is_ready():
            self._source_changed()

    def is_ready(self):
        return self.source.is_ready()

    def common_kwargs(self) -> dict:
        return {"password": self.source.source_password()}

    def require_source(self):
        if not self.source.is_ready():
            raise ValidationError(self.tr("Elige un documento."))
        return self.source.value()


class SplitDialog(_SingleSourceDialog):
    icon_name = "scissors"
    output_mode = "folder"
    output_suffix = "partes"

    def title(self):
        return self.tr("Dividir PDF")

    def description(self):
        return self.tr("Separa un PDF en varios archivos por rangos, cada cierto número de páginas o página a página.")

    def action_text(self):
        return self.tr("Dividir PDF")

    def build(self):
        self.add_source()
        self.mode = SegmentedControl(
            [("ranges", self.tr("Por rangos")), ("every", self.tr("Cada N páginas")), ("each", self.tr("Una por archivo"))]
        )
        self.add_field(self.tr("Modo"), self.mode)
        self.options = AutoStack()
        self.ranges = PageRangeEdit(allow_empty=False)
        self.ranges.setText(self.preset.get("pages", ""))
        self.ranges.validityChanged.connect(lambda _: self.refresh())
        self.ranges.textChanged.connect(lambda _: self.refresh())
        ranges_box = self.field_block(self.tr("Rangos"), self.ranges,
                                      self.tr("Cada rango genera un archivo. Ejemplo: 1-3, 5, 8-10."))
        self.every = QSpinBox()
        self.every.setRange(1, 10000)
        self.every.setValue(2)
        self.every.setSuffix(self.tr(" páginas"))
        every_box = self.field_block(self.tr("Páginas por archivo"), self.every)
        each_box = QWidget()
        self.options.addWidget(ranges_box)
        self.options.addWidget(every_box)
        self.options.addWidget(each_box)
        self.mode.changed.connect(lambda key: (self.options.setCurrentIndex(["ranges", "every", "each"].index(key)), self.refresh()))
        self.form.addWidget(self.options)
        self.add_output("folder")
        self.finish_build()

    def on_source_changed(self):
        self.ranges.set_page_count(self.source.page_count)
        self.every.setMaximum(max(1, self.source.page_count))

    def is_ready(self):
        if not self.source.is_ready():
            return False
        if self.mode.value() == "ranges":
            return bool(self.ranges.text().strip()) and self.ranges.is_valid()
        return True

    def job(self):
        source = self.require_source()
        folder = self.output.path()
        if not folder:
            raise ValidationError(self.tr("Elige la carpeta de destino."))
        kwargs = {"base_name": self.source.stem(), **self.common_kwargs()}
        mode = self.mode.value()
        if mode == "ranges":
            return "blackpdf.core.split:split_by_ranges", (source, self.ranges.text(), folder), kwargs
        if mode == "every":
            return "blackpdf.core.split:split_every", (source, self.every.value(), folder), kwargs
        return "blackpdf.core.split:split_each_page", (source, folder), kwargs


class AlternateDialog(_SingleSourceDialog):
    icon_name = "shuffle"
    output_suffix = "alternado"

    def title(self):
        return self.tr("Alternar páginas")

    def description(self):
        return self.tr(
            "Intercala las páginas de dos PDF: 1 del primero, 1 del segundo... Ideal para escaneos a doble cara."
        )

    def action_text(self):
        return self.tr("Alternar")

    def build(self):
        self.source = SourcePicker(self.context, self, self.tr("Primer PDF (caras delanteras)"))
        self.source.changed.connect(self._source_changed)
        self.add_field(self.tr("Primer documento"), self.source)
        self.second = SourcePicker(None, self, self.tr("Segundo PDF (caras traseras)"))
        self.second.changed.connect(self.refresh)
        self.add_field(self.tr("Segundo documento"), self.second)
        self.reverse = QCheckBox(self.tr("Invertir el orden del segundo documento"))
        self.reverse.setChecked(True)
        self.add_field("", self.reverse, self.tr(
            "Actívalo si escaneaste las caras traseras dando la vuelta al taco de hojas: "
            "la última página escaneada es la trasera de la primera."
        ))
        self.add_output()
        self.finish_build()

    def is_ready(self):
        return self.source.is_ready() and self.second.is_ready()

    def job(self):
        first = self.require_source()
        if not self.second.is_ready():
            raise ValidationError(self.tr("Elige el segundo documento."))
        output = self.require_output(self.output, [self.source.original_path(), self.second.original_path()])
        return "blackpdf.core.alternate:alternate_pdfs", (first, self.second.value(), output), {
            "reverse_b": self.reverse.isChecked(),
            "password_a": self.source.source_password(),
            "password_b": self.second.source_password(),
        }


class ExtractDialog(_SingleSourceDialog):
    icon_name = "file-output"
    output_suffix = "extracto"

    def title(self):
        return self.tr("Extraer páginas")

    def description(self):
        return self.tr("Crea un PDF nuevo solo con las páginas que elijas, en el orden que las escribas, "
                       "o un PDF aparte por cada página o cada rango.")

    def action_text(self):
        return self.tr("Extraer")

    def build(self):
        self.add_source()
        self.ranges = PageRangeEdit(allow_empty=False)
        self.ranges.setText(self.preset.get("pages", ""))
        self.ranges.textChanged.connect(lambda _: self._update_hint())
        self.add_field(self.tr("Páginas"), self.ranges)
        self.each_page = QCheckBox(self.tr("Extraer cada página en un PDF aparte"))
        self.each_range = QCheckBox(self.tr("Un PDF por cada rango"))
        self.add_field("", self.each_page)
        self.add_field("", self.each_range)
        self.hint = label("", "caption", wrap=True)
        self.form.addWidget(self.hint)
        self.each_page.toggled.connect(self._mode_changed)
        self.each_range.toggled.connect(self._mode_changed)
        self.output_file = self.add_output()
        self.output_file_block = self.output_file.parentWidget()
        self.output_folder = OutputPicker("folder", pdf_filter(), self)
        self.output_folder_block = self.add_field(self.tr("Carpeta de destino"), self.output_folder)
        self._mode_changed()
        self.finish_build()

    def _separate(self) -> str:
        """«page», «range» o vacío (todo en un solo PDF)."""
        if self.each_page.isChecked():
            return "page"
        return "range" if self.each_range.isChecked() else ""

    def _mode_changed(self, *_args):
        mode = self._separate()
        # Página a página, el rango ya no cuenta: cada página es su propio archivo.
        self.each_range.setEnabled(not self.each_page.isChecked())
        self.ranges.allow_empty = mode == "page"
        self.ranges.setPlaceholderText(self.tr("Todas las páginas") if mode == "page" else self.tr("Ejemplo: 1-3, 5, 8-10"))
        self.ranges.set_page_count(self.ranges.page_count)
        self.output_file_block.setVisible(not mode)
        self.output_folder_block.setVisible(bool(mode))
        self._update_hint()

    def _update_hint(self):
        mode = self._separate()
        if mode == "page":
            hint = self.tr("Un PDF por cada página: 12 páginas, 12 archivos. Déjalo vacío para extraerlas todas.")
        elif mode == "range":
            hint = self.tr("Un PDF por cada rango: «1-3, 5-6» da dos archivos, uno de la 1 a la 3 y otro de la 5 a la 6.")
        else:
            hint = self.tr("Todo en un solo PDF. Ejemplo: 1-3, 5, 8-10. El orden se respeta.")
        count = self._file_count()
        if count:
            hint += " " + (self.tr("Se creará 1 archivo.") if count == 1 else self.tr("Se crearán {0} archivos.").format(count))
        self.hint.setText(hint)
        self.refresh()

    def _file_count(self) -> int:
        """Archivos que saldrán con el documento elegido (0 si aún no se sabe)."""
        mode, spec, pages = self._separate(), self.ranges.text(), self.source.page_count
        if not mode or pages <= 0 or not self.ranges.is_valid():
            return 0
        try:
            if mode == "page":
                return len(parse_page_selection(spec, pages, allow_empty_all=True))
            return len(parse_page_ranges(spec, pages)) if spec.strip() else 0
        except BlackPdfError:
            return 0

    def on_source_changed(self):
        self.ranges.set_page_count(self.source.page_count)
        self._update_hint()
        if self.source.is_ready():
            self.output_folder.suggest(self.default_folder(self.source, "paginas"))

    def is_ready(self):
        if not self.source.is_ready() or not self.ranges.is_valid():
            return False
        return self._separate() == "page" or bool(self.ranges.text().strip())

    def job(self):
        source = self.require_source()
        mode = self._separate()
        if not mode:
            output = self.require_output(self.output_file, [self.source.original_path()])
            return "blackpdf.core.split:extract_pages", (source, self.ranges.text(), output), self.common_kwargs()
        folder = self.output_folder.path()
        if not folder:
            raise ValidationError(self.tr("Elige la carpeta de destino."))
        kwargs = {"each": mode, "base_name": self.source.stem(), **self.common_kwargs()}
        return "blackpdf.core.split:extract_pages_separately", (source, self.ranges.text(), folder), kwargs


class RotateDialog(_SingleSourceDialog):
    icon_name = "rotate-cw"
    output_suffix = "girado"

    def title(self):
        return self.tr("Rotar PDF")

    def description(self):
        return self.tr("Gira todas las páginas o solo las que indiques.")

    def action_text(self):
        return self.tr("Rotar")

    def build(self):
        self.add_source()
        self.angle = SegmentedControl(
            [("90", self.tr("90° a la derecha")), ("180", self.tr("180°")), ("270", self.tr("90° a la izquierda"))],
            icons_map={"90": "rotate-cw", "270": "rotate-ccw"},
        )
        self.add_field(self.tr("Giro"), self.angle)
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.setText(self.preset.get("pages", ""))
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para girar todas."))
        self.add_output()
        self.finish_build()

    def on_source_changed(self):
        self.ranges.set_page_count(self.source.page_count)

    def is_ready(self):
        return self.source.is_ready() and self.ranges.is_valid()

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.pages:rotate_pdf", (source, output, int(self.angle.value()), self.ranges.text()), \
            self.common_kwargs()
