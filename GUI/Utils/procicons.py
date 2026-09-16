# -*- coding: utf-8 -*-
"""
Application icon resolution for running processes.

Resolves a process to an application icon using, in order of preference:
    1. Steam/Proton appid -> steam_icon_<appid> / librarycache art
    2. Desktop entry (TryExec / Exec basename / StartupWMClass) -> Icon= -> icon theme
    3. Pure-python PE resource extraction for Wine executables (opt-in, cached)
    4. A bundled generic executable icon

All lookups are cached for the lifetime of the resolver. PE extraction is only
attempted when explicitly allowed, so listing processes stays cheap.
"""

import configparser
import glob
import os
import re
import struct
import subprocess

from PyQt6.QtGui import QIcon, QPixmap

from libpince import utils

_ICON_EXTENSIONS = (".png", ".svg", ".xpm", ".ico")
_DESKTOP_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")
_WINDOW_ID = re.compile(r"0x[0-9a-fA-F]+")
_STEAM_GAME_EXEC = "steam://rungameid/"
_GENERIC_ICON = "application.png"


def _xdg_data_dirs() -> list[str]:
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share/:/usr/share/")
    dirs = [entry for entry in data_dirs.split(":") if entry]
    dirs.append(os.path.expanduser("~/.local/share"))
    dirs.append("/var/lib/flatpak/exports/share")
    dirs.append(os.path.expanduser("~/.local/share/flatpak/exports/share"))
    seen = set()
    ordered = []
    for directory in dirs:
        if directory not in seen:
            seen.add(directory)
            ordered.append(directory)
    return ordered


def _icon_roots() -> list[str]:
    roots = []
    for data_dir in _xdg_data_dirs():
        roots.append(os.path.join(data_dir, "icons"))
    roots.append("/usr/share/pixmaps")
    return [root for root in roots if os.path.isdir(root)]


def _icon_size_score(path: str) -> tuple[int, int]:
    """Rank icon files, preferring PNG over SVG and larger sizes."""
    extension_rank = {".png": 3, ".ico": 2, ".svg": 1, ".xpm": 0}
    extension = os.path.splitext(path)[1].lower()
    size = 0
    match = re.search(r"/(\d+)x\1/", path)
    if match:
        size = int(match.group(1))
    elif "/scalable/" in path:
        size = 1024
    return (extension_rank.get(extension, -1), size)


def _find_icon_file(name: str) -> str | None:
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    for root in _icon_roots():
        for extension in _ICON_EXTENSIONS:
            direct = os.path.join(root, name + extension)
            if os.path.exists(direct):
                return direct
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        candidates = []
        for entry in entries:
            theme_dir = os.path.join(root, entry)
            if not os.path.isdir(theme_dir):
                continue
            for extension in _ICON_EXTENSIONS:
                candidates.extend(glob.glob(os.path.join(theme_dir, "*", name + extension)))
                candidates.extend(glob.glob(os.path.join(theme_dir, "*", "*", name + extension)))
        if candidates:
            return max(candidates, key=_icon_size_score)
    return None


def _exec_basename(exec_line: str) -> str:
    tokens = re.findall(r'"[^"]*"|\S+', exec_line)
    index = 0
    if tokens and os.path.basename(tokens[0].strip('"')) == "env":
        index = 1
        while index < len(tokens) and "=" in tokens[index]:
            index += 1
    while index < len(tokens):
        token = _DESKTOP_FIELD_CODES.sub("", tokens[index]).strip('"')
        if token:
            return os.path.basename(token)
        index += 1
    return ""


def _read_desktop_file(path: str) -> dict | None:
    parser = configparser.RawConfigParser(strict=False, delimiters=("=",))
    try:
        with open(path, encoding="utf-8", errors="replace") as desktop_file:
            parser.read_file(desktop_file)
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    if section.get("Type", "Application") != "Application":
        return None
    exec_line = section.get("Exec", "")
    try_exec = section.get("TryExec", "")
    return {
        "id": os.path.basename(path),
        "icon": section.get("Icon", ""),
        "exec_basename": _exec_basename(exec_line).lower(),
        "tryexec_basename": os.path.basename(try_exec).lower(),
        "wmclass": section.get("StartupWMClass", "").lower(),
        "no_display": section.get("NoDisplay", "false").lower() == "true",
        "steam_game": _STEAM_GAME_EXEC in exec_line,
    }


