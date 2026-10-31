"""Mensajes de error claros para el usuario y registro técnico en archivo.

Las trazas nunca se muestran: van al registro (blackpdf.log) por si hace
falta diagnosticar un problema.
"""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from ..core import certstore
from ..core import errors as core_errors

log = logging.getLogger("blackpdf")


def _tr(text: str) -> str:
    return QCoreApplication.translate("Errors", text)


def log_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    folder = Path(base) if base else Path.home() / ".blackpdf"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "blackpdf.log"


def setup_logging() -> None:
    if log.handlers:
        return
    try:
        handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _name(path: str) -> str:
    return Path(path).name if path else _tr("el documento")


def user_message(exc: BaseException) -> tuple[str, str]:
    """(título, explicación) para mostrar al usuario."""
    params = getattr(exc, "params", {}) or {}
    path = str(params.get("path", "") or "")
    if isinstance(exc, core_errors.PasswordRequiredError):
        return _tr("Documento protegido"), _tr("«{0}» está protegido con contraseña.").format(_name(path))
    if isinstance(exc, core_errors.WrongPasswordError):
        return _tr("Contraseña incorrecta"), _tr("La contraseña no es correcta. Inténtalo de nuevo.")
    if isinstance(exc, core_errors.FileOpenError):
        return _tr("No se puede abrir el archivo"), _tr(
            "No se ha podido leer «{0}». Comprueba que existe y que tienes permiso para abrirlo."
        ).format(_name(path))
    if isinstance(exc, core_errors.NotPdfError):
        return _tr("Formato no compatible"), _tr(
            "«{0}» no es un PDF válido o está dañado y no se puede recuperar."
        ).format(_name(path))
    if isinstance(exc, core_errors.InvalidRangeError):
        count = params.get("page_count", 0)
        return _tr("Rango de páginas no válido"), _tr(
            "Escribe páginas entre 1 y {0}, separadas por comas. Ejemplo: 1-3, 5, 8-10."
        ).format(count)
    if isinstance(exc, core_errors.LastPageError):
        return _tr("No se puede eliminar"), _tr("Un PDF debe conservar al menos una página.")
    if isinstance(exc, core_errors.EmptyInputError):
        return _tr("Falta información"), _tr("Selecciona al menos un archivo o una página para continuar.")
    if isinstance(exc, core_errors.WriteError):
        return _tr("No se puede guardar"), _tr(
            "No se ha podido escribir «{0}». Comprueba que la carpeta existe, que hay espacio libre "
            "y que el archivo no está abierto en otro programa."
        ).format(_name(path))
    if isinstance(exc, core_errors.IncrementalSaveError):
        return _tr("No se pueden conservar las firmas"), _tr(
            "Este PDF no admite que se le añadan los cambios al final, así que habría que reescribirlo y sus "
            "firmas digitales dejarían de ser válidas. Vuelve a guardar si quieres hacerlo de todos modos."
        )
    if isinstance(exc, core_errors.OcrUnavailableError):
        return _tr("Reconocimiento de texto no disponible"), _tr(
            "Faltan los datos de idioma del reconocimiento de texto. Reinstala BlackPDF para recuperarlos."
        )
    if isinstance(exc, core_errors.CadUnavailableError):
        return _tr("No se puede leer el archivo de CAD"), _tr(
            "Para leer STEP e IGES hace falta el componente de CAD de BlackPDF, que viene en los instaladores. "
            "Reinstala BlackPDF o exporta la pieza como STL u OBJ."
        )
    if isinstance(exc, core_errors.Model3DEmptyError):
        return _tr("Modelo 3D vacío"), _tr("El modelo 3D no contiene piezas visibles.")
    if isinstance(exc, core_errors.Model3DError):
        return _tr("No se puede abrir el modelo 3D"), _tr(
            "El modelo 3D está dañado o usa una variante del formato que BlackPDF todavía no puede mostrar."
        )
    if isinstance(exc, core_errors.UpdateCheckError):
        return _tr("No se puede comprobar"), _tr(
            "No se ha podido consultar si hay una versión nueva. Comprueba tu conexión a internet "
            "e inténtalo más tarde."
        )
    if isinstance(exc, core_errors.UpdateVerifyError):
        return _tr("Descarga descartada"), _tr(
            "El archivo descargado no coincide con el publicado y se ha borrado por seguridad. "
            "Inténtalo de nuevo más tarde."
        )
    if isinstance(exc, core_errors.UpdateDownloadError):
        return _tr("No se ha podido descargar"), _tr(
            "La descarga de la versión nueva se ha interrumpido. Comprueba tu conexión e inténtalo de nuevo."
        )
    signing = _signing_message(exc, params)
    if signing is not None:
        return signing
    if isinstance(exc, core_errors.InvalidOptionError):
        return _tr("Opción no válida"), _tr("Revisa las opciones marcadas e inténtalo de nuevo.")
    if isinstance(exc, core_errors.BlackPdfError):
        return _tr("No se ha podido completar"), _tr("La operación no se ha podido completar.")
    if isinstance(exc, MemoryError):
        return _tr("Memoria insuficiente"), _tr("El documento es demasiado grande para la memoria disponible.")
    if isinstance(exc, PermissionError):
        return _tr("Permiso denegado"), _tr("No tienes permiso para acceder a ese archivo o carpeta.")
    if isinstance(exc, OSError):
        return _tr("Error de archivo"), _tr("No se ha podido acceder al archivo. Comprueba la ruta y los permisos.")
    return _tr("Error inesperado"), _tr(
        "Ha ocurrido un error inesperado. Los detalles se han guardado en el registro de la aplicación."
    )


