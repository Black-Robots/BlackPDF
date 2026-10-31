"""Certificados para firmar: los que ya hay en el equipo y los archivos .pfx o .p12.

Como en Acrobat, quien firma elige su certificado de una lista, sin buscar archivos:

- En Windows, los del almacén «Personal» (FNMT, DNIe con el lector conectado...): Windows
  firma y pide él el PIN si hace falta. Y los de Firefox y Thunderbird, que tienen su almacén.
- En Linux, los de Firefox, Chrome y Thunderbird (sus almacenes NSS) y los de las tarjetas
  con lector (OpenSC, módulo del DNIe).
- En los dos, los archivos .pfx/.p12 ya usados o que estén en las carpetas habituales.

La clave privada nunca sale de su almacén: se le pide que firme la huella del documento.
"""

from __future__ import annotations

import ctypes.util
import datetime as dt
import glob
import hashlib
import logging
import os
import sys
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .document import read_file
from .errors import BlackPdfError

log = logging.getLogger(__name__)

FILE_SUFFIXES = (".p12", ".pfx")
DIGEST_INFO = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
"""Lo que va delante de la huella en una firma RSA PKCS#1 v1.5 (las tarjetas firman los bytes tal cual)."""

SOFTOKN_PATHS = (
    "/usr/lib/x86_64-linux-gnu/nss/libsoftokn3.so", "/usr/lib/x86_64-linux-gnu/libsoftokn3.so",
    "/usr/lib64/libsoftokn3.so", "/usr/lib64/nss/libsoftokn3.so", "/usr/lib/libsoftokn3.so",
    "/usr/lib/nss/libsoftokn3.so", "/usr/lib/aarch64-linux-gnu/nss/libsoftokn3.so",
)
CARD_MODULES = (
    "/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so", "/usr/lib/x86_64-linux-gnu/pkcs11/opensc-pkcs11.so",
    "/usr/lib64/opensc-pkcs11.so", "/usr/lib64/pkcs11/opensc-pkcs11.so", "/usr/lib/opensc-pkcs11.so",
    "/usr/lib/pkcs11/opensc-pkcs11.so", "/usr/lib/libpkcs11-dnie.so", "/usr/lib64/libpkcs11-dnie.so",
)
"""Módulos de tarjetas: OpenSC (DNIe y la mayoría de tarjetas) y el del DNIe de la Policía."""
_PKCS11_LOCK = threading.Lock()
"""Un módulo PKCS#11 solo se puede abrir una vez por proceso: una búsqueda o una firma cada vez."""


# ======================================================================
# Errores
# ======================================================================
class CertificatePasswordError(BlackPdfError):
    """La contraseña o el PIN no son correctos, o el archivo no es un certificado (params: kind)."""


class CertificateError(BlackPdfError):
    """El certificado no sirve para firmar (caducado, sin clave privada...)."""


class CertificateAccessError(BlackPdfError):
    """No se ha podido usar la clave del certificado (params: reason = cancelled, pin_locked, no_card,
    unavailable)."""


# ======================================================================
# Datos del titular
# ======================================================================
@dataclass(frozen=True)
class CertificateInfo:
    name: str
    """Nombre común del titular (CN)."""
    organization: str
    serial: str
    """Número de serie del titular (en certificados españoles, el NIF: IDCES-...)."""
    issuer: str
    valid_from: dt.datetime
    valid_to: dt.datetime

    @property
    def expired(self) -> bool:
        return dt.datetime.now(dt.timezone.utc) > self.valid_to

    @property
    def not_yet_valid(self) -> bool:
        return dt.datetime.now(dt.timezone.utc) < self.valid_from


def _name_part(name, oid) -> str:
    try:
        values = name.get_attributes_for_oid(oid)
    except Exception:
        return ""
    return str(values[0].value) if values else ""


