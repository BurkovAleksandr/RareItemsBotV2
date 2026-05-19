import json

import pytest

from tools.steam_guard_from_adb_export import (
    build_mafile,
    detect_encrypted_guard_entries,
    scan_directory,
    scan_directory_candidates,
)


pytestmark = pytest.mark.mock


def test_scan_directory_builds_mafile_from_json(tmp_path):
    export_dir = tmp_path / "raw"
    export_dir.mkdir()
    (export_dir / "steam_guard.json").write_text(
        json.dumps(
            {
                "steamid": "76561198187797831",
                "shared_secret": "shared-secret-value",
                "identity_secret": "identity-secret-value",
                "revocation_code": "R12345",
            }
        ),
        encoding="utf-8",
    )

    fields, sources = scan_directory(export_dir)
    mafile = build_mafile(fields)

    assert mafile["steamid"] == "76561198187797831"
    assert mafile["shared_secret"] == "shared-secret-value"
    assert mafile["identity_secret"] == "identity-secret-value"
    assert "shared_secret" in sources


def test_scan_directory_candidates_groups_distinct_values(tmp_path):
    export_dir = tmp_path / "raw"
    export_dir.mkdir()
    (export_dir / "one.json").write_text('{"shared_secret":"one"}', encoding="utf-8")
    (export_dir / "two.json").write_text('{"shared_secret":"two"}', encoding="utf-8")

    candidates = scan_directory_candidates(export_dir)

    assert [candidate["value"] for candidate in candidates["shared_secret"]] == ["one", "two"]


def test_detect_encrypted_guard_entries(tmp_path):
    prefs_dir = tmp_path / "shared_prefs"
    prefs_dir.mkdir()
    (prefs_dir / "SecureStore.xml").write_text(
        """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
        <map><string name="key_v1-SteamGuard_1">encrypted-payload</string></map>
        """,
        encoding="utf-8",
    )

    entries = detect_encrypted_guard_entries(tmp_path)

    assert entries == [
        {
            "path": str(prefs_dir / "SecureStore.xml"),
            "name": "key_v1-SteamGuard_1",
            "value_len": len("encrypted-payload"),
        }
    ]