def _signing_message(exc: BaseException, params: dict) -> tuple[str, str] | None:
    from ..core import signatures

    if isinstance(exc, certstore.CertificatePasswordError):
        kind = params.get("kind", "file")
        if kind == "pin":
            return _tr("PIN incorrecto"), _tr(
                "El PIN de la tarjeta no es correcto. Cuidado: tras varios intentos fallidos, la tarjeta se bloquea.")
        if kind == "nss":
            return _tr("Contraseña incorrecta"), _tr("La contraseña principal del navegador no es correcta.")
        return _tr("Contraseña incorrecta"), _tr("La contraseña del certificado no es correcta.")
    if isinstance(exc, certstore.CertificateAccessError):
        reason = params.get("reason", "")
        if reason == "cancelled":
            return _tr("Firma cancelada"), _tr("Se ha cancelado la petición del PIN: el documento no se ha firmado.")
        if reason == "pin_locked":
            return _tr("Tarjeta bloqueada"), _tr(
                "El PIN está bloqueado por demasiados intentos fallidos. Para desbloquearlo, acude a quien te dio "
                "la tarjeta (el DNIe, en un punto de actualización de una comisaría).")
        if reason == "no_card":
            return _tr("No se encuentra la tarjeta"), _tr(
                "Conecta el lector, mete la tarjeta y vuelve a intentarlo.")
        return _tr("No se puede usar el certificado"), _tr(
            "No se ha podido usar la clave privada del certificado. Comprueba que sigue instalado.")
    if isinstance(exc, certstore.CertificateError):
        return _tr("Certificado no válido"), _tr(
            "El certificado está caducado, aún no es válido o no tiene su clave privada.")
    if isinstance(exc, signatures.TimestampError):
        return _tr("Sin sello de tiempo"), _tr(
            "No se ha podido conectar con el servidor del sello de tiempo. Comprueba tu conexión o firma sin él.")
    return None


def show_error(parent: QWidget | None, exc: BaseException, context: str = "") -> None:
    if not isinstance(exc, core_errors.BlackPdfError):
        log.error("%s\n%s", context or "Error", "".join(traceback.format_exception(exc)))
    title, text = user_message(exc)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(f"<b>{title}</b>")
    box.setInformativeText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def show_crash(parent: QWidget | None, details: str) -> None:
    """Fallo en un subproceso: se registra la traza y se muestra un aviso genérico."""
    log.error("Fallo en operación en segundo plano\n%s", details)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(_tr("Error inesperado"))
    box.setText("<b>" + _tr("No se ha podido completar la operación") + "</b>")
    box.setInformativeText(
        _tr("Puede que el archivo esté dañado o use una característica no compatible. "
            "Los detalles se han guardado en el registro.")
    )
    box.exec()


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("Excepción no controlada\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        app = QApplication.instance()
        if app is not None:
            try:
                show_error(app.activeWindow(), exc)
            except Exception:
                pass

    sys.excepthook = hook
