"""Firma digital con certificado (PAdES) y verificación de firmas.

Una firma digital no es un dibujo: guarda en el PDF una huella criptográfica
del documento, hecha con la clave privada del certificado (FNMT, DNIe,
certificados de empresa...). Cualquiera puede comprobar quién firmó y si el
documento se ha modificado después.

Se usa pyHanko. El certificado puede ser un archivo PKCS#12 (.pfx / .p12)
protegido con contraseña o uno del equipo (almacén de Windows, Firefox,
Chrome, tarjetas): ver certstore. La apariencia visible se dibuja con PyMuPDF
y la fuente Inter para que acentos y eñes se vean bien.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .certstore import (  # noqa: F401  (se importan desde aquí en el resto del programa)
    CertificateAccessError,
    CertificateError,
    CertificateInfo,
    CertificatePasswordError,
    Identity,
    load_certificate,
    open_signer,
)
from .document import PdfSource, open_pdf, read_file
from .errors import BlackPdfError, InvalidOptionError, WriteError
from .files import atomic_write
from .progress import ProgressCallback, report
from .text import FONTS_DIR

# pyHanko registra como error casos previstos (certificado autofirmado, sin cadena de
# confianza): el resultado se informa en SignatureStatus, no hace falta la traza.
for _name in ("pyhanko", "pyhanko_certvalidator"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

DEFAULT_TSA = "https://freetsa.org/tsr"
STAMP_SIZE = (230.0, 74.0)
STAMP_MARGIN = 36.0
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")


class TimestampError(BlackPdfError):
    """No se pudo obtener el sello de tiempo (sin conexión o servidor caído)."""


class SignatureError(BlackPdfError):
    """La firma no se pudo aplicar al documento."""


# ======================================================================
# Firmar
# ======================================================================
@dataclass(frozen=True)
class StampText:
    """Textos del sello visible, en el idioma de quien firma (la interfaz los traduce)."""

    signed_by: str = "Firmado digitalmente por"
    date: str = "Fecha: {0}"
    reason: str = "Motivo: {0}"
    location: str = "Lugar: {0}"
    date_format: str = "%d/%m/%Y %H:%M"


@dataclass
class SignOptions:
    visible: bool = True
    page: int = -1
    """Página del sello visible (-1 = última)."""
    rect: tuple[float, float, float, float] | None = None
    """Zona visible del sello (coordenadas visibles); None = según `position`."""
    position: str = "bottom-right"
    reason: str = ""
    location: str = ""
    contact: str = ""
    handwritten: list = field(default_factory=list)
    """Trazos normalizados de la firma manuscrita a incluir en el sello."""
    timestamp_url: str = ""
    """Servidor de sellado de tiempo (TSA). Vacío = sin sello de tiempo."""
    field_name: str = ""
    text: StampText = field(default_factory=StampText)
    """Textos del sello visible."""
    window: int = 0
    """Ventana (identificador del sistema) sobre la que Windows muestra lo que pida al firmar, como el PIN."""


def stamp_rect(page: pymupdf.Page, position: str, size: tuple[float, float] = STAMP_SIZE) -> pymupdf.Rect:
    """Zona (visible) del sello según la esquina elegida."""
    if position not in POSITIONS:
        raise InvalidOptionError(position, option="position")
    width, height = size
    rect = page.rect
    x0 = rect.x0 + STAMP_MARGIN if position.endswith("left") else rect.x1 - STAMP_MARGIN - width
    y0 = rect.y0 + STAMP_MARGIN if position.startswith("top") else rect.y1 - STAMP_MARGIN - height
    return pymupdf.Rect(x0, y0, x0 + width, y0 + height)


def _pdf_box(page: pymupdf.Page, visual: pymupdf.Rect) -> tuple[float, float, float, float]:
    """Coordenadas visibles -> espacio de usuario PDF (origen abajo a la izquierda).

    No se usa `page.transformation_matrix`: en páginas giradas y recortadas no
    incluye el desplazamiento de la CropBox.
    """
    unrotated = (pymupdf.Rect(visual) * page.derotation_matrix).normalize()
    media, crop = page.mediabox, page.cropbox
    return (
        crop.x0 + unrotated.x0,
        media.y1 - crop.y0 - unrotated.y1,
        crop.x0 + unrotated.x1,
        media.y1 - crop.y0 - unrotated.y0,
    )


def build_appearance(
    width: float,
    height: float,
    signer: str,
    when: dt.datetime,
    reason: str = "",
    location: str = "",
    handwritten: list | None = None,
    text: StampText | None = None,
) -> bytes:
    """PDF de una página con el aspecto del sello: firma manuscrita y datos."""
    text = text or StampText()
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    regular = str(FONTS_DIR / "Inter-Regular.ttf")
    bold = str(FONTS_DIR / "Inter-SemiBold.ttf")
    text_x = 6.0
    if handwritten:
        from .annotations import normalize_strokes

        unit = normalize_strokes(handwritten)
        area = pymupdf.Rect(4, 6, width * 0.42, height - 6)
        xs = [p[0] for s in unit for p in s]
        ys = [p[1] for s in unit for p in s]
        w_unit, h_unit = max(max(xs), 1e-3), max(max(ys), 1e-3)
        scale = min(area.width / w_unit, area.height / h_unit)
        off_x = area.x0 + (area.width - w_unit * scale) / 2
        off_y = area.y0 + (area.height - h_unit * scale) / 2
        shape = page.new_shape()
        for stroke in unit:
            points = [pymupdf.Point(off_x + x * scale, off_y + y * scale) for x, y in stroke]
            if len(points) == 1:
                points.append(points[0] + (0.1, 0))
            shape.draw_polyline(points)
        shape.finish(color=(0.07, 0.11, 0.33), width=1.4, closePath=False, lineCap=1, lineJoin=1)
        shape.commit()
        text_x = width * 0.46
    lines = [(text.signed_by, regular, 6.5, (0.42, 0.42, 0.46))]
    lines.append((signer, bold, 8.5, (0.08, 0.08, 0.1)))
    lines.append((text.date.format(when.astimezone().strftime(text.date_format)), regular, 6.5, (0.25, 0.25, 0.28)))
    if reason:
        lines.append((text.reason.format(reason), regular, 6.5, (0.25, 0.25, 0.28)))
    if location:
        lines.append((text.location.format(location), regular, 6.5, (0.25, 0.25, 0.28)))
    total = sum(size * 1.3 for _, _, size, _ in lines)
    y = max(4.0, (height - total) / 2)
    available = width - text_x - 4
    for line, font_file, size, color in lines:
        font = pymupdf.Font(fontfile=font_file)
        while size > 4.5 and font.text_length(line, fontsize=size) > available:
            size -= 0.5
        y += size * 1.05
        page.insert_text((text_x, y), line, fontsize=size, fontfile=font_file,
                         fontname="InterB" if font_file == bold else "InterR", color=color)
        y += size * 0.25
    page.draw_line((text_x - 4, 6), (text_x - 4, height - 6), color=(0.898, 0.282, 0.302), width=1.2)
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _source_bytes(source: PdfSource, password: str | None) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, pymupdf.Document):
        data = source.tobytes()
    else:
        data = read_file(source)
    # Los PDF cifrados se firman sobre una copia descifrada.
    probe = pymupdf.open(stream=data, filetype="pdf")
    try:
        encrypted = probe.needs_pass or probe.is_encrypted
    finally:
        probe.close()
    if encrypted:
        doc = open_pdf(data, password)
        try:
            data = doc.tobytes()
        finally:
            doc.close()
    return data


def sign_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    certificate: str | os.PathLike | bytes | Identity,
    certificate_password: str,
    options: SignOptions | None = None,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Firma el documento y lo guarda en `output` (nunca sobre el original).

    ``certificate`` es un archivo .pfx/.p12 (ruta o bytes) o un certificado del equipo
    (``Identity``); ``certificate_password``, su contraseña o el PIN, si hacen falta.
    """
    options = options or SignOptions()
    report(progress, 0, 4)
    with open_signer(certificate, certificate_password, options.window) as (signer, info):
        result = _sign_with(source, signer, info, options, password, progress)
    report(progress, 3, 4)
    try:
        path = atomic_write(output, lambda tmp: Path(tmp).write_bytes(result))
    except OSError as exc:
        raise WriteError(str(exc), path=str(output)) from exc
    report(progress, 4, 4)
    return path


