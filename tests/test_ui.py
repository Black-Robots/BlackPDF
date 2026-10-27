"""Pruebas de humo de la interfaz (sin pantalla): flujos reales con ratón simulado."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QLocale, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from .conftest import build_pdf, page_labels


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    settings_dir = tmp_path_factory.mktemp("settings")
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    from blackpdf.ui.settings import settings

    # Los tests nunca se conectan a internet.
    settings().check_updates = False
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    from blackpdf.ui import theme

    theme.load_fonts()
    theme.manager().apply("dark")
    yield app


def pump(app, seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, condition, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(qapp):
    from blackpdf.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(qapp, 0.2)
    yield win
    for tab in win.document_tabs():
        tab.document._modified = False
    win.close()
    win.deleteLater()
    pump(qapp, 0.05)


@pytest.fixture
def opened(qapp, window, tmp_path):
    path = tmp_path / "doc.pdf"
    doc = build_pdf(4)
    doc.save(path)
    window.open_files([str(path)])
    pump(qapp, 0.4)
    tab = window.current_tab()
    assert tab is not None
    return window, tab, path


def mouse(widget, kind, pos: QPointF, buttons=Qt.MouseButton.LeftButton):
    event_type = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                  "release": QEvent.Type.MouseButtonRelease}[kind]
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == "release" else buttons
    event = QMouseEvent(event_type, pos, widget.mapToGlobal(pos.toPoint()).toPointF(), button, held,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def drag(viewer, page: int, start_pt: QPointF, end_pt: QPointF, steps: int = 8) -> None:
    viewport = viewer.viewport()
    a = viewer.page_to_viewport(page, start_pt)
    b = viewer.page_to_viewport(page, end_pt)
    mouse(viewport, "press", a)
    for i in range(1, steps + 1):
        t = i / steps
        mouse(viewport, "move", QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t))
    mouse(viewport, "release", b)


def annots(tab, page=0) -> list[str]:
    """Tipos de anotación de la página (se conserva la página mientras se leen)."""
    pg = tab.document.doc[page]
    return [a.type[1] for a in pg.annots()]


def test_open_document_creates_tab(opened):
    window, tab, path = opened
    assert tab.document.page_count == 4
    assert window.tab_bar.count() == 2
    window.open_files([str(path)])
    assert window.tab_bar.count() == 2, "abrir el mismo archivo reutiliza la pestaña"


def test_page_operations_with_undo(qapp, opened):
    window, tab, _ = opened
    tab.thumbnails.select_pages([0, 1])
    tab.rotate_pages(90)
    assert [p.rotation for p in tab.document.doc][:2] == [90, 90]
    tab.undo()
    assert [p.rotation for p in tab.document.doc][:2] == [0, 0]
    tab.redo()
    assert tab.document.doc[0].rotation == 90
    tab.thumbnails.select_pages([3])
    tab.delete_pages()
    assert tab.document.page_count == 3
    tab.thumbnails.select_pages([0])
    tab.duplicate_pages()
    assert tab.document.page_count == 4
    tab.move_pages([0], 4)
    assert page_labels(tab.document.doc)[-1] == "P1"
    tab.insert_blank(after=True)
    assert tab.document.page_count == 5
    assert tab.document.is_modified


def test_thumbnail_drop_reorders(qapp, opened):
    _, tab, _ = opened
    tab.thumb_model.movePages.emit([3], 0)
    pump(qapp, 0.05)
    assert page_labels(tab.document.doc) == ["P4", "P1", "P2", "P3"]


def _drop(view, mime, pos):
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

    viewport = view.viewport()
    point = QPointF(pos)
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    for cls in (QDragEnterEvent, QDragMoveEvent):
        QApplication.sendEvent(viewport, cls(pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(viewport, QDropEvent(point, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_drag_thumbnail_to_new_position(qapp, opened):
    _, tab, _ = opened
    view = tab.thumbnails
    model = tab.thumb_model
    pump(qapp, 0.2)
    mime = model.mimeData([model.index(3, 0)])
    first = view.visualRect(model.index(0, 0))
    _drop(view, mime, QPoint(first.center().x(), first.top() + 4))
    assert page_labels(tab.document.doc) == ["P4", "P1", "P2", "P3"]
    mime = model.mimeData([model.index(0, 0), model.index(1, 0)])
    last = view.visualRect(model.index(3, 0))
    _drop(view, mime, QPoint(last.center().x(), last.bottom() - 4))
    assert page_labels(tab.document.doc) == ["P2", "P3", "P4", "P1"]


def test_drop_pdf_file_on_thumbnails_inserts_pages(qapp, opened, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    _, tab, _ = opened
    other = tmp_path / "otro.pdf"
    build_pdf(2, prefix="Q").save(other)
    view = tab.thumbnails
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other))])
    second = view.visualRect(tab.thumb_model.index(1, 0))
    _drop(view, mime, QPoint(second.center().x(), second.top() + 4))
    doc = tab.document.doc
    assert doc.page_count == 6
    assert doc[1].get_text().startswith("Q1") and doc[2].get_text().startswith("Q2")
    tab.undo()
    assert tab.document.page_count == 4


def test_pen_and_shapes_create_annotations(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    tab.set_tool("pen")
    drag(viewer, 0, QPointF(100, 300), QPointF(250, 360))
    assert annots(tab) == ["Ink"]
    tab.set_tool("rect")
    drag(viewer, 0, QPointF(100, 400), QPointF(220, 480))
    tab.set_tool("arrow")
    drag(viewer, 0, QPointF(300, 400), QPointF(420, 480))
    assert annots(tab) == ["Ink", "Square", "Line"]
    tab.undo()
    assert len(annots(tab)) == 2


def test_highlight_selected_text(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("highlight")
    drag(tab.viewer, 0, QPointF(74, 134), QPointF(240, 136))
    assert annots(tab) == ["Highlight"]


def test_eraser_removes_annotation(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("rect")
    drag(tab.viewer, 0, QPointF(100, 400), QPointF(220, 480))
    assert len(annots(tab)) == 1
    tab.set_tool("eraser")
    pos = tab.viewer.page_to_viewport(0, QPointF(160, 440))
    mouse(tab.viewer.viewport(), "move", pos, Qt.MouseButton.NoButton)
    mouse(tab.viewer.viewport(), "press", pos)
    mouse(tab.viewer.viewport(), "release", pos)
    assert len(annots(tab)) == 0


def test_text_selection_and_copy(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("select")
    drag(tab.viewer, 0, QPointF(70, 95), QPointF(300, 140))
    text = tab.viewer.selected_text()
    assert "P1" in text and "texto" in text
    tab.copy()
    assert "P1" in QApplication.clipboard().text()


def test_save_keeps_annotations(qapp, opened, tmp_path):
    _, tab, path = opened
    tab.set_tool("pen")
    drag(tab.viewer, 0, QPointF(100, 300), QPointF(250, 360))
    assert tab._save_to(str(path))
    assert not tab.document.is_modified
    saved = pymupdf.open(path)
    assert len(list(saved[0].annots())) == 1


def test_search_finds_all_pages(qapp, opened):
    _, tab, _ = opened
    tab.find()
    tab.search_controller.start("ejemplo")
    assert wait_until(qapp, lambda: len(tab.search_controller.hits) == 4, 5)
    tab.search_controller.next()
    assert tab.search_controller.current == 1


def test_zoom_and_fit(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    viewer.set_zoom(2.0)
    assert viewer.zoom == pytest.approx(2.0)
    viewer.zoom_out()
    assert viewer.zoom < 2.0
    viewer.set_fit_mode("page")
    pump(qapp, 0.1)
    rect = viewer.page_rect(viewer.current_page)
    assert rect.height() <= viewer.viewport().height()


def test_tool_dialog_runs_in_background(qapp, opened, tmp_path):
    window, tab, _ = opened
    from blackpdf.ui import tools

    dialog = tools.dialog_class("compress")(window, window._context_for(tab), {})
    output = tmp_path / "comprimido.pdf"
    dialog.output.picker.set_path(str(output))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 60)
    assert output.exists() and pymupdf.open(output).page_count == 4
    dialog.close()


def test_tool_dialog_reports_errors(qapp, opened, tmp_path, monkeypatch):
    window, tab, _ = opened
    from blackpdf.ui import tools
    from blackpdf.ui.dialogs import base

    shown = []
    monkeypatch.setattr(base, "show_error", lambda parent, exc: shown.append(exc))
    dialog = tools.dialog_class("extract")(window, window._context_for(tab), {})
    dialog.ranges.setText("1-2")
    dialog.ranges.page_count = 0  # salta la validación del formulario para forzar el error en el subproceso
    dialog.ranges.setText("9")
    dialog.output.picker.set_path(str(tmp_path / "x.pdf"))
    dialog.run()
    assert wait_until(qapp, lambda: bool(shown), 60)
    assert type(shown[0]).__name__ == "InvalidRangeError"
    dialog.close()


def test_command_palette_scoring():
    from blackpdf.ui.command_palette import Command, score

    merge = Command("Unir PDF", "Herramientas", "combine", lambda: None)
    compress = Command("Comprimir PDF", "Herramientas", "shrink", lambda: None)
    assert score("unir", merge) > score("unir", compress)
    assert score("cmprm", compress) > 0
    assert score("xyz", merge) == 0


def test_palette_opens_and_runs(qapp, opened):
    window, tab, _ = opened
    window.show_palette()
    pump(qapp, 0.05)
    window.palette_overlay.edit.setText("girar páginas a la derecha")
    item = window.palette_overlay.list.item(0)
    window.palette_overlay._run_item(item)
    assert tab.document.doc[0].rotation == 90


def test_home_tools_and_theme(qapp, window):
    from blackpdf.ui import theme

    window.toggle_theme()
    assert not theme.current().is_dark
    window.toggle_theme()
    assert theme.current().is_dark
    assert window.home.recent_grid is not None


def test_organizer_mode_reorders_and_acts(qapp, opened):
    _, tab, _ = opened
    tab.set_organize_mode(True)
    pump(qapp, 0.3)
    assert tab.view_stack.currentWidget() is tab.organizer_panel
    view = tab.organizer
    model = tab.thumb_model
    mime = model.mimeData([model.index(0, 0)])
    last = view.visualRect(model.index(3, 0))
    _drop(view, mime, QPoint(last.right() - 4, last.center().y()))
    assert page_labels(tab.document.doc) == ["P2", "P3", "P4", "P1"]
    view.rotateRequested.emit([1], 90)
    assert tab.document.doc[1].rotation == 90
    view.select_pages([0, 1])
    tab.delete_pages()
    assert tab.document.page_count == 2
    view.openPage.emit(1)
    assert tab.view_stack.currentWidget() is tab.view_splitter
    assert tab.viewer.current_page == 1


def test_other_views_leave_the_grid(qapp, opened):
    """En la cuadrícula los demás botones siguen activos: al pulsarlos se vuelve al visor."""
    _, tab, _ = opened

    def grid():
        tab.set_organize_mode(True)
        pump(qapp, 0.1)
        assert tab.zoom_in_button.isEnabled() and tab.split_button.isEnabled()

    def in_viewer():
        return tab.view_stack.currentWidget() is tab.view_splitter and not tab.organize_button.isChecked()

    grid()
    tab.fit_width_button.click()
    assert in_viewer() and tab.viewer.fit_mode == "width"
    grid()
    box = tab.organizer.organizer_delegate.box
    tab.zoom_in_button.click()
    assert not in_viewer() and tab.organizer.organizer_delegate.box > box, "en la cuadrícula agranda las miniaturas"
    zoom = tab.viewer.zoom
    tab.set_organize_mode(False)
    tab.zoom_in_button.click()
    assert tab.viewer.zoom > zoom
    grid()
    tab.zoom_box.activated.emit(tab.zoom_box.findData(1.5))
    assert in_viewer() and abs(tab.viewer.zoom - 1.5) < 1e-6
    grid()
    tab.split_button.click()
    pump(qapp, 0.1)
    assert in_viewer() and tab.split_pane is not None
    grid()
    tab.split_button.click()
    assert in_viewer() and tab.split_pane is not None, "ya estaba dividida: sigue dividida"
    tab.set_split(False)
    sidebar_open = tab.splitter.sizes()[0] > 20
    grid()
    tab.sidebar_button.click()
    pump(qapp, 0.4)
    assert in_viewer() and tab.splitter.sizes()[0] > 20 and tab.sidebar_button.isChecked()
    assert sidebar_open or tab.splitter.sizes()[0] > 20
    grid()
    tab.search_button.click()
    assert in_viewer() and tab.search_bar.isVisible()
    tab.search_bar.close_bar()


def test_redact_tool_removes_content(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("redact")
    drag(tab.viewer, 0, QPointF(60, 120), QPointF(400, 150))
    text = tab.document.doc[0].get_text()
    assert "ejemplo" not in text and "P1" in text
    tab.undo()
    assert "ejemplo" in tab.document.doc[0].get_text()


def test_navigation_history(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    viewer.go_to_page(0, smooth=False)
    pump(qapp, 0.05)
    viewer.jump_to_page(3)
    pump(qapp, 0.3)
    assert viewer.current_page == 3
    assert viewer.go_back()
    pump(qapp, 0.3)
    assert viewer.current_page == 0
    assert viewer.go_forward()
    assert viewer.current_page == 3


def test_reading_position_is_restored(qapp, window, tmp_path):
    from blackpdf.ui.settings import settings

    path = tmp_path / "largo.pdf"
    build_pdf(6).save(path)
    settings().set_reading_position(str(path), 4, 1.0, "auto")
    window.open_files([str(path)])
    pump(qapp, 0.5)
    tab = window.current_tab()
    assert tab.viewer.current_page == 4
    tab.viewer.go_to_page(2, smooth=False)
    pump(qapp, 0.1)
    window.close_current_tab()
    assert settings().reading_position(str(path))["page"] == 2


def test_comments_panel_flow(qapp, opened):
    from blackpdf.core.comments import list_comments

    _, tab, _ = opened
    tab.show_comments()
    pump(qapp, 0.2)
    panel = tab.comments
    panel.start_page_comment()
    pump(qapp, 0.05)
    panel._composer.edit.setPlainText("Revisar esta página")
    panel._composer._submit()
    pump(qapp, 0.3)
    comments = list_comments(tab.document.doc)
    assert len(comments) == 1 and comments[0].content == "Revisar esta página"
    assert tab.comments_count.text() == "1"
    card = panel.body.findChildren(type(panel).__mro__[0].__module__ and __import__("blackpdf.ui.comments", fromlist=["CommentCard"]).CommentCard)[0]
    card.replyRequested.emit(card.comment, "Hecho")
    pump(qapp, 0.3)
    assert list_comments(tab.document.doc)[0].replies[0].content == "Hecho"
    card = panel.body.findChildren(__import__("blackpdf.ui.comments", fromlist=["CommentCard"]).CommentCard)[0]
    card.deleteRequested.emit(card.comment)
    pump(qapp, 0.3)
    assert list_comments(tab.document.doc) == []
    tab.undo()
    assert len(list_comments(tab.document.doc)) == 1


def test_highlight_and_comment_opens_editor(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("select")
    drag(tab.viewer, 0, QPointF(74, 134), QPointF(240, 136))
    tab._markup_selection("highlight", comment=True)
    pump(qapp, 0.4)
    assert tab.comments_button.isChecked()
    from blackpdf.ui.comments import CommentCard

    cards = tab.comments.body.findChildren(CommentCard)
    assert cards and cards[0].comment.kind == "Highlight"
    assert cards[0]._composer is not None, "se abre el editor del comentario"


def test_comments_panel_on_the_right(qapp, opened):
    """Los comentarios tienen su propio panel a la derecha y se muestran desde la barra de herramientas."""
    window, tab, _ = opened
    assert tab.splitter.widget(2) is tab.comments_frame
    assert tab.comments.parent() is tab.comments_frame
    assert tab.sidebar_switch.value() in ("pages", "outline")
    tab.set_comments_visible(False)
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[2] == 0 and not tab.comments_button.isChecked()
    tab.comments_button.click()
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[2] >= 240 and tab.comments_button.isChecked()
    assert tab.splitter.sizes()[0] > 0, "el panel lateral sigue abierto"
    tab.set_organize_mode(True)
    pump(qapp, 0.1)
    assert tab.splitter.sizes()[2] == 0
    tab.set_organize_mode(False)
    pump(qapp, 0.1)
    assert tab.splitter.sizes()[2] >= 240
    tab.toggle_comments()
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[2] == 0


def test_open_and_save_buttons_have_menus(qapp, opened):
    window, tab, path = opened
    window._fill_open_menu()
    texts = [a.text() for a in window.open_button.menu.actions()]
    assert "Abrir..." in texts and path.name in texts
    assert [a.text() for a in tab.save_button.menu.actions()] == ["Guardar como..."]
    assert not hasattr(window, "theme_button")


def test_split_view_same_and_other_document(qapp, window, tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    build_pdf(8).save(first)
    build_pdf(8, prefix="Q").save(second)
    window.open_files([str(second)])
    window.open_files([str(first)])
    pump(qapp, 0.4)
    tab = window.current_tab()
    tab.set_split(True)
    pump(qapp, 0.3)
    pane = tab.split_pane
    assert pane is not None and pane.model is tab.document
    assert tab.split_button.isChecked()
    names = [pane.chooser.itemText(i) for i in range(pane.chooser.count())]
    assert "b.pdf" in names
    other = next(m for _, m in window._open_documents() if m is not tab.document)
    pane.set_document(other)
    pump(qapp, 0.2)
    assert pane.model is other
    # sincronizado: mover el principal mueve el secundario manteniendo la diferencia de páginas
    pane.viewer.set_position(1, 0)
    tab.viewer.set_position(0, 0)
    pump(qapp, 0.05)
    pane.set_sync(True)
    assert pane._page_delta == 1
    tab.viewer.set_position(3, 0)
    pump(qapp, 0.05)
    assert pane.viewer.position()[0] == 4
    # cerrar la otra pestaña devuelve el panel a este documento
    other_tab = next(t for t in window.document_tabs() if t.document is other)
    window.close_tab(window._tabs.index(other_tab))
    pump(qapp, 0.2)
    assert pane.model is tab.document
    tab.set_split(False)
    assert tab.split_pane is None and not tab.split_button.isChecked()


def test_comment_flash_paints(qapp, opened):
    from PySide6.QtCore import QRectF

    _, tab, _ = opened
    tab._show_comment(0, QRectF(100, 100, 50, 30))
    pump(qapp, 0.1)
    image = tab.viewer.viewport().grab()
    assert not image.isNull()


def test_signed_document_banner_and_safe_save(qapp, window, tmp_path):
    from tests.test_signatures import PASSWORD, make_certificate
    from blackpdf.core.signatures import SignOptions, list_signatures, sign_pdf

    plain = tmp_path / "contrato.pdf"
    build_pdf(2).save(plain)
    signed = tmp_path / "contrato_firmado.pdf"
    sign_pdf(plain, signed, make_certificate(tmp_path / "c.p12"), PASSWORD, SignOptions())
    original = signed.read_bytes()
    window.open_files([str(signed)])
    tab = window.current_tab()
    assert wait_until(qapp, lambda: bool(tab._signatures), 60)
    assert tab.signature_banner.isVisible()
    assert tab.signature_banner.property("level") == "warning", "certificado de prueba: íntegro pero no de confianza"
    assert tab.save() and signed.read_bytes() == original, "guardar sin cambios no toca el archivo"
    tab.rotate_pages(90)
    assert "se añadirán al final" in tab.signature_banner.text.text()
    tab.undo()

    # Comentar y guardar: los cambios se añaden detrás de lo firmado y la firma los permite.
    tab.document.edit("Nota", lambda doc: doc[0].add_text_annot((100, 100), "Visto bueno"), pages=[0])
    assert tab.save(), "sin preguntar: las firmas siguen valiendo"
    saved = signed.read_bytes()
    assert saved.startswith(original) and saved != original
    status = list_signatures(signed)[0]
    assert status.intact and status.comments_added
    assert wait_until(qapp, lambda: bool(tab._signatures), 60)

    # Censurar no puede dejar el texto en la revisión firmada: obliga a reescribir y se avisa.
    from blackpdf.core.annotations import redact_areas

    tab._edit("Censurar", lambda doc: redact_areas(doc[1], [pymupdf.Rect(60, 60, 300, 160)]), [1], rewrite=True)
    assert not tab.document.saves_incrementally
    assert "Has modificado" in tab.signature_banner.text.text()
    tab.undo()
    tab.document._modified = False
    assert list_signatures(signed)[0].intact


def test_certificate_dialog_signs_current_document(qapp, opened, tmp_path):
    from tests.test_signatures import PASSWORD, make_certificate
    from blackpdf.core.signatures import list_signatures
    from blackpdf.ui import tools

    window, tab, _ = opened
    cert = make_certificate(tmp_path / "yo.p12")
    dialog = tools.dialog_class("sign_certificate")(window, window._context_for(tab), {})
    dialog.certificate.set_path(str(cert))
    dialog.cert_password.edit.setText(PASSWORD)
    dialog._check_certificate()
    assert dialog.is_ready() and "GARCÍA" in dialog.cert_text.text()
    output = tmp_path / "firmado.pdf"
    dialog.output.picker.set_path(str(output))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 90)
    status = list_signatures(output)[0]
    assert status.intact and status.valid and status.covers_whole_file
    dialog.close()


# ---------------------------------------------------------------------------
# Modelos 3D
# ---------------------------------------------------------------------------
MODEL_DATA = os.path.join(os.path.dirname(__file__), "data", "model3d")


def test_3d_model_on_page_opens_viewer_window(qapp, window):
    window.open_files([os.path.join(MODEL_DATA, "views.pdf")])
    pump(qapp, 0.4)
    tab = window.current_tab()
    models = tab.viewer.models(0)
    assert len(models) == 1
    item, rect = models[0]
    assert item.format == "U3D"
    requested = []
    tab.viewer.modelRequested.connect(requested.append)
    center = tab.viewer.page_to_viewport(0, rect.center())
    assert tab.viewer.activate_at(center)
    assert requested and requested[0].xref == item.xref
    pump(qapp, 0.2)
    windows = list(tab._model_windows.values())
    assert len(windows) == 1
    model_window = windows[0]
    # Sin pantalla no hay OpenGL: la ventana lo explica en lugar de fallar.
    assert model_window.overlay.isVisible()
    model_window.close()
    pump(qapp, 0.1)
    assert not tab._model_windows


def test_model3d_window_controls(qapp):
    from blackpdf.core import model3d
    from blackpdf.ui.model3d_window import Model3DWindow

    scene = model3d.load_file(os.path.join(MODEL_DATA, "VertexColor.u3d"))
    win = Model3DWindow("VertexColor.u3d")
    win.resize(1200, 760)
    win.show()
    win.set_scene(scene)
    pump(qapp, 0.1)
    assert win.parts.list.count() == len(scene.parts) == 3
    labels = [win.views_box.itemText(i) for i in range(win.views_box.count())]
    assert "Isométrica" in labels and "Frente" in labels
    for mode in ("outline", "wireframe", "transparent", "solid"):
        win.modes.set_value(mode)
        assert win.viewer.render_mode == mode
    win.section_button.setChecked(True)
    assert win.viewer.section_axis is not None
    win.section_button.setChecked(False)
    assert win.viewer.section_axis is None
    win.parts.select(1)
    win.parts._isolate_current()
    assert win.viewer.hidden == {0, 2}
    win.parts.set_all_visible(True)
    assert not win.viewer.hidden
    win.projection_button.setChecked(True)
    assert win.viewer.camera.orthographic
    win.close()
    pump(qapp, 0.05)


def test_open_model_file_from_main_window(qapp, window):
    tabs_before = window.tab_bar.count()
    window.open_files([os.path.join(MODEL_DATA, "box.u3d")])
    pump(qapp, 0.2)
    assert window.tab_bar.count() == tabs_before
    assert len(window._model_windows) == 1
    for win in list(window._model_windows.values()):
        win.close()
    pump(qapp, 0.05)


def test_closed_tab_ignores_theme_changes(qapp, opened):
    """Al cerrar una pestaña, cambiar el tema no debe tocar sus paneles destruidos."""
    from blackpdf.ui import theme

    window, tab, _ = opened
    tab.show_comments()
    pump(qapp, 0.1)
    errors = []
    previous = sys.excepthook
    sys.excepthook = lambda *info: errors.append(info)
    try:
        window.close_tab(window._tabs.index(tab))
        pump(qapp, 0.1)
        # Como en el bucle principal: las vistas de la pestaña se destruyen de verdad.
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pump(qapp, 0.1)
        theme.manager().apply("light")
        pump(qapp, 0.1)
        theme.manager().apply("dark")
        pump(qapp, 0.1)
    finally:
        sys.excepthook = previous
    assert not errors


def test_welcome_dialog_applies_language_and_theme(qapp):
    from blackpdf.ui import i18n, theme
    from blackpdf.ui.dialogs.welcome import WelcomeDialog
    from blackpdf.ui.settings import settings

    previous = settings().language, settings().theme
    dialog = WelcomeDialog()
    try:
        assert "By Diego Rayado Zumel" in [w.text() for w in dialog.findChildren(QLabel)]
        dialog.language_box.setCurrentIndex(dialog.language_box.findData("en"))
        pump(qapp, 0.1)
        assert dialog.greeting.text() == "Welcome to BlackPDF", "cambia de idioma sin reiniciar"
        dialog.theme_switch.set_value("light")
        assert not theme.current().is_dark
        dialog.updates_box.setChecked(True)
        dialog.accept()
        assert settings().welcomed and settings().language == "en" and settings().theme == "light"
        assert settings().check_updates
    finally:
        i18n.install("es")
        theme.manager().apply("dark")
        settings().language, settings().theme = previous
        settings().check_updates = False


def test_about_dialog_shows_author(qapp):
    from blackpdf.ui.dialogs.about import AboutDialog

    dialog = AboutDialog()
    texts = [w.text() for w in dialog.findChildren(QLabel)]
    assert "By Diego Rayado Zumel" in texts
    assert any("Texto de la licencia" in t and "LICENSE" in t for t in texts), "enlace a la licencia local"


def _fake_release(tmp_path, version="9.0.0"):
    import hashlib

    from blackpdf.core.updates import Release

    folder = tmp_path / "publicado"
    folder.mkdir(exist_ok=True)
    setup = folder / f"BlackPDF-{version}-Setup.exe"
    setup.write_bytes(b"instalador nuevo" * 1000)
    sums = folder / "SHA256SUMS.txt"
    sums.write_text(f"{hashlib.sha256(setup.read_bytes()).hexdigest()}  {setup.name}\n")
    return Release(version, "https://example.org/release", "### Novedades\n- Algo nuevo",
                   assets={setup.name: setup.as_uri(), sums.name: sums.as_uri()})


def test_update_dialog_depends_on_install_kind(qapp, tmp_path):
    from blackpdf.core import updates as core
    from blackpdf.ui.settings import settings
    from blackpdf.ui.updates import UpdateDialog

    release = _fake_release(tmp_path)
    assert UpdateDialog(release, kind=core.KIND_WINDOWS).update_button.text() == "Actualizar ahora"
    without_deb = UpdateDialog(release, kind=core.KIND_DEB)
    assert without_deb.kind == core.KIND_MANUAL and without_deb.update_button.text() == "Ir a la descarga"
    previous = settings().skipped_version
    try:
        dialog = UpdateDialog(release, kind=core.KIND_WINDOWS)
        dialog.skip_button.click()
        assert settings().skipped_version == "9.0.0"
    finally:
        settings().skipped_version = previous


def test_update_dialog_downloads_and_verifies_in_background(qapp, tmp_path, monkeypatch):
    from blackpdf.core import updates as core
    from blackpdf.ui.updates import UpdateDialog

    dialog = UpdateDialog(_fake_release(tmp_path), kind=core.KIND_WINDOWS)
    monkeypatch.setattr(dialog, "_folder", lambda: tmp_path / "descarga")
    requested = []
    dialog.installRequested.connect(lambda kind, path: requested.append((kind, path)))
    dialog.update_button.click()
    assert not dialog.update_button.isEnabled() and dialog.later_button.text() == "Cancelar"
    assert wait_until(qapp, lambda: requested, timeout=60)
    kind, path = requested[0]
    assert kind == core.KIND_WINDOWS and Path(path).read_bytes().startswith(b"instalador nuevo")


def test_main_window_update_notices(qapp, window, monkeypatch, tmp_path):
    from blackpdf.ui import main_window
    from blackpdf.ui.settings import settings

    toasts, shown = [], []
    monkeypatch.setattr(main_window.Toast, "show_message",
                        classmethod(lambda cls, parent, text, *args, **kwargs: toasts.append(text)))
    monkeypatch.setattr(window, "show_update", lambda release: shown.append(release.version))
    release = _fake_release(tmp_path)
    window._update_available(release)
    assert toasts == ["BlackPDF 9.0.0 ya está disponible"] and not shown, "automática: aviso discreto"
    previous = settings().skipped_version
    try:
        settings().skipped_version = "9.0.0"
        window._update_available(release)
        assert len(toasts) == 1, "una versión omitida no vuelve a avisar sola"
        window._manual_update_check = True
        window._update_available(release)
        assert shown == ["9.0.0"], "pedida desde el menú: se enseña aunque se omitiera"
    finally:
        settings().skipped_version = previous
    window._manual_update_check = True
    window._update_up_to_date()
    assert toasts[-1].startswith("Tienes la última versión de BlackPDF")
    window._fill_app_menu()
    texts = [a.text() for a in window.app_menu.actions()]
    assert "Buscar actualizaciones..." in texts and "Avisar de versiones nuevas" in texts


def test_update_checks_are_monthly_and_can_be_muted(qapp, tmp_path):
    import time as clock

    from blackpdf.core import updates as core
    from blackpdf.ui.settings import settings
    from blackpdf.ui.updates import UpdateDialog, check_due

    previous = settings().check_updates, settings().last_update_check
    try:
        settings().check_updates = True
        settings().last_update_check = clock.time() - 20 * 24 * 3600
        assert not check_due(), "hace 20 días: todavía no toca"
        settings().last_update_check = clock.time() - 31 * 24 * 3600
        assert check_due(), "hace más de un mes: toca comprobar"
        dialog = UpdateDialog(_fake_release(tmp_path), kind=core.KIND_WINDOWS)
        assert dialog.mute_box.isVisibleTo(dialog) and not dialog.mute_box.isChecked()
        dialog.mute_box.setChecked(True)
        dialog.later_button.click()
        assert not settings().check_updates and not check_due(), "el usuario ha quitado los avisos"
        muted = UpdateDialog(_fake_release(tmp_path), kind=core.KIND_WINDOWS)
        assert not muted.mute_box.isVisibleTo(muted), "con los avisos quitados no se ofrece la casilla"
    finally:
        settings().check_updates, settings().last_update_check = previous


def test_first_page_stays_below_annotation_bar_when_panels_change(qapp, opened):
    """Al abrir los comentarios el zoom se ajusta, pero el principio de la página no queda bajo la barra."""
    _, tab, _ = opened
    if not tab.annotation_bar.isVisible():
        tab.toggle_annotation_bar()
    tab.set_comments_visible(False)
    tab.viewer.set_fit_mode("auto")
    pump(qapp, 0.4)
    tab.viewer.verticalScrollBar().setValue(0)
    pump(qapp, 0.05)
    viewer = tab.viewer
    for visible in (True, False, True):
        tab.set_comments_visible(visible)
        pump(qapp, 0.4)
        top = viewer._rects[0].top() - viewer.verticalScrollBar().value()
        assert top >= viewer._top_inset, f"la página empieza en {top}, bajo la barra ({viewer._top_inset})"


def test_attachments_panel(qapp, window, tmp_path, monkeypatch):
    from blackpdf.core import attachments as core_attachments
    from blackpdf.ui import document_tab

    doc = build_pdf(3)
    doc.embfile_add("planos.step", b"ISO-10303-21;", filename="planos.step", desc="Pieza")
    inner = build_pdf(1)
    doc.embfile_add("anexo.pdf", inner.tobytes(), filename="anexo.pdf")
    doc[1].add_file_annot((80, 80), b"cotas", "cotas.txt")
    path = tmp_path / "planos.pdf"
    doc.save(path)
    toasts = []
    monkeypatch.setattr(document_tab.Toast, "show_message",
                        classmethod(lambda cls, parent, text, *args, **kwargs: toasts.append(text)))
    window.open_files([str(path)])
    pump(qapp, 0.8)
    tab = window.current_tab()
    # MuPDF guarda los PDF con adjuntos pidiendo abrirse con ellos a la vista: se abre el panel, sin aviso.
    assert tab.sidebar_switch.value() == "attachments" and not toasts
    tab.show_attachments()
    pump(qapp, 0.5)
    panel = tab.attachments
    assert tab.sidebar_switch.value() == "attachments" and tab.sidebar_count.text() == "3"
    assert [item.filename for item in panel.items] == ["planos.step", "anexo.pdf", "cotas.txt"], "orden del PDF"
    opened = []
    panel.openFiles.connect(opened.append)
    panel.open_attachment(next(item for item in panel.items if item.filename == "anexo.pdf"))
    assert opened and opened[0][0].endswith("anexo.pdf"), "un PDF adjunto se abre en BlackPDF"
    pump(qapp, 0.4)
    assert window.current_tab() is not tab, "en una pestaña nueva"
    window.tab_bar.setCurrentIndex(window._tabs.index(tab))
    pump(qapp, 0.3)

    extra = tmp_path / "medidas.xlsx"
    extra.write_bytes(b"hoja")
    panel.add_files([str(extra)])
    pump(qapp, 0.4)
    assert "medidas.xlsx" in [item.filename for item in panel.items] and tab.document.is_modified
    panel.remove_attachment(next(item for item in panel.items if item.filename == "planos.step"))
    pump(qapp, 0.4)
    assert "planos.step" not in [item.filename for item in panel.items]
    tab.undo()
    pump(qapp, 0.4)
    assert core_attachments.count_attachments(tab.document.doc) == 4
    tab.document._modified = False


def _stl_cube(path):
    import struct

    cube = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = [(q[0], q[1], q[2]) for q in quads] + [(q[0], q[2], q[3]) for q in quads]
    data = bytearray(b"\0" * 80 + struct.pack("<I", len(tris)))
    for tri in tris:
        data += struct.pack("<12fH", 0, 0, 0, *cube[tri[0]], *cube[tri[1]], *cube[tri[2]], 0)
    path.write_bytes(bytes(data))
    return path


def test_insert3d_dialog_validates_file(qapp, tmp_path):
    from blackpdf.ui.dialogs.model3d_insert import Insert3DDialog

    dialog = Insert3DDialog(None, "")
    assert not dialog.ok.isEnabled()
    dialog.picker.set_path(str(_stl_cube(tmp_path / "pieza.stl")))
    assert dialog.ok.isEnabled()
    dialog.placement.set_value("page")
    assert dialog.ok.text() == "Insertar"
    dialog.placement.set_value("rect")
    assert dialog.ok.text() == "Dibujar el recuadro"
    other = tmp_path / "plano.dwg"
    other.write_bytes(b"x")
    dialog.picker.set_path(str(other))
    assert not dialog.ok.isEnabled() and dialog.cad_note.isVisibleTo(dialog)
    attached = Insert3DDialog(None, str(tmp_path / "pieza.stl"), already_attached=True)
    assert not attached.attach.isChecked(), "si ya va adjunta no se vuelve a adjuntar"


def test_insert_model_in_rect_and_new_page(qapp, opened, tmp_path):
    import pymupdf

    from blackpdf.core import attachments as core_attachments
    from blackpdf.core import model3d
    from blackpdf.ui.dialogs.model3d_insert import Insert3DOptions

    _, tab, _ = opened
    stl = _stl_cube(tmp_path / "pieza.stl")
    pages = tab.document.page_count
    rect = pymupdf.Rect(60, 60, 360, 285)
    tab._prepare_model(Insert3DOptions(str(stl), "rect", (1.0, 1.0, 1.0), False), 0, rect, rect.width / rect.height)
    assert wait_until(qapp, lambda: model3d.find_models(tab.document.doc), timeout=60)
    assert tab.document.page_count == pages and model3d.find_models(tab.document.doc)[0].page == 0
    middle = QPointF((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    assert tab.viewer.model_at(tab.viewer.hit_test(tab.viewer.page_to_viewport(0, middle))) is not None, "se puede pulsar"

    tab._prepare_model(Insert3DOptions(str(stl), "page", (0.11, 0.11, 0.13), True), None, None, 4 / 3)
    assert wait_until(qapp, lambda: tab.document.page_count == pages + 1, timeout=60)
    pump(qapp, 0.2)
    assert len(model3d.find_models(tab.document.doc)) == 2
    assert [a.filename for a in core_attachments.list_attachments(tab.document.doc)] == ["pieza.stl"]
    tab.undo()
    pump(qapp, 0.3)
    assert tab.document.page_count == pages and len(model3d.find_models(tab.document.doc)) == 1
    assert core_attachments.count_attachments(tab.document.doc) == 0
    tab.document._modified = False


def test_place_rect_tool_click_gives_default_rect(qapp, opened):
    from blackpdf.ui.interaction import PlaceRectTool

    _, tab, _ = opened
    tool = PlaceRectTool(tab.viewer)
    tab.viewer.set_tool(tool)
    placed = []
    tool.placed.connect(lambda page, rect: placed.append((page, rect)))
    point = tab.viewer.page_to_viewport(0, QPointF(300, 400))
    mouse(tab.viewer.viewport(), "press", point)
    mouse(tab.viewer.viewport(), "release", point)
    assert placed and placed[0][0] == 0
    rect = placed[0][1]
    size = tab.document.page_size(0)
    assert rect.width == size.width() * 0.5 and 0 <= rect.x0 and rect.x1 <= size.width()
    tab.set_tool("select")


def test_open_mesh_file_in_3d_viewer(qapp, window, tmp_path):
    from blackpdf.core import model3d

    stl = _stl_cube(tmp_path / "pieza.stl")
    tabs_before = window.tab_bar.count()
    window.open_files([str(stl)])
    pump(qapp, 0.2)
    assert window.tab_bar.count() == tabs_before and len(window._model_windows) == 1, "va al visor 3D"
    assert model3d.load_file(str(stl)).triangle_count == 12
    for win in list(window._model_windows.values()):
        win.close()
    pump(qapp, 0.05)


def test_properties_dialog_edits_metadata(qapp, opened, monkeypatch):
    from blackpdf.core import metadata
    from blackpdf.ui import document_tab
    from blackpdf.ui.dialogs.common import PropertiesDialog

    _, tab, _ = opened
    values = metadata.read_metadata(tab.document.doc)
    dialog = PropertiesDialog(document_tab.document_info(tab.document.doc), "", None, values)
    assert not dialog.save_button.isEnabled(), "sin cambios no se guarda"
    dialog._edits["title"].setText("Planos de la nave")
    dialog._edits["keywords"].setText("nave, estructura")
    assert dialog.save_button.isEnabled()
    assert dialog.changes() == {"title": "Planos de la nave", "keywords": "nave, estructura"}

    class Accepting(PropertiesDialog):
        def exec(self):
            self._edits["author"].setText("Ana")
            return PropertiesDialog.DialogCode.Accepted

    monkeypatch.setattr(document_tab, "PropertiesDialog", Accepting)
    tab.show_properties()
    assert metadata.read_metadata(tab.document.doc)["author"] == "Ana" and tab.document.is_modified
    tab.undo()
    assert metadata.read_metadata(tab.document.doc)["author"] == values["author"]
    tab.document._modified = False


def test_extract_images_tool(qapp, window, tmp_path):
    from blackpdf.ui import tools

    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    pix.set_rect(pix.irect, (20, 120, 220))
    page.insert_image(pymupdf.Rect(50, 50, 290, 210), stream=pix.tobytes("jpeg"))
    path = tmp_path / "foto.pdf"
    doc.save(path)
    window.open_files([str(path)])
    pump(qapp, 0.6)
    tab = window.current_tab()
    dialog = tools.dialog_class("extract_images")(window, window._context_for(tab), {})
    dialog.output.picker.set_path(str(tmp_path / "salida"))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 60)
    assert [p.suffix for p in (tmp_path / "salida").iterdir()] == [".jpg"]
    dialog.close()


def test_clean_tool(qapp, window, tmp_path):
    from blackpdf.ui import tools

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Hola")
    doc.set_metadata({"author": "Diego", "title": "Interno"})
    doc.embfile_add("datos.csv", b"a,b", filename="datos.csv")
    path = tmp_path / "sucio.pdf"
    doc.save(path)
    window.open_files([str(path)])
    pump(qapp, 0.6)
    tab = window.current_tab()
    dialog = tools.dialog_class("clean")(window, window._context_for(tab), {})
    assert dialog.options["metadata"].isChecked() and not dialog.options["comments"].isChecked()
    output = tmp_path / "limpio.pdf"
    dialog.output.picker.set_path(str(output))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 60)
    clean = pymupdf.open(output)
    assert not clean.metadata["author"] and clean.embfile_count() == 0
    dialog.close()


def test_page_labels_in_page_box_and_thumbnails(qapp, opened, monkeypatch):
    from blackpdf.core.page_labels import LabelRule
    from blackpdf.ui.dialogs import page_labels as dialog_module

    _, tab, _ = opened
    dialog = dialog_module.PageLabelsDialog([], tab.document.page_count, None)
    assert len(dialog.rows) == 1 and dialog.preview.text() == "1, 2, 3, 4"
    dialog.rows[0]["style"].setCurrentIndex(dialog.rows[0]["style"].findData("r"))
    dialog.add_rule(LabelRule(2, "D", "A-", 101))
    assert dialog.preview.text() == "i, ii, A-101, A-102"
    dialog.rows[1]["start"].setValue(1)
    assert not dialog.ok.isEnabled(), "dos reglas en la misma página"

    class Accepting(dialog_module.PageLabelsDialog):
        def exec(self):
            return dialog_module.PageLabelsDialog.DialogCode.Accepted

        def rules(self):
            return [LabelRule(0, "r"), LabelRule(2, "D", "A-", 101)]

    monkeypatch.setattr(dialog_module, "PageLabelsDialog", Accepting)
    tab.edit_page_labels()
    pump(qapp, 0.2)
    assert tab.document.labels == ["i", "ii", "A-101", "A-102"]
    assert tab.thumb_model.data(tab.thumb_model.index(3)) == "A-102"
    tab.page_edit.setText("a-102")
    tab._jump_to_page()
    pump(qapp, 0.6)
    assert tab.viewer.current_page == 3
    assert tab.page_edit.text() == "A-102" and tab.page_total.text() == "(4 de 4)"
    tab.undo()
    pump(qapp, 0.2)
    assert tab.document.labels is None and tab.page_edit.text() == "4"
    tab.document._modified = False


def test_layers_panel_toggles_what_is_seen(qapp, window, tmp_path):
    from blackpdf.ui.layers import LayerRow

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    walls = doc.add_ocg("Muros", on=True)
    furniture = doc.add_ocg("Mobiliario", on=True)
    page.draw_rect(pymupdf.Rect(20, 20, 120, 120), color=(1, 0, 0), fill=(1, 0, 0), oc=walls)
    page.draw_rect(pymupdf.Rect(160, 20, 260, 120), color=(0, 0, 1), fill=(0, 0, 1), oc=furniture)
    path = tmp_path / "plano.pdf"
    doc.save(path)
    original = path.read_bytes()
    window.open_files([str(path)])
    pump(qapp, 0.4)
    tab = window.current_tab()
    assert tab.sidebar_switch._buttons["layers"].isVisibleTo(tab), "el PDF tiene capas: aparece su pestaña"
    tab.show_layers()
    pump(qapp, 0.1)
    assert tab.sidebar_switch.value() == "layers" and tab.sidebar_count.text() == "2"
    rows = [w for w in tab.layers_panel.findChildren(LayerRow)]
    assert [row.layer.name for row in rows] == ["Muros", "Mobiliario"]
    rows[1].eye.click()
    pump(qapp, 0.1)
    assert tab.document.doc[0].get_pixmap().pixel(200, 60) == (255, 255, 255), "el mobiliario ya no se dibuja"
    assert not tab.document.is_modified, "ocultar una capa no modifica el archivo"
    assert tab.layers_panel.save_default.isEnabled()

    # Al deshacer otra edición, la capa sigue oculta.
    tab.document.edit("Nota", lambda d: d[0].add_text_annot((10, 180), "Nota"), pages=[0])
    tab.undo()
    assert not {layer.name: layer.visible for layer in tab.document.layers()}["Mobiliario"]

    tab.layers_panel.save_default.click()
    assert tab.document.is_modified and not tab.layers_panel.save_default.isEnabled()
    assert tab.save() and path.read_bytes() != original
    with pymupdf.open(path) as saved:
        assert saved[0].get_pixmap().pixel(200, 60) == (255, 255, 255), "el PDF se abre ya sin el mobiliario"


def test_layers_tab_hidden_without_layers(qapp, opened):
    _window, tab, _path = opened
    assert not tab.sidebar_switch._buttons["layers"].isVisibleTo(tab)
    tab.show_layers()
    assert tab.sidebar_switch.value() != "layers"


def test_stamp_tool_places_stamp_in_interface_language(qapp, opened):
    from blackpdf.ui.stamps import StampChoice, StampPopup, StampTile

    _, tab, _ = opened
    tab.set_tool("stamp")
    bar = tab.annotation_bar
    assert bar.stamp_button.isVisibleTo(bar) and not bar.color_button.isVisibleTo(bar)
    popup = StampPopup(bar._stamp, bar)
    tiles = popup.findChildren(StampTile)
    assert len(tiles) >= 12 and not tiles[0]._image.isNull()
    picked = []
    popup.picked.connect(picked.append)
    popup.details.setChecked(False)
    tiles[2].click()  # «Revisado»
    assert picked and picked[-1].kind == "reviewed" and not picked[-1].details
    bar._stamp_picked(picked[-1])
    assert bar.stamp_button.text() == "Revisado"
    point = tab.viewer.page_to_viewport(0, QPointF(300, 300))
    mouse(tab.viewer.viewport(), "move", point)
    mouse(tab.viewer.viewport(), "press", point)
    mouse(tab.viewer.viewport(), "release", point)
    page = tab.document.doc[0]
    annot = page.first_annot
    assert annot is not None and annot.type[0] == pymupdf.PDF_ANNOT_STAMP
    assert annot.info["content"] == "REVISADO" and b"REVISADO" in annot._getAP()
    assert tab._tool_name == "select", "tras sellar se vuelve a seleccionar"
    assert StampChoice.load().kind == "reviewed", "se recuerda el último sello"


def test_polygon_tool_click_by_click(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("polygon")
    viewport = tab.viewer.viewport()
    for x, y in ((100, 100), (250, 120), (220, 260)):
        point = tab.viewer.page_to_viewport(0, QPointF(x, y))
        mouse(viewport, "press", point)
        mouse(viewport, "release", point)
    page = tab.document.doc[0]
    assert tab.viewer.tool.busy and page.first_annot is None
    tab._escape()
    assert not tab.viewer.tool.busy and tab._tool_name == "polygon", "Esc descarta el polígono, no la herramienta"
    for x, y in ((100, 100), (250, 120), (220, 260), (100, 100.5)):  # el último clic, sobre el primer vértice
        point = tab.viewer.page_to_viewport(0, QPointF(x, y))
        mouse(viewport, "press", point)
        mouse(viewport, "release", point)
    page = tab.document.doc[0]
    annot = page.first_annot
    assert annot is not None and annot.type[0] == pymupdf.PDF_ANNOT_POLYGON and len(annot.vertices) == 3
    tab.set_tool("select")


def test_tables_tool(qapp, window, tmp_path):
    from tests.test_tables import _pdf

    from blackpdf.ui import tools

    path = tmp_path / "presupuesto.pdf"
    path.write_bytes(_pdf())
    window.open_files([str(path)])
    pump(qapp, 0.6)
    tab = window.current_tab()
    dialog = tools.dialog_class("tables")(window, window._context_for(tab), {})
    assert dialog.output.path().endswith("presupuesto_tablas.xlsx")
    dialog.format.set_value("csv")
    assert dialog.output.path().endswith("presupuesto_tablas.csv"), "cambia la extensión con el formato"
    dialog.format.set_value("xlsx")
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 60)
    assert (tmp_path / "presupuesto_tablas.xlsx").is_file()
    assert dialog.open_button.isVisibleTo(dialog) and "1 tabla" in dialog.result_panel.detail.text()
    dialog.close()


def test_measure_tool_with_scale_snapping_and_calibration(qapp, opened, monkeypatch):
    from blackpdf.core import measure as core_measure
    from blackpdf.ui import measure as ui_measure

    _, tab, _ = opened
    page = tab.document.doc[0]
    tab.document.edit("Línea", lambda d: d[0].draw_line((100, 260), (400, 260)) and None, pages=[0])
    tab.set_tool("measure")
    bar = tab.annotation_bar
    assert bar.measure_button.isVisibleTo(bar) and bar.scale_button.text() == "1:1 · mm"
    tab._set_measure_scale(core_measure.Scale.from_ratio(100, "m"))
    assert bar.scale_button.text() == "1:100 · m"
    viewport = tab.viewer.viewport()
    # Cerca de los extremos de la línea dibujada: el cursor se ajusta a ellos.
    for x, y in ((101.5, 261), (398.5, 259)):
        point = tab.viewer.page_to_viewport(0, QPointF(x, y))
        mouse(viewport, "move", point)
        mouse(viewport, "press", point)
        mouse(viewport, "release", point)
    page = tab.document.doc[0]
    annot = page.first_annot
    assert annot is not None and core_measure.is_measurement(annot)
    expected = ui_measure.format_measure(300 * core_measure.PT_MM * 100 / 1000, "m")
    assert core_measure.measurement_label(annot) == expected and expected.endswith(" m")
    assert abs(core_measure.measure("distance", annot.vertices, core_measure.Scale.from_ratio(100, "m")) - 10.583) < 0.01

    # Calibrar: una línea de 300 pt que en realidad mide 6 m.
    monkeypatch.setattr(ui_measure.CalibrateDialog, "exec", lambda self: (self.value.setValue(6.0), 1)[1])
    tab._measure_mode_changed("calibrate")
    for x in (100, 400):
        point = tab.viewer.page_to_viewport(0, QPointF(x, 200))
        mouse(viewport, "move", point)
        mouse(viewport, "press", point)
        mouse(viewport, "release", point)
    scale = tab.document.measure_scale
    assert scale.unit == "m" and abs(scale.length(300) - 6.0) < 1e-6
    assert tab.viewer.tool.mode == "distance" and bar.measure_button.text() == "Distancia"

    # Área: clic en cada vértice y doble clic (aquí, Intro) para terminar.
    tab._measure_mode_changed("area")
    for x, y in ((100, 100), (200, 100), (200, 200), (100, 200)):
        point = tab.viewer.page_to_viewport(0, QPointF(x, y))
        mouse(viewport, "move", point)
        mouse(viewport, "press", point)
        mouse(viewport, "release", point)
    tab.viewer.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    page = tab.document.doc[0]
    labels = [core_measure.measurement_label(a) for a in page.annots() if core_measure.is_measurement(a)]
    assert ui_measure.format_measure(4.0, "m", area=True) in labels, "100 × 100 pt a 2 cm por punto"
    tab.set_tool("select")


def test_attachments_badge_and_initial_panel(qapp, window, tmp_path):
    doc = build_pdf(2)
    doc.embfile_add("medidas.csv", b"a;b", filename="medidas.csv")
    doc.embfile_add("pieza.step", b"ISO-10303-21;", filename="pieza.step")
    path = tmp_path / "con_adjuntos.pdf"
    doc.save(path)  # MuPDF lo marca para abrirse con los adjuntos a la vista (/PageMode)
    window.open_files([str(path)])
    pump(qapp, 0.8)
    tab = window.current_tab()
    assert tab.sidebar_switch.badge("attachments") == "2", "el clip dice cuántos hay"
    assert tab.sidebar_switch.value() == "attachments", "el PDF pide abrirse con sus adjuntos a la vista"
    tab.sidebar_switch.set_value("pages")
    tab.document.edit("Adjuntar", lambda d: d.embfile_add("otro.txt", b"x", filename="otro.txt"))
    assert wait_until(qapp, lambda: tab.sidebar_switch.badge("attachments") == "3", 5), \
        "se actualiza aunque el panel esté cerrado"

    # El clip de la barra de arriba no depende del panel lateral: con él cerrado, sigue a la vista.
    clip = tab.attachments_button
    assert clip.isVisibleTo(tab) and clip.badge() == "3"
    tab.toggle_sidebar()
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[0] < 20 and clip.isVisibleTo(tab)
    clip.click()
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[0] >= 20 and tab.sidebar_switch.value() == "attachments"
    clip.click()
    pump(qapp, 0.4)
    assert tab.splitter.sizes()[0] < 20, "volver a pulsarlo cierra el panel"
    clip.click()
    pump(qapp, 0.4)

    outline = build_pdf(3)
    outline.set_toc([[1, "Introducción", 1], [1, "Anexo", 3]])
    outline.set_pagemode("UseOutlines")
    with_outline = tmp_path / "con_indice.pdf"
    outline.save(with_outline)
    window.open_files([str(with_outline)])
    pump(qapp, 0.8)
    tab = window.current_tab()
    assert tab.sidebar_switch.value() == "outline" and tab.sidebar_switch.badge("attachments") == ""
    assert not tab.attachments_button.isVisibleTo(tab), "sin adjuntos, el clip de arriba no estorba"


def test_clicking_the_active_tool_again_goes_back_to_select(qapp, opened):
    _, tab, _ = opened
    bar = tab.annotation_bar
    bar._buttons["highlight"].click()
    assert tab._tool_name == "highlight" and bar._buttons["highlight"].isChecked()
    bar._buttons["highlight"].click()
    assert tab._tool_name == "select" and bar._buttons["select"].isChecked()
    assert not bar._buttons["highlight"].isChecked()
    tab.toggle_tool("pen")
    tab.toggle_tool("rect")
    assert tab._tool_name == "rect", "cambiar de herramienta sigue siendo un solo clic"
    tab.toggle_tool("rect")
    tab.toggle_tool("select")
    assert tab._tool_name == "select", "pulsar Seleccionar con Seleccionar activo no hace nada raro"


def test_measure_in_3d_viewer(qapp, monkeypatch):
    """Medir en el visor 3D: sin OpenGL en las pruebas, el punto bajo el ratón se simula."""
    from PySide6.QtGui import QVector3D

    from blackpdf.core.model3d import measure3d as core3d
    from blackpdf.core.model3d import meshes
    from blackpdf.ui.model3d_window import Model3DWindow
    from tests.test_measure3d import _cube

    scene = meshes.parts_scene([_cube()])
    win = Model3DWindow("cubo.stl")
    win.resize(1200, 760)
    win.show()
    win.set_scene(scene)
    pump(qapp, 0.1)
    queue: list[tuple[float, float, float]] = []
    current = {}

    def pick_point(pos):
        current["point"] = queue.pop(0) if queue else None
        return QVector3D(*current["point"]) if current["point"] is not None else None

    def pick_ray(pos):
        # Como mirar desde arriba (eje Z) justo sobre el punto simulado.
        if current.get("point") is None:
            return None
        x, y, z = current["point"]
        return QVector3D(x, y, z + 100), QVector3D(0, 0, -1)

    monkeypatch.setattr(win.viewer, "pick_point", pick_point)
    monkeypatch.setattr(win.viewer, "pick_ray", pick_ray)
    monkeypatch.setattr(win.viewer, "snap_tolerance", lambda origin, direction, pixels: (0.6, 0.0))
    monkeypatch.setattr(win.viewer, "project", lambda point: QPointF(point.x() * 10, point.y() * 10))
    clicked = []
    win.viewer.partClicked.connect(clicked.append)

    win.measure_button.setChecked(True)
    assert win.viewer.measure_tool is win.measure and win.measure_bar.isVisibleTo(win)
    assert "no dice en qué unidades" in win.measure_bar.unit.toolTip(), "un STL no trae unidades"

    def click(point):
        queue.append(point)
        win.measure.click(QPointF(0, 0))

    click((0.3, 0.2, 10.0))      # cerca de la esquina (0, 0, 10)
    click((10.1, 0.25, 9.9))     # cerca de la esquina (10, 0, 10)
    result = win.measure.results[-1]
    assert result.kind == "distance" and result.points == [(0, 0, 10), (10, 0, 10)] and result.value == 10
    assert win.measure_bar.readout.text() == win.measure.detail(result)
    win.measure_bar.unit.setCurrentIndex(win.measure_bar.unit.findData("cm"))
    assert win.measure.length_text(10) == f"{QLocale().toString(10.0, 'f', 2)} cm", \
        "sin unidades en el archivo, se elige en qué está el modelo: no se convierte"

    win.measure_bar.modes.set_value("angle")
    for point in ((10.0, 0.1, 10.0), (0.2, 0.2, 10.0), (0.1, 10.0, 10.0)):
        click(point)
    assert win.measure.results[-1].kind == "angle" and abs(win.measure.results[-1].value - 90) < 1e-9

    win.measure_bar.modes.set_value("radius")
    for point in ((5.0, 5.0, 10.0), (5.0, 5.0, 10.0), (5.0, 5.0, 10.0)):
        click(point)
    assert win.measure.results[-1].kind == "angle", "tres puntos iguales no son una circunferencia: se descartan"
    assert "en línea" in win.measure_bar.readout.text(), "y se avisa"
    import math as _math

    for t in (0.0, 2.0, 4.0):
        click((5 + 3 * _math.cos(t), 5 + 3 * _math.sin(t), 10.0))
    radius = win.measure.results[-1]
    assert radius.kind == "radius" and abs(radius.value - 3) < 1e-6  # el visor da puntos en float32
    assert abs(core3d.convert(radius.value, 1.0, "cm") - 0.3) < 1e-6

    # Midiendo, un clic no selecciona piezas.
    pos = QPointF(200, 200)
    for kind in ("press", "release"):
        mouse(win.viewer, kind, pos)
    assert not clicked
    # Esc descarta la medida a medias y, con otro Esc, se deja de medir.
    click((1.0, 1.0, 10.0))
    win._escape()
    assert not win.measure.points and win.measure_button.isChecked()
    win._escape()
    assert not win.measure_button.isChecked() and win.viewer.measure_tool is None
    win.measure_bar.clear_button.click() if win.measure_bar.isVisible() else win.measure.clear()
    assert not win.measure.results

    scene.unit_mm = 25.4
    win.set_scene(scene)
    assert win.measure.unit == "in" and "no dice" not in win.measure_bar.unit.toolTip()
    win.measure_bar.unit.setCurrentIndex(win.measure_bar.unit.findData("mm"))
    assert win.measure.length_text(10) == f"{QLocale().toString(254.0, 'f', 2)} mm", "con unidades, se convierte"
    win.close()
    pump(qapp, 0.05)
