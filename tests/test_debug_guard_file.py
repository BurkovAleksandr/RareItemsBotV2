import pytest
import base64

from debug_guard_file import build_code_candidates, summarize_mafile


pytestmark = pytest.mark.mock


def test_summarize_mafile_flags_non_steamid64():
    summary = summarize_mafile(
        path="mafiles/test.txt",
        data={
            "steamid": "2275832103",
            "shared_secret": "shared",
            "identity_secret": "identity",
        },
        session_steam_id="76561198187797831",
    )

    assert summary["mafile_steamid_looks_64bit"] is False
    assert summary["steamid_matches_session"] is False
    assert summary["shared_secret_present"] is True
    assert summary["identity_secret_present"] is True


def test_build_code_candidates_supports_base32_uri():
    secret = b"01234567890123456789"
    data = {
        "shared_secret": base64.b64encode(secret).decode("ascii"),
        "uri": "otpauth://totp/Steam:test?secret="
        + base64.b32encode(secret).decode("ascii").rstrip("="),
    }

    candidates = build_code_candidates(data, timestamp=1_779_186_900)
    preferred = [candidate for candidate in candidates if candidate["preferred"]]

    assert {candidate["encoding"] for candidate in candidates} >= {"base64", "base32"}
    assert all(candidate["byte_len"] == 20 for candidate in preferred)
    assert len({candidate["current"] for candidate in preferred}) == 1
