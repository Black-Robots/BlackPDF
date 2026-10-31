"""Certificados del almacén de Windows («Personal»), como los usa Acrobat.

Ahí están los que se instalan con doble clic en un .pfx, los de la FNMT y los de las tarjetas
(DNIe y otras) cuando el lector está conectado. La clave privada no sale nunca del almacén:
Windows firma la huella del documento y, si hace falta, pide él mismo el PIN o permiso.
Solo funciona en Windows (ctypes con crypt32, ncrypt y advapi32).
"""

from __future__ import annotations

import ctypes
import hashlib
from ctypes import wintypes
from typing import NamedTuple

MY, CA, ROOT = "MY", "CA", "ROOT"
CERT_KEY_PROV_INFO_PROP_ID = 2
CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG = 0x20000
CRYPT_ACQUIRE_WINDOW_HANDLE_FLAG = 0x80
CERT_NCRYPT_KEY_SPEC = 0xFFFFFFFF
BCRYPT_PAD_PKCS1 = 0x2
PROV_RSA_AES = 24
CRYPT_MACHINE_KEYSET = 0x20
HP_HASHVAL = 0x2
MS_ENH_RSA_AES_PROV = "Microsoft Enhanced RSA and AES Cryptographic Provider"
ALG_IDS = {"sha1": 0x8004, "sha256": 0x800C, "sha384": 0x800D, "sha512": 0x800E}
CNG_NAMES = {"sha1": "SHA1", "sha256": "SHA256", "sha384": "SHA384", "sha512": "SHA512"}

# Códigos que significan algo para quien firma.
CANCELLED = {0x80090036, 0x8010006E, 0x800704C7}
"""NTE_USER_CANCELLED, SCARD_W_CANCELLED_BY_USER, ERROR_CANCELLED."""
WRONG_PIN = {0x8010006B, 0x80090033}
"""SCARD_W_WRONG_CHV y NTE_INCORRECT_PASSWORD."""
PIN_LOCKED = {0x8010006C}
"""SCARD_W_CHV_BLOCKED."""
NO_CARD = {0x8010000C, 0x80100069, 0x8010002E, 0x80100017, 0x80090030, 0x80090035}
"""Sin tarjeta, tarjeta retirada, sin lector, lector no disponible, dispositivo no listo o no encontrado."""


class WindowsCertError(Exception):
    def __init__(self, function: str, code: int):
        super().__init__(f"{function}: 0x{code & 0xFFFFFFFF:08X}")
        self.function = function
        self.code = code & 0xFFFFFFFF


class _CertContext(ctypes.Structure):
    _fields_ = [("encoding", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte)), ("size", wintypes.DWORD),
                ("info", ctypes.c_void_p), ("store", ctypes.c_void_p)]