def _sign_with(source: PdfSource, signer, info: CertificateInfo, options: SignOptions, password: str | None,
               progress: ProgressCallback | None) -> bytes:
    from pyhanko import stamp
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    data = _source_bytes(source, password)
    doc = pymupdf.open(stream=data, filetype="pdf")
    existing = {w.field_name for p in doc for w in (p.widgets() or [])}
    name = options.field_name or next(f"Firma{i}" for i in range(1, 1000) if f"Firma{i}" not in existing)
    report(progress, 1, 4)

    box = None
    appearance_file = None
    page_index = 0
    if options.visible:
        page_index = options.page if options.page >= 0 else doc.page_count - 1
        if not 0 <= page_index < doc.page_count:
            raise InvalidOptionError(f"página {options.page}", option="page")
        page = doc[page_index]
        visual = pymupdf.Rect(options.rect) if options.rect else stamp_rect(page, options.position)
        if visual.is_empty or visual.width < 20 or visual.height < 12:
            raise InvalidOptionError("zona de firma demasiado pequeña", option="rect")
        box = _pdf_box(page, visual)
        appearance = build_appearance(
            visual.width, visual.height, info.name, dt.datetime.now(dt.timezone.utc),
            options.reason, options.location, options.handwritten, options.text,
        )
        handle, appearance_file = tempfile.mkstemp(suffix=".pdf", prefix="blackpdf-firma-")
        os.close(handle)
        Path(appearance_file).write_bytes(appearance)
    doc.close()

    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
        spec = fields.SigFieldSpec(name, on_page=page_index, box=box) if box else fields.SigFieldSpec(name)
        fields.append_signature_field(writer, sig_field_spec=spec)
        meta = signers.PdfSignatureMetadata(
            field_name=name,
            md_algorithm="sha256",
            reason=options.reason or None,
            location=options.location or None,
            contact_info=options.contact or None,
            name=info.name,
            subfilter=fields.SigSeedSubFilter.PADES,
        )
        style = stamp.StaticStampStyle.from_pdf_file(appearance_file, border_width=0) if appearance_file else None
        timestamper = None
        if options.timestamp_url:
            from pyhanko.sign.timestamps import HTTPTimeStamper

            timestamper = HTTPTimeStamper(options.timestamp_url, timeout=15)
        pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style, timestamper=timestamper)
        report(progress, 2, 4)
        result = io.BytesIO()
        try:
            pdf_signer.sign_pdf(writer, output=result)
        except BlackPdfError:
            raise  # La tarjeta o el almacén ya han dicho qué pasa (PIN, cancelado...).
        except Exception as exc:
            if isinstance(exc.__cause__ or exc.__context__, BlackPdfError):
                raise (exc.__cause__ or exc.__context__) from exc
            if timestamper is not None and _looks_like_network(exc):
                raise TimestampError(str(exc), url=options.timestamp_url) from exc
            raise SignatureError(str(exc)) from exc
    finally:
        if appearance_file:
            Path(appearance_file).unlink(missing_ok=True)
    return result.getvalue()


