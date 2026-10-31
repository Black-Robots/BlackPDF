"""Acceso mínimo a módulos PKCS#11: los almacenes de certificados de Firefox y Chrome (NSS)
y las tarjetas con lector (DNIe y otras, con OpenSC).

PKCS#11 es la interfaz estándar para usar una clave privada sin sacarla de donde está: el
programa pide «firma estos bytes» y el almacén (o la tarjeta) devuelve la firma. Aquí se usa
lo justo para listar certificados con su clave y firmar, con ctypes y sin dependencias.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager

CK_ULONG = ctypes.c_ulong
CK_RV = CK_ULONG
CK_BYTE_P = ctypes.POINTER(ctypes.c_ubyte)

CKF_OS_LOCKING_OK = 0x2
CKF_SERIAL_SESSION = 0x4
CKF_TOKEN_PRESENT = 0x1
CKF_LOGIN_REQUIRED = 0x4
CKF_USER_PIN_INITIALIZED = 0x8
CKF_PROTECTED_AUTHENTICATION_PATH = 0x100
CKU_USER = 1
CKO_CERTIFICATE = 0x1
CKO_PRIVATE_KEY = 0x3
CKA_CLASS = 0x0
CKA_LABEL = 0x3
CKA_VALUE = 0x11
CKA_ID = 0x102
CKM_RSA_PKCS = 0x1
CKM_ECDSA = 0x1041
CKR_OK = 0x0
CKR_CRYPTOKI_ALREADY_INITIALIZED = 0x191
CKR_USER_ALREADY_LOGGED_IN = 0x100
CKR_PIN_INCORRECT = 0xA0
CKR_PIN_LOCKED = 0xA4
CKR_PIN_LEN_RANGE = 0xA2
CKR_FUNCTION_CANCELED = 0x50
CKR_TOKEN_NOT_PRESENT = 0xE0
CKR_DEVICE_REMOVED = 0x32
CKR_BUFFER_TOO_SMALL = 0x150

_FUNCTIONS = (
    "C_Initialize", "C_Finalize", "C_GetInfo", "C_GetFunctionList", "C_GetSlotList", "C_GetSlotInfo",
    "C_GetTokenInfo", "C_GetMechanismList", "C_GetMechanismInfo", "C_InitToken", "C_InitPIN", "C_SetPIN",
    "C_OpenSession", "C_CloseSession", "C_CloseAllSessions", "C_GetSessionInfo", "C_GetOperationState",
    "C_SetOperationState", "C_Login", "C_Logout", "C_CreateObject", "C_CopyObject", "C_DestroyObject",
    "C_GetObjectSize", "C_GetAttributeValue", "C_SetAttributeValue", "C_FindObjectsInit", "C_FindObjects",
    "C_FindObjectsFinal", "C_EncryptInit", "C_Encrypt", "C_EncryptUpdate", "C_EncryptFinal", "C_DecryptInit",
    "C_Decrypt", "C_DecryptUpdate", "C_DecryptFinal", "C_DigestInit", "C_Digest", "C_DigestUpdate", "C_DigestKey",
    "C_DigestFinal", "C_SignInit", "C_Sign",
)
"""Las primeras funciones de la tabla de PKCS#11, en su orden: no hacen falta las de después."""

# En Windows las estructuras de PKCS#11 van empaquetadas a 1 byte; en Linux, con la alineación normal.
_PACK = {"_pack_": 1} if sys.platform == "win32" else {}


class _Version(ctypes.Structure):
    _fields_ = [("major", ctypes.c_ubyte), ("minor", ctypes.c_ubyte)]


class _InitArgs(ctypes.Structure):
    _fields_ = [("create_mutex", ctypes.c_void_p), ("destroy_mutex", ctypes.c_void_p),
                ("lock_mutex", ctypes.c_void_p), ("unlock_mutex", ctypes.c_void_p),
                ("flags", CK_ULONG), ("reserved", ctypes.c_char_p)]
    locals().update(_PACK)