class _KeyProvInfo(ctypes.Structure):
    _fields_ = [("container", wintypes.LPWSTR), ("provider", wintypes.LPWSTR), ("provider_type", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("param_count", wintypes.DWORD), ("params", ctypes.c_void_p),
                ("key_spec", wintypes.DWORD)]


class _Pkcs1Padding(ctypes.Structure):
    _fields_ = [("algorithm", wintypes.LPCWSTR)]


_CONTEXT = ctypes.POINTER(_CertContext)


class KeyLocation(NamedTuple):
    container: str
    provider: str
    """Nombre del proveedor: el de las tarjetas lleva «Smart Card»."""
    flags: int

_libraries: dict[str, ctypes.WinDLL] = {}


def _lib(name: str):
    library = _libraries.get(name)
    if library is None:
        library = _libraries[name] = ctypes.WinDLL(name, use_last_error=True)
        if name == "crypt32":
            library.CertOpenSystemStoreW.restype = ctypes.c_void_p
            library.CertOpenSystemStoreW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            library.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            library.CertEnumCertificatesInStore.restype = _CONTEXT
            library.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, _CONTEXT]
            library.CertDuplicateCertificateContext.restype = _CONTEXT
            library.CertDuplicateCertificateContext.argtypes = [_CONTEXT]
            library.CertFreeCertificateContext.argtypes = [_CONTEXT]
            library.CertGetCertificateContextProperty.restype = wintypes.BOOL
            library.CertGetCertificateContextProperty.argtypes = [_CONTEXT, wintypes.DWORD, ctypes.c_void_p,
                                                                  ctypes.POINTER(wintypes.DWORD)]
            library.CryptAcquireCertificatePrivateKey.restype = wintypes.BOOL
            library.CryptAcquireCertificatePrivateKey.argtypes = [
                _CONTEXT, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.BOOL)]
        elif name == "ncrypt":
            library.NCryptSignHash.restype = ctypes.c_long
            library.NCryptSignHash.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                               wintypes.DWORD]
            library.NCryptFreeObject.argtypes = [ctypes.c_size_t]
        elif name == "advapi32":
            library.CryptAcquireContextW.restype = wintypes.BOOL
            library.CryptAcquireContextW.argtypes = [ctypes.POINTER(ctypes.c_size_t), wintypes.LPCWSTR,
                                                     wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            library.CryptReleaseContext.argtypes = [ctypes.c_size_t, wintypes.DWORD]
            library.CryptCreateHash.restype = wintypes.BOOL
            library.CryptCreateHash.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_size_t, wintypes.DWORD,
                                                ctypes.POINTER(ctypes.c_size_t)]
            library.CryptSetHashParam.restype = wintypes.BOOL
            library.CryptSetHashParam.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            library.CryptSignHashW.restype = wintypes.BOOL
            library.CryptSignHashW.argtypes = [ctypes.c_size_t, wintypes.DWORD, wintypes.LPCWSTR, wintypes.DWORD,
                                               ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
            library.CryptDestroyHash.argtypes = [ctypes.c_size_t]
    return library


def _fail(function: str) -> WindowsCertError:
    return WindowsCertError(function, ctypes.get_last_error())


def _der(context) -> bytes:
    item = context.contents
    return ctypes.string_at(item.data, item.size)


def _provider(context) -> KeyLocation | None:
    """Dónde guarda Windows la clave privada del certificado (None si no tiene)."""
    crypt32 = _lib("crypt32")
    size = wintypes.DWORD(0)
    if not crypt32.CertGetCertificateContextProperty(context, CERT_KEY_PROV_INFO_PROP_ID, None, ctypes.byref(size)):
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not crypt32.CertGetCertificateContextProperty(context, CERT_KEY_PROV_INFO_PROP_ID, buffer, ctypes.byref(size)):
        return None
    # Los textos apuntan dentro de «buffer»: se leen mientras sigue vivo.
    info = _KeyProvInfo.from_buffer(buffer)
    return KeyLocation(info.container or "", info.provider or "", info.flags)


def _store_certificates(name: str) -> list[tuple[bytes, KeyLocation | None]]:
    crypt32 = _lib("crypt32")
    store = crypt32.CertOpenSystemStoreW(None, name)
    if not store:
        raise _fail("CertOpenSystemStoreW")
    found = []
    try:
        context = crypt32.CertEnumCertificatesInStore(store, None)
        while context:
            found.append((_der(context), _provider(context) if name == MY else None))
            context = crypt32.CertEnumCertificatesInStore(store, context)
    finally:
        crypt32.CertCloseStore(store, 0)
    return found


def personal_certificates() -> list[tuple[bytes, str]]:
    """Certificados con clave privada del almacén «Personal» del usuario: (DER, proveedor de la clave)."""
    return [(der, provider.provider or "") for der, provider in _store_certificates(MY) if provider is not None]


def authority_certificates() -> list[bytes]:
    """Certificados de las autoridades (intermedias y raíz), para incluir la cadena en la firma."""
    found: list[bytes] = []
    for name in (CA, ROOT):
        try:
            found += [der for der, _ in _store_certificates(name)]
        except WindowsCertError:
            continue
    return found


def thumbprint(der: bytes) -> str:
    return hashlib.sha1(der).hexdigest().upper()


def _find(thumb: str):
    """El certificado del almacén «Personal» con esa huella (hay que liberarlo)."""
    crypt32 = _lib("crypt32")
    store = crypt32.CertOpenSystemStoreW(None, MY)
    if not store:
        raise _fail("CertOpenSystemStoreW")
    try:
        context = crypt32.CertEnumCertificatesInStore(store, None)
        while context:
            if thumbprint(_der(context)) == thumb.upper():
                found = crypt32.CertDuplicateCertificateContext(context)
                crypt32.CertFreeCertificateContext(context)  # La enumeración se corta aquí: se suelta a mano.
                return found
            context = crypt32.CertEnumCertificatesInStore(store, context)
    finally:
        crypt32.CertCloseStore(store, 0)
    return None


def sign_digest(thumb: str, digest: bytes, algorithm: str, key_type: str, window: int = 0) -> bytes:
    """Firma la huella con la clave del certificado. RSA: PKCS#1 v1.5; EC: r||s (P1363).

    ``window``: la ventana sobre la que Windows muestra lo que tenga que pedir (el PIN de una tarjeta).
    """
    crypt32 = _lib("crypt32")
    context = _find(thumb)
    if context is None:
        raise WindowsCertError("CertFindCertificate", 0x80092004)  # CRYPT_E_NOT_FOUND
    try:
        handle = ctypes.c_size_t(0)
        key_spec = wintypes.DWORD(0)
        must_free = wintypes.BOOL(False)
        flags, parent = CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG, None
        if window:
            flags |= CRYPT_ACQUIRE_WINDOW_HANDLE_FLAG
            parent = ctypes.byref(ctypes.c_void_p(window))
        if not crypt32.CryptAcquireCertificatePrivateKey(context, flags, parent, ctypes.byref(handle),
                                                         ctypes.byref(key_spec), ctypes.byref(must_free)):
            raise _fail("CryptAcquireCertificatePrivateKey")
        try:
            if key_spec.value == CERT_NCRYPT_KEY_SPEC:
                return _sign_cng(handle.value, digest, algorithm, key_type)
            return _sign_capi(context, handle.value, key_spec.value, digest, algorithm)
        finally:
            if must_free.value:
                if key_spec.value == CERT_NCRYPT_KEY_SPEC:
                    _lib("ncrypt").NCryptFreeObject(handle.value)
                else:
                    _lib("advapi32").CryptReleaseContext(handle.value, 0)
    finally:
        crypt32.CertFreeCertificateContext(context)


def _sign_cng(key: int, digest: bytes, algorithm: str, key_type: str) -> bytes:
    ncrypt = _lib("ncrypt")
    padding, flags = None, 0
    if key_type == "rsa":
        padding = _Pkcs1Padding(CNG_NAMES[algorithm])
        flags = BCRYPT_PAD_PKCS1
    source = ctypes.create_string_buffer(digest, len(digest))
    size = wintypes.DWORD(0)
    pointer = ctypes.byref(padding) if padding is not None else None
    status = ncrypt.NCryptSignHash(key, pointer, source, len(digest), None, 0, ctypes.byref(size), flags)
    if status != 0:
        raise WindowsCertError("NCryptSignHash", status)
    target = ctypes.create_string_buffer(size.value)
    status = ncrypt.NCryptSignHash(key, pointer, source, len(digest), target, size.value, ctypes.byref(size), flags)
    if status != 0:
        raise WindowsCertError("NCryptSignHash", status)
    return target.raw[:size.value]


def _sign_capi(context, provider: int, key_spec: int, digest: bytes, algorithm: str) -> bytes:
    """Claves de los proveedores antiguos (CryptoAPI). SHA-256 necesita el proveedor «RSA and AES»:
    se abre el mismo contenedor con él; si no se puede (otro fabricante), con el que tenga."""
    advapi32 = _lib("advapi32")
    info = _provider(context)
    aes = ctypes.c_size_t(0)
    if info is not None and info.container and (info.provider or "").startswith("Microsoft"):
        flags = info.flags & CRYPT_MACHINE_KEYSET
        if not advapi32.CryptAcquireContextW(ctypes.byref(aes), info.container, MS_ENH_RSA_AES_PROV, PROV_RSA_AES,
                                             flags):
            aes = ctypes.c_size_t(0)
    try:
        hash_handle = ctypes.c_size_t(0)
        if not advapi32.CryptCreateHash(aes.value or provider, ALG_IDS[algorithm], 0, 0, ctypes.byref(hash_handle)):
            raise _fail("CryptCreateHash")
        try:
            source = ctypes.create_string_buffer(digest, len(digest))
            if not advapi32.CryptSetHashParam(hash_handle.value, HP_HASHVAL, source, 0):
                raise _fail("CryptSetHashParam")
            size = wintypes.DWORD(0)
            if not advapi32.CryptSignHashW(hash_handle.value, key_spec, None, 0, None, ctypes.byref(size)):
                raise _fail("CryptSignHashW")
            target = ctypes.create_string_buffer(size.value)
            if not advapi32.CryptSignHashW(hash_handle.value, key_spec, None, 0, target, ctypes.byref(size)):
                raise _fail("CryptSignHashW")
            # CryptoAPI devuelve la firma al revés (little-endian).
            return target.raw[:size.value][::-1]
        finally:
            advapi32.CryptDestroyHash(hash_handle.value)
    finally:
        if aes.value:
            advapi32.CryptReleaseContext(aes.value, 0)
