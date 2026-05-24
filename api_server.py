from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from steampy.models import GameOptions


from assets.inventory import (
    build_inventory_cards,
    CS_INVENTORY_GAME,
    fetch_steam_inventory,
    fetch_market_price,
    normalize_active_listings,
    target_buyer_price_to_receive,
)
from assets.database import SqliteItemsRepository
from web_app import (
    AsyncBotController,
    CONFIG_FIELDS,
    SECRET_FIELDS,
    parse_bool,
    parse_track_items,
    read_text_file,
    serialize_track_items,
    write_text_file,
)
from assets.runtime import load_config_data, write_config_data

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class ItemsPayload(BaseModel):
    items_text: str = ""
    mode: str = "replace"
    expand_exteriors: bool = False


class ProxiesPayload(BaseModel):
    proxies_text: str = ""
    use_proxies: bool | None = None


class ConfigPayload(BaseModel):
    config: dict[str, Any]


class SellInventoryPayload(BaseModel):
    purchase_id: int
    asset_id: str
    price: float


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(config)
    for key in SECRET_FIELDS:
        if sanitized.get(key):
            sanitized[key] = "********"
    return sanitized


def parse_config_update(
    current: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    checkbox_fields = {
        name for name, _, field_type, _ in CONFIG_FIELDS if field_type == "checkbox"
    }
    number_fields = {
        name for name, _, field_type, _ in CONFIG_FIELDS if field_type == "number"
    }
    updated = dict(current)

    for name, _, _, default in CONFIG_FIELDS:
        if name not in incoming:
            continue
        value = incoming[name]
        if (
            name in SECRET_FIELDS
            and str(value or "").strip() in {"", "********"}
            and name in current
        ):
            continue
        if name in checkbox_fields:
            updated[name] = parse_bool(value)
        elif name in number_fields:
            updated[name] = float(value) if str(value).strip() else 0
        else:
            updated[name] = value if value is not None else default
    return updated


class ApiState:
    def __init__(self, config_path: str | None):
        self.config_path = config_path
        self.controller = AsyncBotController(config_path)

    def shutdown(self) -> None:
        self.controller.shutdown()


def inspect_steam_session(
    config: dict, role: str, session: Any = None, include_wallet: bool = False
) -> dict[str, Any]:
    login = str(config.get(f"{role}_LOGIN") or "").strip()
    summary: dict[str, Any] = {
        "login": login or "-",
        "active": None,
        "wallet_balance": None,
        "error": None,
        "source": "runtime" if session else "saved",
    }
    if not login:
        summary["error"] = "not configured"
        return summary

    if session is None:
        try:
            from assets.session import AsyncSteamSession, SteamPyClient

            session = AsyncSteamSession(
                SteamPyClient(),
                login,
                "",
                str(config.get(f"{role}_MAFILE") or ""),
                api_key=str(config.get("API_KEY") or ""),
            )
            session.load_client(str(config.get("ACCOUNTS_DIR") or "./accounts/"))
        except Exception as exc:
            summary["active"] = False
            summary["error"] = f"{type(exc).__name__}: {exc}"
            return summary

    try:
        summary["active"] = bool(session.is_alive())
    except Exception as exc:
        summary["active"] = False
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    if include_wallet and summary["active"]:
        try:
            summary["wallet_balance"] = str(
                session.get_client().get_wallet_balance(convert_to_decimal=True)
            )
        except Exception as exc:
            summary["error"] = f"wallet: {type(exc).__name__}: {exc}"

    return summary


def active_proxy_count(proxies_text: str) -> int:
    return len(
        [
            line
            for line in proxies_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    )


def repository_from_config(config: dict[str, Any]) -> SqliteItemsRepository:
    return SqliteItemsRepository(str(config.get("DB_PATH") or "./db.db"))


def load_steam_session_from_config(config: dict[str, Any], role: str):
    from assets.session import AsyncSteamSession, SteamPyClient

    login = str(config.get(f"{role}_LOGIN") or "").strip()
    if not login:
        raise RuntimeError(f"{role} login is not configured")
    session = AsyncSteamSession(
        SteamPyClient(),
        login,
        str(config.get(f"{role}_PASSWORD") or ""),
        str(config.get(f"{role}_MAFILE") or ""),
        api_key=str(config.get("API_KEY") or ""),
    )
    session.load_client(str(config.get("ACCOUNTS_DIR") or "./accounts/"))
    return session


def get_buyer_session(state: ApiState):
    bot = getattr(state.controller, "bot", None)
    buyer_session = getattr(getattr(bot, "buy_module", None), "steam_session", None)
    if buyer_session is not None:
        return buyer_session
    config = load_config_data(state.config_path)
    return load_steam_session_from_config(config, "BUYER")


def ensure_market_session(client: Any) -> None:
    session_id = client._get_session_id()
    if not session_id:
        raise RuntimeError("Missing Steam sessionid cookie")
    steam_guard = getattr(client, "steam_guard", None) or {}
    if not steam_guard.get("steamid"):
        steam_guard = {**steam_guard, "steamid": str(client.get_steam_id())}
    if hasattr(client, "market") and hasattr(client.market, "_set_login_executed"):
        client.market._set_login_executed(steam_guard, session_id)


def build_dashboard_summary(
    state: ApiState,
    *,
    recent_purchase_count: int | None = None,
    recent_checked_count: int | None = None,
    recent_sticker_price_count: int | None = None,
    include_runtime: bool = False,
    include_sessions: bool = False,
) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")
    repository = repository_from_config(config)
    proxies_text = read_text_file(proxies_path)
    status = state.controller.status()

    dashboard = {
        "bot_state": (
            "STARTING"
            if status["starting"]
            else "RUNNING" if status["running"] else "STOPPED"
        ),
        "bot_state_class": (
            "starting" if status["starting"] else "ok" if status["running"] else "idle"
        ),
        "tracked_count": repository.count_track_items(),
        "purchase_count": repository.count_bought_items(),
        "recent_purchase_count": recent_purchase_count,
        "recent_checked_count": recent_checked_count,
        "sticker_price_count": repository.count_sticker_prices(),
        "recent_sticker_price_count": recent_sticker_price_count,
        "proxy_count": active_proxy_count(proxies_text),
        "proxies_enabled": parse_bool(config.get("USE_PROXIES")),
    }
    if include_runtime:
        dashboard["runtime"] = state.controller.runtime_status.snapshot()
    if include_sessions:
        dashboard.update(build_sessions_payload(state)["sessions"])

    return {"status": status, "dashboard": dashboard}


def build_sessions_payload(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    bot = getattr(state.controller, "bot", None)
    buyer_session = getattr(getattr(bot, "buy_module", None), "steam_session", None)
    parser_session = getattr(bot, "session", None)
    return {
        "sessions": {
            "buyer_session": inspect_steam_session(
                config, "BUYER", buyer_session, include_wallet=True
            ),
            "parser_session": inspect_steam_session(
                config, "PARSER", parser_session, include_wallet=False
            ),
        }
    }


def build_config_payload(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    return {
        "config": sanitize_config(config),
        "config_fields": [
            {
                "name": name,
                "label": label,
                "type": field_type,
                "default": default,
                "secret": name in SECRET_FIELDS,
            }
            for name, label, field_type, default in CONFIG_FIELDS
        ],
    }


def build_tracked_items_payload(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    repository = repository_from_config(config)
    tracked_items = repository.get_track_items()
    return {
        "tracked_items": tracked_items,
        "items_text": serialize_track_items(tracked_items),
    }


def build_proxies_payload(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")
    proxies_text = read_text_file(proxies_path)
    return {
        "proxies_text": proxies_text,
        "proxy_count": active_proxy_count(proxies_text),
        "proxies_enabled": parse_bool(config.get("USE_PROXIES")),
        "paths": {"proxies": proxies_path},
    }


def build_recent_checked_payload(state: ApiState, limit: int = 10) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    items = repository_from_config(config).get_recent_checked_items(limit=limit)
    return {"items": items, "count": len(items)}


def build_recent_purchases_payload(state: ApiState, limit: int = 8) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    purchases = repository_from_config(config).get_recent_bought_items(limit=limit)
    return {"items": purchases, "count": len(purchases)}


def build_purchase_history_payload(
    state: ApiState,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    min_stickers_price: float | None = None,
    max_stickers_price: float | None = None,
    min_item_price: float | None = None,
    max_item_price: float | None = None,
    success: bool | None = None,
    listed: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    purchases = repository_from_config(config).get_bought_items(
        date_from=date_from,
        date_to=date_to,
        min_stickers_price=min_stickers_price,
        max_stickers_price=max_stickers_price,
        min_item_price=min_item_price,
        max_item_price=max_item_price,
        success=success,
        listed=listed,
        limit=limit,
    )
    return {"items": purchases, "count": len(purchases)}


def build_recent_sticker_prices_payload(state: ApiState, limit: int = 8) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    rows = repository_from_config(config).get_recent_sticker_prices(limit=limit)
    return {"rows": rows, "count": len(rows)}


def build_inventory_payload(state: ApiState, include_prices: bool = True) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    repository = repository_from_config(config)
    purchases = repository.get_bought_items(success=True, limit=None)
    if not purchases:
        return {"items": [], "count": 0, "errors": []}

    errors = []
    try:
        steam_session = get_buyer_session(state)
        if not steam_session.is_alive():
            raise RuntimeError("Buyer Steam session is inactive")
        client = steam_session.get_client()
        ensure_market_session(client)
        inventory = fetch_steam_inventory(client, CS_INVENTORY_GAME, merge=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not load Steam inventory: {type(exc).__name__}: {exc}") from exc

    active_listings = {}
    try:
        active_listings = normalize_active_listings(client.market.get_my_market_listings())
    except Exception as exc:
        errors.append(f"Could not load active market listings: {type(exc).__name__}: {exc}")

    price_cache: dict[str, float | None] = {}

    def market_price_lookup(item_name: str) -> float | None:
        if not include_prices:
            return None
        if item_name not in price_cache:
            try:
                price_cache[item_name] = fetch_market_price(client, item_name)
            except Exception as exc:
                price_cache[item_name] = None
                errors.append(f"Could not load market price for {item_name}: {type(exc).__name__}: {exc}")
        return price_cache[item_name]

    items = build_inventory_cards(
        purchases=purchases,
        inventory=inventory,
        active_listings=active_listings,
        market_price_lookup=market_price_lookup,
    )
    return {"items": items, "count": len(items), "errors": errors}


def sell_inventory_item(state: ApiState, payload: SellInventoryPayload) -> dict[str, Any]:
    if payload.price <= 0:
        raise HTTPException(status_code=400, detail="price must be greater than 0")
    config = load_config_data(state.config_path)
    repository = repository_from_config(config)
    price_to_receive = target_buyer_price_to_receive(payload.price)
    if price_to_receive <= 0:
        raise HTTPException(status_code=400, detail="price_to_receive must be greater than 0")

    try:
        steam_session = get_buyer_session(state)
        if not steam_session.is_alive():
            raise RuntimeError("Buyer Steam session is inactive")
        client = steam_session.get_client()
        ensure_market_session(client)
        response = client.market.create_sell_order(
            payload.asset_id,
            CS_INVENTORY_GAME,
            str(int(round(price_to_receive * 100))),
        )
    except Exception as exc:
        repository.record_sale_listing(
            payload.purchase_id,
            asset_id=payload.asset_id,
            sell_price=payload.price,
            sell_price_to_receive=price_to_receive,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise HTTPException(status_code=502, detail=f"Could not create Steam sell listing: {type(exc).__name__}: {exc}") from exc

    active_listing = {}
    try:
        active_listing = normalize_active_listings(client.market.get_my_market_listings()).get(payload.asset_id) or {}
    except Exception:
        active_listing = {}

    sell_listing_id = str(
        active_listing.get("listing_id")
        or response.get("sellid")
        or response.get("sell_listing_id")
        or ""
    )
    pending = bool(
        response.get("needs_mobile_confirmation")
        or response.get("requires_confirmation")
        or response.get("need_confirmation")
    )
    success = response.get("success") in (True, 1) or bool(sell_listing_id)
    if not success and not pending:
        message = response.get("message") or response
        repository.record_sale_listing(
            payload.purchase_id,
            asset_id=payload.asset_id,
            sell_price=payload.price,
            sell_price_to_receive=price_to_receive,
            status="error",
            error=str(message),
        )
        raise HTTPException(status_code=502, detail=f"Steam rejected sell listing: {message}")

    status = "listed" if sell_listing_id or success else "pending_confirmation"
    repository.record_sale_listing(
        payload.purchase_id,
        asset_id=payload.asset_id,
        sell_listing_id=sell_listing_id,
        sell_price=payload.price,
        sell_price_to_receive=price_to_receive,
        status=status,
        error="",
    )
    return {
        "message": "Item listed on Steam" if status == "listed" else "Steam listing is awaiting confirmation",
        "sale": {
            "purchase_id": payload.purchase_id,
            "asset_id": payload.asset_id,
            "sell_listing_id": sell_listing_id,
            "sell_price": payload.price,
            "sell_price_to_receive": price_to_receive,
            "status": status,
            "listing": active_listing,
        },
    }


def build_dashboard(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    db_path = str(config.get("DB_PATH") or "./db.db")
    recent_purchases_payload = build_recent_purchases_payload(state)
    recent_checked_payload = build_recent_checked_payload(state)
    recent_sticker_prices_payload = build_recent_sticker_prices_payload(state)
    summary = build_dashboard_summary(
        state,
        recent_purchase_count=recent_purchases_payload["count"],
        recent_checked_count=recent_checked_payload["count"],
        recent_sticker_price_count=recent_sticker_prices_payload["count"],
        include_runtime=True,
        include_sessions=True,
    )
    config_payload = build_config_payload(state)
    tracked_items_payload = build_tracked_items_payload(state)
    proxies_payload = build_proxies_payload(state)

    return {
        **summary,
        **config_payload,
        **tracked_items_payload,
        "recent_purchases": recent_purchases_payload["items"],
        "recent_checked_items": recent_checked_payload["items"],
        "recent_sticker_prices": recent_sticker_prices_payload["rows"],
        "proxies_text": proxies_payload["proxies_text"],
        "paths": {"db": db_path, "proxies": proxies_payload["paths"]["proxies"]},
    }


def create_app(config_path: str | None = None) -> FastAPI:
    state = ApiState(config_path or os.getenv("BOT_CONFIG_PATH", "./config.json"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(title="RareItemsBot API", version="1.0.0", lifespan=lifespan)
    app.state.bot_state = state
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, Any]:
        return build_dashboard(state)

    @app.get("/api/dashboard/summary")
    def dashboard_summary() -> dict[str, Any]:
        return build_dashboard_summary(state)

    @app.get("/api/dashboard/runtime")
    def dashboard_runtime() -> dict[str, Any]:
        return {"runtime": state.controller.runtime_status.snapshot()}

    @app.get("/api/dashboard/sessions")
    def dashboard_sessions() -> dict[str, Any]:
        return build_sessions_payload(state)

    @app.get("/api/dashboard/recent-checked")
    def dashboard_recent_checked(limit: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
        return build_recent_checked_payload(state, limit=limit)

    @app.get("/api/dashboard/recent-purchases")
    def dashboard_recent_purchases(limit: int = Query(8, ge=1, le=100)) -> dict[str, Any]:
        return build_recent_purchases_payload(state, limit=limit)

    @app.get("/api/dashboard/sticker-prices")
    def dashboard_sticker_prices(limit: int = Query(8, ge=1, le=100)) -> dict[str, Any]:
        return build_recent_sticker_prices_payload(state, limit=limit)

    @app.get("/api/dashboard/config")
    def dashboard_config() -> dict[str, Any]:
        return build_config_payload(state)

    @app.get("/api/dashboard/tracked-items")
    def dashboard_tracked_items() -> dict[str, Any]:
        return build_tracked_items_payload(state)

    @app.get("/api/dashboard/proxies")
    def dashboard_proxies() -> dict[str, Any]:
        return build_proxies_payload(state)

    @app.get("/api/checked-items")
    def checked_items(
        date_from: str | None = None,
        date_to: str | None = None,
        min_stickers_price: float | None = None,
        max_stickers_price: float | None = None,
        min_item_price: float | None = None,
        max_item_price: float | None = None,
        has_streak: bool | None = None,
        limit: int | None = Query(None, ge=1, le=50000),
    ) -> dict[str, Any]:
        config = load_config_data(state.config_path)
        repository = SqliteItemsRepository(str(config.get("DB_PATH") or "./db.db"))
        items = repository.get_checked_items(
            date_from=date_from,
            date_to=date_to,
            min_stickers_price=min_stickers_price,
            max_stickers_price=max_stickers_price,
            min_item_price=min_item_price,
            max_item_price=max_item_price,
            has_streak=has_streak,
            limit=limit,
        )
        return {"items": items, "count": len(items)}

    @app.get("/api/purchases")
    def purchases(
        date_from: str | None = None,
        date_to: str | None = None,
        min_stickers_price: float | None = None,
        max_stickers_price: float | None = None,
        min_item_price: float | None = None,
        max_item_price: float | None = None,
        success: bool | None = None,
        listed: bool | None = None,
        limit: int | None = Query(None, ge=1, le=50000),
    ) -> dict[str, Any]:
        return build_purchase_history_payload(
            state,
            date_from=date_from,
            date_to=date_to,
            min_stickers_price=min_stickers_price,
            max_stickers_price=max_stickers_price,
            min_item_price=min_item_price,
            max_item_price=max_item_price,
            success=success,
            listed=listed,
            limit=limit,
        )

    @app.get("/api/inventory")
    def inventory(include_prices: bool = True) -> dict[str, Any]:
        return build_inventory_payload(state, include_prices=include_prices)

    @app.post("/api/inventory/sell")
    def sell_inventory(payload: SellInventoryPayload) -> dict[str, Any]:
        return sell_inventory_item(state, payload)

    @app.post("/api/bot/start")
    def start_bot() -> dict[str, str]:
        return {"message": state.controller.start_bot()}

    @app.post("/api/bot/stop")
    def stop_bot() -> dict[str, str]:
        return {"message": state.controller.stop_bot()}

    @app.put("/api/config")
    def save_config(payload: ConfigPayload) -> dict[str, Any]:
        current = load_config_data(state.config_path)
        updated = parse_config_update(current, payload.config)
        write_config_data(state.config_path, updated)
        return {"message": "Config saved", "config": sanitize_config(updated)}

    @app.put("/api/items")
    def save_items(payload: ItemsPayload) -> dict[str, Any]:
        config = load_config_data(state.config_path)
        db_path = str(config.get("DB_PATH") or "./db.db")
        items = parse_track_items(
            payload.items_text, expand_exteriors=payload.expand_exteriors
        )
        repository = SqliteItemsRepository(db_path)
        if payload.mode == "append":
            count = repository.add_track_items(items)
            action = "added"
        elif payload.mode == "replace":
            count = repository.replace_track_items(items)
            action = "saved"
        else:
            raise HTTPException(
                status_code=400, detail="mode must be append or replace"
            )
        return {"message": f"Items {action}: {count}", "count": count}

    @app.put("/api/proxies")
    def save_proxies(payload: ProxiesPayload) -> dict[str, Any]:
        config = load_config_data(state.config_path)
        proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")
        write_text_file(proxies_path, payload.proxies_text)
        if payload.use_proxies is not None:
            config["USE_PROXIES"] = payload.use_proxies
            write_config_data(state.config_path, config)
        proxy_count = len(
            [
                line
                for line in payload.proxies_text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        )
        return {"message": f"Proxies saved: {proxy_count}", "count": proxy_count}

    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        if (FRONTEND_DIST / "assets").exists():
            app.mount(
                "/assets",
                StaticFiles(directory=FRONTEND_DIST / "assets"),
                name="frontend-assets",
            )

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str = ""):
            file_path = FRONTEND_DIST / path
            if path and file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RareItemsBot FastAPI server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--config", default=os.getenv("BOT_CONFIG_PATH", "./config.json")
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
