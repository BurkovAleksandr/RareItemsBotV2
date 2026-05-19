from __future__ import annotations

import argparse
import asyncio
import html
import os
import sys
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from assets.database import SqliteItemsRepository
from assets.runtime import load_config_data, load_runtime_config, write_config_data
from utils.items_for_track_finder import get_all_exteriors_for_item


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_SITE_PACKAGES = PROJECT_ROOT / "venv" / "Lib" / "site-packages"
if LOCAL_SITE_PACKAGES.exists() and str(LOCAL_SITE_PACKAGES) not in sys.path:
    sys.path.append(str(LOCAL_SITE_PACKAGES))


CONFIG_FIELDS = [
    ("API_KEY", "Steam API key", "password", ""),
    ("PARSER_LOGIN", "Parser login", "text", ""),
    ("PARSER_PASSWORD", "Parser password", "password", ""),
    ("PARSER_MAFILE", "Parser maFile", "text", "./mafiles/parser.maFile"),
    ("BUYER_LOGIN", "Buyer login", "text", ""),
    ("BUYER_PASSWORD", "Buyer password", "password", ""),
    ("BUYER_MAFILE", "Buyer maFile", "text", "./mafiles/buyer.maFile"),
    ("STRICK3", "3-sticker threshold", "number", "0"),
    ("STRICK45", "4/5-sticker threshold", "number", "0"),
    ("NOSTRICK", "No-strick threshold", "number", "0"),
    ("MIN_STICKERS_PRICE", "Min stickers price", "number", "0"),
    ("AUTOBUY", "Autobuy", "checkbox", False),
    ("USE_PROXIES", "Use proxies", "checkbox", False),
    ("REFRESH_CURRENCY_RATES", "Refresh currency rates", "checkbox", True),
    ("REFRESH_ITEM_PRICES", "Refresh sticker prices", "checkbox", False),
    ("ACCOUNTS_DIR", "Accounts dir", "text", "./accounts/"),
    ("DB_PATH", "SQLite DB path", "text", "./db.db"),
    ("PROXIES_PATH", "Proxy file path", "text", "./proxies.txt"),
]
SECRET_FIELDS = {"API_KEY", "PARSER_PASSWORD", "BUYER_PASSWORD"}


def build_market_url(item_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(item_name)}"


def parse_track_items(raw_text: str, expand_exteriors: bool = False) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parsed_rows = _parse_track_item_line(line, expand_exteriors=expand_exteriors)
        for name, url in parsed_rows:
            key = (name, url)
            if key in seen:
                continue
            rows.append(key)
            seen.add(key)

    return rows


def _parse_track_item_line(line: str, expand_exteriors: bool) -> list[tuple[str, str]]:
    if " | " in line:
        name, url = [part.strip() for part in line.rsplit(" | ", 1)]
        if url.startswith(("http://", "https://")):
            return [(name, url)]

    if "\t" in line:
        name, url = [part.strip() for part in line.split("\t", 1)]
        if url.startswith(("http://", "https://")):
            return [(name, url)]

    if line.startswith(("http://", "https://")):
        path = urlparse(line).path.rstrip("/")
        name = unquote(path.rsplit("/", 1)[-1]) if path else line
        return [(name, line)]

    item_names = get_all_exteriors_for_item(line) if expand_exteriors else [line]
    return [(item_name, build_market_url(item_name)) for item_name in item_names]


def serialize_track_items(items: list[dict[str, str]]) -> str:
    lines = []
    for item in items:
        name, url = next(iter(item.items()))
        lines.append(f"{name} | {url}")
    return "\n".join(lines)


