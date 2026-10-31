"""Preferencias persistentes del usuario (QSettings en formato INI)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

from .. import APP_NAME, ORGANIZATION

MAX_RECENTS = 12
MAX_SIGNATURES = 3
MAX_CUSTOM_STAMPS = 6


class Settings:
    def __init__(self):
        self._qs = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORGANIZATION, APP_NAME)

    # ---------- genéricos ----------
    def value(self, key: str, default=None):
        return self._qs.value(key, default)

    def set_value(self, key: str, value) -> None:
        self._qs.setValue(key, value)

    def _bool(self, key: str, default: bool) -> bool:
        value = self._qs.value(key, default)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)

    def _json(self, key: str, default):
        raw = self._qs.value(key, "")
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def _set_json(self, key: str, value) -> None:
        self._qs.setValue(key, json.dumps(value))

    def sync(self) -> None:
        self._qs.sync()

    # ---------- idioma ----------
    @property
    def language(self) -> str:
        """«auto» (el del sistema) o un código de idioma (es, en, pt, fr)."""
        return str(self._qs.value("appearance/language", "auto"))

    @language.setter
    def language(self, value: str) -> None:
        self._qs.setValue("appearance/language", value)

    @property
    def welcomed(self) -> bool:
        """Si ya se mostró la bienvenida de la primera vez."""
        return self._bool("app/welcomed", False)

    @welcomed.setter
    def welcomed(self, value: bool) -> None:
        self._qs.setValue("app/welcomed", bool(value))

    # ---------- actualizaciones ----------
    @property
    def check_updates(self) -> bool:
        """Si se comprueba sola (como mucho una vez al mes) si hay una versión nueva."""
        return self._bool("updates/check", True)

    @check_updates.setter
    def check_updates(self, value: bool) -> None:
        self._qs.setValue("updates/check", bool(value))

    @property
    def last_update_check(self) -> float:
        try:
            return float(self._qs.value("updates/last", 0))
        except (TypeError, ValueError):
            return 0.0

    @last_update_check.setter
    def last_update_check(self, value: float) -> None:
        self._qs.setValue("updates/last", float(value))

    @property
    def skipped_version(self) -> str:
        return str(self._qs.value("updates/skipped", ""))

    @skipped_version.setter
    def skipped_version(self, value: str) -> None:
        self._qs.setValue("updates/skipped", value)

    def take_reopen_paths(self) -> list[str]:
        """Documentos que estaban abiertos antes de actualizar (se leen una sola vez)."""
        paths = self._json("updates/reopen", [])
        self._qs.remove("updates/reopen")
        return [p for p in paths if isinstance(p, str)]

    def set_reopen_paths(self, paths: list[str]) -> None:
        self._set_json("updates/reopen", list(paths))

    # ---------- apariencia ----------
    @property
    def theme(self) -> str:
        value = str(self._qs.value("appearance/theme", "dark"))
        return value if value in ("dark", "light", "system") else "dark"

    @theme.setter
    def theme(self, value: str) -> None:
        self._qs.setValue("appearance/theme", value)

    @property
    def night_mode(self) -> bool:
        return self._bool("viewer/night", False)

    @night_mode.setter
    def night_mode(self, value: bool) -> None:
        self._qs.setValue("viewer/night", bool(value))

    @property
    def sidebar_visible(self) -> bool:
        return self._bool("viewer/sidebar", True)

    @sidebar_visible.setter
    def sidebar_visible(self, value: bool) -> None:
        self._qs.setValue("viewer/sidebar", bool(value))

    @property
    def annotation_bar_visible(self) -> bool:
        return self._bool("viewer/annotationBar", True)

    @annotation_bar_visible.setter
    def annotation_bar_visible(self, value: bool) -> None:
        self._qs.setValue("viewer/annotationBar", bool(value))

    @property
    def sidebar_width(self) -> int:
        try:
            return max(160, min(480, int(self._qs.value("viewer/sidebarWidth", 216))))
        except (TypeError, ValueError):
            return 216

    @sidebar_width.setter
    def sidebar_width(self, value: int) -> None:
        self._qs.setValue("viewer/sidebarWidth", int(value))

    @property
    def comments_visible(self) -> bool:
        return self._bool("viewer/comments", False)

    @comments_visible.setter
    def comments_visible(self, value: bool) -> None:
        self._qs.setValue("viewer/comments", bool(value))

    @property
    def comments_width(self) -> int:
        try:
            return max(240, min(560, int(self._qs.value("viewer/commentsWidth", 320))))
        except (TypeError, ValueError):
            return 320

    @comments_width.setter
    def comments_width(self, value: int) -> None:
        self._qs.setValue("viewer/commentsWidth", int(value))

    # ---------- ventana ----------
    def window_geometry(self) -> QByteArray | None:
        value = self._qs.value("window/geometry")
        return value if isinstance(value, QByteArray) else None

    def set_window_geometry(self, value: QByteArray) -> None:
        self._qs.setValue("window/geometry", value)

    # ---------- certificados para firmar ----------
    def signing_files(self) -> dict[str, str]:
        """Archivos .pfx/.p12 ya usados, con el nombre de su titular (vacío si aún no se sabe)."""
        items = self._json("signing/files", {})
        found = {p: n for p, n in items.items() if isinstance(p, str) and isinstance(n, str)} \
            if isinstance(items, dict) else {}
        legacy = str(self._qs.value("signing/certificate", "") or "")
        if legacy and legacy not in found:
            found[legacy] = ""
        return found

    def remember_signing_file(self, path: str, holder: str = "") -> None:
        items = self.signing_files()
        items[path] = holder or items.get(path, "")
        self._set_json("signing/files", items)

    def forget_signing_file(self, path: str) -> None:
        items = self.signing_files()
        items.pop(path, None)
        if str(self._qs.value("signing/certificate", "") or "") == path:
            self._qs.remove("signing/certificate")
        self._set_json("signing/files", items)

    # ---------- recientes ----------
    def recent_files(self) -> list[str]:
        items = self._json("files/recent", [])
        return [p for p in items if isinstance(p, str)][:MAX_RECENTS]

    def add_recent(self, path: str | os.PathLike) -> None:
        path = str(Path(path).resolve())
        items = [p for p in self.recent_files() if os.path.normcase(p) != os.path.normcase(path)]
        items.insert(0, path)
        self._set_json("files/recent", items[:MAX_RECENTS])

    def remove_recent(self, path: str) -> None:
        self._set_json("files/recent", [p for p in self.recent_files() if p != path])

    def clear_recents(self) -> None:
        self._set_json("files/recent", [])

    @property
    def last_directory(self) -> str:
        default = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        value = str(self._qs.value("files/lastDirectory", default) or default)
        return value if Path(value).is_dir() else default

    @last_directory.setter
    def last_directory(self, value: str | os.PathLike) -> None:
        folder = Path(value)
        if folder.is_file():
            folder = folder.parent
        self._qs.setValue("files/lastDirectory", str(folder))

    # ---------- posición de lectura ----------
    def reading_position(self, path: str) -> dict | None:
        data = self._json("files/positions", {})
        entry = data.get(os.path.normcase(str(Path(path).resolve()))) if isinstance(data, dict) else None
        return entry if isinstance(entry, dict) else None

    def set_reading_position(self, path: str, page: int, zoom: float, fit: str) -> None:
        data = self._json("files/positions", {})
        if not isinstance(data, dict):
            data = {}
        key = os.path.normcase(str(Path(path).resolve()))
        data.pop(key, None)
        data[key] = {"page": int(page), "zoom": round(float(zoom), 4), "fit": fit}
        while len(data) > 200:
            data.pop(next(iter(data)))
        self._set_json("files/positions", data)

    # ---------- comentarios ----------
    @property
    def author_name(self) -> str:
        value = str(self._qs.value("comments/author", "") or "").strip()
        return value or system_user_name()

    @author_name.setter
    def author_name(self, value: str) -> None:
        self._qs.setValue("comments/author", value.strip())

    # ---------- anotaciones ----------
    def tool_style(self, tool: str) -> dict:
        return self._json(f"annotate/{tool}", {})

    def set_tool_style(self, tool: str, style: dict) -> None:
        self._set_json(f"annotate/{tool}", style)

    def signatures(self) -> list[list[list[list[float]]]]:
        items = self._json("annotate/signatures", [])
        return [s for s in items if isinstance(s, list) and s][:MAX_SIGNATURES]

    def set_signatures(self, items: list) -> None:
        self._set_json("annotate/signatures", items[:MAX_SIGNATURES])

    def hint_seen(self, name: str) -> bool:
        """Si ya se mostró una ayuda de uso; la primera vez la marca como vista."""
        key = f"hints/{name}"
        if self._qs.value(key, False, type=bool):
            return True
        self._qs.setValue(key, True)
        return False

    def stamp_choice(self) -> dict:
        value = self._json("annotate/stamp", {})
        return value if isinstance(value, dict) else {}

    def set_stamp_choice(self, value: dict) -> None:
        self._set_json("annotate/stamp", value)

    def custom_stamps(self) -> list[dict]:
        items = self._json("annotate/customStamps", [])
        return [s for s in items if isinstance(s, dict) and str(s.get("text", "")).strip()][:MAX_CUSTOM_STAMPS]

    def set_custom_stamps(self, items: list[dict]) -> None:
        self._set_json("annotate/customStamps", items[:MAX_CUSTOM_STAMPS])

    @property
    def custom_colors(self) -> list[str]:
        return [c for c in self._json("annotate/customColors", []) if isinstance(c, str)][:8]

    @custom_colors.setter
    def custom_colors(self, values: list[str]) -> None:
        self._set_json("annotate/customColors", values[:8])


def system_user_name() -> str:
    """Nombre visible del usuario del sistema (para firmar los comentarios)."""
    import getpass

    try:
        login = getpass.getuser()
    except Exception:
        login = ""
    if os.name == "nt":
        try:
            import ctypes

            size = ctypes.c_ulong(0)
            ctypes.windll.secur32.GetUserNameExW(3, None, ctypes.byref(size))
            buffer = ctypes.create_unicode_buffer(size.value or 256)
            if ctypes.windll.secur32.GetUserNameExW(3, buffer, ctypes.byref(size)) and buffer.value.strip():
                return buffer.value.strip()
        except Exception:
            pass
    else:
        try:
            import pwd

            gecos = pwd.getpwnam(login).pw_gecos.split(",")[0].strip()
            if gecos:
                return gecos
        except Exception:
            pass
    return login.capitalize() if login else "Usuario"


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
