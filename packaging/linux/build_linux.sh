#!/usr/bin/env bash
# Compila BlackPDF para Linux y genera un AppImage y un paquete .deb.
# Uso: packaging/linux/build_linux.sh   (desde la raíz del proyecto)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
VERSION="$("$PYTHON" -c 'import blackpdf; print(blackpdf.__version__)')"
OUT="$ROOT/packaging/out"
BUILD="$ROOT/build"
mkdir -p "$OUT" "$BUILD/tools"

echo "==> Biblioteca nativa PRC"
env -u PYTHONPATH "$PYTHON" native/prc/build.py

echo "==> PyInstaller"
env -u PYTHONPATH "$PYTHON" -m PyInstaller --noconfirm --clean \
    --distpath "$BUILD/dist-linux" --workpath "$BUILD/work-linux" packaging/blackpdf.spec
DIST="$BUILD/dist-linux/BlackPDF"
env -u PYTHONPATH "$DIST/BlackPDF" --self-test

install_icons() {
    local base="$1"
    for size in 16 24 32 48 64 128 256 512; do
        install -Dm644 "blackpdf/resources/app/blackpdf-$size.png" "$base/icons/hicolor/${size}x${size}/apps/blackpdf.png"
    done
    install -Dm644 blackpdf/resources/app/blackpdf.svg "$base/icons/hicolor/scalable/apps/blackpdf.svg"
}

echo "==> AppImage"
APPDIR="$BUILD/BlackPDF.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a "$DIST" "$APPDIR/usr/lib/blackpdf"
install -Dm644 packaging/linux/blackpdf.desktop "$APPDIR/usr/share/applications/blackpdf.desktop"
install -Dm644 packaging/linux/org.blackrobots.BlackPDF.metainfo.xml \
    "$APPDIR/usr/share/metainfo/org.blackrobots.BlackPDF.metainfo.xml"
install_icons "$APPDIR/usr/share"
cp packaging/linux/blackpdf.desktop "$APPDIR/blackpdf.desktop"
cp blackpdf/resources/app/blackpdf-256.png "$APPDIR/blackpdf.png"
ln -sf blackpdf.png "$APPDIR/.DirIcon"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
unset PYTHONPATH PYTHONHOME
exec "$HERE/usr/lib/blackpdf/BlackPDF" "$@"
RUN
chmod +x "$APPDIR/AppRun"
TOOL="$BUILD/tools/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT/BlackPDF-$VERSION-x86_64.AppImage"

echo "==> Paquete .deb"
PKG="$BUILD/deb/blackpdf_${VERSION}_amd64"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin"
cp -a "$DIST" "$PKG/opt/blackpdf"
ln -sf /opt/blackpdf/BlackPDF "$PKG/usr/bin/blackpdf"
install -Dm644 packaging/linux/blackpdf.desktop "$PKG/usr/share/applications/blackpdf.desktop"
install -Dm644 packaging/linux/org.blackrobots.BlackPDF.metainfo.xml \
    "$PKG/usr/share/metainfo/org.blackrobots.BlackPDF.metainfo.xml"
install_icons "$PKG/usr/share"
install -Dm644 LICENSE "$PKG/usr/share/doc/blackpdf/copyright"
SIZE="$(du -sk "$PKG" | cut -f1)"
# libnss3 (recomendado): firmar con los certificados de Firefox y Chrome; opensc: con tarjetas como el DNIe.
cat > "$PKG/DEBIAN/control" <<CTRL
Package: blackpdf
Version: $VERSION
Section: office
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Depends: libc6 (>= 2.35), libgl1, libegl1, libfontconfig1, libxkbcommon0, libxinerama1
Recommends: libnss3
Suggests: opensc
Maintainer: The Black Robots Foundation <hello@blackrobots.org>
Homepage: https://blackrobots.org/blackpdf
Description: Visor y editor de PDF libre y sin conexión
 BlackPDF permite ver, organizar, unir, dividir, comprimir, convertir,
 anotar, firmar, proteger y reconocer texto en documentos PDF sin
 conexión a internet. Los archivos nunca salen del equipo.
CTRL
cat > "$PKG/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
exit 0
POST
cp "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
dpkg-deb --root-owner-group --build -Zxz "$PKG" "$OUT/blackpdf_${VERSION}_amd64.deb"

echo "==> Listo:"
ls -lh "$OUT"
