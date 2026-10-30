"""Captura de una escena concreta de la interfaz, sin pantalla, para revisar un cambio.

    python scripts/captura.py SALIDA.png [opciones]

    --pdf FILE              documento a abrir (sin él, la pantalla de inicio)
    --pagina N              página a mostrar (desde 1)
    --ajuste ancho|pagina   ajuste del zoom
    --herramienta NOMBRE    herramienta activa (pen, highlight, stamp, polygon, measure...)
    --panel NOMBRE          pestaña del panel lateral: pages, outline, layers, attachments; «no» lo cierra
    --comentarios           muestra el panel de comentarios
    --clics "x,y;x,y"       clics en esos puntos de la página (en pt), con la herramienta activa
    --raton x,y             deja el ratón en ese punto (vista previa del sello, cifra de la medida...)
    --dialogo ID            abre el diálogo de esa herramienta (ids de blackpdf/ui/tools.py) y captura solo el diálogo
    --codigo PYTHON         con --dialogo, código que se ejecuta antes de capturar («dialogo» es el diálogo)
    --desplegable NOMBRE    sellos o escala: abre ese desplegable de la barra de anotación y lo captura
    --claro                 tema claro
    --tamano 1280x800       tamaño de la ventana

Usa unos ajustes temporales: no toca los del usuario ni sus recientes. Para revisar todos los
diálogos de una vez están scripts/screenshot_dialogs.py y scripts/screenshot.py.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _points(text: str) -> list[tuple[float, float]]:
    return [tuple(float(v) for v in item.split(",")) for item in text.split(";") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("salida")
    parser.add_argument("--pdf")
    parser.add_argument("--pagina", type=int, default=1)
    parser.add_argument("--ajuste", choices=("ancho", "pagina"))
    parser.add_argument("--herramienta")
    parser.add_argument("--panel")
    parser.add_argument("--comentarios", action="store_true")
    parser.add_argument("--clics", default="")
    parser.add_argument("--raton")
    parser.add_argument("--dialogo")
    parser.add_argument("--codigo")
    parser.add_argument("--desplegable", choices=("sellos", "escala"))
    parser.add_argument("--claro", action="store_true")
    parser.add_argument("--tamano", default="1280x800")
    args = parser.parse_args()

    from PySide6.QtCore import QEvent, QPointF, QSettings, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp(prefix="blackpdf-captura-"))
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from blackpdf.ui import icons, theme, tools
    from blackpdf.ui.main_window import MainWindow
    from blackpdf.ui.settings import settings

    settings().check_updates = False
    theme.load_fonts()
    theme.manager().apply("light" if args.claro else "dark")
    app.setWindowIcon(icons.app_icon())

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    width, height = (int(v) for v in args.tamano.lower().split("x"))
    window = MainWindow()
    window.resize(width, height)
    window.show()
    pump(0.4)
    target = window
    if args.pdf:
        window.open_files([str(Path(args.pdf).resolve())])
        pump(1.5)
        tab = window.current_tab()
        if tab is None:
            print("No se ha podido abrir el PDF.")
            return 1
        if args.ajuste:
            tab.viewer.set_fit_mode("width" if args.ajuste == "ancho" else "page")
        tab.viewer.go_to_page(max(0, args.pagina - 1), smooth=False)
        if args.panel == "no":
            if tab.splitter.sizes()[0] >= 20:
                tab.toggle_sidebar()
        elif args.panel:
            if tab.splitter.sizes()[0] < 20:
                tab.toggle_sidebar()
            tab.sidebar_switch.set_value(args.panel)
        if args.comentarios:
            tab.set_comments_visible(True)
        pump(0.6)
        if args.herramienta:
            tab.set_tool(args.herramienta)
            pump(0.3)
        viewport = tab.viewer.viewport()
        page = max(0, args.pagina - 1)

        def send(kind: str, point: tuple[float, float]) -> None:
            pos = tab.viewer.page_to_viewport(page, QPointF(*point))
            types = {"press": QEvent.Type.MouseButtonPress, "release": QEvent.Type.MouseButtonRelease,
                     "move": QEvent.Type.MouseMove}
            button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
            held = Qt.MouseButton.LeftButton if kind == "press" else Qt.MouseButton.NoButton
            event = QMouseEvent(types[kind], pos, viewport.mapToGlobal(pos.toPoint()).toPointF(), button, held,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(viewport, event)

        for point in _points(args.clics):
            send("move", point)
            send("press", point)
            send("release", point)
        if args.raton:
            send("move", _points(args.raton)[0])
        pump(0.5)
        if args.dialogo:
            dialog_class = tools.dialog_class(args.dialogo)
            if dialog_class is None:
                print(f"No hay diálogo «{args.dialogo}».")
                return 1
            target = dialog_class(window, window._context_for(tab), {})
            target.show()
            pump(0.8)
            if args.codigo:
                # Para capturar el diálogo en otro estado: marcar una casilla, escribir algo...
                exec(args.codigo, {"dialogo": target, "ventana": window})
                pump(0.6)
        elif args.desplegable:
            bar = tab.annotation_bar
            if args.desplegable == "sellos":
                tab.set_tool("stamp")
                pump(0.2)
                from blackpdf.ui.stamps import StampPopup

                target = StampPopup(bar._stamp, bar)
                target.show_below(bar.stamp_button)
            else:
                tab.set_tool("measure")
                pump(0.2)
                from blackpdf.ui.measure import ScalePopup

                target = ScalePopup(tab._measure_scale(tab.viewer.current_page), None, bar)
                target.show_below(bar.scale_button)
            pump(0.5)
    output = Path(args.salida)
    output.parent.mkdir(parents=True, exist_ok=True)
    target.grab().save(str(output))
    for tab in window.document_tabs():
        tab.document._modified = False
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
