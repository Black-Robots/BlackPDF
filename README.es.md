# BlackPDF

[English](README.md) · **Español**

Visor y editor de PDF de escritorio, libre y gratuito. Reúne lo que ofrecen los servicios web de PDF, pero funciona sin conexión: los documentos nunca salen del equipo.

Disponible para **Windows 10/11** y **Linux**, en español, inglés, portugués y francés (elige el idioma del sistema; se cambia en Menú > Idioma).

**[Descargar la última versión](https://github.com/Black-Robots/BlackPDF/releases/latest)** · [Página web](https://blackrobots.org/blackpdf) · [Informar de un fallo](https://github.com/Black-Robots/BlackPDF/issues)

![BlackPDF con un documento y sus comentarios](docs/screenshots/es/document.webp)

<p align="center">
  <img src="docs/screenshots/es/home.webp" width="49%" alt="Pantalla de inicio con todas las herramientas">
  <img src="docs/screenshots/es/viewer3d.webp" width="49%" alt="Visor 3D">
</p>

## Funciones

**Visor**
- Pestañas para varios documentos, archivos recientes y vuelta a la última página leída.
- Scroll continuo con render por teselas (solo se dibuja lo visible), zoom con Ctrl+rueda, ajuste al ancho o a la página.
- Modo noche que conserva los colores, pantalla completa, miniaturas, índice y búsqueda con resaltado.
- Historial de navegación (Alt+Izquierda o botón Atrás del ratón) y enlaces internos y externos.
- Vista dividida (Ctrl+\\): el mismo documento en dos puntos, otro PDF abierto o cualquier PDF del disco, con desplazamiento sincronizado opcional para comparar versiones.
- Abre también imágenes, XPS y EPUB convirtiéndolos a PDF.
- Panel de capas para planos: mostrar u ocultar cada capa y elegir cuáles se ven al abrir el PDF.
- Etiquetas de página (i, ii, iii o A-101...) en el cuadro de página y las miniaturas, con editor y vista previa.

**Organizar**
- Arrastrar miniaturas para reordenar, en el panel lateral o en el modo Organizar a pantalla completa (Ctrl+G).
- Girar, eliminar, duplicar, insertar páginas en blanco o de otros PDF (también soltándolos sobre las miniaturas).
- Selección múltiple con Ctrl y Mayús. Deshacer y rehacer (hasta 30 pasos).

**Herramientas**
- Unir, dividir (por rangos, cada N páginas o una por archivo), alternar páginas de dos PDF, extraer, rotar.
- Comprimir (tres niveles), PDF a imagen (PNG/JPG) e imagen a PDF.
- Marca de agua de texto o imagen y números de página, con vista previa en vivo.
- Proteger con AES-256 y desbloquear, recortar márgenes (automático o manual), cambiar el tamaño de página.
- Rellenar formularios y aplanar.
- Reconocimiento de texto (OCR) en español, inglés, portugués y francés incluido, sin instalar nada más.
- Tablas a Excel (.xlsx) o CSV, una hoja por tabla, con los números como números.
- Extraer imágenes con su calidad original, cada una una sola vez.
- Limpiar datos ocultos antes de compartir (metadatos, JavaScript, adjuntos, miniaturas y, si se quiere, comentarios, enlaces y formularios).
- Propiedades del documento editables (título, autor, asunto, palabras clave), también en los metadatos XMP.

**Comentarios**
- Panel de comentarios por página con autor y fecha, cita del texto resaltado y respuestas en hilo.
- Comentar una página, una anotación o un resaltado («Resaltar y comentar»); editar y eliminar con deshacer.
- Compatibles con Acrobat y otros lectores (notas PDF estándar con respuestas /IRT).

**Anotar y firmar**
- Lápiz, resaltador, subrayado, tachado, rectángulo, elipse, flecha, línea, polígono, cuadro de texto y notas.
- Sellos (Aprobado, Borrador, Revisado... en el idioma de la interfaz, con nombre y fecha) y sellos personalizados.
- Medir a escala: distancias, perímetros y áreas, ajustándose a lo dibujado, con la escala del propio PDF, las habituales de plano o calibrando con una medida conocida. Las medidas son anotaciones de medida estándar, como las de Acrobat.
- Firma: se dibuja una vez, se guarda y se estampa donde se quiera.
- Goma para borrar anotaciones y Censurar, que elimina de forma definitiva el contenido de una zona o todas las apariciones de un texto.
- Todo se guarda como anotaciones PDF estándar, visibles en cualquier lector.

**Firma digital con certificado**
- Firma PAdES con un certificado personal o de empresa (archivo .pfx / .p12, por ejemplo de la FNMT): sello visible con nombre, fecha, motivo y lugar, y opcionalmente la firma manuscrita guardada.
- La firma se añade como revisión incremental, sin reescribir el documento: los lectores muestran que no se ha modificado después de firmar.
- Los PDF firmados también se guardan de forma incremental: los comentarios añadidos después mantienen las firmas válidas.
- Sello de tiempo opcional de un servidor TSA y verificación de las firmas de cualquier PDF (quién firmó, cuándo y si el documento sigue intacto).

**Archivos adjuntos**
- Ver, abrir, guardar, añadir y quitar los archivos que lleva un PDF (del documento y de sus páginas); un contador en la barra avisa cuando los hay.

**Modelos 3D**
- Meter piezas STEP, IGES, STL, OBJ, 3MF, glTF, PRC o U3D en un PDF como vista 3D interactiva, en un recuadro o en una página nueva.
- Medir distancias, ángulos y radios en el visor 3D, pegándose a esquinas y aristas, con las unidades del archivo cuando las trae.
- Visor 3D para los PDF con modelos U3D y PRC (Acrobat, Tetra4D, exportaciones CAD, LaTeX/media9) y para archivos `.u3d` y `.prc` sueltos.
- Vistas guardadas en el PDF, giro, desplazamiento y zoom al cursor, vistas estándar, modos sólido, contorno, alámbrico y transparente, iluminación, corte por plano, panel de piezas para ocultar o aislar, y captura a imagen.
- Lector U3D propio en Python. El PRC usa nanoPRC (incluida teselación comprimida) con un lector propio de respaldo.

Paleta de comandos con Ctrl+K y atajos de teclado estándar (lista completa en Menú > Atajos de teclado).

## Instalación

| Sistema | Archivo | Cómo |
|---|---|---|
| Windows 10 (1903+) / 11 | `BlackPDF-<versión>-Setup.exe` | Ejecutar el instalador. No pide permisos de administrador. Añade BlackPDF a «Abrir con» de los PDF y, si se marca la opción, abre la configuración para elegirlo como lector predeterminado. |
| Linux (cualquier distribución con glibc 2.35+) | `BlackPDF-<versión>-x86_64.AppImage` | `chmod +x BlackPDF-*.AppImage` y ejecutarlo. |
| Debian, Ubuntu, Mint... | `blackpdf_<versión>_amd64.deb` | `sudo apt install ./blackpdf_*_amd64.deb`. Aparece en el menú de aplicaciones y en «Abrir con». |

**Actualizaciones.** Comprobar si hay versiones nuevas es lo único que BlackPDF hace por internet, y lo decides al abrirlo por primera vez (Menú > Avisar de versiones nuevas). Consulta GitHub como mucho una vez al mes y no envía ningún dato tuyo; los avisos se pueden quitar desde el propio aviso. Cada descarga se compara con las sumas SHA-256 publicadas. La instalación de Windows y el AppImage se actualizan solos y vuelven a abrir tus documentos; con el `.deb` se abre el instalador del sistema. También puedes comprobarlo cuando quieras en Menú > Buscar actualizaciones.

## Desarrollo

Requisitos: Python 3.12 o superior (se recomienda la versión estable más reciente), y CMake 3.24+ con un compilador de C para la biblioteca nativa de modelos PRC.

```bash
uv venv --python 3.15 .venv            # o: python3 -m venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python native/prc/build.py    # biblioteca PRC (opcional en desarrollo)
.venv/bin/python -m blackpdf            # arrancar la aplicación
.venv/bin/python -m pytest             # tests
.venv/bin/python -m blackpdf --self-test   # comprobación rápida de una instalación
```

Estructura:

```
blackpdf/core/      operaciones PDF puras con PyMuPDF (sin Qt), cada una con su test
blackpdf/ui/        ventanas, visor, miniaturas, diálogos y tema
blackpdf/procworker.py  ejecución de herramientas en un subproceso
blackpdf/app.py     punto de entrada
tests/              tests del núcleo y pruebas de humo de la interfaz
native/prc/         capa en C sobre nanoPRC para leer modelos PRC (se compila con build.py)
packaging/          PyInstaller, AppImage, .deb e instalador de Windows (Inno Setup)
```

PyMuPDF no admite varios hilos, así que las operaciones largas se ejecutan en un subproceso supervisado desde el `QThreadPool`: la interfaz nunca se bloquea y cancelar es inmediato.

### Traducciones

Los textos del código están en español. Las traducciones están en `translations/blackpdf_<idioma>.ts` (formato de Qt Linguist):

```bash
.venv/bin/python scripts/i18n.py update   # añade los textos nuevos a los .ts
.venv/bin/pyside6-linguist translations/blackpdf_fr.ts   # traducir (o con un editor de texto)
.venv/bin/python scripts/i18n.py build    # compila los .qm que usa la aplicación
```

Los tests fallan si queda algún texto sin traducir o si una traducción pierde un marcador como `{0}`.

### Empaquetado

- Linux: `packaging/linux/build_linux.sh` genera el AppImage y el `.deb` en `packaging/out/`.
- Windows nativo: `powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1` (requiere Inno Setup 6).
- Windows desde Linux: `packaging/windows/build_windows_wine.sh` (requiere Wine, Xvfb y mingw-w64 para la DLL de PRC).
- GitHub Actions (`.github/workflows/build.yml`) ejecuta los tests y, al publicar una etiqueta `v*`, compila los tres paquetes y crea la versión.

## Licencia

BlackPDF es software libre bajo la [GNU Affero General Public License v3.0](LICENSE), la misma licencia que PyMuPDF y MuPDF.

Componentes de terceros: PyMuPDF y MuPDF (AGPL-3.0), Qt y PySide6 (LGPL-3.0), pyHanko (MIT), gmsh (GPL-2.0 o posterior) con Open CASCADE Technology (LGPL-2.1 con excepción) para leer STEP e IGES, nanoPRC de CascadiaVoxel LLC (AGPL-3.0) y zlib (licencia zlib) en la biblioteca nativa de PRC, tipografía Inter (SIL Open Font License), iconos Lucide (ISC) y datos de idioma de Tesseract (Apache 2.0). La tabla de valores frecuentes de los números PRC procede de Asymptote (LGPL-3.0) y el modelo `cubo.u3d` de la implementación de referencia de U3D (Apache 2.0).