def read_text_file(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def write_text_file(path: str, content: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content.strip() + ("\n" if content.strip() else ""), encoding="utf-8")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def config_value(config: dict, name: str, default: Any) -> Any:
    value = config.get(name, default)
    return "" if value is None else value


class AsyncBotController:
    def __init__(self, config_path: str | None):
        self.config_path = config_path
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.lock = threading.RLock()
        self.task: asyncio.Task | None = None
        self.bot = None
        self.startup_future = None
        self.started_at: datetime | None = None
        self.last_error: str | None = None
        self.running = False
        self.starting = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "starting": self.starting,
                "started_at": self.started_at.isoformat(sep=" ", timespec="seconds")
                if self.started_at
                else None,
                "last_error": self.last_error,
            }

    def start_bot(self) -> str:
        with self.lock:
            if self.running:
                return "Бот уже запущен."
            if self.starting:
                return "Запуск уже выполняется."
            self.starting = True
            self.last_error = None

        future = asyncio.run_coroutine_threadsafe(self._start_bot(), self.loop)
        with self.lock:
            self.startup_future = future
        future.add_done_callback(self._startup_done)
        return "Запуск бота начат."

    async def _start_bot(self) -> None:
        from assets.runtime import create_bot

        runtime_config = load_runtime_config(self.config_path)
        bot = await create_bot(runtime_config)
        self.bot = bot
        task = self.loop.create_task(bot.start())
        task.add_done_callback(self._bot_done)
        with self.lock:
            self.task = task
            self.running = True
            self.starting = False
            self.started_at = datetime.now()

    def _startup_done(self, future) -> None:
        if future.cancelled():
            with self.lock:
                self.starting = False
            return
        try:
            future.result()
        except Exception as exc:
            with self.lock:
                self.starting = False
                self.running = False
                self.last_error = f"{type(exc).__name__}: {exc}"

    def _bot_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            error = None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        else:
            error = None

        with self.lock:
            self.running = False
            self.starting = False
            self.bot = None
            if error:
                self.last_error = error

    def stop_bot(self) -> str:
        with self.lock:
            if self.startup_future and not self.startup_future.done():
                self.startup_future.cancel()
                self.starting = False
                return "Запуск остановлен."
            if not self.running or self.task is None:
                return "Бот не запущен."

        future = asyncio.run_coroutine_threadsafe(self._stop_bot(), self.loop)
        future.result(timeout=15)
        return "Бот остановлен."

    async def _stop_bot(self) -> None:
        with self.lock:
            task = self.task
            bot = self.bot

        if bot:
            bot.stop()

        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=15)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        with self.lock:
            self.running = False
            self.starting = False
            self.bot = None

    def shutdown(self) -> None:
        try:
            self.stop_bot()
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


