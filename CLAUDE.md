# BlackPDF — guía para Claude Code

Visor y editor de PDF de escritorio, libre y gratuito. Todo lo de iLovePDF, pero offline y en local.
Para todo el mundo. Estética muy cuidada y moderna.

## Stack
- Python 3.12+ (se usa la última estable, gestionada con `uv`), PySide6-Essentials (Qt 6), PyMuPDF (`pymupdf`).
- Licencia del proyecto: AGPL-3.0 (obligatoria por PyMuPDF).
- Empaquetado: PyInstaller (Linux AppImage + .deb, Windows instalador Inno Setup con «Abrir con»). Ver `packaging/` y README.
- Gestión: `pyproject.toml` + venv. Tests con `pytest`.

## Arquitectura (respetar SIEMPRE)
```
blackpdf/core/   → operaciones PDF puras con PyMuPDF. PROHIBIDO importar Qt.
blackpdf/ui/     → ventanas, visor, miniaturas, diálogos, tema.
blackpdf/procworker.py → ejecuta herramientas en un subproceso (sin Qt).
blackpdf/app.py  → punto de entrada.
tests/           → tests de core con PDFs generados en el propio test.
```
- Cada herramienta = función en `core` (entrada: rutas o `fitz.Document`, salida: documento/archivo) + diálogo en `ui`.
- Operaciones largas fuera del hilo de la UI con barra de progreso: un `QRunnable` del `QThreadPool` supervisa un subproceso (`procworker`), porque PyMuPDF se inicializa en modo monohilo y no es seguro usarlo desde varios hilos. El render del visor va troceado en el hilo de la UI. El análisis de piezas para medir en 3D (`core/model3d/topology.py`, Python puro sin PyMuPDF) va en un hilo propio.
- Coordenadas: `core/annotations.py` y la UI trabajan en coordenadas *visibles* (`page.rect`); la conversión a PDF sin rotar se hace en core con `derotation_matrix`.
- Deshacer/rehacer en el editor (snapshot `doc.tobytes()` por operación, máx. 30).
- Nunca sobrescribir el original sin "Guardar"; "Guardar como" por defecto en herramientas.

## Funciones
**Visor**
- Abrir con diálogo o arrastrando archivos, pestañas para varios PDFs, recientes.
- Scroll continuo con render perezoso (solo páginas visibles) y caché.
- Zoom (Ctrl+rueda, ajustar a ancho/página), modo noche, pantalla completa.
- Panel de miniaturas, índice (marcadores), búsqueda de texto con resaltado.

**Organizar (en el panel de miniaturas)**
- Reordenar arrastrando, rotar, eliminar, duplicar, insertar páginas en blanco o de otro PDF.
- Selección múltiple con Ctrl/Shift.

**Herramientas tipo iLovePDF**
- Unir varios PDFs (lista reordenable).
- Dividir por rangos (`1-3,5,8-10`), cada N páginas o una por archivo.
- Alternar/intercalar dos PDFs (opción de invertir el segundo para escaneos a doble cara).
- Extraer páginas.
- Comprimir (niveles bajo/medio/alto: recomprimir imágenes + `garbage=4, deflate`).
- PDF a imágenes (PNG/JPG, DPI) e imágenes a PDF.
- Marca de agua (texto o imagen, opacidad, rotación, posición).
- Números de página (posición, formato "1", "1/10", "Página 1").
- Proteger con contraseña (AES-256) y desbloquear.
- Recortar márgenes, redimensionar a A4/Carta.
- Rellenar formularios y aplanar.
- OCR con el Tesseract integrado en MuPDF; datos de español e inglés incluidos en `resources/tessdata`.

**Anotar y pintar**
- Lápiz libre (color, grosor), resaltador, subrayado, tachado.
- Rectángulo, círculo, flecha, línea, cuadro de texto, nota.
- Firma: dibujarla una vez, guardarla y estamparla.
- Goma: borra la anotación bajo el cursor. Las anotaciones se guardan como anotaciones PDF estándar.

## Diseño visual (prioridad alta)
- Tema oscuro grafito por defecto + claro. Colores SOLO desde `blackpdf/ui/theme.py`.
- Acento: rojo `#E5484D` (identidad BlackPDF) sobre negro/grafito. Icono: folio con borde rojo sobre negro. Sin emojis.
- Tipografía Inter, iconos Lucide (SVG), bordes redondeados 8 px, espaciado múltiplo de 4.
- Pantalla de inicio con las herramientas en tarjetas (como iLovePDF) + zona para soltar archivos.
- Barra de anotación flotante sobre el visor. Animaciones cortas (150-200 ms).
- Atajos estándar (Ctrl+O/S/Z/Y/F, Supr, +/-) y paleta de comandos Ctrl+K.

## Reglas
1. Toda función de `core` lleva test en `tests/`.
2. Textos de UI en español con `self.tr()` (o `QT_TRANSLATE_NOOP` en listas de módulo). Traducciones en `translations/*.ts` (en, pt de Brasil, fr) con la skill `traducir`: `scripts/i18n.py update`, `pending`, `fill` y `build`; los tests fallan si falta alguna.
3. Errores al usuario con mensajes claros, nunca trazas.
4. Antes de dar una tarea por terminada o hacer commit: `scripts/verificar.py` en VERDE (pruebas, autocomprobación, ruff y traducciones).
5. Cambios pequeños y enfocados.

## Flujo de trabajo con Claude Code
Todo está en `.claude/` y `scripts/`, versionado.
- Skills: `/nueva-funcion` (receta completa de una función), `traducir` (con glosario), `verificar`,
  `captura` (revisión visual), `pymupdf` (trampas conocidas; se carga sola al tocar `core/`) y
  `/publicar` (solo la lanza el usuario).
- Subagentes: `revisor` (revisa el cambio antes del commit), `traductor` y `explorador-pymupdf`.
- Scripts: `verificar.py`, `captura.py`, `demo_pdf.py` (PDF de prueba con de todo), `i18n.py`.
- Ajustes: `git push`, `git tag` y `gh release` siempre piden permiso; ruff revisa cada `.py` al
  editarlo; un `git commit` con la verificación rápida en rojo se bloquea.
- Ejecuta Python con `env -u PYTHONPATH .venv/bin/python` (un PYTHONPATH ajeno, como el de ROS, rompe el venv).

## Orden de trabajo
1. Esqueleto: `pyproject.toml`, venv, ventana con tema, abrir y ver un PDF.
2. Miniaturas + organizar páginas + guardar + deshacer.
3. Herramientas: unir, dividir, alternar, extraer, rotar.
4. Anotar y pintar.
5. Resto de herramientas.
6. Pantalla de inicio, pulido visual, empaquetado.
