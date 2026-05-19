"""Dry-run and optional buy test for a Steam market listing.

Default mode is safe: it loads a saved Steam session and parses an HTML market
listing page, but does not buy anything. To actually buy, pass all safeguards:

    python debug_buy_listing.py --html page.html --execute --yes --max-total-rub 10
    python debug_buy_listing.py --session cookies.pkl --confirm-only 123 --auto-confirm
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import pickle
import re
import struct
import time
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha1
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from assets.steampy_compat import apply_steampy_compat

apply_steampy_compat()

from assets.session import SteamPyClient
from steampy import guard
from steampy.client import SteamClient
from steampy.models import Currency, GameOptions


CONFIRMATION_URL = "https://steamcommunity.com/mobileconf"
STEAM_CODE_CHARS = "23456789BCDFGHJKMNPQRTVWXY"
EXPECTED_SECRET_BYTES = 20


@dataclass
class ListingCandidate:
    listing_id: str
    market_name: str | None
    subtotal: int
    fee: int
    total: int
    currency_id: int | None
    app_id: int = 730
    context_id: str = "2"
    asset_id: str | None = None

    @property
    def total_rub(self) -> Decimal:
        return Decimal(self.total) / 100


@dataclass
class MobileConfirmation:
    data_confid: str
    nonce: str
    creator_id: str | None = None
    type: int | None = None
    type_name: str | None = None
    headline: str | None = None
    summary: list[str] | None = None

    def matches(self, confirmation_id: str) -> bool:
        return self.data_confid == confirmation_id or self.creator_id == confirmation_id

    def safe_summary(self) -> dict[str, Any]:
        return {
            "id": self.data_confid,
            "creator_id": self.creator_id,
            "type": self.type,
            "type_name": self.type_name,
            "headline": self.headline,
            "summary": self.summary,
        }


class MobileConfirmationFetchError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]], fatal: bool = False) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.fatal = fatal


def parse_wallet_info(raw_html: str) -> dict[str, Any]:
    try:
        return _extract_js_object(raw_html, "g_rgWalletInfo")
    except ValueError:
        return {}


def _extract_js_object(raw_html: str, variable_name: str) -> dict[str, Any]:
    marker = f"var {variable_name} = "
    marker_index = raw_html.find(marker)
    if marker_index == -1:
        raise ValueError(f"Could not find {variable_name} in HTML")

    start = raw_html.find("{", marker_index)
    if start == -1:
        raise ValueError(f"Could not find object start for {variable_name}")

    depth = 0
    in_string = False
    escape = False
    quote = ""

    for index in range(start, len(raw_html)):
        char = raw_html[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw_html[start : index + 1])

    raise ValueError(f"Could not find object end for {variable_name}")


def _asset_description(assets: dict[str, Any], listing_data: dict[str, Any]) -> dict[str, Any]:
    asset = listing_data.get("asset") or {}
    appid = str(asset.get("appid") or "730")
    contextid = str(asset.get("contextid") or "2")
    assetid = str(asset.get("id") or asset.get("assetid") or "")
    return assets.get(appid, {}).get(contextid, {}).get(assetid, {})


def parse_listing_candidates(raw_html: str, market_name_override: str | None = None) -> list[ListingCandidate]:
    listing_info = _extract_js_object(raw_html, "g_rgListingInfo")
    try:
        assets = _extract_js_object(raw_html, "g_rgAssets")
    except ValueError:
        assets = {}

    candidates: list[ListingCandidate] = []
    for listing_id, listing_data in listing_info.items():
        subtotal = int(listing_data.get("converted_price") or listing_data.get("price") or 0)
        fee = int(listing_data.get("converted_fee") or listing_data.get("fee") or 0)
        total = subtotal + fee
        if total <= 0:
            continue

        description = _asset_description(assets, listing_data)
        market_name = (
            market_name_override
            or description.get("market_hash_name")
            or description.get("market_name")
            or description.get("name")
        )
        currency_id = listing_data.get("converted_currencyid") or listing_data.get("currencyid")
        asset = listing_data.get("asset") or {}
        candidates.append(
            ListingCandidate(
                listing_id=str(listing_id),
                market_name=market_name,
                subtotal=subtotal,
                fee=fee,
                total=total,
                currency_id=int(currency_id) if currency_id is not None else None,
                app_id=int(asset.get("appid") or 730),
                context_id=str(asset.get("contextid") or "2"),
                asset_id=str(asset.get("id") or asset.get("assetid")) if asset.get("id") or asset.get("assetid") else None,
            )
        )

    return candidates


def load_saved_client(path: Path) -> SteamClient:
    with path.open("rb") as session_file:
        loaded = pickle.load(session_file)

    if isinstance(loaded, SteamPyClient):
        client = loaded.get_client()
    elif isinstance(loaded, SteamClient):
        client = loaded
    else:
        raise TypeError(f"Unsupported saved session type: {type(loaded).__name__}")

    client.was_login_executed = True
    client.market._set_login_executed(client.steam_guard, client._get_session_id())
    if not getattr(client, "_access_token", None):
        client._access_token = client._set_access_token()
    return client


def _is_probable_steam_id64(value: Any) -> bool:
    value = str(value or "")
    return value.isdigit() and len(value) == 17


def _steam_id_from_login_secure_cookie(client: SteamClient) -> str | None:
    for cookie in client._session.cookies:
        if cookie.name != "steamLoginSecure":
            continue
        steam_id = unquote(cookie.value).split("|", 1)[0]
        if _is_probable_steam_id64(steam_id):
            return steam_id
    return None


def _current_session_steam_id(client: SteamClient) -> str | None:
    try:
        steam_id = str(client.get_steam_id())
        if _is_probable_steam_id64(steam_id):
            return steam_id
    except Exception:
        pass

    steam_id = _steam_id_from_login_secure_cookie(client)
    if steam_id:
        return steam_id

    if isinstance(client.steam_guard, dict):
        steam_id = str(client.steam_guard.get("steamid") or "")
        if _is_probable_steam_id64(steam_id):
            return steam_id
    return None


def apply_mafile(client: SteamClient, mafile_path: Path | None) -> None:
    if mafile_path is None:
        return

    guard_data = guard.load_steam_guard(str(mafile_path))
    session_steam_id = _current_session_steam_id(client)
    mafile_steam_id = str(guard_data.get("steamid") or "")
    client.steam_guard.update(guard_data)

    if session_steam_id:
        if mafile_steam_id and mafile_steam_id != session_steam_id:
            print(
                "maFile steamid differs from authorized session; "
                f"using session steamid {_mask_secret(session_steam_id)} "
                f"instead of {_mask_secret(mafile_steam_id)}"
            )
        client.steam_guard["steamid"] = session_steam_id
    elif not _is_probable_steam_id64(client.steam_guard.get("steamid")):
        raise ValueError(
            "maFile does not contain a valid SteamID64 and current session SteamID could not be detected"
        )

    client.market._set_login_executed(client.steam_guard, client._get_session_id())

    print("Loaded maFile:", mafile_path)
    print("  mobileconf steamid:", _mask_secret(client.steam_guard.get("steamid")))
    print("  device_id present:", bool(client.steam_guard.get("device_id") or client.steam_guard.get("android_id")))
    print("  shared_secret present:", bool(client.steam_guard.get("shared_secret")))
    print("  identity_secret present:", bool(client.steam_guard.get("identity_secret")))


def _require_guard_value(client: SteamClient, key: str) -> str:
    value = client.steam_guard.get(key) if isinstance(client.steam_guard, dict) else None
    if not value:
        raise ValueError(
            f"Missing {key} in saved session/mafile. "
            f"Re-run debug_session.py with a full maFile or pass --mafile."
        )
    return str(value)


def _mask_secret(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value)
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _add_padding(value: str, modulo: int) -> str:
    value = re.sub(r"\s+", "", value)
    return value + ("=" * ((modulo - len(value) % modulo) % modulo))


def _decode_base64_secret(value: str) -> bytes | None:
    try:
        return base64.b64decode(_add_padding(value, 4), validate=True)
    except (binascii.Error, ValueError):
        return None


def _decode_base64url_secret(value: str) -> bytes | None:
    compact = re.sub(r"\s+", "", value)
    if not re.fullmatch(r"[A-Za-z0-9_-]+=*", compact):
        return None
    try:
        return base64.urlsafe_b64decode(_add_padding(compact, 4))
    except (binascii.Error, ValueError):
        return None


def _decode_base32_secret(value: str) -> bytes | None:
    try:
        return base64.b32decode(_add_padding(value.upper(), 8), casefold=True)
    except (binascii.Error, ValueError):
        return None


def _decode_hex_secret(value: str) -> bytes | None:
    compact = re.sub(r"[\s:-]+", "", value)
    if not compact or len(compact) % 2 != 0 or not re.fullmatch(r"[0-9a-fA-F]+", compact):
        return None
    try:
        return bytes.fromhex(compact)
    except ValueError:
        return None


def _uri_query_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return None
    query = parse_qs(parsed.query)
    secret_values = query.get("secret")
    if not secret_values:
        return None
    return secret_values[0]


def decode_secret_candidates(value: Any, *, uri: str | None = None) -> list[dict[str, Any]]:
    raw_values: list[tuple[str, str]] = []
    if value:
        raw_values.append(("field", str(value).strip()))
    uri_secret = _uri_query_secret(uri)
    if uri_secret:
        raw_values.append(("uri_secret", uri_secret.strip()))

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, bytes]] = set()
    decoders = (
        ("base64", _decode_base64_secret),
        ("base64url", _decode_base64url_secret),
        ("base32", _decode_base32_secret),
        ("hex", _decode_hex_secret),
    )
    for source, raw_value in raw_values:
        for encoding, decoder in decoders:
            decoded = decoder(raw_value)
            if not decoded:
                continue
            key = (encoding, decoded)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "source": source,
                    "encoding": encoding,
                    "bytes": decoded,
                    "byte_len": len(decoded),
                    "preferred": len(decoded) == EXPECTED_SECRET_BYTES,
                }
            )
    return candidates


def select_secret_bytes(value: Any, *, uri: str | None = None, field_name: str = "secret") -> bytes:
    candidates = decode_secret_candidates(value, uri=uri)
    if not candidates:
        raise ValueError(f"Could not decode {field_name}")
    for candidate in candidates:
        if candidate["preferred"]:
            return candidate["bytes"]
    return candidates[0]["bytes"]


def generate_steam_code_from_secret_bytes(secret: bytes, timestamp: int | None = None) -> str:
    if timestamp is None:
        timestamp = int(time.time())
    time_buffer = struct.pack(">Q", timestamp // 30)
    time_hmac = hmac.new(secret, time_buffer, digestmod=sha1).digest()
    begin = time_hmac[19] & 0x0F
    full_code = struct.unpack(">I", time_hmac[begin : begin + 4])[0] & 0x7FFFFFFF
    code = ""
    for _ in range(5):
        full_code, index = divmod(full_code, len(STEAM_CODE_CHARS))
        code += STEAM_CODE_CHARS[index]
    return code


def generate_confirmation_key_from_secret_bytes(secret: bytes, tag: str, timestamp: int) -> str:
    buffer = struct.pack(">Q", timestamp) + tag.encode("ascii")
    return base64.b64encode(hmac.new(secret, buffer, digestmod=sha1).digest()).decode("ascii")


def select_candidate(candidates: list[ListingCandidate], listing_id: str | None) -> ListingCandidate:
    if not candidates:
        raise ValueError("No purchasable listings were found in HTML")

    if listing_id:
        for candidate in candidates:
            if candidate.listing_id == listing_id:
                return candidate
        raise ValueError(f"Listing {listing_id} was not found in HTML")

    return candidates[0]


def extract_confirmation_id(payload: dict[str, Any]) -> str | None:
    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict) and confirmation.get("confirmation_id"):
        return str(confirmation["confirmation_id"])
    if payload.get("confirmation_id"):
        return str(payload["confirmation_id"])
    return None


def _confirmation_device_id(client: SteamClient) -> str:
    if isinstance(client.steam_guard, dict):
        device_id = client.steam_guard.get("device_id") or client.steam_guard.get("android_id")
        if device_id:
            return str(device_id)
    steam_id = _require_guard_value(client, "steamid")
    return guard.generate_device_id(steam_id)


def create_confirmation_params(client: SteamClient, tag: str) -> dict[str, Any]:
    identity_secret = select_secret_bytes(
        _require_guard_value(client, "identity_secret"),
        field_name="identity_secret",
    )
    steam_id = _require_guard_value(client, "steamid")
    timestamp = int(time.time())
    confirmation_key = generate_confirmation_key_from_secret_bytes(identity_secret, tag, timestamp)
    return {
        "p": _confirmation_device_id(client),
        "a": steam_id,
        "k": confirmation_key,
        "t": timestamp,
        "m": "react",
        "tag": tag,
    }


def _cookie_debug(client: SteamClient) -> dict[str, list[str]]:
    interesting = {"sessionid", "steamLoginSecure", "steamRefresh_steam"}
    cookies: dict[str, list[str]] = {}
    for cookie in client._session.cookies:
        if cookie.name in interesting:
            cookies.setdefault(cookie.name, []).append(cookie.domain)
    return {name: sorted(set(domains)) for name, domains in cookies.items()}


def _safe_mobileconf_summary(
    response,
    payload: dict[str, Any],
    params: dict[str, Any],
    variant: str,
) -> dict[str, Any]:
    safe_params = dict(params)
    safe_params["k"] = _mask_secret(safe_params.get("k"))
    safe_params["p"] = _mask_secret(safe_params.get("p"))
    safe_params["a"] = _mask_secret(safe_params.get("a"))
    return {
        "variant": variant,
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type"),
        "payload": payload,
        "params": safe_params,
    }


def _is_invalid_authenticator(payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return "invalid authenticator" in text or "incorrect steam guard codes" in text


def fetch_mobile_confirmations(client: SteamClient) -> list[MobileConfirmation]:
    params = create_confirmation_params(client, "list")
    variants = [
        ("mobile_app", {"X-Requested-With": "com.valvesoftware.android.steam.community"}),
        ("ajax", {"X-Requested-With": "XMLHttpRequest"}),
        ("browser", {"User-Agent": "Mozilla/5.0 Steam Client Bootstrapper/01"}),
    ]
    attempts: list[dict[str, Any]] = []

    for variant, headers in variants:
        response = client._session.get(f"{CONFIRMATION_URL}/getlist", params=params, headers=headers)
        payload = _response_json_or_text(response)
        attempts.append(_safe_mobileconf_summary(response, payload, params, variant))

        if response.status_code != HTTPStatus.OK:
            continue

        if not isinstance(payload, dict):
            continue
        if _is_invalid_authenticator(payload):
            raise MobileConfirmationFetchError(
                "Could not fetch mobile confirmations: invalid authenticator",
                attempts=attempts,
                fatal=True,
            )
        if payload.get("success") is False:
            continue

        confirmations = []
        for item in payload.get("conf") or []:
            data_confid = item.get("id")
            nonce = item.get("nonce")
            if data_confid and nonce:
                confirmations.append(
                    MobileConfirmation(
                        data_confid=str(data_confid),
                        nonce=str(nonce),
                        creator_id=str(item["creator_id"]) if item.get("creator_id") is not None else None,
                        type=int(item["type"]) if item.get("type") is not None else None,
                        type_name=item.get("type_name"),
                        headline=item.get("headline"),
                        summary=item.get("summary"),
                    )
                )
        return confirmations

    raise MobileConfirmationFetchError(
        "Could not fetch mobile confirmations",
        attempts=attempts,
        fatal=False,
    )


def send_mobile_confirmation(
    client: SteamClient,
    confirmation: MobileConfirmation,
    action: str = "allow",
) -> dict[str, Any]:
    if action not in {"allow", "cancel"}:
        raise ValueError("action must be allow or cancel")

    params = create_confirmation_params(client, action)
    params.update(
        {
            "op": action,
            "cid": confirmation.data_confid,
            "ck": confirmation.nonce,
        }
    )
    headers = {"X-Requested-With": "XMLHttpRequest"}
    response = client._session.get(f"{CONFIRMATION_URL}/ajaxop", params=params, headers=headers)
    payload = _response_json_or_text(response)
    safe_params = dict(params)
    safe_params["k"] = _mask_secret(safe_params.get("k"))
    safe_params["p"] = _mask_secret(safe_params.get("p"))
    safe_params["a"] = _mask_secret(safe_params.get("a"))
    return {
        "request": {
            "url": f"{CONFIRMATION_URL}/ajaxop",
            "status_code": response.status_code,
            "confirmation_id": confirmation.data_confid,
            "params": safe_params,
        },
        "response": payload,
    }


def is_buy_success(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("wallet_info"), dict) and payload["wallet_info"].get(
        "success"
    ) == 1


def is_confirmation_success(result: dict[str, Any]) -> bool:
    payload = result.get("response")
    return isinstance(payload, dict) and payload.get("success") is True


def purchase_verification_succeeded(result: dict[str, Any]) -> bool:
    check = result.get("final_check") if isinstance(result.get("final_check"), dict) else result
    return bool(check.get("balance_decreased") or check.get("asset_found_in_inventory"))


def inventory_contains_asset(client: SteamClient, candidate: ListingCandidate) -> bool | None:
    if not candidate.asset_id:
        return None

    if candidate.app_id != int(GameOptions.CS.app_id) or candidate.context_id != str(GameOptions.CS.context_id):
        return None

    inventory = client.get_my_inventory(game=GameOptions.CS, merge=False, count=5000)
    for asset in inventory.get("assets") or []:
        if str(asset.get("assetid")) == candidate.asset_id:
            return True
    return False


def verify_purchase_after_confirmation(
    client: SteamClient,
    candidate: ListingCandidate,
    wallet_before: Decimal,
    confirmation_id: str,
    timeout_seconds: Decimal,
    interval_seconds: Decimal,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_seconds)
    checks: list[dict[str, Any]] = []
    expected_balance_at_most = wallet_before - candidate.total_rub

    while True:
        check: dict[str, Any] = {}

        try:
            wallet_after = client.get_wallet_balance(convert_to_decimal=True)
            check["wallet_balance"] = str(wallet_after)
            check["expected_balance_at_most"] = str(expected_balance_at_most)
            check["balance_decreased"] = wallet_after <= expected_balance_at_most
        except Exception as exc:
            check["wallet_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

        try:
            asset_found = inventory_contains_asset(client, candidate)
            check["asset_id"] = candidate.asset_id
            check["asset_found_in_inventory"] = asset_found
        except Exception as exc:
            check["inventory_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

        try:
            confirmations = fetch_mobile_confirmations(client)
            matching = [
                confirmation.safe_summary()
                for confirmation in confirmations
                if confirmation.matches(confirmation_id)
            ]
            check["matching_confirmations"] = matching
            check["confirmation_removed"] = len(matching) == 0
        except Exception as exc:
            check["confirmations_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

        checks.append(check)
        if purchase_verification_succeeded(check):
            return {
                "attempts": len(checks),
                "success": True,
                "final_check": check,
                "checks": checks,
            }

        if time.monotonic() >= deadline:
            return {
                "attempts": len(checks),
                "timed_out": True,
                "final_check": check,
                "checks": checks,
            }
        time.sleep(float(interval_seconds))


def retry_buy_after_confirmation(
    client: SteamClient,
    candidate: ListingCandidate,
    wallet_currency: int,
    retries: int,
    delay_seconds: Decimal,
) -> dict[str, Any] | None:
    if retries <= 0:
        return None

    last_result: dict[str, Any] | None = None
    for attempt in range(1, retries + 1):
        time.sleep(float(delay_seconds))
        result = buy_listing_raw(client, candidate, wallet_currency=wallet_currency, app_id=int(GameOptions.CS.app_id))
        result["attempt"] = attempt
        last_result = result
        if is_buy_success(result.get("response")):
            return result
        if isinstance(result.get("response"), dict) and result["response"].get("need_confirmation"):
            return result
    return last_result


def complete_buy_after_mobile_confirmation(
    client: SteamClient,
    candidate: ListingCandidate,
    wallet_currency: int,
    confirmation_id: str,
    timeout_seconds: Decimal,
    interval_seconds: Decimal,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_seconds)
    results: list[dict[str, Any]] = []

    while True:
        result = buy_listing_raw(
            client,
            candidate,
            wallet_currency=wallet_currency,
            app_id=int(GameOptions.CS.app_id),
            confirmation_id=confirmation_id,
        )
        results.append(result)

        payload = result.get("response")
        if is_buy_success(payload):
            return {
                "success": True,
                "attempts": len(results),
                "final_result": result,
                "results": results,
            }

        if isinstance(payload, dict) and payload.get("need_confirmation"):
            next_confirmation_id = extract_confirmation_id(payload)
            if next_confirmation_id and next_confirmation_id != confirmation_id:
                return {
                    "success": False,
                    "new_confirmation_required": next_confirmation_id,
                    "attempts": len(results),
                    "final_result": result,
                    "results": results,
                }

        if time.monotonic() >= deadline:
            return {
                "success": False,
                "timed_out": True,
                "attempts": len(results),
                "final_result": result,
                "results": results,
            }

        time.sleep(float(interval_seconds))


def confirm_mobile_confirmation_by_id(
    client: SteamClient,
    confirmation_id: str,
    timeout_seconds: Decimal,
    interval_seconds: Decimal,
    action: str = "allow",
) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout_seconds)
    attempts = 0
    seen_confirmations: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, Any]] = []

    while True:
        attempts += 1
        try:
            confirmations = fetch_mobile_confirmations(client)
        except MobileConfirmationFetchError as exc:
            fetch_errors.append(
                {
                    "attempt": attempts,
                    "fatal": exc.fatal,
                    "message": str(exc),
                    "mobileconf_attempts": exc.attempts,
                    "cookies": _cookie_debug(client),
                }
            )
            if exc.fatal:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not fetch mobile confirmations before timeout. "
                    f"Last error: {fetch_errors[-1]}"
                ) from exc
            time.sleep(float(interval_seconds))
            continue

        seen_confirmations = [confirmation.safe_summary() for confirmation in confirmations]
        for confirmation in confirmations:
            if confirmation.matches(confirmation_id):
                result = send_mobile_confirmation(client, confirmation, action=action)
                result["attempts"] = attempts
                result["matched_confirmation"] = confirmation.safe_summary()
                result["fetch_errors"] = fetch_errors
                return result

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Confirmation {confirmation_id} was not found in mobileconf list. "
                f"Seen confirmations: {seen_confirmations}. Fetch errors: {fetch_errors[-3:]}"
            )
        time.sleep(float(interval_seconds))


def print_mobile_confirmation_fetch_error(exc: MobileConfirmationFetchError) -> None:
    print("Mobile confirmation fetch failed:")
    print(
        json.dumps(
            {
                "message": str(exc),
                "fatal": exc.fatal,
                "attempts": exc.attempts,
                "hint": (
                    "Invalid authenticator usually means the maFile identity_secret is not from "
                    "the current Steam Guard authenticator, or the machine clock is wrong."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _response_json_or_text(response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        return {
            "non_json": True,
            "status_code": response.status_code,
            "text_preview": response.text[:1000],
        }


def build_buy_listing_data(
    session_id: str,
    candidate: ListingCandidate,
    wallet_currency: int,
    confirmation_id: str | int | None = None,
) -> dict[str, str]:
    """Build the same form payload Steam sends from the browser market page."""
    return {
        "sessionid": session_id,
        "currency": str(wallet_currency),
        "subtotal": str(candidate.subtotal),
        "fee": str(candidate.fee),
        "total": str(candidate.total),
        "quantity": "1",
        "billing_state": "",
        "save_my_address": "0",
        "tradefee_tax": "0",
        "confirmation": str(confirmation_id or 0),
    }


def build_buy_listing_headers(candidate: ListingCandidate, app_id: int = 730) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://steamcommunity.com",
        "Referer": f"https://steamcommunity.com/market/listings/{app_id}/{quote(candidate.market_name or '')}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    }


def buy_listing_raw(
    client: SteamClient,
    candidate: ListingCandidate,
    wallet_currency: int,
    app_id: int = 730,
    confirmation_id: str | int | None = None,
) -> dict[str, Any]:
    session_id = client._get_session_id()
    if not session_id:
        raise ValueError("Missing sessionid cookie")

    data = build_buy_listing_data(session_id, candidate, wallet_currency, confirmation_id=confirmation_id)
    headers = build_buy_listing_headers(candidate, app_id)
    response = client._session.post(
        f"https://steamcommunity.com/market/buylisting/{candidate.listing_id}",
        data=data,
        headers=headers,
    )
    payload = _response_json_or_text(response)
    safe_data = dict(data)
    safe_data["sessionid"] = _mask_secret(safe_data.get("sessionid"))
    return {
        "request": {
            "url": response.url,
            "status_code": response.status_code,
            "data": safe_data,
            "referer": headers["Referer"],
        },
        "response": payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam market buy dry-run helper")
    parser.add_argument("--html", type=Path, help="Saved Steam market listing HTML")
    parser.add_argument("--session", default=Path("cookies.pkl"), type=Path, help="Saved session pickle")
    parser.add_argument("--mafile", type=Path, help="Optional maFile to load identity_secret for confirmations")
    parser.add_argument("--listing-id", help="Specific listing id to test")
    parser.add_argument("--market-name", help="Override market hash name if it is not present in HTML")
    parser.add_argument("--show", default=5, type=int, help="How many parsed listings to print")
    parser.add_argument("--execute", action="store_true", help="Actually buy the selected listing")
    parser.add_argument("--yes", action="store_true", help="Required together with --execute")
    parser.add_argument("--max-total-rub", type=Decimal, help="Required maximum total price for --execute")
    parser.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Approve Steam Guard mobile confirmation using identity_secret from session/maFile",
    )
    parser.add_argument("--confirm-only", help="Only approve this mobile confirmation id, without buying")
    parser.add_argument(
        "--confirm-action",
        choices=("allow", "cancel"),
        default="allow",
        help="Action for --confirm-only; buy flow always uses allow",
    )
    parser.add_argument("--confirmation-timeout", type=Decimal, default=Decimal("60"), help="Seconds to wait")
    parser.add_argument("--confirmation-interval", type=Decimal, default=Decimal("2"), help="Polling interval")
    parser.add_argument(
        "--post-confirm-retries",
        type=int,
        default=0,
        help="Dangerous: retry the same buylisting request after mobile confirmation",
    )
    parser.add_argument(
        "--post-confirm-delay",
        type=Decimal,
        default=Decimal("2"),
        help="Seconds to wait before post-confirm buy retry",
    )
    parser.add_argument("--verify-timeout", type=Decimal, default=Decimal("20"), help="Seconds to verify purchase")
    parser.add_argument("--verify-interval", type=Decimal, default=Decimal("2"), help="Verification polling interval")
    args = parser.parse_args()

    client = load_saved_client(args.session)
    apply_mafile(client, args.mafile)
    if args.auto_confirm and args.mafile is None:
        print("Auto-confirm maFile: not passed, using Steam Guard data saved inside session pickle")

    if args.confirm_only:
        if not args.auto_confirm:
            raise SystemExit("--confirm-only requires --auto-confirm")
        print("Session alive:", client.is_session_alive())
        print("Confirming mobile confirmation:", args.confirm_only)
        try:
            confirmation_result = confirm_mobile_confirmation_by_id(
                client,
                confirmation_id=args.confirm_only,
                timeout_seconds=args.confirmation_timeout,
                interval_seconds=args.confirmation_interval,
                action=args.confirm_action,
            )
        except MobileConfirmationFetchError as exc:
            print_mobile_confirmation_fetch_error(exc)
            raise SystemExit(2) from exc
        print("Confirmation request/response:")
        print(json.dumps(confirmation_result, ensure_ascii=False, indent=2))
        return

    if args.html is None:
        raise SystemExit("--html is required unless --confirm-only is used")

    raw_html = args.html.read_text(encoding="utf-8", errors="ignore")
    wallet_info = parse_wallet_info(raw_html)
    candidates = parse_listing_candidates(raw_html, market_name_override=args.market_name)
    selected = select_candidate(candidates, args.listing_id)
    wallet_currency = int(wallet_info.get("wallet_currency") or Currency.RUB.value)

    wallet_before = client.get_wallet_balance(convert_to_decimal=True)
    print("Session alive:", client.is_session_alive())
    print("Wallet balance:", wallet_before)
    print("HTML wallet_currency:", wallet_currency)
    print(f"Parsed listings: {len(candidates)}")
    print("\nFirst listings:")
    for candidate in candidates[: args.show]:
        print(
            f"  {candidate.listing_id} | {candidate.total_rub} RUB "
            f"(subtotal={candidate.subtotal}, fee={candidate.fee}) | {candidate.market_name}"
        )

    print("\nSelected listing:")
    print("  listing_id:", selected.listing_id)
    print("  market_name:", selected.market_name)
    print("  subtotal:", selected.subtotal)
    print("  fee:", selected.fee)
    print("  total:", selected.total)
    print("  total_rub:", selected.total_rub)
    print("  currency_id:", selected.currency_id)
    print("  asset_id:", selected.asset_id)

    if not selected.market_name:
        raise ValueError("Market name is missing. Re-run with --market-name")

    if not args.execute:
        print("\nDry run only. Nothing was bought.")
        return

    if not args.yes:
        raise SystemExit("--execute requires --yes")
    if args.max_total_rub is None:
        raise SystemExit("--execute requires --max-total-rub")
    if selected.total_rub > args.max_total_rub:
        raise SystemExit(
            f"Selected price {selected.total_rub} RUB exceeds max {args.max_total_rub} RUB"
        )

    print("\nBuying selected listing...")
    result = buy_listing_raw(client, selected, wallet_currency=wallet_currency, app_id=int(GameOptions.CS.app_id))
    print("Buy request/response:")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    response_payload = result["response"]
    if isinstance(response_payload, dict) and response_payload.get("need_confirmation"):
        confirmation_id = extract_confirmation_id(response_payload)
        print("\nMobile confirmation required:", confirmation_id)
        if not confirmation_id:
            raise SystemExit("Steam requires mobile confirmation but did not return confirmation_id")
        if not args.auto_confirm:
            raise SystemExit("Re-run with --auto-confirm to approve it via maFile/identity_secret")

        try:
            confirmation_result = confirm_mobile_confirmation_by_id(
                client,
                confirmation_id=confirmation_id,
                timeout_seconds=args.confirmation_timeout,
                interval_seconds=args.confirmation_interval,
                action="allow",
            )
        except MobileConfirmationFetchError as exc:
            print_mobile_confirmation_fetch_error(exc)
            raise SystemExit(2) from exc
        print("Confirmation request/response:")
        print(json.dumps(confirmation_result, ensure_ascii=False, indent=2))
        if not is_confirmation_success(confirmation_result):
            raise SystemExit("Mobile confirmation did not return success=true")

        print("\nCompleting buylisting after mobile confirmation...")
        completion_result = complete_buy_after_mobile_confirmation(
            client,
            selected,
            wallet_currency=wallet_currency,
            confirmation_id=confirmation_id,
            timeout_seconds=args.verify_timeout,
            interval_seconds=args.verify_interval,
        )
        print("Post-confirm completion request/response:")
        print(json.dumps(completion_result, ensure_ascii=False, indent=2))
        if completion_result.get("success"):
            print("Wallet balance after buy:", client.get_wallet_balance(convert_to_decimal=True))
            return

        print("\nVerifying purchase after confirmation...")
        verification_result = verify_purchase_after_confirmation(
            client,
            selected,
            wallet_before=wallet_before,
            confirmation_id=confirmation_id,
            timeout_seconds=args.verify_timeout,
            interval_seconds=args.verify_interval,
        )
        print("Purchase verification:")
        print(json.dumps(verification_result, ensure_ascii=False, indent=2))

        if purchase_verification_succeeded(verification_result):
            print("Wallet balance after confirmation:", client.get_wallet_balance(convert_to_decimal=True))
            return

        if args.post_confirm_retries <= 0:
            raise SystemExit(
                "Purchase was confirmed but verification did not observe wallet/inventory changes. "
                "Not retrying buylisting by default because a retry creates a new mobile confirmation."
            )

        print("\nRetrying buylisting after confirmation because --post-confirm-retries was set...")
        post_confirm_result = retry_buy_after_confirmation(
            client,
            selected,
            wallet_currency=wallet_currency,
            retries=args.post_confirm_retries,
            delay_seconds=args.post_confirm_delay,
        )
        print("Post-confirm buy request/response:")
        print(json.dumps(post_confirm_result, ensure_ascii=False, indent=2))
        if not post_confirm_result or not is_buy_success(post_confirm_result.get("response")):
            raise SystemExit("Post-confirm buy retry did not return wallet_info.success=1")

        print("Wallet balance after buy:", client.get_wallet_balance(convert_to_decimal=True))
        return

    wallet_info_response = response_payload.get("wallet_info") if isinstance(response_payload, dict) else None
    if not isinstance(wallet_info_response, dict) or wallet_info_response.get("success") != 1:
        raise SystemExit("Buy request did not return wallet_info.success=1")

    print("Wallet balance after buy:", client.get_wallet_balance(convert_to_decimal=True))


if __name__ == "__main__":
    main()