class _TokenInfo(ctypes.Structure):
    _fields_ = [("label", ctypes.c_char * 32), ("manufacturer", ctypes.c_char * 32), ("model", ctypes.c_char * 16),
                ("serial", ctypes.c_char * 16), ("flags", CK_ULONG), ("max_sessions", CK_ULONG),
                ("sessions", CK_ULONG), ("max_rw_sessions", CK_ULONG), ("rw_sessions", CK_ULONG),
                ("max_pin", CK_ULONG), ("min_pin", CK_ULONG), ("total_public", CK_ULONG), ("free_public", CK_ULONG),
                ("total_private", CK_ULONG), ("free_private", CK_ULONG), ("hardware", _Version),
                ("firmware", _Version), ("time", ctypes.c_char * 16)]
    locals().update(_PACK)


class _Attribute(ctypes.Structure):
    _fields_ = [("type", CK_ULONG), ("value", ctypes.c_void_p), ("length", CK_ULONG)]
    locals().update(_PACK)


class _Mechanism(ctypes.Structure):
    _fields_ = [("mechanism", CK_ULONG), ("parameter", ctypes.c_void_p), ("length", CK_ULONG)]
    locals().update(_PACK)


class _FunctionList(ctypes.Structure):
    _fields_ = [("version", _Version)] + [(name, ctypes.c_void_p) for name in _FUNCTIONS]
    locals().update(_PACK)


class Pkcs11Error(Exception):
    def __init__(self, function: str, code: int):
        super().__init__(f"{function}: 0x{code:X}")
        self.function = function
        self.code = code


_SIGNATURES = {
    "C_Initialize": (ctypes.c_void_p,),
    "C_Finalize": (ctypes.c_void_p,),
    "C_GetSlotList": (ctypes.c_ubyte, ctypes.POINTER(CK_ULONG), ctypes.POINTER(CK_ULONG)),
    "C_GetTokenInfo": (CK_ULONG, ctypes.POINTER(_TokenInfo)),
    "C_OpenSession": (CK_ULONG, CK_ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(CK_ULONG)),
    "C_CloseSession": (CK_ULONG,),
    "C_Login": (CK_ULONG, CK_ULONG, ctypes.c_char_p, CK_ULONG),
    "C_GetAttributeValue": (CK_ULONG, CK_ULONG, ctypes.POINTER(_Attribute), CK_ULONG),
    "C_FindObjectsInit": (CK_ULONG, ctypes.POINTER(_Attribute), CK_ULONG),
    "C_FindObjects": (CK_ULONG, ctypes.POINTER(CK_ULONG), CK_ULONG, ctypes.POINTER(CK_ULONG)),
    "C_FindObjectsFinal": (CK_ULONG,),
    "C_SignInit": (CK_ULONG, ctypes.POINTER(_Mechanism), CK_ULONG),
    "C_Sign": (CK_ULONG, CK_BYTE_P, CK_ULONG, CK_BYTE_P, ctypes.POINTER(CK_ULONG)),
}