def certificate_info(cert) -> CertificateInfo:
    """Datos del titular de un certificado (``cryptography.x509.Certificate``)."""
    from cryptography.x509.oid import NameOID

    subject, issuer = cert.subject, cert.issuer
    return CertificateInfo(
        name=_name_part(subject, NameOID.COMMON_NAME) or subject.rfc4514_string(),
        organization=_name_part(subject, NameOID.ORGANIZATION_NAME),
        serial=_name_part(subject, NameOID.SERIAL_NUMBER),
        issuer=_name_part(issuer, NameOID.COMMON_NAME) or _name_part(issuer, NameOID.ORGANIZATION_NAME)
        or issuer.rfc4514_string(),
        valid_from=cert.not_valid_before_utc,
        valid_to=cert.not_valid_after_utc,
    )


def _x509(der: bytes):
    from cryptography import x509

    return x509.load_der_x509_certificate(der)


def can_sign(cert) -> bool:
    """Sirve para firmar documentos: no es de una autoridad y su uso permite firmar."""
    from cryptography import x509

    try:
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return False
    except x509.ExtensionNotFound:
        pass
    except Exception:
        return False
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    except Exception:
        return False
    return usage.digital_signature or usage.content_commitment


# ======================================================================
# Certificados disponibles
# ======================================================================
@dataclass(frozen=True)
class Identity:
    """Un certificado con el que se puede firmar, y dónde está su clave."""

    source: str
    """«windows», «nss» (Firefox, Chrome...), «token» (tarjeta con lector) o «file» (.pfx/.p12)."""
    location: str
    """Windows: huella SHA-1; nss: carpeta del almacén; token: módulo PKCS#11; file: ruta."""
    origin: str = ""
    """Para mostrar de dónde viene: «windows», «firefox», «chrome», «thunderbird», «card» o «file»."""
    detail: str = ""
    """Perfil de Firefox, nombre de la tarjeta, proveedor de Windows o nombre del archivo."""
    certificate: bytes = b""
    """El certificado (DER). En los archivos, vacío hasta abrirlos con su contraseña."""
    key_id: str = ""
    """nss y token: identificador (CKA_ID, en hexadecimal) de la clave y su certificado."""
    token: str = ""
    """nss y token: nombre del almacén o la tarjeta dentro del módulo."""
    secret: str = ""
    """Lo que hay que escribir para firmar: «» (nada, o lo pide el sistema), «file» (contraseña del
    archivo), «nss» (contraseña principal del navegador) o «pin» (PIN de la tarjeta)."""

    @property
    def key(self) -> str:
        """Identifica el certificado entre sesiones, para recordar el último usado."""
        return f"{self.source}|{self.location}|{self.key_id}"

    def info(self) -> CertificateInfo | None:
        return certificate_info(_x509(self.certificate)) if self.certificate else None


def list_identities(files: Iterable[str] = (), folders: Iterable[str] = (),
                    home: str | None = None) -> list[Identity]:
    """Todos los certificados con los que se puede firmar en este equipo.

    ``files``: archivos .pfx/.p12 ya usados; ``folders``: carpetas donde buscar más (Descargas,
    Documentos...). Lo que falla en un almacén no impide ver los demás.
    """
    found: list[Identity] = []
    steps: list[Callable[[], list[Identity]]] = []
    if sys.platform == "win32":
        steps.append(_windows_identities)
        steps.append(lambda: _nss_identities(home))
    elif sys.platform.startswith("linux"):
        steps.append(lambda: _nss_identities(home))
        steps.append(_card_identities)
    for step in steps:
        try:
            found += step()
        except Exception:
            log.warning("No se han podido leer certificados de un almacén", exc_info=True)
    seen = set()
    paths = list(files) + find_certificate_files(folders)
    for path in paths:
        resolved = str(Path(path).expanduser().resolve())
        if resolved in seen or not Path(resolved).is_file():
            continue
        seen.add(resolved)
        found.append(Identity("file", resolved, "file", Path(resolved).name, secret="file"))
    return found


