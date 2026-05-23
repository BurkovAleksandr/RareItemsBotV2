"""Compatibility patches for SteamPy auth.

The PyPI 1.2.0 release still uses Steam's old web login flow. Steam now uses
IAuthenticationService with JWT web-session finalization, so we patch the small
surface SteamPy needs instead of relying on a hand-edited virtualenv.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
import urllib.parse as urlparse
from decimal import Decimal
from http import HTTPStatus
from typing import Any

import requests
from rsa import PublicKey, encrypt

from steampy import guard
from steampy.client import SteamClient
from steampy.exceptions import ApiException, CaptchaRequired
from steampy.login import LoginExecutor
from steampy.models import Asset, GameOptions, SteamUrl


_PATCHED = False
MOBILE_APP_CODE_TYPE = 3
MOBILE_APP_CODE_TYPE_NAME = "DeviceCode"
STEAM_WEB_OAUTH_CLIENT_ID = "DE45CD61"
STEAM_COOKIE_DOMAINS = (
    "steamcommunity.com",
    ".steamcommunity.com",
    "store.steampowered.com",
    ".store.steampowered.com",
    "login.steampowered.com",
    ".steampowered.com",
    "checkout.steampowered.com",
)


def _community_url() -> str:
    return getattr(SteamUrl, "COMMUNITY_URL", "https://steamcommunity.com").rstrip("/")


def _store_url() -> str:
    return getattr(SteamUrl, "STORE_URL", "https://store.steampowered.com").rstrip("/")


def _login_url() -> str:
    return getattr(SteamUrl, "LOGIN_URL", "https://login.steampowered.com").rstrip("/")


def _api_url() -> str:
    return getattr(SteamUrl, "API_URL", "https://api.steampowered.com").rstrip("/")


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise ApiException(f"Steam returned a non-JSON auth response: {response.text[:300]}") from exc


def _response_info(response: requests.Response) -> dict[str, Any]:
    payload = _response_json(response)
    if payload.get("captcha_needed"):
        raise CaptchaRequired("Captcha required")
    if payload.get("success") is False:
        raise ApiException(f"Steam auth failed: {payload.get('message', payload)}")
    info = payload.get("response")
    if not isinstance(info, dict) or not info:
        raise ApiException(f"Unexpected Steam auth response: {payload}")
    return info


def _int_from_steam_key(value: str) -> int:
    value = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]+", value):
        return int(value, 16)

    padding = "=" * (-len(value) % 4)
    try:
        return int.from_bytes(base64.b64decode(value + padding), "big")
    except Exception:
        return int.from_bytes(base64.urlsafe_b64decode(value + padding), "big")


def _set_cookie_for_domains(
    session: requests.Session,
    name: str,
    value: str,
    domains: tuple[str, ...] = STEAM_COOKIE_DOMAINS,
) -> None:
    for domain in domains:
        session.cookies.set(name, value, domain=domain, path="/")


def _cookie_value(
    session: requests.Session,
    name: str,
    domains: tuple[str, ...] = STEAM_COOKIE_DOMAINS,
) -> str | None:
    for domain in domains:
        value = session.cookies.get_dict(domain=domain, path="/").get(name)
        if value:
            return value

    for cookie in session.cookies:
        if cookie.name == name:
            return cookie.value
    return None


def _cookie_presence(session: requests.Session) -> dict[str, list[str]]:
    interesting_names = {"sessionid", "steamLogin", "steamLoginSecure", "steamRefresh_steam", "steamCountry"}
    presence: dict[str, list[str]] = {}
    for cookie in session.cookies:
        if cookie.name in interesting_names:
            presence.setdefault(cookie.name, []).append(cookie.domain)
    return {name: sorted(set(domains)) for name, domains in presence.items()}


def _allowed_confirmations_summary(response_info: dict[str, Any]) -> list[dict[str, Any]]:
    confirmations = response_info.get("allowed_confirmations") or []
    if not isinstance(confirmations, list):
        return []

    summary = []
    for confirmation in confirmations:
        if not isinstance(confirmation, dict):
            summary.append({"raw": str(confirmation)})
            continue
        confirmation_type = (
            confirmation.get("confirmation_type")
            or confirmation.get("confirmationType")
            or confirmation.get("type")
        )
        summary.append(
            {
                "type": confirmation_type,
                "message_present": bool(
                    confirmation.get("associated_message")
                    or confirmation.get("associatedMessage")
                    or confirmation.get("message")
                ),
            }
        )
    return summary


def _clear_cookie_if_present(session: requests.Session, domain: str, name: str) -> None:
    try:
        session.cookies.clear(domain=domain, path="/", name=name)
    except KeyError:
        pass


def _dedupe_request_cookies(session: requests.Session) -> None:
    cookie_names = ("sessionid", "steamLogin", "steamLoginSecure", "steamRefresh_steam", "steamCountry")
    domain_groups = (
        ("steamcommunity.com", (".steamcommunity.com",)),
        ("store.steampowered.com", (".store.steampowered.com", ".steampowered.com")),
    )

    for preferred_domain, duplicate_domains in domain_groups:
        for name in cookie_names:
            preferred_value = session.cookies.get_dict(domain=preferred_domain, path="/").get(name)
            if not preferred_value:
                continue
            for duplicate_domain in duplicate_domains:
                _clear_cookie_if_present(session, duplicate_domain, name)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _steam_login_secure_parts(session: requests.Session) -> tuple[str | None, str | None]:
    cookie_value = _cookie_value(
        session,
        "steamLoginSecure",
        ("steamcommunity.com", ".steamcommunity.com", ".steampowered.com"),
    )
    if not cookie_value:
        return None, None

    decoded_cookie_value = urlparse.unquote(cookie_value)
    parts = decoded_cookie_value.split("||", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, decoded_cookie_value


def _patched_api_call(
    self: LoginExecutor,
    method: str,
    service: str,
    endpoint: str,
    version: str = "v1",
    params: dict[str, Any] | None = None,
) -> requests.Response:
    url = f"{_api_url()}/{service}/{endpoint}/{version}"
    headers = {
        "Referer": f"{_community_url()}/",
        "Origin": _community_url(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if method.upper() == "GET":
        return self.session.get(url, params=params, headers=headers)
    if method.upper() == "POST":
        return self.session.post(url, data=params, headers=headers)
    raise ValueError("Method must be either GET or POST")


def _patched_login(self: LoginExecutor) -> requests.Session:
    self.session._steam_auth_debug = {
        "tokens": {"access": False, "refresh": False},
        "finalize": {},
        "transfers": [],
        "cookies": {},
    }
    response_info = _response_info(self._send_login_request())
    self.session._steam_auth_debug["begin"] = {
        "keys": sorted(response_info.keys()),
        "has_client_id": bool(response_info.get("client_id")),
        "has_request_id": bool(response_info.get("request_id")),
        "has_steamid": bool(response_info.get("steamid")),
        "allowed_confirmations": _allowed_confirmations_summary(response_info),
    }

    if "access_token" in response_info and "refresh_token" in response_info:
        self.access_token = response_info["access_token"]
        self.refresh_token = response_info["refresh_token"]
    elif response_info.get("allowed_confirmations") or response_info.get("steamid"):
        self._update_steam_guard(response_info)
    elif response_info.get("client_id") and response_info.get("request_id"):
        self._pool_sessions_steam(response_info["client_id"], response_info["request_id"])
    else:
        raise ApiException(f"Unexpected Steam auth response: {response_info}")

    final_response = self._finalize_login()
    self.session._steam_auth_debug["tokens"] = {
        "access": bool(self.access_token),
        "refresh": bool(self.refresh_token),
    }
    self.session._steam_auth_debug["finalize"]["status_code"] = getattr(final_response, "status_code", None)
    self.session._steam_auth_debug["finalize"]["content_type"] = getattr(final_response, "headers", {}).get("Content-Type")
    final_data = {}
    try:
        final_data = final_response.json()
        self.session._steam_auth_debug["finalize"]["keys"] = sorted(final_data.keys())
        self.session._steam_auth_debug["finalize"]["transfer_count"] = len(final_data.get("transfer_info") or [])
    except ValueError:
        self.session._steam_auth_debug["finalize"]["keys"] = []
        self.session._steam_auth_debug["finalize"]["body_preview"] = final_response.text[:120]
        pass
    self._perform_redirects(final_data)
    self.set_sessionid_cookies()
    _dedupe_request_cookies(self.session)
    self.session.get(f"{_community_url()}/")
    self.session._steam_auth_debug["cookies"] = _cookie_presence(self.session)
    return self.session


def _patched_send_login_request(self: LoginExecutor) -> requests.Response:
    rsa_params = self._fetch_rsa_params()
    encrypted_password = self._encrypt_password(rsa_params)
    request_data = self._prepare_login_request_data(encrypted_password, rsa_params["rsa_timestamp"])
    return self._api_call("POST", "IAuthenticationService", "BeginAuthSessionViaCredentials", params=request_data)


def _patched_fetch_rsa_params(self: LoginExecutor, current_number_of_repetitions: int = 0) -> dict[str, Any]:
    self.session.get(f"{_community_url()}/")
    response = self._api_call(
        "GET",
        "IAuthenticationService",
        "GetPasswordRSAPublicKey",
        params={"account_name": self.username},
    )

    if response.status_code == HTTPStatus.OK:
        response_info = _response_json(response).get("response", {})
        timestamp = response_info.get("timestamp") or response_info.get("publickey_timestamp")

        if {"publickey_mod", "publickey_exp"} <= set(response_info) and timestamp:
            rsa_mod = int(response_info["publickey_mod"], 16)
            rsa_exp = int(response_info["publickey_exp"], 16)
            return {"rsa_key": PublicKey(rsa_mod, rsa_exp), "rsa_timestamp": timestamp}

        if {"publickey_modulo", "publickey_exponent"} <= set(response_info) and timestamp:
            rsa_mod = _int_from_steam_key(response_info["publickey_modulo"])
            rsa_exp = _int_from_steam_key(response_info["publickey_exponent"])
            return {"rsa_key": PublicKey(rsa_mod, rsa_exp), "rsa_timestamp": timestamp}

    if current_number_of_repetitions < 5:
        time.sleep(1)
        return self._fetch_rsa_params(current_number_of_repetitions + 1)

    raise ApiException(f"Could not obtain Steam RSA key. Status code: {response.status_code}")


def _patched_encrypt_password(self: LoginExecutor, rsa_params: dict[str, Any]) -> str:
    return base64.b64encode(encrypt(self.password.encode("utf-8"), rsa_params["rsa_key"])).decode("ascii")


def _patched_prepare_login_request_data(
    self: LoginExecutor,
    encrypted_password: str | bytes,
    rsa_timestamp: str,
) -> dict[str, Any]:
    if isinstance(encrypted_password, bytes):
        encrypted_password = encrypted_password.decode("ascii")

    return {
        "account_name": self.username,
        "encrypted_password": encrypted_password,
        "encryption_timestamp": rsa_timestamp,
        "persistence": "1",
        "remember_login": "true",
        "website_id": "Community",
        "device_friendly_name": "SteamPy Client",
        "platform_type": "2",
        "oauth_client_id": STEAM_WEB_OAUTH_CLIENT_ID,
    }


def _patched_update_steam_guard(self: LoginExecutor, response_info: dict[str, Any]) -> None:
    client_id = response_info["client_id"]
    request_id = response_info["request_id"]
    steam_id = response_info.get("steamid")
    one_time_code = guard.generate_one_time_code(self.shared_secret)
    debug = getattr(self.session, "_steam_auth_debug", None)

    data = {
        "client_id": client_id,
        "code": one_time_code,
        "code_type": MOBILE_APP_CODE_TYPE,
    }
    if steam_id:
        data["steamid"] = steam_id

    if debug is not None:
        debug["steam_guard_code"] = {
            "submitted": True,
            "code_type": MOBILE_APP_CODE_TYPE,
            "code_type_name": MOBILE_APP_CODE_TYPE_NAME,
            "steamid_present": bool(steam_id),
        }

    response = self._api_call("POST", "IAuthenticationService", "UpdateAuthSessionWithSteamGuardCode", params=data)
    if debug is not None:
        debug["steam_guard_code"]["status_code"] = response.status_code
    if response.status_code != HTTPStatus.OK:
        raise ApiException(f"Steam Guard update failed with status {response.status_code}: {response.text[:300]}")

    payload = _response_json(response)
    if debug is not None:
        response_info_debug = payload.get("response")
        debug["steam_guard_code"]["response_keys"] = (
            sorted(response_info_debug.keys())
            if isinstance(response_info_debug, dict)
            else sorted(payload.keys())
        )
    if payload.get("success") is False:
        raise ApiException(f"Steam Guard rejected the code: {payload.get('message', payload)}")

    self._pool_sessions_steam(client_id, request_id)


def _patched_pool_sessions_steam(self: LoginExecutor, client_id: str, request_id: str) -> None:
    debug = getattr(self.session, "_steam_auth_debug", None)
    if debug is not None:
        debug.setdefault("poll", [])

    for attempt in range(1, 21):
        response = self._api_call(
            "POST",
            "IAuthenticationService",
            "PollAuthSessionStatus",
            params={"client_id": client_id, "request_id": request_id},
        )
        if response.status_code != HTTPStatus.OK:
            raise ApiException(f"Steam auth poll failed with status {response.status_code}: {response.text[:300]}")

        payload = _response_json(response)
        response_info = payload.get("response", {})
        if debug is not None:
            debug["poll"].append(
                {
                    "attempt": attempt,
                    "status_code": response.status_code,
                    "response_keys": sorted(response_info.keys())
                    if isinstance(response_info, dict)
                    else [],
                    "has_access_token": bool(response_info.get("access_token"))
                    if isinstance(response_info, dict)
                    else False,
                    "has_refresh_token": bool(response_info.get("refresh_token"))
                    if isinstance(response_info, dict)
                    else False,
                    "had_remote_interaction": response_info.get("had_remote_interaction")
                    if isinstance(response_info, dict)
                    else None,
                }
            )
        if response_info.get("access_token") and response_info.get("refresh_token"):
            self.access_token = response_info["access_token"]
            self.refresh_token = response_info["refresh_token"]
            return

        status = response_info.get("status")
        if status in {"failed", "cancelled", "expired"}:
            raise ApiException(f"Steam authentication {status}: {response_info.get('message', payload)}")

        time.sleep(int(response_info.get("interval") or 2))

    raise ApiException("Steam authentication polling exceeded maximum attempts")


def _patched_finalize_login(self: LoginExecutor) -> requests.Response:
    session_id = _cookie_value(self.session, "sessionid")
    if not session_id:
        session_id = secrets.token_hex(12)
    _set_cookie_for_domains(self.session, "sessionid", session_id, ("login.steampowered.com",))

    return self.session.post(
        f"{_login_url()}/jwt/finalizelogin",
        headers={
            "Origin": _login_url(),
            "Referer": f"{_login_url()}/",
        },
        data={
            "nonce": self.refresh_token,
            "sessionid": session_id,
            "redir": f"{_community_url()}/login/home/?goto=",
        },
    )


def _patched_perform_redirects(self: LoginExecutor, response_dict: dict[str, Any]) -> None:
    transfer_info = response_dict.get("transfer_info") or []
    steam_id = response_dict.get("steamID", "")
    for transfer in transfer_info:
        params = transfer.get("params") or {}
        if steam_id:
            params.setdefault("steamID", steam_id)
        response = self.session.post(
            transfer["url"],
            headers={
                "Origin": _login_url(),
                "Referer": f"{_login_url()}/",
            },
            data=params,
        )
        debug = getattr(self.session, "_steam_auth_debug", None)
        if debug is not None:
            debug["transfers"].append(
                {
                    "domain": urlparse.urlparse(transfer["url"]).netloc,
                    "status_code": response.status_code,
                    "set_cookie": "set-cookie" in {key.lower() for key in response.headers},
                }
            )


def _patched_set_sessionid_cookies(self: LoginExecutor) -> None:
    """Keep SteamPy's public hook, but do not duplicate auth cookies.

    Steam's jwt/finalizelogin transfer endpoints set domain-specific cookies.
    Duplicating them manually creates repeated Cookie header names, which makes
    the community market treat the request as logged out.
    """
    return None


def _patched_client_get_session_id(self: SteamClient) -> str | None:
    return _cookie_value(self._session, "sessionid", ("steamcommunity.com", ".steamcommunity.com"))


def _patched_client_set_access_token(self: SteamClient, login_executor: LoginExecutor | None = None) -> str | None:
    if login_executor and getattr(login_executor, "access_token", None):
        return login_executor.access_token

    for cookie_name in ("steamLoginSecure", "steamLogin"):
        cookie_value = _cookie_value(self._session, cookie_name)
        if not cookie_value:
            continue
        decoded_cookie_value = urlparse.unquote(cookie_value)
        access_token_parts = decoded_cookie_value.split("||")
        if len(access_token_parts) >= 2:
            return access_token_parts[1]
    return None


def _patched_client_is_session_alive(self: SteamClient) -> bool:
    if not getattr(self, "was_login_executed", False):
        return False
    if not _cookie_value(self._session, "sessionid", ("steamcommunity.com", ".steamcommunity.com")):
        return False

    _, token = _steam_login_secure_parts(self._session)
    if not token:
        return False
    payload = _decode_jwt_payload(token)
    if payload.get("exp") and int(payload["exp"]) <= int(time.time()):
        return False

    try:
        response = self._session.get(f"{_community_url()}/", timeout=15)
    except requests.RequestException:
        return True

    if response.status_code != HTTPStatus.OK:
        return False

    text = response.text
    logged_in_markers = (
        'g_steamID = "',
        '"logged_in":true',
        "&quot;logged_in&quot;:true",
        "/login/logout/",
        "Logout",
    )
    if any(marker in text for marker in logged_in_markers):
        return True

    return True


def _patched_client_get_steam_id(self: SteamClient) -> int:
    response = self._session.get(f"{_community_url()}/")
    patterns = (
        r'g_steamID = "(\d+)";',
        r'"steamid"\s*:\s*"(\d+)"',
        r"&quot;steamid&quot;:&quot;(\d+)&quot;",
    )
    for pattern in patterns:
        match = re.search(pattern, response.text)
        if match:
            return int(match.group(1))
    steam_id, token = _steam_login_secure_parts(self._session)
    if steam_id:
        return int(steam_id)
    if token:
        payload = _decode_jwt_payload(token)
        if payload.get("sub"):
            return int(payload["sub"])
    raise ValueError("Could not find Steam ID in the community session page")


def _patched_get_my_inventory(
    self: SteamClient,
    game: GameOptions,
    merge: bool = True,
    count: int = 5000,
) -> dict:
    steam_id = self.steam_guard["steamid"]
    return self.get_partner_inventory(steam_id, game, merge, count)


def _patched_get_partner_inventory(
    self: SteamClient,
    partner_steam_id: str,
    game: GameOptions,
    merge: bool = True,
    count: int = 5000,
) -> dict:
    from steampy.exceptions import TooManyRequests
    from steampy.utils import merge_items_with_descriptions_from_inventory

    url = f"{_community_url()}/inventory/{partner_steam_id}/{game.app_id}/{game.context_id}"
    response = self._session.get(url, params={"l": "english", "count": count})
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        raise TooManyRequests("Too many requests, try again later.")

    response_dict = response.json()
    if response_dict is None or response_dict.get("success") != 1:
        raise ApiException("Success value should be 1.")

    return merge_items_with_descriptions_from_inventory(response_dict, game) if merge else response_dict


def _patched_get_wallet_balance(
    self: SteamClient,
    convert_to_decimal: bool = True,
    on_hold: bool = False,
) -> str | Decimal:
    response = self._session.get(f"{_community_url()}/market")
    wallet_info_match = re.search(r"var g_rgWalletInfo = (.*?);", response.text)
    if not wallet_info_match:
        raise ApiException("Unable to find wallet info in Steam market page")

    balance_dict = json.loads(wallet_info_match.group(1))
    balance_dict_key = "wallet_delayed_balance" if on_hold else "wallet_balance"
    if convert_to_decimal:
        return Decimal(balance_dict[balance_dict_key]) / 100
    return balance_dict[balance_dict_key]


def apply_steampy_compat() -> None:
    global _PATCHED
    if _PATCHED:
        return

    LoginExecutor._api_call = _patched_api_call
    LoginExecutor.login = _patched_login
    LoginExecutor._send_login_request = _patched_send_login_request
    LoginExecutor._fetch_rsa_params = _patched_fetch_rsa_params
    LoginExecutor._encrypt_password = _patched_encrypt_password
    LoginExecutor._prepare_login_request_data = _patched_prepare_login_request_data
    LoginExecutor._update_steam_guard = _patched_update_steam_guard
    LoginExecutor._pool_sessions_steam = _patched_pool_sessions_steam
    LoginExecutor._finalize_login = _patched_finalize_login
    LoginExecutor._perform_redirects = _patched_perform_redirects
    LoginExecutor.set_sessionid_cookies = _patched_set_sessionid_cookies

    SteamClient._get_session_id = _patched_client_get_session_id
    SteamClient._set_access_token = _patched_client_set_access_token
    SteamClient.is_session_alive = _patched_client_is_session_alive
    SteamClient.get_steam_id = _patched_client_get_steam_id
    SteamClient.get_my_inventory = _patched_get_my_inventory
    SteamClient.get_partner_inventory = _patched_get_partner_inventory
    SteamClient.get_wallet_balance = _patched_get_wallet_balance

    if not hasattr(Asset, "from_dict"):
        Asset.from_dict = classmethod(_asset_from_dict)

    _PATCHED = True


def _asset_from_dict(cls: type[Asset], data: dict[str, Any]) -> Asset:
    app_id = str(data.get("app_id") or data.get("appid"))
    context_id = str(data.get("context_id") or data.get("contextid"))
    asset_id = str(data.get("assetid") or data.get("asset_id"))
    amount = int(data.get("amount", 1))
    return cls(asset_id, GameOptions(app_id, context_id), amount)