def _load_desktop_entries() -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    by_exec: dict[str, list[dict]] = {}
    by_tryexec: dict[str, list[dict]] = {}
    by_wmclass: dict[str, list[dict]] = {}
    seen_paths = set()
    for data_dir in _xdg_data_dirs():
        applications = os.path.join(data_dir, "applications")
        if not os.path.isdir(applications):
            continue
        for dirpath, _dirnames, filenames in os.walk(applications):
            for filename in filenames:
                if not filename.endswith(".desktop"):
                    continue
                path = os.path.join(dirpath, filename)
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                entry = _read_desktop_file(path)
                if entry is None:
                    continue
                if entry["exec_basename"]:
                    by_exec.setdefault(entry["exec_basename"], []).append(entry)
                if entry["tryexec_basename"]:
                    by_tryexec.setdefault(entry["tryexec_basename"], []).append(entry)
                if entry["wmclass"]:
                    by_wmclass.setdefault(entry["wmclass"], []).append(entry)
    return by_exec, by_tryexec, by_wmclass


def _read_proc_link(pid: int | str) -> str:
    try:
        target = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""
    return re.sub(r"\s+\(deleted\)$", "", target)


def _read_proc_cmdline(pid: int | str) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as cmdline:
            return cmdline.read().decode("utf-8", "replace").replace("\0", " ")
    except OSError:
        return ""


def _read_proc_environ(pid: int | str, key: str) -> str | None:
    try:
        with open(f"/proc/{pid}/environ", "rb") as environ:
            content = environ.read()
    except OSError:
        return None
    prefix = (key + "=").encode()
    for variable in content.split(b"\0"):
        if variable.startswith(prefix):
            return variable[len(prefix):].decode("utf-8", "replace")
    return None


def _steam_appid(pid: int | str, exe_path: str, cmdline: str) -> str | None:
    for key in ("SteamAppId", "SteamGameId"):
        value = _read_proc_environ(pid, key)
        if value and value.isdigit():
            return value
    match = re.search(r"compatdata/(\d+)", exe_path + " " + cmdline)
    if match:
        return match.group(1)
    return None


def _librarycache_icon(appid: str) -> str | None:
    steam_roots = [
        os.path.expanduser("~/.steam/steam"),
        os.path.expanduser("~/.steam/root"),
        os.path.expanduser("~/.steam/debian-installation"),
        os.path.expanduser("~/.local/share/Steam"),
    ]
    for root in steam_roots:
        cache_dir = os.path.join(root, "appcache", "librarycache", appid)
        for candidate in ("logo.png", "library_600x900.jpg", "header.jpg"):
            path = os.path.join(cache_dir, candidate)
            if os.path.exists(path):
                return path
    return None


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _resource_entries(data: bytes, base: int, dir_offset: int) -> list[tuple[int, bool, int]]:
    if dir_offset + 16 > len(data):
        return []
    named = _u16(data, dir_offset + 12)
    ids = _u16(data, dir_offset + 14)
    entries = []
    for index in range(named + ids):
        entry = dir_offset + 16 + index * 8
        if entry + 8 > len(data):
            break
        name_or_id = _u32(data, entry)
        offset = _u32(data, entry + 4)
        entries.append((name_or_id, bool(offset & 0x80000000), offset & 0x7FFFFFFF))
    return entries


def _pe_resource_base(data: bytes) -> tuple[int, int] | None:
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe_offset = _u32(data, 0x3C)
    if pe_offset + 0x18 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        return None
    coff = pe_offset + 4
    num_sections = _u16(data, coff + 2)
    size_optional = _u16(data, coff + 16)
    optional = coff + 20
    if optional + 2 > len(data):
        return None
    magic = _u16(data, optional)
    data_directory = optional + (112 if magic == 0x20B else 96)
    if data_directory + 16 > len(data):
        return None
    resource_rva = _u32(data, data_directory + 2 * 8)
    if resource_rva == 0:
        return None
    section_table = optional + size_optional
    for index in range(num_sections):
        section = section_table + index * 40
        if section + 40 > len(data):
            return None
        virtual_address = _u32(data, section + 12)
        virtual_size = _u32(data, section + 8)
        raw_pointer = _u32(data, section + 20)
        if virtual_address <= resource_rva < virtual_address + max(virtual_size, 1):
            return raw_pointer, virtual_address
    return None


