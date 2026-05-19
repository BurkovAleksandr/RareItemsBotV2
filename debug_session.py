import json
import os
import pickle
import re
from pprint import pprint

from assets.session import SteamPyClient
from assets.steampy_compat import apply_steampy_compat

apply_steampy_compat()

from steampy.client import SteamClient
from steampy.models import GameOptions, SteamUrl


def _config_value(config: dict, env_name: str, config_name: str) -> str | None:
    return os.getenv(env_name) or config.get(config_name)


def _mask(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _cookie_value(client: SteamClient, name: str) -> str | None:
    for cookie in client._session.cookies:
        if cookie.name == name:
            return cookie.value
    return None


def _save_session(client: SteamClient) -> None:
    with open("cookies.pkl", "wb") as f:
        pickle.dump(client, f)

    os.makedirs("accounts", exist_ok=True)
    wrapped_client = SteamPyClient()
    wrapped_client._client = client
    account_path = os.path.join("accounts", f"{client.username}_client")
    with open(account_path, "wb") as f:
        pickle.dump(wrapped_client, f)

    print("Saved session:")
    print("  cookies.pkl")
    print(f"  {account_path}")


def _summarize_dict(value):
    if not isinstance(value, dict):
        return {"type": type(value).__name__, "repr": repr(value)[:160]}

    if value and all(not isinstance(item, (dict, list, tuple, set)) for item in value.values()):
        return value

    summary = {"keys": sorted(value.keys())[:20]}
    for key in ("response", "assets", "descriptions", "sell_listings", "buy_orders"):
        if key in value:
            nested = value[key]
            if isinstance(nested, dict):
                summary[f"{key}_keys"] = sorted(nested.keys())[:20]
            elif isinstance(nested, list):
                summary[f"{key}_len"] = len(nested)
    return summary


def _probe(name: str, func):
    print(f"\nProbe: {name}")
    try:
        result = func()
    except Exception as exc:
        print("  status: ERROR")
        print("  error_type:", type(exc).__name__)
        print("  error:", str(exc)[:500])
        return None

    print("  status: OK")
    if isinstance(result, dict):
        pprint(_summarize_dict(result))
    else:
        print("  result:", repr(result)[:300])
    return result


def _cookie_header_summary(cookie_header: str | None) -> dict:
    if not cookie_header:
        return {"cookie_header_len": 0, "cookie_names": [], "duplicate_cookie_names": []}

    names = []
    for part in cookie_header.split(";"):
        if "=" in part:
            names.append(part.split("=", 1)[0].strip())
    duplicates = sorted({name for name in names if names.count(name) > 1})
    return {
        "cookie_header_len": len(cookie_header),
        "cookie_names": names,
        "duplicate_cookie_names": duplicates,
    }


def _probe_page(client: SteamClient, name: str, url: str):
    def request_page():
        response = client._session.get(url, timeout=20)
        text = response.text
        steam_id_match = re.search(r'g_steamID = "(\d+)";', text)
        data = {
            "status_code": response.status_code,
            "final_url": response.url,
            "length": len(text),
            "steam_id": steam_id_match.group(1) if steam_id_match else None,
            "has_logged_in_json": '"logged_in":true' in text or "&quot;logged_in&quot;:true" in text,
            "has_logout_link": "/login/logout/" in text or "Logout" in text,
            "has_wallet_info": "g_rgWalletInfo" in text,
            "has_login_link": "/login/" in text or "Sign In" in text,
            "contains_username": bool(client.username and client.username.lower() in text.lower()),
        }
        data.update(_cookie_header_summary(response.request.headers.get("Cookie")))
        return data

    return _probe(name, request_page)


with open("config.json", "r", encoding="utf-8") as config_file:
    config = json.load(config_file)

steam_client = SteamClient(
    api_key=_config_value(config, "STEAM_API_KEY", "API_KEY"),
    username=_config_value(config, "STEAM_USERNAME", "PARSER_LOGIN"),
    password=_config_value(config, "STEAM_PASSWORD", "PARSER_PASSWORD"),
    steam_guard=_config_value(config, "STEAM_MAFILE_PATH", "PARSER_MAFILE"),
)

print("Before login:")
print("  was_login_executed:", steam_client.was_login_executed)
print("  cookie keys:", list(steam_client._session.cookies.keys()))

steam_client.login()

print("\nAfter login:")
print("  was_login_executed:", steam_client.was_login_executed)
print("  cookie keys:", list(steam_client._session.cookies.keys()))
print("  sessionid:", _mask(_cookie_value(steam_client, "sessionid")))
print("  steamLoginSecure:", _mask(_cookie_value(steam_client, "steamLoginSecure")))

_save_session(steam_client)

print("\nAuthorized as:", steam_client.username)
print("is_session_alive:", steam_client.is_session_alive())
print("\nAuth debug:")
pprint(getattr(steam_client._session, "_steam_auth_debug", {}))

print("\nLive API probes:")
print("access_token_present:", bool(getattr(steam_client, "_access_token", None)))
_probe_page(steam_client, "community_home", SteamUrl.COMMUNITY_URL)
_probe_page(steam_client, "market_page", f"{SteamUrl.COMMUNITY_URL}/market")
_probe("get_steam_id", steam_client.get_steam_id)
_probe("get_wallet_balance", lambda: steam_client.get_wallet_balance(convert_to_decimal=True))
_probe("get_trade_offers_summary", steam_client.get_trade_offers_summary)
_probe(
    "get_trade_offers_api_key",
    lambda: steam_client.get_trade_offers(merge=False, use_webtoken=False, max_retry=1),
)
_probe(
    "get_trade_offers_webtoken",
    lambda: steam_client.get_trade_offers(merge=False, use_webtoken=True, max_retry=1),
)
_probe(
    "get_my_inventory_cs2",
    lambda: steam_client.get_my_inventory(game=GameOptions.CS, merge=False, count=5),
)
_probe("get_my_market_listings", steam_client.market.get_my_market_listings)
