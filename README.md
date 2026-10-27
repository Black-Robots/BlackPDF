# BlackPDF

**English** · [Español](README.es.md)

A free and open PDF viewer and editor for the desktop. It does what online PDF services do, but it works offline: your documents never leave your computer.

Available for **Windows 10/11** and **Linux**, in English, Spanish, Portuguese and French (it follows the system language; change it in Menu > Language).

**[Download the latest version](https://github.com/Black-Robots/BlackPDF/releases/latest)** · [Website](https://blackrobots.org/blackpdf) · [Report a bug](https://github.com/Black-Robots/BlackPDF/issues)

![BlackPDF with a document and its comments](docs/screenshots/document.webp)

<p align="center">
  <img src="docs/screenshots/home.webp" width="49%" alt="Home screen with all the tools">
  <img src="docs/screenshots/viewer3d.webp" width="49%" alt="3D viewer">
</p>

## Features

**Viewer**
- Tabs for several documents, recent files and reopening at the last page read.
- Continuous scrolling with tiled rendering (only what is visible is drawn), Ctrl+wheel zoom, fit to width or page.
- Night mode that keeps colors, full screen, thumbnails, outline and search with highlighting.
- Navigation history (Alt+Left or the mouse Back button) and internal and external links.
- Split view (Ctrl+\\): the same document at two places, another open PDF or any PDF on disk, with optional synchronized scrolling to compare versions.
- Also opens images, XPS and EPUB by converting them to PDF.
- Layers panel for drawings: show or hide each layer and choose which ones are visible when the PDF opens.
- Page labels (i, ii, iii or A-101...) in the page box and the thumbnails, with an editor and live preview.

**Organize**
- Drag thumbnails to reorder pages, in the side panel or in full-screen Organize mode (Ctrl+G).
- Rotate, delete, duplicate, insert blank pages or pages from other PDFs (also by dropping files onto the thumbnails).
- Multiple selection with Ctrl and Shift. Undo and redo (up to 30 steps).

**Tools**
- Merge, split (by ranges, every N pages or one per file), alternate the pages of two PDFs, extract, rotate.
- Compress (three levels), PDF to image (PNG/JPG) and image to PDF.
- Text or image watermarks and page numbers, with a live preview.
- Protect with AES-256 and unlock, crop margins (automatic or manual), change the page size.
- Fill in and flatten forms.
- Built-in text recognition (OCR) in English, Spanish, Portuguese and French, with nothing else to install.
- Tables to Excel (.xlsx) or CSV, one sheet per table, with numbers stored as numbers.
- Extract images at their original quality, each one once.
- Remove hidden data before sharing (metadata, JavaScript, attachments, thumbnails and, optionally, comments, links and forms).
- Editable document properties (title, author, subject, keywords), also in the XMP metadata.

**Comments**
- Comments panel per page with author and date, quote of the highlighted text and threaded replies.
- Comment on a page, an annotation or a highlight ("Highlight and comment"); edit and delete with undo.
- Compatible with Acrobat and other readers (standard PDF notes with /IRT replies).

**Annotate and sign**
- Pen, highlighter, underline, strikethrough, rectangle, ellipse, arrow, line, polygon, text box and notes.
- Stamps (Approved, Draft, Reviewed... in the interface language, with name and date) and custom stamps.
- Measure to scale: distances, perimeters and areas, snapping to the drawing, with the PDF's own scale, the usual drawing scales or a calibration with a known length. Measurements are standard measurement annotations, like Acrobat's.
- Signature: draw it once, save it and stamp it wherever you want.
- Eraser for annotations and Redact, which permanently removes the content of an area or every occurrence of a text.
- Everything is saved as standard PDF annotations, visible in any reader.

**Digital signature with a certificate**
- PAdES signatures with a personal, national eID or company certificate (.pfx / .p12 file): visible stamp with name, date, reason and location, optionally with your saved handwritten signature.
- The signature is added as an incremental revision, without rewriting the document, so readers show that it has not been modified after signing.
- Signed PDFs are saved incrementally too: comments added later keep the signatures valid.
- Optional timestamp from a TSA server, and verification of the signatures in any PDF (who signed, when and whether the document is still intact).

**Attachments**
- See, open, save, add and remove the files embedded in a PDF (document and page attachments); a counter in the toolbar shows when there are any.

**3D models**
- Insert STEP, IGES, STL, OBJ, 3MF, glTF, PRC or U3D parts into a PDF as an interactive 3D view, in a box or on a new page.
- Measure distances, angles and radii in the 3D viewer, snapping to corners and edges, with the file's units when it has them.
- 3D viewer for PDFs with U3D and PRC models (Acrobat, Tetra4D, CAD exports, LaTeX/media9) and for standalone `.u3d` and `.prc` files.
- Views saved in the PDF, rotate, pan and zoom to the cursor, standard views, solid, outline, wireframe and transparent modes, lighting, section plane, parts panel to hide or isolate parts, and export to an image.
- Own U3D reader in Python. PRC uses nanoPRC (including compressed tessellation) plus an own reader for exact geometry (B-rep, NURBS and Bézier surfaces from Asymptote).

Command palette with Ctrl+K and standard keyboard shortcuts (full list in Menu > Keyboard shortcuts).

## Installation

| System | File | How |
|---|---|---|
| Windows 10 (1903+) / 11 | `BlackPDF-<version>-Setup.exe` | Run the installer. It does not need administrator rights. It adds BlackPDF to "Open with" for PDF files and, if you tick the option, opens the settings to make it the default reader. |
| Linux (any distribution with glibc 2.35+) | `BlackPDF-<version>-x86_64.AppImage` | `chmod +x BlackPDF-*.AppImage` and run it. |
| Debian, Ubuntu, Mint... | `blackpdf_<version>_amd64.deb` | `sudo apt install ./blackpdf_*_amd64.deb`. It appears in the applications menu and in "Open with". |

The Windows installer is not code-signed yet, so Windows SmartScreen may show a warning the first time ("More info" > "Run anyway").

**Updates.** Checking for new versions is the only thing BlackPDF does online, and you decide on first launch (Menu > Notify me of new versions). It asks GitHub at most once a month and sends none of your data; you can also turn the notices off from the notice itself. Each download is checked against the published SHA-256 sums. The Windows installation and the AppImage update themselves and reopen your documents; with the `.deb` the system installer opens. You can also check at any time in Menu > Check for updates.

## Development

Requirements: Python 3.12 or later (the latest stable release is recommended), and CMake 3.24+ with a C compiler for the native PRC library.

```bash
uv venv --python 3.15 .venv            # or: python3 -m venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python native/prc/build.py    # native PRC library (optional for development)
.venv/bin/python -m blackpdf            # run the application
.venv/bin/python -m pytest             # tests
.venv/bin/python -m blackpdf --self-test   # quick check of an installation
```

Layout:

```
blackpdf/core/      pure PDF operations with PyMuPDF (no Qt), each with its test
blackpdf/ui/        windows, viewer, thumbnails, dialogs and theme
blackpdf/procworker.py  runs tools in a subprocess
blackpdf/app.py     entry point
tests/              core tests and user interface smoke tests
translations/       Qt Linguist translation files (en, pt, fr)
native/prc/         C layer over nanoPRC to read PRC models (built with build.py)
packaging/          PyInstaller, AppImage, .deb and Windows installer (Inno Setup)
```

PyMuPDF is not thread-safe, so long operations run in a subprocess supervised from the `QThreadPool`: the interface never freezes and cancelling is immediate. The source code and comments are in Spanish.

### Translations

The texts in the code are in Spanish. Translations live in `translations/blackpdf_<language>.ts` (Qt Linguist format):

```bash
.venv/bin/python scripts/i18n.py update   # adds new texts to the .ts files
.venv/bin/pyside6-linguist translations/blackpdf_fr.ts   # translate (or use a text editor)
.venv/bin/python scripts/i18n.py build    # compiles the .qm files used by the application
```

The tests fail if any text is left untranslated or if a translation loses a placeholder such as `{0}`. New languages are welcome: add the code to `LANGUAGES` in `scripts/i18n.py` and `blackpdf/ui/i18n.py`.

### Packaging

- Linux: `packaging/linux/build_linux.sh` builds the AppImage and the `.deb` in `packaging/out/`.
- Native Windows: `powershell -ExecutionPolicy Bypass -File packaging\windows\build_windows.ps1` (requires Inno Setup 6).
- Windows from Linux: `packaging/windows/build_windows_wine.sh` (requires Wine, Xvfb and mingw-w64 for the PRC DLL).
- GitHub Actions (`.github/workflows/build.yml`) runs the tests and, when a `v*` tag is pushed, builds the three packages and creates the release.

## License

BlackPDF is free software under the [GNU Affero General Public License v3.0](LICENSE), the same license as PyMuPDF and MuPDF. It is developed by [The Black Robots Foundation](https://blackrobots.org).

Third-party components: PyMuPDF and MuPDF (AGPL-3.0), Qt and PySide6 (LGPL-3.0), pyHanko (MIT), gmsh (GPL-2.0-or-later) with Open CASCADE Technology (LGPL-2.1 with exception) to read STEP and IGES, nanoPRC by CascadiaVoxel LLC (AGPL-3.0) and zlib (zlib license) in the native PRC library, Inter typeface (SIL Open Font License), Lucide icons (ISC) and Tesseract language data (Apache 2.0). The table of frequent values for PRC numbers comes from Asymptote (LGPL-3.0) and the `cubo.u3d` model from the U3D reference implementation (Apache 2.0).
