"""Build a Steam maFile from an exported Android Steam app data directory.

The script scans text, JSON, XML and SQLite files for Steam Guard fields. It
does not print secret values to stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any


SECRET_KEYS = {
    "steamid",
    "account_name",
    "shared_secret",
    "identity_secret",
    "revocation_code",
    "serial_number",
    "token_gid",
    "device_id",
    "android_id",
    "uri",
    "secret_1",
    "status",
}

REQUIRED_KEYS = {"shared_secret", "identity_secret"}
TEXT_MAX_BYTES = 20 * 1024 * 1024
SQLITE_MAX_ROWS_PER_TABLE = 5000


def mask(value: str | None) -> str:
    if not value:
        return "missing"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def decode_text(raw: bytes) -> str | None:
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def flatten_json(value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            key_str = str(key)
            if key_str in SECRET_KEYS and not isinstance(item, (dict, list)):
                yield key_str, str(item)
            yield from flatten_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from flatten_json(item)


def parse_json_text(text: str) -> Iterator[tuple[str, str]]:
    try:
        yield from flatten_json(json.loads(text))
    except json.JSONDecodeError:
        return


def parse_loose_text(text: str) -> Iterator[tuple[str, str]]:
    key_pattern = "|".join(re.escape(key) for key in sorted(SECRET_KEYS, key=len, reverse=True))
    quoted_pair = re.compile(
        rf"""["']?(?P<key>{key_pattern})["']?\s*[:=]\s*["'](?P<value>[^"']+)["']""",
        re.IGNORECASE,
    )
    xml_string = re.compile(
        rf"""<string[^>]+name=["'](?P<key>{key_pattern})["'][^>]*>(?P<value>.*?)</string>""",
        re.IGNORECASE | re.DOTALL,
    )

    for match in quoted_pair.finditer(text):
        yield match.group("key"), match.group("value").strip()

    for match in xml_string.finditer(text):
        yield match.group("key"), re.sub(r"\s+", "", match.group("value"))


def parse_text(text: str) -> Iterator[tuple[str, str]]:
    yield from parse_json_text(text)
    yield from parse_loose_text(text)


def is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def scan_sqlite(path: Path) -> Iterator[tuple[str, str]]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        table_rows = connection.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
        for (table_name,) in table_rows:
            if table_name.startswith("sqlite_"):
                continue

            columns = connection.execute(f"pragma table_info({quote_identifier(table_name)})").fetchall()
            text_columns = [
                column[1]
                for column in columns
                if not column[2] or any(token in column[2].upper() for token in ("TEXT", "CHAR", "CLOB", "VARCHAR"))
            ]
            if not text_columns:
                continue

            selected_columns = ", ".join(quote_identifier(column) for column in text_columns)
            query = f"select {selected_columns} from {quote_identifier(table_name)} limit {SQLITE_MAX_ROWS_PER_TABLE}"
            for row in connection.execute(query):
                for value in row:
                    if isinstance(value, str):
                        yield from parse_text(value)
                    elif isinstance(value, bytes):
                        decoded = decode_text(value)
                        if decoded:
                            yield from parse_text(decoded)
    finally:
        connection.close()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def scan_file(path: Path) -> Iterator[tuple[str, str]]:
    if is_sqlite(path):
        try:
            yield from scan_sqlite(path)
        except sqlite3.DatabaseError:
            pass

    try:
        if path.stat().st_size > TEXT_MAX_BYTES:
            return
        raw = path.read_bytes()
    except OSError:
        return

    decoded = decode_text(raw)
    if not decoded:
        return

    yield from parse_text(decoded)


def scan_directory_candidates(root: Path) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        for key, value in scan_file(path):
            key = key.lower()
            value = value.strip()
            if key not in SECRET_KEYS or not value:
                continue

            existing = None
            for candidate in candidates.setdefault(key, []):
                if candidate["value"] == value:
                    existing = candidate
                    break
            if existing is None:
                existing = {"value": value, "sources": []}
                candidates[key].append(existing)

            path_str = str(path)
            if path_str not in existing["sources"]:
                existing["sources"].append(path_str)

    return candidates


def scan_directory(root: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    candidates = scan_directory_candidates(root)
    found: dict[str, str] = {}
    sources: dict[str, list[str]] = {}

    for key, values in candidates.items():
        if not values:
            continue
        found[key] = values[0]["value"]
        sources[key] = values[0]["sources"]

    return found, sources


def detect_encrypted_guard_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in root.rglob("SecureStore.xml"):
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue

        for element in tree.getroot():
            name = element.attrib.get("name") or ""
            value = element.text or element.attrib.get("value") or ""
            if "steamguard" in name.lower():
                entries.append(
                    {
                        "path": str(path),
                        "name": name,
                        "value_len": len(value),
                    }
                )
    return entries


def build_mafile(fields: dict[str, str], steamid: str | None = None) -> dict[str, Any]:
    mafile: dict[str, Any] = {}
    if steamid:
        mafile["steamid"] = steamid

    for key in (
        "steamid",
        "account_name",
        "shared_secret",
        "identity_secret",
        "revocation_code",
        "serial_number",
        "token_gid",
        "device_id",
        "android_id",
        "uri",
        "secret_1",
        "status",
    ):
        if key in fields and key not in mafile:
            value: Any = fields[key]
            if key == "status":
                try:
                    value = int(value)
                except ValueError:
                    pass
            mafile[key] = value

    missing = REQUIRED_KEYS - mafile.keys()
    if missing:
        raise ValueError(f"Required fields were not found: {', '.join(sorted(missing))}")
    return mafile


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Steam Guard maFile fields from exported Android data")
    parser.add_argument("input", type=Path, help="Extracted Android app data directory")
    parser.add_argument("--out", type=Path, help="Output maFile path")
    parser.add_argument("--steamid", help="SteamID64 override if it is not present in the export")
    parser.add_argument("--report-only", action="store_true", help="Scan and print a masked report without writing maFile")
    parser.add_argument("--show-sources", action="store_true", help="Print source paths for discovered fields")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input path does not exist: {args.input}")

    encrypted_guard_entries = detect_encrypted_guard_entries(args.input)
    candidates = scan_directory_candidates(args.input)
    fields, sources = scan_directory(args.input)

    if encrypted_guard_entries:
        print("Encrypted Steam Guard entries detected:")
        for entry in encrypted_guard_entries:
            print(f"  {entry['name']} value_len={entry['value_len']} source={entry['path']}")
        print(
            "  Note: Expo SecureStore values are encrypted by Android Keystore. "
            "Plain adb file export cannot turn these entries into maFile secrets."
        )

    print("Scan summary:")
    for key in sorted(SECRET_KEYS):
        value = fields.get(key)
        if value:
            source_count = len(sources.get(key, []))
            candidate_count = len(candidates.get(key, []))
            print(f"  {key}: found ({mask(value)}), candidates={candidate_count}, sources={source_count}")
            if args.show_sources:
                for index, candidate in enumerate(candidates.get(key, []), start=1):
                    print(f"    candidate {index}: {mask(candidate['value'])}")
                    for source in candidate["sources"][:10]:
                        print(f"      {source}")
                    if len(candidate["sources"]) > 10:
                        print(f"      ... and {len(candidate['sources']) - 10} more")
        else:
            print(f"  {key}: missing")

    if args.report_only:
        return

    if args.out is None:
        raise SystemExit("--out is required unless --report-only is used")

    try:
        mafile = build_mafile(fields, steamid=args.steamid)
    except ValueError as exc:
        print("")
        print(str(exc))
        print("Raw export was scanned, but no complete maFile could be built.")
        raise SystemExit(2) from exc

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(mafile, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(f"Wrote maFile: {args.out}")


if __name__ == "__main__":
    main()
