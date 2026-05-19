"""Inspect and probe a Steam Guard maFile without buying anything."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from assets.steampy_compat import apply_steampy_compat

apply_steampy_compat()

from debug_buy_listing import (
    MobileConfirmationFetchError,
    _confirmation_device_id,
    _current_session_steam_id,
    _is_probable_steam_id64,
    _mask_secret,
    apply_mafile,
    decode_secret_candidates,
    fetch_mobile_confirmations,
    generate_steam_code_from_secret_bytes,
    load_saved_client,
    print_mobile_confirmation_fetch_error,
    select_secret_bytes,
)
from steampy import guard


def _load_guard_data(path: Path) -> dict[str, Any]:
    return guard.load_steam_guard(str(path))


def _secret_present(data: dict[str, Any], key: str) -> bool:
    return bool(data.get(key))


def summarize_mafile(path: Path, data: dict[str, Any], session_steam_id: str | None) -> dict[str, Any]:
    mafile_steam_id = str(data.get("steamid") or "")
    return {
        "path": str(path),
        "keys": sorted(data.keys()),
        "account_name": data.get("account_name"),
        "mafile_steamid": _mask_secret(mafile_steam_id),
        "mafile_steamid_looks_64bit": _is_probable_steam_id64(mafile_steam_id),
        "session_steamid": _mask_secret(session_steam_id),
        "steamid_matches_session": bool(session_steam_id and mafile_steam_id == session_steam_id),
        "shared_secret_present": _secret_present(data, "shared_secret"),
        "identity_secret_present": _secret_present(data, "identity_secret"),
        "device_id_present": _secret_present(data, "device_id") or _secret_present(data, "android_id"),
        "uri_present": _secret_present(data, "uri"),
    }


def build_code_candidates(data: dict[str, Any], timestamp: int | None = None) -> list[dict[str, Any]]:
    if timestamp is None:
        timestamp = int(time.time())

    candidates = decode_secret_candidates(data.get("shared_secret"), uri=data.get("uri"))
    report: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        item = {
            "candidate": index,
            "source": candidate["source"],
            "encoding": candidate["encoding"],
            "byte_len": candidate["byte_len"],
            "preferred": candidate["preferred"],
            "previous": generate_steam_code_from_secret_bytes(candidate["bytes"], timestamp - 30),
            "current": generate_steam_code_from_secret_bytes(candidate["bytes"], timestamp),
            "next": generate_steam_code_from_secret_bytes(candidate["bytes"], timestamp + 30),
        }
        report.append(item)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam Guard maFile diagnostic helper")
    parser.add_argument("--mafile", required=True, type=Path, help="maFile to inspect")
    parser.add_argument("--session", default=Path("cookies.pkl"), type=Path, help="Saved Steam session pickle")
    parser.add_argument("--show-code", action="store_true", help="Print a current 2FA code for manual comparison")
    parser.add_argument("--probe-confirmations", action="store_true", help="Call mobileconf/getlist without buying")
    args = parser.parse_args()

    data = _load_guard_data(args.mafile)
    client = None
    session_steam_id = None
    if args.session.exists():
        client = load_saved_client(args.session)
        session_steam_id = _current_session_steam_id(client)

    print("maFile summary:")
    print(json.dumps(summarize_mafile(args.mafile, data, session_steam_id), ensure_ascii=False, indent=2))

    if args.show_code:
        if not data.get("shared_secret") and not data.get("uri"):
            raise SystemExit("Cannot generate 2FA code: shared_secret and uri are missing")
        seconds_left = 30 - (int(time.time()) % 30)
        print("2FA code candidates:")
        print(json.dumps(build_code_candidates(data), ensure_ascii=False, indent=2))
        selected_secret = select_secret_bytes(data.get("shared_secret"), uri=data.get("uri"), field_name="shared_secret")
        print("Selected current 2FA code:", generate_steam_code_from_secret_bytes(selected_secret))
        print("Seconds left:", seconds_left)

    if args.probe_confirmations:
        if client is None:
            raise SystemExit("--probe-confirmations requires an existing --session pickle")
        apply_mafile(client, args.mafile)
        try:
            confirmations = fetch_mobile_confirmations(client)
        except MobileConfirmationFetchError as exc:
            print_mobile_confirmation_fetch_error(exc)
            raise SystemExit(2) from exc

        print("mobileconf/getlist OK")
        print("Generated device id:", _mask_secret(_confirmation_device_id(client)))
        print("Confirmations:")
        print(json.dumps([confirmation.safe_summary() for confirmation in confirmations], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