def _build_ico(group_data: bytes, icon_datas: dict[int, bytes]) -> bytes | None:
    count = _u16(group_data, 4)
    best = None
    for index in range(count):
        entry = 6 + index * 14
        if entry + 14 > len(group_data):
            break
        width = group_data[entry]
        height = group_data[entry + 1]
        planes = _u16(group_data, entry + 4)
        bit_count = _u16(group_data, entry + 6)
        size = _u32(group_data, entry + 8)
        icon_id = _u16(group_data, entry + 12)
        if icon_id not in icon_datas:
            continue
        area = (width or 256) * (height or 256)
        if best is None or area > best[0]:
            best = (area, width, height, planes or 1, bit_count, icon_datas[icon_id])
    if best is None:
        return None
    _, width, height, planes, bit_count, image = best
    header = struct.pack("<HHH", 0, 1, 1)
    directory_entry = struct.pack("<BBBBHHII", width, height, 0, 0, planes, bit_count, len(image), 6 + 16)
    return header + directory_entry + image


def extract_pe_icon(path: str) -> bytes | None:
    """Extract the largest RT_ICON from a PE executable as a single-image ICO."""
    try:
        with open(path, "rb") as executable:
            data = executable.read()
    except OSError:
        return None
    resource_base = _pe_resource_base(data)
    if resource_base is None:
        return None
    raw_pointer, virtual_address = resource_base
    base = raw_pointer

    def rva_to_offset(rva: int) -> int:
        return raw_pointer + (rva - virtual_address)

    icon_datas: dict[int, bytes] = {}
    group_data = None
    for type_id, is_dir, offset in _resource_entries(data, base, base):
        if not is_dir or type_id not in (3, 14):
            continue
        for name_id, is_dir2, offset2 in _resource_entries(data, base, base + offset):
            if not is_dir2:
                continue
            for _lang, is_dir3, offset3 in _resource_entries(data, base, base + offset2):
                if is_dir3:
                    continue
                data_entry = base + offset3
                if data_entry + 16 > len(data):
                    continue
                data_rva = _u32(data, data_entry)
                size = _u32(data, data_entry + 4)
                start = rva_to_offset(data_rva)
                blob = data[start:start + size]
                if type_id == 3:
                    icon_datas[name_id] = blob
                else:
                    group_data = blob
    if group_data is None or not icon_datas:
        return None
    return _build_ico(group_data, icon_datas)


