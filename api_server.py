from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


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


def build_dashboard(state: ApiState) -> dict[str, Any]:
    config = load_config_data(state.config_path)
    db_path = str(config.get("DB_PATH") or "./db.db")
    proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")
    repository = SqliteItemsRepository(db_path)
    tracked_items = repository.get_track_items()
    recent_purchases = repository.get_recent_bought_items(limit=8)
    recent_checked_items = repository.get_recent_checked_items(limit=10)
    recent_sticker_prices = repository.get_recent_sticker_prices(limit=8)
    proxies_text = read_text_file(proxies_path)
    proxy_count = len(
        [
            line
            for line in proxies_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    )
    status = state.controller.status()
    bot = getattr(state.controller, "bot", None)
    buyer_session = getattr(getattr(bot, "buy_module", None), "steam_session", None)
    parser_session = getattr(bot, "session", None)

    dashboard = {
        "bot_state": (
            "STARTING"
            if status["starting"]
            else "RUNNING" if status["running"] else "STOPPED"
        ),
        "bot_state_class": (
            "starting" if status["starting"] else "ok" if status["running"] else "idle"
        ),
        "buyer_session": inspect_steam_session(
            config, "BUYER", buyer_session, include_wallet=True
        ),
        "parser_session": inspect_steam_session(
            config, "PARSER", parser_session, include_wallet=False
        ),
        "tracked_count": len(tracked_items),
        "purchase_count": repository.count_bought_items(),
        "recent_purchase_count": len(recent_purchases),
        "recent_checked_count": len(recent_checked_items),
        "sticker_price_count": repository.count_sticker_prices(),
        "recent_sticker_price_count": len(recent_sticker_prices),
        "proxy_count": proxy_count,
        "proxies_enabled": parse_bool(config.get("USE_PROXIES")),
        "runtime": state.controller.runtime_status.snapshot(),
    }

    return {
        "status": status,
        "dashboard": dashboard,
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
        "tracked_items": tracked_items,
        "items_text": serialize_track_items(tracked_items),
        "recent_purchases": recent_purchases,
        "recent_checked_items": recent_checked_items,
        "recent_sticker_prices": recent_sticker_prices,
        "proxies_text": proxies_text,
        "paths": {"db": db_path, "proxies": proxies_path},
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