def _looks_like_network(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    chain = exc.__cause__ or exc.__context__
    if chain is not None:
        text += f" {type(chain).__name__} {chain}".lower()
    return any(word in text for word in ("timestamp", "connect", "timeout", "client", "http", "dns", "resolve"))


# ======================================================================
# Verificar
# ======================================================================
@dataclass(frozen=True)
class SignatureStatus:
    field_name: str
    signer: str
    signer_details: str
    issuer: str
    signing_time: str
    reason: str
    location: str
    intact: bool
    """El contenido firmado no ha cambiado."""
    valid: bool
    """La firma criptográfica es correcta."""
    trusted: bool
    """El certificado procede de una autoridad de confianza conocida."""
    covers_whole_file: bool
    """No hay cambios posteriores a la firma."""
    changes_allowed: bool
    """Los cambios posteriores son de los que la firma permite (rellenar formularios, anotar)."""
    timestamped: bool
    page: int
    rect: tuple[float, float, float, float] | None
    subject: tuple[tuple[str, str], ...] = ()
    """Datos del titular en el orden de SUBJECT_FIELDS: ((campo, valor), ...)."""
    signed_at: dt.datetime | None = None
    comments_added: bool = False
    """Después de firmar solo se han añadido o cambiado comentarios (como admite Acrobat)."""

    @property
    def level(self) -> str:
        """"valid", "warning" o "invalid"."""
        if not (self.intact and self.valid):
            return "invalid"
        if not self.trusted or not (self.covers_whole_file or self.changes_allowed):
            return "warning"
        return "valid"


def trust_roots() -> list:
    """Autoridades de confianza: almacén de Mozilla (incluye FNMT y la mayoría de CA europeas)."""
    from asn1crypto import pem, x509

    try:
        import certifi

        data = Path(certifi.where()).read_bytes()
    except Exception:
        return []
    roots = []
    for _, _, der in pem.unarmor(data, multiple=True):
        try:
            roots.append(x509.Certificate.load(der))
        except Exception:
            continue
    return roots


SUBJECT_FIELDS = (
    "common_name", "given_name", "surname", "serial_number", "organization_name", "organizational_unit_name",
    "organization_identifier", "title", "email_address", "locality_name", "state_or_province_name", "country_name",
)

_SUBJECT_LABELS = (
    ("common_name", "Nombre"),
    ("given_name", "Nombre de pila"),
    ("surname", "Apellidos"),
    ("serial_number", "Número de serie"),
    ("organization_name", "Organización"),
    ("organizational_unit_name", "Unidad"),
    ("organization_identifier", "Identificador de la organización"),
    ("title", "Cargo"),
    ("email_address", "Correo"),
    ("locality_name", "Localidad"),
    ("state_or_province_name", "Provincia"),
    ("country_name", "País"),
)


def subject_fields(native: dict) -> tuple[tuple[str, str], ...]:
    """Campos del titular presentes, en un orden fijo: ((campo, valor), ...)."""
    result = []
    for key in SUBJECT_FIELDS:
        value = native.get(key)
        if value:
            result.append((key, ", ".join(value) if isinstance(value, list) else str(value)))
    return tuple(result)


def describe_subject(native: dict, labels: dict[str, str] | None = None) -> str:
    """Datos del titular: «Nombre: ... · Número de serie: ... · País: ES».

    ``labels`` traduce los nombres de los campos (por defecto, en español)."""
    names = dict(_SUBJECT_LABELS)
    if labels:
        names.update(labels)
    return "  ·  ".join(f"{names.get(key, key)}: {value}" for key, value in subject_fields(native))


def _format_time(value) -> str:
    if value is None:
        return ""
    try:
        return value.astimezone().strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def has_signatures(source: PdfSource) -> bool:
    """Comprobación rápida (sin validar) de si el PDF tiene firmas digitales."""
    doc = source if isinstance(source, pymupdf.Document) else pymupdf.open(
        stream=bytes(source) if isinstance(source, (bytes, bytearray)) else read_file(source), filetype="pdf"
    )
    try:
        if doc.get_sigflags() in (1, 3):
            return True
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed:
                    return True
        return False
    finally:
        if doc is not source:
            doc.close()


def comments_only_after(data: bytes, revision_end: int) -> bool:
    """True si lo añadido después de la revisión firmada son solo comentarios.

    pyHanko no reconoce como permitido añadir comentarios; Acrobat sí. Se comparan la
    revisión firmada y la final: mismas páginas, mismo contenido, mismos campos de
    formulario y enlaces, y las anotaciones nuevas o cambiadas son de tipo comentario.
    """
    from .sanitize import _COMMENT_TYPES

    try:
        signed = pymupdf.open(stream=data[:revision_end], filetype="pdf")
        final = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return False
    if signed.page_count != final.page_count:
        return False
    try:
        for before, after in zip(signed, final):
            if before.rect != after.rect or before.rotation != after.rotation:
                return False
            if before.get_contents() != after.get_contents():
                return False
            if any(signed.xref_stream_raw(x) != final.xref_stream_raw(x) for x in before.get_contents()):
                return False
            old = {annot.xref: annot.type[0] for annot in before.annots()}
            new = {annot.xref: annot.type[0] for annot in after.annots()}
            changed = [xref for xref, kind in new.items()
                       if xref not in old or signed.xref_object(xref) != final.xref_object(xref)]
            if any(new[xref] not in _COMMENT_TYPES for xref in changed):
                return False
            fields = lambda page: {(w.field_name, str(w.field_value)) for w in page.widgets()}  # noqa: E731
            if fields(before) != fields(after) or before.get_links() != after.get_links():
                return False
    except Exception:
        return False
    return True


def list_signatures(source: PdfSource, *, progress: ProgressCallback | None = None) -> list[SignatureStatus]:
    """Valida cada firma del documento (sin conexión: no se consulta la revocación)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.sign.validation.status import ModificationLevel
    from pyhanko_certvalidator import ValidationContext

    # Se validan los bytes tal como están en el archivo: reescribirlos invalidaría las firmas.
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, pymupdf.Document):
        raise InvalidOptionError("hace falta el archivo original, no un documento abierto", option="source")
    else:
        data = read_file(source)
    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        embedded = list(reader.embedded_signatures)
    except Exception:
        return []
    locations = _signature_locations(data)
    context_roots = trust_roots()
    results: list[SignatureStatus] = []
    for position, signature in enumerate(embedded):
        report(progress, position, len(embedded))
        context = ValidationContext(trust_roots=context_roots, allow_fetching=False)
        name = signature.field_name or f"Firma{position + 1}"
        try:
            status = validate_pdf_signature(signature, context)
        except Exception:  # firma dañada o formato no admitido: se informa como no válida
            results.append(SignatureStatus(name, "", "", "", "", "", "", False, False, False, False, False,
                                           False, *locations.get(name, (-1, None))))
            continue
        cert = status.signing_cert
        subject = cert.subject.native if cert is not None else {}
        issuer = cert.issuer.native if cert is not None else {}
        sig_object = signature.sig_object
        coverage = getattr(status, "coverage", None)
        level = getattr(status, "modification_level", None)
        covers_whole_file = coverage is not None and coverage.name == "ENTIRE_FILE"
        changes_allowed = level is not None and level in (
            ModificationLevel.NONE, ModificationLevel.LTA_UPDATES, ModificationLevel.FORM_FILLING,
        )
        comments_added = False
        if status.intact and status.valid and not covers_whole_file and not changes_allowed:
            try:
                byte_range = [int(v) for v in sig_object["/ByteRange"]]
                comments_added = comments_only_after(data, byte_range[2] + byte_range[3])
            except Exception:
                comments_added = False
            changes_allowed = comments_added
        results.append(SignatureStatus(
            field_name=name,
            signer=str(subject.get("common_name", "") or (cert.subject.human_friendly if cert else "")),
            signer_details=describe_subject(subject) if cert is not None else "",
            issuer=str(issuer.get("common_name", "") or issuer.get("organization_name", "")),
            signing_time=_format_time(status.signer_reported_dt),
            reason=str(sig_object.get("/Reason", "") or ""),
            location=str(sig_object.get("/Location", "") or ""),
            intact=bool(status.intact),
            valid=bool(status.valid),
            trusted=bool(status.trusted),
            covers_whole_file=covers_whole_file,
            changes_allowed=changes_allowed,
            timestamped=getattr(status, "timestamp_validity", None) is not None,
            page=locations.get(name, (-1, None))[0],
            rect=locations.get(name, (-1, None))[1],
            subject=subject_fields(subject) if cert is not None else (),
            signed_at=status.signer_reported_dt,
            comments_added=comments_added,
        ))
    report(progress, len(embedded), len(embedded))
    return results


def _signature_locations(data: bytes) -> dict[str, tuple[int, tuple[float, float, float, float] | None]]:
    """Página y zona visible de cada campo de firma."""
    locations: dict[str, tuple[int, tuple[float, float, float, float] | None]] = {}
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return locations
    try:
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_type != pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                    continue
                rect = pymupdf.Rect(widget.rect) * page.rotation_matrix
                visible = None if rect.is_empty or rect.width < 1 else (rect.x0, rect.y0, rect.x1, rect.y1)
                locations[widget.field_name] = (page.number, visible)
    finally:
        doc.close()
    return locations