def _wmclass_by_pid() -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        root = subprocess.run(
            ["xprop", "-root", "_NET_CLIENT_LIST"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return mapping
    for window_id in _WINDOW_ID.findall(root.stdout):
        try:
            window = subprocess.run(
                ["xprop", "-id", window_id, "_NET_WM_PID", "WM_CLASS"],
                capture_output=True,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        pid_match = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", window.stdout)
        class_matches = re.findall(r'"([^"]*)"', window.stdout)
        if pid_match and class_matches:
            mapping[pid_match.group(1)] = class_matches[-1].lower()
    return mapping


class ProcessIconResolver:
    def __init__(self) -> None:
        self._by_exec: dict[str, list[dict]] | None = None
        self._by_tryexec: dict[str, list[dict]] | None = None
        self._by_wmclass: dict[str, list[dict]] | None = None
        self._wmclass_by_pid: dict[str, str] | None = None
        self._exe_by_pid: dict[str, str] = {}
        self._cmdline_by_pid: dict[str, str] = {}
        self._icon_cache: dict[str, QIcon] = {}
        self._pe_attempted: set[str] = set()
        self._pe_pending: set[str] = set()
        self._generic_icon: QIcon | None = None

    def _ensure_desktop_index(self) -> None:
        if self._by_exec is None:
            self._by_exec, self._by_tryexec, self._by_wmclass = _load_desktop_entries()

    def _ensure_wmclass(self) -> None:
        if self._wmclass_by_pid is None:
            self._wmclass_by_pid = _wmclass_by_pid()

    def _exe_path(self, pid: int | str) -> str:
        key = str(pid)
        if key not in self._exe_by_pid:
            self._exe_by_pid[key] = _read_proc_link(pid)
        return self._exe_by_pid[key]

    def _cmdline(self, pid: int | str) -> str:
        key = str(pid)
        if key not in self._cmdline_by_pid:
            self._cmdline_by_pid[key] = _read_proc_cmdline(pid)
        return self._cmdline_by_pid[key]

    def _match_desktop(self, exe_basename: str, comm: str, pid: int | str) -> dict | None:
        self._ensure_desktop_index()
        self._ensure_wmclass()
        assert self._by_exec is not None and self._by_tryexec is not None and self._by_wmclass is not None
        wmclass = (self._wmclass_by_pid or {}).get(str(pid), "")
        candidates: list[dict] = []
        for group in (
            self._by_wmclass.get(wmclass, []) if wmclass else [],
            self._by_tryexec.get(exe_basename, []),
            self._by_exec.get(exe_basename, []),
            self._by_exec.get(comm, []),
        ):
            for entry in group:
                if entry not in candidates:
                    candidates.append(entry)
        if not candidates:
            return None
        candidates.sort(key=lambda entry: (entry["steam_game"], entry["no_display"]))
        return candidates[0]

    def _icon_from_name(self, name: str) -> QIcon | None:
        if not name:
            return None
        if name in self._icon_cache:
            return self._icon_cache[name]
        icon = QIcon.fromTheme(name)
        if icon.isNull():
            path = _find_icon_file(name)
            if path:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    icon = QIcon(pixmap)
        if icon.isNull():
            return None
        self._icon_cache[name] = icon
        return icon

    def _generic(self) -> QIcon:
        if self._generic_icon is None:
            path = os.path.join(utils.get_script_directory(), "media", "icons", _GENERIC_ICON)
            pixmap = QPixmap(path)
            self._generic_icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
        return self._generic_icon

    def get_icon(self, pid: int | str, comm: str = "", *, allow_pe: bool = False) -> QIcon:
        exe_path = self._exe_path(pid)
        exe_basename = os.path.basename(exe_path).lower() if exe_path else ""
        cache_key = exe_path or f"comm:{comm.lower()}"

        # A wine row that was resolved eagerly holds a provisional generic icon until PE extraction runs on selection
        if cache_key in self._icon_cache and not (allow_pe and cache_key in self._pe_pending):
            return self._icon_cache[cache_key]

        cmdline = self._cmdline(pid)
        appid = _steam_appid(pid, exe_path, cmdline)
        if appid:
            icon = self._icon_from_name(f"steam_icon_{appid}")
            if icon is None:
                cache_dir_icon = _librarycache_icon(appid)
                if cache_dir_icon:
                    pixmap = QPixmap(cache_dir_icon)
                    if not pixmap.isNull():
                        icon = QIcon(pixmap)
            if icon is not None:
                self._icon_cache[cache_key] = icon
                self._pe_pending.discard(cache_key)
                return icon

        is_wine = exe_basename.endswith(".exe") or ".exe" in cmdline.lower()
        if is_wine:
            attempted = exe_path in self._pe_attempted
            if allow_pe and exe_path and not attempted:
                self._pe_attempted.add(exe_path)
                attempted = True
                try:
                    ico_data = extract_pe_icon(exe_path)
                except Exception as exception:  # noqa: BLE001 - never let icon lookup break the dialog
                    utils.logger.debug(f"PE icon extraction failed for {exe_path}: {exception}")
                    ico_data = None
                if ico_data:
                    pixmap = QPixmap()
                    if pixmap.loadFromData(ico_data, "ICO") and not pixmap.isNull():
                        icon = QIcon(pixmap)
                        self._icon_cache[cache_key] = icon
                        self._pe_pending.discard(cache_key)
                        return icon
            self._icon_cache[cache_key] = self._generic()
            if attempted:
                self._pe_pending.discard(cache_key)
            else:
                self._pe_pending.add(cache_key)
            return self._generic()

        match = self._match_desktop(exe_basename, comm.lower(), pid)
        if match:
            icon = self._icon_from_name(match["icon"])
            if icon is not None:
                self._icon_cache[cache_key] = icon
                return icon

        self._icon_cache[cache_key] = self._generic()
        return self._generic()


_resolver = ProcessIconResolver()


def get_process_icon(pid: int | str, comm: str = "", *, allow_pe: bool = False) -> QIcon:
    """Return the application icon for a process, falling back to a generic icon."""
    return _resolver.get_icon(pid, comm, allow_pe=allow_pe)