def find_certificate_files(folders: Iterable[str], depth: int = 2, limit: int = 4000) -> list[str]:
    """Archivos .pfx/.p12 en esas carpetas y sus subcarpetas (sin las ocultas), mirando como
    mucho ``limit`` entradas para no tardar en carpetas enormes."""
    found: list[str] = []
    budget = limit
    for folder in folders:
        pending = [(Path(folder).expanduser(), 0)]
        while pending and budget > 0:
            current, level = pending.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                budget -= 1
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if level < depth:
                            pending.append((Path(entry.path), level + 1))
                    elif entry.name.lower().endswith(FILE_SUFFIXES) and entry.is_file():
                        found.append(entry.path)
                except OSError:
                    continue
    return sorted(dict.fromkeys(found))


def _windows_identities() -> list[Identity]:
    from . import wincert

    found = []
    for der, provider in wincert.personal_certificates():
        try:
            cert = _x509(der)
        except Exception:
            continue
        if not can_sign(cert):
            continue
        origin = "card" if "smart card" in provider.lower() or "tarjeta" in provider.lower() else "windows"
        found.append(Identity("windows", wincert.thumbprint(der), origin, provider, der))
    return found


# ---------- NSS: Firefox, Chrome, Thunderbird ----------
def nss_databases(home: str | None = None) -> list[tuple[str, str, str]]:
    """Almacenes de certificados de los navegadores: (carpeta, origen, perfil)."""
    base = Path(home or Path.home())
    found: list[tuple[str, str, str]] = []
    if sys.platform == "win32":
        # Chrome y Edge usan el almacén de Windows; Firefox y Thunderbird, el suyo.
        roaming = Path(os.environ.get("APPDATA") or base / "AppData" / "Roaming") if home is None \
            else base / "AppData" / "Roaming"
        for pattern, origin in (("Mozilla/Firefox/Profiles/*", "firefox"), ("Thunderbird/Profiles/*", "thunderbird")):
            for folder in sorted(glob.glob(str(roaming / pattern))):
                if Path(folder, "cert9.db").is_file():
                    found.append((folder, origin, Path(folder).name.split(".", 1)[-1]))
        return found
    for folder, origin in (
        (base / ".pki" / "nssdb", "chrome"),
        (base / "snap" / "chromium" / "current" / ".pki" / "nssdb", "chrome"),
    ):
        if (folder / "cert9.db").is_file():
            found.append((str(folder), origin, ""))
    patterns = (
        (".mozilla/firefox/*", "firefox"), ("snap/firefox/common/.mozilla/firefox/*", "firefox"),
        (".var/app/org.mozilla.firefox/.mozilla/firefox/*", "firefox"), (".config/mozilla/firefox/*", "firefox"),
        (".thunderbird/*", "thunderbird"), ("snap/thunderbird/common/.thunderbird/*", "thunderbird"),
    )
    for pattern, origin in patterns:
        for folder in sorted(glob.glob(str(base / pattern))):
            if Path(folder, "cert9.db").is_file():
                profile = Path(folder).name.split(".", 1)[-1]
                found.append((folder, origin, profile))
    return found


def softokn_path() -> str | None:
    """El módulo de NSS con el que se abren los almacenes de los navegadores (en Windows, el de Firefox)."""
    if sys.platform == "win32":
        folders = [os.environ.get(name, "") for name in ("ProgramW6432", "ProgramFiles", "LOCALAPPDATA")]
        for folder in filter(None, folders):
            for app in ("Mozilla Firefox", "Mozilla Thunderbird"):
                path = Path(folder, app, "softokn3.dll")
                if path.is_file():
                    return str(path)
        return None
    for path in SOFTOKN_PATHS:
        if Path(path).is_file():
            return path
    return ctypes.util.find_library("softokn3")


def _nss_identities(home: str | None = None) -> list[Identity]:
    databases = nss_databases(home)
    module = softokn_path() if databases else None
    if not module:
        return []
    found: list[Identity] = []
    profiles = {}
    for _folder, origin, _profile in databases:
        profiles[origin] = profiles.get(origin, 0) + 1
    for folder, origin, profile in databases:
        detail = profile if profiles[origin] > 1 else ""
        try:
            found += _pkcs11_identities(module, "nss", origin, detail, nss_folder=folder)
        except Exception:
            log.warning("No se ha podido leer el almacén %s", folder, exc_info=True)
    return found