class Module:
    """Un módulo PKCS#11 cargado e inicializado. Úsalo con ``with``: al salir se cierra."""

    def __init__(self, path: str, parameters: str = ""):
        loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
        self._library = loader(path)
        get_list = self._library.C_GetFunctionList
        get_list.restype = CK_RV
        pointer = ctypes.POINTER(_FunctionList)()
        code = get_list(ctypes.byref(pointer))
        if code != CKR_OK:
            raise Pkcs11Error("C_GetFunctionList", code)
        table = pointer.contents
        prototype = ctypes.WINFUNCTYPE if sys.platform == "win32" else ctypes.CFUNCTYPE
        self._calls = {name: prototype(CK_RV, *arguments)(getattr(table, name))
                       for name, arguments in _SIGNATURES.items()}
        args = _InitArgs(flags=CKF_OS_LOCKING_OK)
        # Los almacenes de NSS (Firefox, Chrome) reciben su carpeta por aquí.
        args.reserved = parameters.encode("utf-8") if parameters else None
        code = self._calls["C_Initialize"](ctypes.byref(args))
        self._initialized = code == CKR_OK
        if code not in (CKR_OK, CKR_CRYPTOKI_ALREADY_INITIALIZED):
            raise Pkcs11Error("C_Initialize", code)

    def close(self) -> None:
        if self._initialized:
            self._calls["C_Finalize"](None)
            self._initialized = False

    def __enter__(self) -> Module:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def call(self, name: str, *args) -> None:
        code = self._calls[name](*args)
        if code != CKR_OK:
            raise Pkcs11Error(name, code)

    # ---------- ranuras y sesiones ----------
    def slots(self) -> list[int]:
        """Ranuras con un almacén o una tarjeta dentro."""
        count = CK_ULONG()
        self.call("C_GetSlotList", 1, None, ctypes.byref(count))
        found = (CK_ULONG * max(count.value, 1))()
        self.call("C_GetSlotList", 1, found, ctypes.byref(count))
        return list(found[:count.value])

    def token(self, slot: int) -> _TokenInfo:
        info = _TokenInfo()
        self.call("C_GetTokenInfo", slot, ctypes.byref(info))
        return info

    @contextmanager
    def session(self, slot: int) -> Iterator[int]:
        handle = CK_ULONG()
        self.call("C_OpenSession", slot, CKF_SERIAL_SESSION, None, None, ctypes.byref(handle))
        try:
            yield handle.value
        finally:
            self._calls["C_CloseSession"](handle.value)

    def login(self, session: int, pin: str | None) -> None:
        """Abre la sesión con el PIN o la contraseña. Con ``None``, el lector pide el PIN (teclado propio)."""
        data = pin.encode("utf-8") if pin is not None else None
        code = self._calls["C_Login"](session, CKU_USER, data, len(data) if data is not None else 0)
        if code not in (CKR_OK, CKR_USER_ALREADY_LOGGED_IN):
            raise Pkcs11Error("C_Login", code)

    # ---------- objetos ----------
    def find(self, session: int, object_class: int, key_id: bytes | None = None) -> list[int]:
        klass = CK_ULONG(object_class)
        template = [_Attribute(CKA_CLASS, ctypes.cast(ctypes.byref(klass), ctypes.c_void_p), ctypes.sizeof(klass))]
        if key_id is not None:
            buffer = ctypes.create_string_buffer(key_id, len(key_id))
            template.append(_Attribute(CKA_ID, ctypes.cast(buffer, ctypes.c_void_p), len(key_id)))
        array = (_Attribute * len(template))(*template)
        self.call("C_FindObjectsInit", session, array, len(template))
        found: list[int] = []
        try:
            chunk = (CK_ULONG * 64)()
            count = CK_ULONG()
            while True:
                self.call("C_FindObjects", session, chunk, 64, ctypes.byref(count))
                if not count.value:
                    break
                found += chunk[:count.value]
        finally:
            self._calls["C_FindObjectsFinal"](session)
        return found

    def attribute(self, session: int, handle: int, kind: int) -> bytes:
        query = _Attribute(kind, None, 0)
        self.call("C_GetAttributeValue", session, handle, ctypes.byref(query), 1)
        if query.length in (0, CK_ULONG(-1).value):
            return b""
        buffer = ctypes.create_string_buffer(query.length)
        query.value = ctypes.cast(buffer, ctypes.c_void_p)
        self.call("C_GetAttributeValue", session, handle, ctypes.byref(query), 1)
        return buffer.raw[:query.length]

    def sign(self, session: int, key: int, mechanism: int, data: bytes) -> bytes:
        mech = _Mechanism(mechanism, None, 0)
        self.call("C_SignInit", session, ctypes.byref(mech), key)
        source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        size = CK_ULONG(0)
        self.call("C_Sign", session, source, len(data), None, ctypes.byref(size))
        target = (ctypes.c_ubyte * size.value)()
        self.call("C_Sign", session, source, len(data), target, ctypes.byref(size))
        return bytes(target[:size.value])


def nss_parameters(folder: str) -> str:
    """Parámetros para abrir con el módulo de NSS el almacén de certificados de una carpeta, sin cambiarlo."""
    escaped = folder.replace("\\", "\\\\").replace("'", "\\'")
    return f"configdir='sql:{escaped}' certPrefix='' keyPrefix='' secmod='secmod.db' flags=readOnly,noModDB"


def text(field: bytes) -> str:
    """Los textos de PKCS#11 van rellenos de espacios hasta su tamaño."""
    return field.decode("utf-8", "replace").rstrip(" \x00")