class BotWebHandler(BaseHTTPRequestHandler):
    controller: AsyncBotController
    config_path: str | None

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self.send_text("ok")
            return
        self.render_index()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        form = self.parse_post()
        try:
            if path == "/control/start":
                message = self.controller.start_bot()
            elif path == "/control/stop":
                message = self.controller.stop_bot()
            elif path == "/config":
                message = self.save_config(form)
            elif path == "/items":
                message = self.save_items(form)
            elif path == "/proxies":
                message = self.save_proxies(form)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except Exception as exc:
            message = f"Ошибка: {type(exc).__name__}: {exc}"
        self.redirect(message)

    def log_message(self, format, *args) -> None:
        return

    def parse_post(self) -> dict[str, str]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if content_type.startswith("multipart/form-data"):
            return self._multipart_to_dict(content_type, body)

        decoded_body = body.decode("utf-8", errors="replace")
        return {key: values[-1] for key, values in parse_qs(decoded_body, keep_blank_values=True).items()}

    def _multipart_to_dict(self, content_type: str, body: bytes) -> dict[str, str]:
        result: dict[str, str] = {}
        raw_message = (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n"
        ).encode("utf-8") + body
        message = BytesParser(policy=default).parsebytes(raw_message)
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            result[str(name)] = payload.decode(charset, errors="replace")
        return result

    def save_config(self, form: dict[str, str]) -> str:
        config = load_config_data(self.config_path)
        checkbox_fields = {name for name, _, field_type, _ in CONFIG_FIELDS if field_type == "checkbox"}
        number_fields = {name for name, _, field_type, _ in CONFIG_FIELDS if field_type == "number"}

        for name, _, field_type, default in CONFIG_FIELDS:
            if name in checkbox_fields:
                config[name] = name in form
            elif name in number_fields:
                raw_value = form.get(name, default)
                config[name] = float(raw_value) if str(raw_value).strip() else 0
            else:
                raw_value = form.get(name, default)
                if name in SECRET_FIELDS and not str(raw_value).strip() and name in config:
                    continue
                config[name] = raw_value

        write_config_data(self.config_path, config)
        return "Конфиг сохранен."

    def save_items(self, form: dict[str, str]) -> str:
        config = load_config_data(self.config_path)
        db_path = str(config.get("DB_PATH") or "./db.db")
        raw_items = "\n".join(
            part for part in [form.get("items_text", ""), form.get("items_file", "")] if part.strip()
        )
        items = parse_track_items(raw_items, expand_exteriors=parse_bool(form.get("expand_exteriors")))
        repository = SqliteItemsRepository(db_path)
        mode = form.get("items_mode", "replace")
        count = repository.add_track_items(items) if mode == "append" else repository.replace_track_items(items)
        action = "добавлено" if mode == "append" else "сохранено"
        return f"Предметов {action}: {count}."

    def save_proxies(self, form: dict[str, str]) -> str:
        config = load_config_data(self.config_path)
        proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")
        raw_proxies = "\n".join(
            part for part in [form.get("proxies_text", ""), form.get("proxies_file", "")] if part.strip()
        )
        write_text_file(proxies_path, raw_proxies)
        proxy_count = len([line for line in raw_proxies.splitlines() if line.strip() and not line.strip().startswith("#")])
        return f"Прокси сохранены: {proxy_count}."

    def render_index(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        message = query.get("message", [""])[-1]
        config = load_config_data(self.config_path)
        db_path = str(config.get("DB_PATH") or "./db.db")
        proxies_path = str(config.get("PROXIES_PATH") or "./proxies.txt")

        repository = SqliteItemsRepository(db_path)
        items_text = serialize_track_items(repository.get_track_items())
        proxies_text = read_text_file(proxies_path)
        status = self.controller.status()
        html_body = self.build_html(
            config=config,
            message=message,
            status=status,
            items_text=items_text,
            proxies_text=proxies_text,
            db_path=db_path,
            proxies_path=proxies_path,
        )
        self.send_html(html_body)

    def build_html(
        self,
        config: dict,
        message: str,
        status: dict[str, Any],
        items_text: str,
        proxies_text: str,
        db_path: str,
        proxies_path: str,
    ) -> str:
        status_label = "STARTING" if status["starting"] else "RUNNING" if status["running"] else "STOPPED"
        status_class = "running" if status["running"] else "starting" if status["starting"] else "stopped"
        config_fields = "\n".join(self.render_config_field(config, field) for field in CONFIG_FIELDS)
        log_tail = html.escape(self.read_log_tail())

        message_html = f'<div class="notice">{html.escape(message)}</div>' if message else ""
        started_at = html.escape(status["started_at"] or "-")
        last_error = html.escape(status["last_error"] or "-")

        return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RareItemsBot</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --border: #d9e0ea;
      --text: #172033;
      --muted: #627084;
      --accent: #0f766e;
      --danger: #b42318;
      --shadow: 0 1px 2px rgba(20, 32, 48, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Segoe UI, Arial, sans-serif;
      font-size: 14px;
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 24px;
      background: #102033;
      color: #fff;
    }}
    h1 {{ margin: 0; font-size: 20px; font-weight: 650; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }}
    main {{
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(520px, 1fr);
      gap: 16px;
      padding: 16px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
      margin-bottom: 16px;
    }}
    label {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 5px; }}
    input[type="text"], input[type="password"], input[type="number"], textarea, select {{
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--text);
      background: #fff;
      font: inherit;
    }}
    textarea {{ min-height: 220px; resize: vertical; font-family: Consolas, monospace; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .field {{ min-width: 0; }}
    .checkrow {{ display: flex; align-items: center; gap: 8px; min-height: 34px; }}
    .checkrow label {{ margin: 0; color: var(--text); font-size: 13px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    button {{
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 8px 12px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      background: var(--accent);
      color: #fff;
    }}
    button.secondary {{ background: #fff; color: var(--text); border-color: var(--border); }}
    button.danger {{ background: var(--danger); }}
    .status {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 10px;
      font-weight: 700;
      background: #eef2f7;
      color: #344054;
    }}
    .status.running {{ background: #dcfce7; color: #166534; }}
    .status.starting {{ background: #fef3c7; color: #92400e; }}
    .notice {{
      margin: 16px 16px 0;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid #b6d7c9;
      background: #ecfdf3;
      color: #14532d;
    }}
    .meta {{ color: var(--muted); line-height: 1.7; }}
    .log {{
      min-height: 180px;
      max-height: 280px;
      overflow: auto;
      white-space: pre-wrap;
      font-family: Consolas, monospace;
      font-size: 12px;
      background: #111827;
      color: #d1d5db;
      border-radius: 6px;
      padding: 10px;
    }}
    @media (max-width: 980px) {{
      main {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>RareItemsBot</h1>
    <span class="status {status_class}">{status_label}</span>
  </header>
  {message_html}
  <main>
    <div>
      <section>
        <h2>Управление</h2>
        <div class="meta">
          Запущен: {started_at}<br>
          Ошибка: {last_error}<br>
          База: {html.escape(db_path)}<br>
          Прокси: {html.escape(proxies_path)}
        </div>
        <div class="actions">
          <form method="post" action="/control/start"><button type="submit">Start</button></form>
          <form method="post" action="/control/stop"><button type="submit" class="danger">Stop</button></form>
          <form method="get" action="/"><button type="submit" class="secondary">Refresh</button></form>
        </div>
      </section>
      <section>
        <h2>Лог</h2>
        <div class="log">{log_tail}</div>
      </section>
    </div>
    <div>
      <section>
        <h2>Конфиг</h2>
        <form method="post" action="/config">
          <div class="grid">{config_fields}</div>
          <div class="actions"><button type="submit">Save config</button></div>
        </form>
      </section>
      <section>
        <h2>Предметы</h2>
        <form method="post" action="/items" enctype="multipart/form-data">
          <textarea name="items_text">{html.escape(items_text)}</textarea>
          <div class="grid" style="margin-top: 12px;">
            <div class="field">
              <label>Файл</label>
              <input type="file" name="items_file" accept=".txt,.csv">
            </div>
            <div class="field">
              <label>Режим</label>
              <select name="items_mode">
                <option value="replace">Replace</option>
                <option value="append">Append</option>
              </select>
            </div>
            <div class="checkrow">
              <input id="expand_exteriors" type="checkbox" name="expand_exteriors" value="1">
              <label for="expand_exteriors">Expand CS2 exteriors</label>
            </div>
          </div>
          <div class="actions"><button type="submit">Save items</button></div>
        </form>
      </section>
      <section>
        <h2>Прокси</h2>
        <form method="post" action="/proxies" enctype="multipart/form-data">
          <textarea name="proxies_text">{html.escape(proxies_text)}</textarea>
          <div class="field" style="margin-top: 12px;">
            <label>Файл</label>
            <input type="file" name="proxies_file" accept=".txt">
          </div>
          <div class="actions"><button type="submit">Save proxies</button></div>
        </form>
      </section>
    </div>
  </main>
</body>
</html>"""

    def render_config_field(self, config: dict, field: tuple[str, str, str, Any]) -> str:
        name, label, field_type, default = field
        value = config_value(config, name, default)
        is_secret = name in SECRET_FIELDS
        escaped_name = html.escape(name)
        escaped_label = html.escape(label)

        if field_type == "checkbox":
            checked = " checked" if parse_bool(value) else ""
            return (
                '<div class="checkrow">'
                f'<input id="{escaped_name}" type="checkbox" name="{escaped_name}" value="1"{checked}>'
                f'<label for="{escaped_name}">{escaped_label}</label>'
                "</div>"
            )

        step = ' step="0.01"' if field_type == "number" else ""
        input_value = "" if is_secret else str(value)
        placeholder = ' placeholder="unchanged"' if is_secret and value else ""
        return (
            '<div class="field">'
            f"<label for=\"{escaped_name}\">{escaped_label}</label>"
            f"<input id=\"{escaped_name}\" name=\"{escaped_name}\" type=\"{field_type}\""
            f"{step}{placeholder} value=\"{html.escape(input_value, quote=True)}\">"
            "</div>"
        )

    def read_log_tail(self) -> str:
        log_path = Path(os.getenv("BOT_LOGS_DIR", "logs")) / "bot.log"
        if not log_path.exists():
            return ""
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-80:])

    def send_html(self, html_body: str) -> None:
        data = html_body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, message: str) -> None:
        location = "/?" + urlencode({"message": message})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()


def make_handler(controller: AsyncBotController, config_path: str | None):
    class Handler(BotWebHandler):
        pass

    Handler.controller = controller
    Handler.config_path = config_path
    return Handler


def run_server(host: str, port: int, config_path: str | None) -> None:
    controller = AsyncBotController(config_path)
    server = ThreadingHTTPServer((host, port), make_handler(controller, config_path))
    print(f"RareItemsBot web UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local RareItemsBot web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", default=os.getenv("BOT_CONFIG_PATH", "./config.json"))
    args = parser.parse_args()
    run_server(args.host, args.port, args.config)


if __name__ == "__main__":
    main()