def card_modules() -> list[str]:
    return [path for path in CARD_MODULES if Path(path).is_file()]


def _card_identities() -> list[Identity]:
    found: list[Identity] = []
    for module in card_modules():
        try:
            found += _pkcs11_identities(module, "token", "card", "")
        except Exception:
            log.warning("No se ha podido leer el módulo %s", module, exc_info=True)
    return found


def _pkcs11_identities(module_path: str, source: str, origin: str, detail: str,
                       nss_folder: str = "") -> list[Identity]:
    from . import pkcs11

    found: list[Identity] = []
    parameters = pkcs11.nss_parameters(nss_folder) if nss_folder else ""
    with _PKCS11_LOCK, pkcs11.Module(module_path, parameters) as module:
        for slot in module.slots():
            token = module.token(slot)
            label = pkcs11.text(token.label)
            locked = bool(token.flags & pkcs11.CKF_LOGIN_REQUIRED) and bool(token.flags & pkcs11.CKF_USER_PIN_INITIALIZED)
            with module.session(slot) as session:
                # Sin iniciar sesión solo se ven las claves de los almacenes sin contraseña.
                keys = {module.attribute(session, key, pkcs11.CKA_ID)
                        for key in module.find(session, pkcs11.CKO_PRIVATE_KEY)}
                for handle in module.find(session, pkcs11.CKO_CERTIFICATE):
                    key_id = module.attribute(session, handle, pkcs11.CKA_ID)
                    if not locked and key_id not in keys:
                        continue
                    der = module.attribute(session, handle, pkcs11.CKA_VALUE)
                    try:
                        cert = _x509(der)
                    except Exception:
                        continue
                    if not can_sign(cert):
                        continue
                    secret = ""
                    if locked:
                        secret = "nss" if source == "nss" else "pin"
                        if token.flags & pkcs11.CKF_PROTECTED_AUTHENTICATION_PATH:
                            secret = ""  # El lector tiene teclado: el PIN se escribe en él.
                    # En una tarjeta, su nombre («DNI electrónico»); en un navegador, el perfil (si hay varios).
                    shown = label if source == "token" else detail
                    found.append(Identity(source, nss_folder or module_path, origin, shown, der,
                                          key_id.hex(), label, secret))
    return found


# ======================================================================
# Firmar
# ======================================================================
def chain_for(der: bytes, pool: Iterable[bytes]) -> list[bytes]:
    """Los certificados de las autoridades que emitieron este, sacados de ``pool``, hasta la raíz."""
    from cryptography import x509

    candidates = []
    for item in pool:
        try:
            candidates.append((item, x509.load_der_x509_certificate(item)))
        except Exception:
            continue
    chain: list[bytes] = []
    current = _x509(der)
    for _level in range(8):
        if current.issuer == current.subject:
            break
        parent = None
        for item, cert in candidates:
            if cert.subject != current.issuer or item in chain or item == der:
                continue
            try:
                current.verify_directly_issued_by(cert)
            except Exception:
                continue
            parent = (item, cert)
            break
        if parent is None:
            break
        chain.append(parent[0])
        current = parent[1]
    return chain


def _signer_class():
    from pyhanko.sign.signers import Signer

    class ExternalSigner(Signer):
        """Firma con una clave que no se puede sacar de su almacén: se le pasa la huella."""

        def __init__(self, der: bytes, chain: list[bytes], sign_digest: Callable[[bytes, str], bytes]):
            from asn1crypto import x509 as asn1_x509
            from pyhanko_certvalidator.registry import SimpleCertificateStore

            cert = asn1_x509.Certificate.load(der)
            super().__init__(signing_cert=cert, cert_registry=SimpleCertificateStore.from_certs(
                [asn1_x509.Certificate.load(item) for item in chain]))
            self._sign_digest = sign_digest
            self._size = _signature_size(cert)

        async def async_sign_raw(self, data: bytes, digest_algorithm: str, dry_run: bool = False) -> bytes:
            if dry_run:
                return bytes(self._size)
            return self._sign_digest(hashlib.new(digest_algorithm, data).digest(), digest_algorithm)

    return ExternalSigner


