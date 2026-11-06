#!/usr/bin/env python3
"""Local REST bridge for an installed Parashara's Light 9 copy.

This bridge writes native PL9 BirthData XML, asks the installed PL9 GUI to open
it, and exposes PL9's documented export files. It does not calculate astrology
itself and never substitutes another engine.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, json, os, re, secrets, subprocess, sys, time, uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

API_VERSION = "0.2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10810
MAX_BODY_BYTES = 1_048_576
BIRTH_EXPORT_FIELDS = [
    "name", "gender", "anka", "city", "state", "country", "birth_date",
    "birth_time", "birth_julian_day_local", "timezone_pl9", "dst_hours",
    "longitude_pl9", "latitude_pl9",
]
PLANET_FIELDS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]

class RequestError(ValueError):
    pass

def _finite_number(value: Any, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError(f"{field} must be a number")
    value = float(value)
    if not (low <= value <= high):
        raise RequestError(f"{field} must be between {low} and {high}")
    return value

def _required_text(data: dict[str, Any], field: str, limit: int = 160) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{field} is required and must be a non-empty string")
    value = value.strip()
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise RequestError(f"{field} is too long or contains control characters")
    return value

def julian_day_local(local_datetime: dt.datetime) -> float:
    """PL9 BirthDate is local civil date/time as a Julian day, not converted to UTC."""
    if local_datetime.tzinfo is not None:
        raise ValueError("local_datetime must be timezone-naive")
    epoch = dt.datetime(1970, 1, 1)
    seconds = (local_datetime - epoch).total_seconds()
    return 2440587.5 + seconds / 86400.0

def _split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    if not parts:
        raise RequestError("name is required")
    return parts[0], " ".join(parts[1:])

def build_birth_xml(birth: dict[str, Any]) -> bytes:
    """Create the documented native PL9 BirthData XML file."""
    if not isinstance(birth, dict):
        raise RequestError("birth must be a JSON object")
    name = _required_text(birth, "name", 120)
    first_name, last_name = _split_name(name)
    gender_value = birth.get("gender")
    if isinstance(gender_value, bool):
        raise RequestError("gender must be 'male' or 'female'")
    gender_map = {"male": 0, "female": 1, 0: 0, 1: 1}
    if gender_value not in gender_map:
        raise RequestError("gender must be 'male' or 'female'")
    date_text = _required_text(birth, "date", 10)
    time_text = _required_text(birth, "time", 8)
    try:
        birth_date = dt.date.fromisoformat(date_text)
        try:
            birth_time = dt.time.fromisoformat(time_text)
        except ValueError:
            if re.fullmatch(r"\d{2}:\d{2}", time_text):
                birth_time = dt.time.fromisoformat(time_text + ":00")
            else:
                raise
    except ValueError as exc:
        raise RequestError("date must be YYYY-MM-DD and time HH:MM[:SS]") from exc
    local_dt = dt.datetime.combine(birth_date, birth_time)
    jd_local = julian_day_local(local_dt)
    # API uses standard geographic signs; PL9 stores east as negative.
    utc_offset = _finite_number(birth.get("utc_offset_hours"), "utc_offset_hours", -14, 14)
    dst_hours = _finite_number(birth.get("dst_hours", 0), "dst_hours", 0, 3)
    longitude_east = _finite_number(birth.get("longitude_east_deg"), "longitude_east_deg", -180, 180)
    latitude_north = _finite_number(birth.get("latitude_north_deg"), "latitude_north_deg", -90, 90)
    anka = birth.get("anka", 1)
    if isinstance(anka, bool) or not isinstance(anka, int) or not 1 <= anka <= 5:
        raise RequestError("anka must be an integer from 1 to 5")
    rodden = birth.get("rodden_rating", 0)
    if isinstance(rodden, bool) or not isinstance(rodden, int) or not 0 <= rodden <= 5:
        raise RequestError("rodden_rating must be an integer from 0 to 5")
    lookup_mode = birth.get("bd_lookup_mode", "D")
    if lookup_mode not in ("A", "D", "S"):
        raise RequestError("bd_lookup_mode must be one of the native PL9 values: A, D, S")
    city = _required_text(birth, "city", 120)
    state = str(birth.get("state", "")).strip()
    country = _required_text(birth, "country", 120)
    if len(state) > 120 or any(ord(ch) < 32 for ch in state):
        raise RequestError("state is too long or contains control characters")
    root = ET.Element("BirthData")
    info = ET.SubElement(root, "BirthInfo")
    values = [
        ("FirstName", first_name), ("LastName", last_name), ("Anka", str(anka)),
        ("Gender", str(gender_map[gender_value])), ("BirthDate", f"{jd_local:.12f}"),
        ("Longitude", f"{(-longitude_east if longitude_east != 0 else 0.0):.12f}"),
        ("Latitude", f"{latitude_north:.12f}"),
        ("TimeZone", f"{(-utc_offset if utc_offset != 0 else 0.0):.12f}"),
        ("DST", f"{dst_hours:.12f}"), ("City", city), ("State", state),
        ("Country", country), ("BDLoopUpMode", lookup_mode), ("RoddenRating", str(rodden)),
    ]
    for tag, value in values:
        ET.SubElement(info, tag).text = value
    ET.SubElement(root, "Events")
    ET.SubElement(root, "DetailedNotes").text = ""
    ET.SubElement(root, "BirthdataPicklist").text = ""
    ET.SubElement(root, "AnimatedPicklist").text = ""
    ET.SubElement(root, "MuhurtaPicklist").text = ""
    family = ET.SubElement(root, "FamilyInfo")
    for tag in ("GrandFather", "Father", "Mother", "Caste", "Gotra", "Address1",
                "Address2", "Address3", "Email", "Phone", "Tags", "Unused1",
                "Unused2", "Unused3"):
        ET.SubElement(family, tag).text = ""
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=False)
    return body.replace(b"?>", b"?>\n<!DOCTYPE BirthData>", 1)

def parse_birth_data_export(path: Path) -> dict[str, Any]:
    """Parse PL9 export; preserve auxiliary fields whose manual numbering conflicts."""
    if not path.is_file():
        raise FileNotFoundError(f"PL9 birth-data export not found: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    stat = path.stat()
    if not lines:
        return {
            "source": "PL9_EXPORT_BIRTHDAT", "path": str(path),
            "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            "delimiter": None, "rows": [], "documentation_note": "Export file is present but empty.",
        }
    delimiter = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(lines, delimiter=delimiter))
    parsed_rows = []
    for row_number, row in enumerate(rows, 1):
        if not row:
            continue
        item: dict[str, Any] = {"row": row_number, "raw_fields": row, "field_count": len(row)}
        for index, field in enumerate(BIRTH_EXPORT_FIELDS):
            if index < len(row):
                item[field] = row[index]
        if len(row) >= 22:
            item["planet_longitudes"] = {planet: row[13 + i] for i, planet in enumerate(PLANET_FIELDS)}
        if len(row) >= 29:
            item["auxiliary_points_raw_fields_23_to_29"] = row[22:29]
        if len(row) >= 32:
            item["outer_planet_longitudes"] = {"Uranus": row[29], "Neptune": row[30], "Pluto": row[31]}
        if len(row) >= 33:
            item["ascendant_longitude"] = row[32]
        parsed_rows.append(item)
    return {
        "source": "PL9_EXPORT_BIRTHDAT", "path": str(path),
        "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
        "delimiter": "TAB" if delimiter == "\t" else "COMMA", "rows": parsed_rows,
        "documentation_note": (
            "PL9 manual and bundled sample differ on delimiter and auxiliary-point field numbering. "
            "Auxiliary fields are therefore preserved raw."
        ),
    }

def match_birth_data_export_row(export: dict[str, Any], birth: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the PL9 export row matching every submitted birth-data field.

    This is the gate that permits calculation_verified=True. The PL9 sample
    export has 33 fields; ambiguous auxiliary fields remain as parsed by the
    existing parser and are not re-labelled here.
    """
    name = _required_text(birth, "name", 120)
    birth_date = dt.date.fromisoformat(_required_text(birth, "date", 10)).strftime("%d:%m:%Y")
    time_text = _required_text(birth, "time", 8)
    try:
        parsed_time = dt.time.fromisoformat(time_text)
    except ValueError as exc:
        raise RequestError("time must be HH:MM[:SS]") from exc
    birth_time = parsed_time.strftime("%H:%M:%S")
    local_dt = dt.datetime.combine(dt.date.fromisoformat(_required_text(birth, "date", 10)), parsed_time)
    expected_gender = {"male": "0", "female": "1", 0: "0", 1: "1"}.get(birth.get("gender", "male"))
    expected_anka = str(birth.get("anka", 1))
    expected_text = {
        "name": name, "birth_date": birth_date, "birth_time": birth_time,
        "city": _required_text(birth, "city", 120),
        "country": _required_text(birth, "country", 120),
    }
    if str(birth.get("state", "")).strip():
        expected_text["state"] = str(birth["state"]).strip()
    expected_numbers = {
        "birth_julian_day_local": julian_day_local(local_dt),
        "timezone_pl9": -float(birth["utc_offset_hours"]),
        "dst_hours": float(birth.get("dst_hours", 0)),
        "longitude_pl9": -float(birth["longitude_east_deg"]),
        "latitude_pl9": float(birth["latitude_north_deg"]),
    }
    for row in export.get("rows", []):
        if any(str(row.get(key, "")).strip().casefold() != value.casefold()
               for key, value in expected_text.items()):
            continue
        if expected_gender is not None and str(row.get("gender", "")).strip() != expected_gender:
            continue
        if str(row.get("anka", "")).strip() != expected_anka:
            continue
        try:
            if any(abs(float(row[key]) - value) > 1e-6 for key, value in expected_numbers.items()):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        return {
            **row,
            "matched_input": True,
            "verification": {
                "identity": True, "date_time": True, "city_region_country": True,
                "timezone_dst": True, "coordinates": True, "julian_day": True,
            },
        }
    return None