def _signature_size(cert) -> int:
    """Lo que ocupa como mucho la firma, para reservarle sitio en el PDF."""
    key = cert.public_key
    if key.algorithm == "rsa":
        return (key.bit_size + 7) // 8
    return 2 * ((key.bit_size + 7) // 8) + 9


def _key_type(der: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    key = _x509(der).public_key()
    if isinstance(key, rsa.RSAPublicKey):
        return "rsa"
    if isinstance(key, ec.EllipticCurvePublicKey):
        return "ec"
    raise CertificateError("tipo de clave no admitido")


def _ec_der(raw: bytes) -> bytes:
    """Firma EC en r||s (lo que devuelven Windows y las tarjetas) a DER, como la quiere CMS."""
    from asn1crypto.algos import DSASignature

    return DSASignature.from_p1363(raw).dump()


@contextmanager
def open_signer(certificate, password: str = "", window: int = 0) -> Iterator[tuple[object, CertificateInfo]]:
    """El firmante de pyHanko para ese certificado (una ruta, sus bytes o una ``Identity``), con su titular.

    Úsalo con ``with``: la tarjeta o el almacén siguen abiertos hasta terminar de firmar. ``window``
    es la ventana sobre la que Windows pide el PIN.
    """
    if isinstance(certificate, Identity) and certificate.source == "file":
        certificate = certificate.location
    if not isinstance(certificate, Identity):
        yield _file_signer(certificate, password)
        return
    info = certificate.info()
    if info is None:
        raise CertificateError("certificado vacío")
    _check_dates(info)
    if certificate.source == "windows":
        yield _windows_signer(certificate, window), info
        return
    with _pkcs11_signer(certificate, password) as signer:
        yield signer, info


def _check_dates(info: CertificateInfo) -> None:
    if info.expired:
        raise CertificateError("certificado caducado", valid_to=info.valid_to.isoformat())
    if info.not_yet_valid:
        raise CertificateError("certificado aún no válido", valid_from=info.valid_from.isoformat())


def load_certificate(certificate: str | os.PathLike | bytes, password: str) -> CertificateInfo:
    """Abre un archivo .pfx/.p12 con su contraseña y devuelve los datos del titular."""
    from cryptography.hazmat.primitives.serialization import pkcs12

    data = _certificate_bytes(certificate)
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(data, password.encode("utf-8") if password else None)
    except ValueError as exc:
        raise CertificatePasswordError(str(exc), kind="file") from exc
    if cert is None or key is None:
        raise CertificateError("el archivo no contiene un certificado con clave privada")
    return certificate_info(cert)


def _certificate_bytes(certificate: str | os.PathLike | bytes) -> bytes:
    if isinstance(certificate, (bytes, bytearray)):
        return bytes(certificate)
    return read_file(certificate)


def _file_signer(certificate, password: str):
    from pyhanko.sign import signers

    info = load_certificate(certificate, password)
    _check_dates(info)
    try:
        signer = signers.SimpleSigner.load_pkcs12_data(
            _certificate_bytes(certificate), other_certs=None,
            passphrase=password.encode("utf-8") if password else None,
        )
    except ValueError as exc:
        raise CertificatePasswordError(str(exc), kind="file") from exc
    return signer, info


def _windows_signer(identity: Identity, window: int = 0):
    from . import wincert

    key_type = _key_type(identity.certificate)

    def sign_digest(digest: bytes, algorithm: str) -> bytes:
        try:
            raw = wincert.sign_digest(identity.location, digest, algorithm, key_type, window)
        except wincert.WindowsCertError as exc:
            raise _windows_error(exc) from exc
        return _ec_der(raw) if key_type == "ec" else raw

    try:
        pool = wincert.authority_certificates()
    except Exception:
        pool = []
    return _signer_class()(identity.certificate, chain_for(identity.certificate, pool), sign_digest)


def _windows_error(exc) -> BlackPdfError:
    from . import wincert

    if exc.code in wincert.CANCELLED:
        return CertificateAccessError(str(exc), reason="cancelled")
    if exc.code in wincert.WRONG_PIN:
        return CertificatePasswordError(str(exc), kind="pin")
    if exc.code in wincert.PIN_LOCKED:
        return CertificateAccessError(str(exc), reason="pin_locked")
    if exc.code in wincert.NO_CARD:
        return CertificateAccessError(str(exc), reason="no_card")
    return CertificateAccessError(str(exc), reason="unavailable")


@contextmanager
def _pkcs11_signer(identity: Identity, password: str):
    from . import pkcs11

    if identity.source == "nss":
        module_path, parameters = softokn_path(), pkcs11.nss_parameters(identity.location)
    else:
        module_path, parameters = identity.location, ""
    if not module_path:
        raise CertificateAccessError("no está el módulo NSS", reason="unavailable")
    key_id = bytes.fromhex(identity.key_id)
    key_type = _key_type(identity.certificate)
    try:
        with _PKCS11_LOCK, pkcs11.Module(module_path, parameters) as module:
            slot = _find_slot(module, identity)
            with module.session(slot) as session:
                token = module.token(slot)
                # Con un lector con teclado, el PIN se escribe en él (None); si no, el que ha escrito quien firma.
                pin = None if token.flags & pkcs11.CKF_PROTECTED_AUTHENTICATION_PATH and not password else password
                if token.flags & pkcs11.CKF_LOGIN_REQUIRED and token.flags & pkcs11.CKF_USER_PIN_INITIALIZED:
                    module.login(session, pin)
                keys = module.find(session, pkcs11.CKO_PRIVATE_KEY, key_id)
                if not keys:
                    raise CertificateError("el almacén no tiene la clave privada de este certificado")
                pool = [module.attribute(session, handle, pkcs11.CKA_VALUE)
                        for handle in module.find(session, pkcs11.CKO_CERTIFICATE)]

                def sign_digest(digest: bytes, algorithm: str) -> bytes:
                    try:
                        if key_type == "rsa":
                            return module.sign(session, keys[0], pkcs11.CKM_RSA_PKCS, DIGEST_INFO[algorithm] + digest, pin)
                        return _ec_der(module.sign(session, keys[0], pkcs11.CKM_ECDSA, digest, pin))
                    except pkcs11.Pkcs11Error as exc:
                        raise _pkcs11_error(exc, identity) from exc

                yield _signer_class()(identity.certificate, chain_for(identity.certificate, pool), sign_digest)
    except pkcs11.Pkcs11Error as exc:
        raise _pkcs11_error(exc, identity) from exc


def _find_slot(module, identity: Identity) -> int:
    from . import pkcs11

    slots = module.slots()
    for slot in slots:
        if pkcs11.text(module.token(slot).label) == identity.token:
            return slot
    raise CertificateAccessError("no está la tarjeta", reason="no_card")


def _pkcs11_error(exc, identity: Identity) -> BlackPdfError:
    from . import pkcs11

    if exc.code in (pkcs11.CKR_PIN_INCORRECT, pkcs11.CKR_PIN_INVALID, pkcs11.CKR_PIN_LEN_RANGE):
        return CertificatePasswordError(str(exc), kind=identity.secret or "pin")
    if exc.code in (pkcs11.CKR_PIN_LOCKED, pkcs11.CKR_PIN_EXPIRED):
        return CertificateAccessError(str(exc), reason="pin_locked")
    if exc.code == pkcs11.CKR_FUNCTION_CANCELED:
        return CertificateAccessError(str(exc), reason="cancelled")
    if exc.code in (pkcs11.CKR_TOKEN_NOT_PRESENT, pkcs11.CKR_DEVICE_REMOVED, pkcs11.CKR_TOKEN_NOT_RECOGNIZED,
                    pkcs11.CKR_DEVICE_ERROR):
        return CertificateAccessError(str(exc), reason="no_card")
    return CertificateAccessError(str(exc), reason="unavailable")