def parse_ephemeris_export(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"PL9 ephemeris export not found: {path}")
    stat = path.stat()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return {
        "source": "PL9_EXPORT_EPHEMERIS", "path": str(path),
        "modified_utc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
        "line_count": len(text.splitlines()), "content": text,
    }

def _find_pl9_exe() -> Path | None:
    env = os.environ.get("PL9_EXE")
    candidates = [Path(env)] if env else []
    candidates.extend([
        Path(r"C:\GeoVision\PL9\PL9.exe"),
        Path(r"C:\Program Files\GeoVision\PL9\PL9.exe"),
        Path.home() / "GeoVision" / "PL9" / "PL9.exe",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None

@dataclass
class Config:
    exe: Path | None
    charts_dir: Path
    export_dir: Path
    host: str
    port: int
    api_key: str
    @classmethod
    def from_env(cls) -> "Config":
        exe = _find_pl9_exe()
        if os.environ.get("PL9_CHARTS_DIR"):
            charts = Path(os.environ["PL9_CHARTS_DIR"])
        elif exe:
            charts = exe.parent.parent / "GeoVisionCharts"
        else:
            charts = Path.home() / "Documents" / "GeoVisionCharts"
        if os.environ.get("PL9_EXPORT_DIR"):
            export_dir = Path(os.environ["PL9_EXPORT_DIR"])
        elif exe:
            export_dir = exe.parent / "Export"
        else:
            export_dir = Path.home() / "Documents" / "PL9" / "Export"
        host = os.environ.get("PL9_API_HOST", DEFAULT_HOST)
        if host != DEFAULT_HOST:
            raise RuntimeError("PL9 API is loopback-only; PL9_API_HOST must be 127.0.0.1")
        key = os.environ.get("PL9_API_KEY") or secrets.token_urlsafe(32)
        return cls(exe, charts, export_dir, host, int(os.environ.get("PL9_API_PORT", DEFAULT_PORT)), key)

class PL9Controller:
    def __init__(self, config: Config):
        self.config = config
    def health(self) -> dict[str, Any]:
        exe = self.config.exe
        running, pid = False, None
        if os.name == "nt":
            try:
                output = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq PL9.exe", "/FO", "CSV", "/NH"],
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for row in csv.reader(output.splitlines()):
                    if row and row[0].lower() == "pl9.exe":
                        running = True
                        if len(row) > 1:
                            pid = int(row[1])
                        break
            except Exception:
                pass
        return {
            "service": "PL9 Local API", "api_version": API_VERSION,
            "engine": "Parashara's Light 9", "engine_executable": str(exe) if exe else None,
            "executable_found": bool(exe and exe.is_file()), "process_running": running,
            "process_id": pid, "charts_directory": str(self.config.charts_dir),
            "export_directory": str(self.config.export_dir),
            "automation_dependency": "pywinauto", "calculation_verified": False,
            "note": "Health status does not prove that a horoscope calculation has run.",
        }
    def _connect_or_start(self):
        if os.name != "nt":
            raise RuntimeError("PL9 GUI automation is available only on Windows")
        if not self.config.exe or not self.config.exe.is_file():
            raise RuntimeError("PL9.exe not found. Set PL9_EXE to the installed executable.")
        try:
            from pywinauto import Application, Desktop
        except ImportError as exc:
            raise RuntimeError("Install pywinauto: python -m pip install -r requirements.txt") from exc

        desktop = Desktop(backend="win32")

        def find_main():
            visible = desktop.windows(visible_only=True)
            matches = []
            for candidate in visible:
                try:
                    title = candidate.window_text().casefold()
                    if "parashara" in title and "light" in title:
                        matches.append(candidate)
                except Exception:
                    continue
            if not matches:
                return None, visible
            # PL9's main top-level window is titled "Parashara's Light 9.0".
            matches.sort(key=lambda w: ("parashara's light" not in w.window_text().casefold(),
                                        len(w.window_text())))
            return matches[0], visible

        app = Application(backend="win32")
        win, visible = find_main()
        if win is not None:
            pid = win.process_id()
            app.connect(process=pid, timeout=10)
            # Desktop.windows(visible_only=True) already returns visible wrappers.
            # pywinauto DialogWrapper has no wait() method.
            win.set_focus()
            return app, win

        try:
            app.connect(path=str(self.config.exe), timeout=2)
        except Exception:
            app.start(f'"{self.config.exe}"', work_dir=str(self.config.exe.parent), timeout=30)

        deadline = time.time() + 60
        last_titles = []
        while time.time() < deadline:
            win, visible = find_main()
            last_titles = []
            for candidate in visible:
                try:
                    last_titles.append({
                        "title": candidate.window_text(),
                        "handle": hex(candidate.handle),
                        "pid": candidate.process_id(),
                    })
                except Exception:
                    pass
            if win is not None:
                pid = win.process_id()
                try:
                    app.connect(process=pid, timeout=10)
                except Exception:
                    pass
                # Desktop.windows(visible_only=True) already returns visible wrappers.
                # pywinauto DialogWrapper has no wait() method.
                win.set_focus()
                return app, win
            time.sleep(0.5)
        raise RuntimeError(f"PL9 process started/connected but main window was not found. Visible windows: {last_titles}")

    def _other_windows(self, pid: int, main_handle: int):
        from pywinauto import Desktop
        return [
            w for w in Desktop(backend="win32").windows(process=pid, visible_only=True)
            if w.handle != main_handle
        ]

    @staticmethod
    def _window_info(window):
        try:
            return {
                "title": window.window_text(),
                "handle": hex(window.handle),
                "process_id": window.process_id(),
                "class_name": window.class_name(),
            }
        except Exception:
            return {"title": str(window)}

    def open_chart(self, chart_path: Path, display_name: str) -> dict[str, Any]:
        from pywinauto import Desktop
        pid = None
        try:
            app, win = self._connect_or_start()
            pid = win.process_id()
            existing = self._other_windows(pid, win.handle)
            if existing:
                return {
                    "status": "MODAL_DIALOG_BLOCKS_OPEN",
                    "process_id": pid,
                    "main_window": win.window_text(),
                    "blocking_windows": [self._window_info(w) for w in existing],
                    "chart_file": str(chart_path),
                    "calculation_verified": False,
                }
            win.set_focus()
            # PL9 manual confirms Ctrl+O opens the native chart file dialog.
            # Send a real Windows key chord to the foreground PL9 window;
            # pywinauto type_keys did not activate the Qt shortcut on the runner.
            import ctypes
            user32 = ctypes.windll.user32
            user32.ShowWindow(win.handle, 9)
            user32.SetForegroundWindow(win.handle)
            time.sleep(0.2)
            user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
            time.sleep(0.05)
            user32.keybd_event(0x4F, 0, 0, 0)  # O down
            user32.keybd_event(0x4F, 0, 0x0002, 0)  # O up
            user32.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl up
            dialog = None
            dialogs = []
            deadline = time.time() + 12
            popup_classes = {"qt5qwindowpopupdropshadowsavebits", "qt5qwindowtool"}
            while time.time() < deadline:
                candidates = self._other_windows(pid, win.handle)
                dialogs = []
                for candidate in candidates:
                    try:
                        candidate_pid = candidate.process_id()
                        candidate_class = candidate.class_name().casefold()
                        candidate_title = candidate.window_text().casefold()
                        if candidate_pid != pid or candidate_class in popup_classes:
                            continue
                        if "file manager" in candidate_title:
                            continue
                        if not candidate_class and not candidate_title:
                            continue
                        dialogs.append(candidate)
                    except Exception:
                        continue
                dialog = next(
                    (w for w in dialogs if any(
                        token in w.window_text().casefold()
                        for token in ("open", "chart", "открыть")
                    )),
                    None,
                )
                # The Qt file dialog may have the generic title "PL9".
                if dialog is None and len(dialogs) == 1:
                    candidate = dialogs[0]
                    candidate_class = candidate.class_name().casefold()
                    if candidate_class in {"qt5qwindowicon", "#32770"}:
                        dialog = candidate
                if dialog is not None:
                    break
                time.sleep(0.25)
            if dialog is None:
                return {
                    "status": "OPEN_DIALOG_NOT_DETECTED",
                    "process_id": pid,
                    "main_window": win.window_text(),
                    "visible_dialogs": [self._window_info(w) for w in dialogs],
                    "all_process_windows": [self._window_info(w) for w in Desktop(backend="win32").windows(process=pid, visible_only=True)],
                    "chart_file": str(chart_path),
                    "calculation_verified": False,
                }
            dialog.set_focus()
            controls = dialog.descendants()
            edit_controls = []
            list_controls = []
            for control in controls:
                try:
                    kind = control.friendly_class_name().casefold()
                    if kind in {"edit", "editbox"}:
                        edit_controls.append(control)
                    if kind in {"listbox", "listview", "syslistview32", "list"}:
                        list_controls.append(control)
                except Exception:
                    continue
            file_name_edit = None
            for control in edit_controls:
                try:
                    if control.control_id() == 1152:
                        file_name_edit = control
                        break
                except Exception:
                    continue
            if file_name_edit is not None:
                file_name_edit.set_focus()
                file_name_edit.set_edit_text(str(chart_path))
                file_name_edit.type_keys("{ENTER}")
            else:
                # The standard PL9 "Open chart" dialog uses the File name field
                # (Alt+N), not the Explorer location bar (Ctrl+L).
                dialog.set_focus()
                dialog.type_keys("%n")
                time.sleep(0.2)
                dialog.type_keys(str(chart_path), with_spaces=True)
                time.sleep(0.2)
                dialog.type_keys("{ENTER}")
            time.sleep(1.5)
            title = win.window_text()
            remaining = self._other_windows(pid, win.handle)
            confirmed = (
                display_name.casefold() in title.casefold()
                or chart_path.stem.casefold() in title.casefold()
            )
            return {
                "status": "CHART_OPEN_CONFIRMED" if confirmed else "OPEN_REQUEST_SENT_UNCONFIRMED",
                "process_id": pid,
                "main_window": title,
                "chart_file": str(chart_path),
                "open_dialog": self._window_info(dialog),
                "remaining_windows": [self._window_info(w) for w in remaining],
                "calculation_verified": False,
            }
        except Exception as exc:
            return {
                "status": "OPEN_FAILED", "process_id": pid, "chart_file": str(chart_path),
                "error": str(exc), "calculation_verified": False,
            }
    def trigger_birth_export(self, expected_birth: dict[str, Any] | None = None) -> dict[str, Any]:
        export_file = self.config.export_dir / "birthdat.txt"
        before_mtime = export_file.stat().st_mtime_ns if export_file.exists() else None
        before_hash = hashlib.sha256(export_file.read_bytes()).hexdigest() if export_file.exists() else None
        try:
            app, win = self._connect_or_start()
            pid = win.process_id()
            existing = self._other_windows(pid, win.handle)
            if existing:
                return {
                    "status": "MODAL_DIALOG_BLOCKS_EXPORT",
                    "blocking_windows": [w.window_text() for w in existing],
                    "export_file": str(export_file),
                    "calculation_verified": False,
                }
            # PL9's Qt menu bar is not exposed as a Win32 HMENU.
            # Visible order from the native PL9 window is:
            # File, Edit, Charts, Reports, Classical references, Options,
            # Tools, Print, Research, Windows, Help.
            # From the open File menu, Research is eight Right moves away.
            # Its fourth item is Export Birth Chart.
            win.set_focus()
            win.type_keys("%f", set_foreground=True)
            time.sleep(0.2)
            win.type_keys("{RIGHT 8}{DOWN 4}{ENTER}")
            deadline = time.time() + 25
            while time.time() < deadline:
                if export_file.exists():
                    stat = export_file.stat()
                    current_hash = hashlib.sha256(export_file.read_bytes()).hexdigest()
                    changed = before_mtime is None or stat.st_mtime_ns != before_mtime or current_hash != before_hash
                    if changed:
                        exported = parse_birth_data_export(export_file)
                        if expected_birth is None:
                            return {
                                "status": "EXPORT_FILE_CHANGED",
                                "export": exported,
                                "pl9_export_generated": True,
                                "calculation_verified": False,
                                "verification": "Fresh PL9 export detected; no specific chart was supplied for identity matching.",
                            }
                        matched = match_birth_data_export_row(exported, expected_birth)
                        if matched is not None:
                            return {
                                "status": "CALCULATION_VERIFIED",
                                "export": {
                                    "source": exported["source"], "path": exported["path"],
                                    "modified_utc": exported["modified_utc"],
                                    "delimiter": exported["delimiter"],
                                },
                                "matched_chart": matched,
                                "pl9_export_generated": True,
                                "calculation_verified": True,
                                "verification": "A fresh PL9 export row matches the submitted name, birth date/time, place, coordinates, timezone, DST and local Julian day.",
                            }
                        return {
                            "status": "EXPORT_FILE_CHANGED_NO_EXACT_MATCH",
                            "export_file": str(export_file),
                            "export_row_count": len(exported.get("rows", [])),
                            "pl9_export_generated": True,
                            "calculation_verified": False,
                            "verification": "The export changed, but no row matched every submitted birth-data field. No result is certified for this chart.",
                        }
                time.sleep(0.5)
            return {
                "status": "EXPORT_NOT_CONFIRMED", "export_file": str(export_file),
                "manual_action": "In PL9 choose Research > Export Birth Chart; then call GET /exports/birth-data.",
                "calculation_verified": False,
            }
        except Exception as exc:
            return {
                "status": "EXPORT_TRIGGER_FAILED", "export_file": str(export_file),
                "error": str(exc), "calculation_verified": False,
            }

    def calculate_chart(self, chart_path: Path, display_name: str, expected_birth: dict[str, Any]) -> dict[str, Any]:
        opened = self.open_chart(chart_path, display_name)
        if opened.get("status") in {"OPEN_FAILED", "MODAL_DIALOG_BLOCKS_OPEN", "OPEN_DIALOG_NOT_DETECTED"}:
            return {
                "status": "CHART_OPEN_FAILED",
                "open": opened,
                "calculation_verified": False,
            }
        exported = self.trigger_birth_export(expected_birth)
        verified = exported.get("calculation_verified") is True
        return {
            "status": "CALCULATION_VERIFIED" if verified else exported.get("status", "CALCULATION_NOT_VERIFIED"),
            "open": opened,
            "export": exported,
            "calculation_verified": verified,
        }

    def select_menu(self, menu_path: str) -> dict[str, Any]:
        parts = [part.strip() for part in menu_path.split("->")]
        allowed_roots = {"Reports", "Research", "Charts", "Print", "Tools", "Interpretations", "Classical references"}
        if len(parts) < 2 or len(parts) > 5 or parts[0] not in allowed_roots or any(not p for p in parts):
            raise RequestError("menu_path must be a PL9 menu path beginning with Reports, Research, Charts, Print, Tools, Interpretations or Classical references")
        app, win = self._connect_or_start()
        root_indexes = {
            "Charts": 2, "Reports": 3, "Interpretations": 4,
            "Classical references": 5, "Tools": 7, "Print": 8, "Research": 9,
        }
        win.set_focus()
        try:
            # Qt's menu bar has no Win32 HMENU (GetMenu returned NULL in the
            # Windows probe). Navigate from the confirmed File hotkey instead.
            win.type_keys("%f", set_foreground=True)
            time.sleep(0.15)
            root_index = root_indexes[parts[0]]
            if root_index:
                win.type_keys(f"{{RIGHT {root_index}}}")
            time.sleep(0.15)
            win.type_keys("{DOWN}")
            for segment in parts[1:]:
                time.sleep(0.15)
                if parts[0] == "Research" and segment == "Export Birth Chart":
                    win.type_keys("{DOWN 3}")
                else:
                    win.type_keys(segment, with_spaces=True)
                win.type_keys("{ENTER}")
            time.sleep(0.35)
            return {
                "status": "MENU_INPUT_SENT_UNVERIFIED",
                "menu_path": "->".join(parts),
                "main_window": win.window_text(),
                "visible_windows": [self._window_info(w) for w in self._other_windows(win.process_id(), win.handle)],
                "calculation_verified": False,
                "note": "Keyboard navigation was sent; the selected report content has not been independently read.",
            }
        except Exception as exc:
            return {
                "status": "MENU_SELECTION_FAILED",
                "menu_path": "->".join(parts),
                "error": str(exc),
                "calculation_verified": False,
            }

def _chart_id_from_path(path: str) -> str | None:
    match = re.fullmatch(r"/charts/([0-9a-fA-F]{32})(?:/(?:open|calculate))?", path)
    return match.group(1).lower() if match else None

class PL9RequestHandler(BaseHTTPRequestHandler):
    server_version = f"PL9LocalAPI/{API_VERSION}"
    controller: PL9Controller
    config: Config
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[PL9-API] " + (fmt % args) + "\n")
    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def _authorized(self) -> bool:
        supplied = self.headers.get("X-API-Key", "")
        return secrets.compare_digest(supplied, self.config.api_key)
    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise RequestError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise RequestError(f"request body must be between 1 and {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("body must be valid UTF-8 JSON") from exc
        if not isinstance(data, dict):
            raise RequestError("JSON body must be an object")
        return data
    def do_GET(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            if path in ("/health", "/status"):
                self._send(200, self.controller.health())
                return
            if path == "/version":
                self._send(200, {"api": "PL9 Local API", "api_version": API_VERSION, "engine": "Parashara's Light 9"})
                return
            if path == "/exports/birth-data":
                self._send(200, parse_birth_data_export(self.config.export_dir / "birthdat.txt"))
                return
            if path == "/exports/ephemeris":
                self._send(200, parse_ephemeris_export(self.config.export_dir / "ephemeris.txt"))
                return
            chart_id = _chart_id_from_path(path)
            if chart_id and path == f"/charts/{chart_id}":
                chart_file = self.config.charts_dir / f"{chart_id}.xml"
                if not chart_file.is_file():
                    self._send(404, {"error": "chart_not_found", "chart_id": chart_id})
                    return
                metadata_path = self.config.export_dir / ".pl9_api" / f"{chart_id}.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
                self._send(200, {
                    "chart_id": chart_id, "chart_file": str(chart_file),
                    "xml_bytes": chart_file.stat().st_size, "status": metadata.get("status", "NATIVE_PL9_XML_PRESENT"),
                    "calculation_verified": metadata.get("calculation_verified", False),
                    "last_calculation_utc": metadata.get("last_calculation_utc"),
                })
                return
            self._send(404, {"error": "not_found"})
        except FileNotFoundError as exc:
            self._send(404, {"error": "export_not_found", "detail": str(exc)})
        except Exception as exc:
            self._send(500, {"error": "internal_error", "detail": str(exc)})
    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            if path in ("/charts", "/horoscope"):
                data = self._body()
                open_requested = data.get("open", True)
                calculate_requested = data.get("calculate", False)
                if not isinstance(open_requested, bool) or not isinstance(calculate_requested, bool):
                    raise RequestError("open and calculate must be boolean")
                if calculate_requested and not open_requested:
                    raise RequestError("calculate=true requires open=true")
                birth = data.get("birth")
                xml = build_birth_xml(birth)
                display_name = _required_text(birth, "name", 120)
                chart_id = uuid.uuid4().hex
                self.config.charts_dir.mkdir(parents=True, exist_ok=True)
                chart_file = self.config.charts_dir / f"{chart_id}.xml"
                temp_file = chart_file.with_suffix(".xml.tmp")
                temp_file.write_bytes(xml)
                temp_file.replace(chart_file)
                metadata_dir = self.config.export_dir / ".pl9_api"
                metadata_dir.mkdir(parents=True, exist_ok=True)
                metadata_path = metadata_dir / f"{chart_id}.json"
                metadata = {
                    "chart_id": chart_id, "birth": birth, "status": "NATIVE_PL9_XML_PRESENT",
                    "calculation_verified": False,
                    "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                if calculate_requested:
                    operation = self.controller.calculate_chart(chart_file, display_name, birth)
                    metadata.update({
                        "status": operation.get("status"),
                        "calculation_verified": operation.get("calculation_verified") is True,
                        "last_calculation_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    })
                    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                    self._send(200 if metadata["calculation_verified"] else 502, {
                        "chart_id": chart_id, "chart_file": str(chart_file), "xml_bytes": len(xml),
                        "file_status": "CREATED", "operation": operation,
                        "calculation_verified": metadata["calculation_verified"],
                    })
                    return
                opened = (
                    self.controller.open_chart(chart_file, display_name)
                    if open_requested else {"status": "NOT_REQUESTED", "calculation_verified": False}
                )
                self._send(201, {
                    "chart_id": chart_id, "chart_file": str(chart_file), "xml_bytes": len(xml),
                    "file_status": "CREATED", "open": opened, "calculation_verified": False,
                })
                return
            if path == "/exports/birth-data/run":
                result = self.controller.trigger_birth_export()
                self._send(200 if result.get("status") == "EXPORT_FILE_CHANGED" else 202, result)
                return
            if path == "/ui/menu":
                data = self._body()
                menu_path = _required_text(data, "menu_path", 240)
                result = self.controller.select_menu(menu_path)
                self._send(202 if result.get("status") == "MENU_INPUT_SENT_UNVERIFIED" else 502, result)
                return
            chart_id = _chart_id_from_path(path)
            if chart_id and path.endswith("/calculate"):
                chart_file = self.config.charts_dir / f"{chart_id}.xml"
                metadata_path = self.config.export_dir / ".pl9_api" / f"{chart_id}.json"
                if not chart_file.is_file() or not metadata_path.is_file():
                    self._send(404, {"error": "chart_or_birth_metadata_not_found", "chart_id": chart_id})
                    return
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                birth = metadata.get("birth")
                if not isinstance(birth, dict):
                    self._send(409, {"error": "birth_metadata_invalid", "chart_id": chart_id})
                    return
                display_name = _required_text(birth, "name", 120)
                operation = self.controller.calculate_chart(chart_file, display_name, birth)
                metadata.update({
                    "status": operation.get("status"),
                    "calculation_verified": operation.get("calculation_verified") is True,
                    "last_calculation_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                })
                metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send(200 if metadata["calculation_verified"] else 502, {
                    "chart_id": chart_id, "operation": operation,
                    "calculation_verified": metadata["calculation_verified"],
                })
                return
            if chart_id and path.endswith("/open"):
                chart_file = self.config.charts_dir / f"{chart_id}.xml"
                if not chart_file.is_file():
                    self._send(404, {"error": "chart_not_found", "chart_id": chart_id})
                    return
                root = ET.parse(chart_file).getroot()
                first = root.findtext("./BirthInfo/FirstName", "")
                last = root.findtext("./BirthInfo/LastName", "")
                result = self.controller.open_chart(chart_file, f"{first} {last}".strip())
                self._send(200 if result.get("status") != "OPEN_FAILED" else 503, result)
                return
            self._send(404, {"error": "not_found"})
        except RequestError as exc:
            self._send(400, {"error": "invalid_request", "detail": str(exc)})
        except Exception as exc:
            self._send(500, {"error": "internal_error", "detail": str(exc)})

def make_server(config: Config) -> ThreadingHTTPServer:
    controller = PL9Controller(config)
    class Handler(PL9RequestHandler):
        pass
    Handler.controller = controller
    Handler.config = config
    return ThreadingHTTPServer((config.host, config.port), Handler)

def main() -> int:
    parser = argparse.ArgumentParser(description="Local REST bridge for Parashara's Light 9")
    parser.add_argument("--check-xml", metavar="JSON_FILE", help="validate birth JSON and write native XML to stdout")
    args = parser.parse_args()
    if args.check_xml:
        data = json.loads(Path(args.check_xml).read_text(encoding="utf-8"))
        sys.stdout.buffer.write(build_birth_xml(data["birth"]))
        return 0
    config = Config.from_env()
    print("PL9 Local API — loopback only")
    print(f"URL: http://{config.host}:{config.port}")
    print(f"PL9 executable: {config.exe or 'NOT FOUND (set PL9_EXE)'}")
    print(f"Charts directory: {config.charts_dir}")
    print(f"Export directory: {config.export_dir}")
    print(f"API key (X-API-Key): {config.api_key}")
    print("No birth data or request bodies are written to the console.")
    server = make_server(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
