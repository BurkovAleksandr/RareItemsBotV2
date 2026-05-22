from __future__ import annotations

import sqlite3
import threading
import json
from collections import Counter
from datetime import datetime
from typing import Iterable
from urllib.parse import quote


def _market_url(item_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(str(item_name or ''))}"


def _sticker_market_url(sticker_name: str) -> str:
    sticker_name = str(sticker_name or "").strip()
    if not sticker_name:
        return ""
    if not sticker_name.lower().startswith("sticker | "):
        sticker_name = f"Sticker | {sticker_name}"
    return _market_url(sticker_name)


def _parse_stickers(raw_value) -> list[dict]:
    if not raw_value:
        return []
    try:
        stickers = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return stickers if isinstance(stickers, list) else []


def _streak_info(stickers: list[dict]) -> dict[str, object]:
    sticker_names = [
        str(sticker.get("name") or "")
        for sticker in stickers
        if isinstance(sticker, dict) and sticker.get("name")
    ]
    streaks = [
        (name, count)
        for name, count in Counter(sticker_names).items()
        if count >= 3
    ]
    if not streaks:
        return {
            "has_streak": False,
            "name": "",
            "count": 0,
            "single_price": 0,
            "sum_price": 0,
        }

    streak_name, streak_count = max(streaks, key=lambda value: (value[1], value[0]))
    single_price = 0.0
    for sticker in stickers:
        if isinstance(sticker, dict) and sticker.get("name") == streak_name:
            try:
                single_price = float(sticker.get("price") or 0)
            except (TypeError, ValueError):
                single_price = 0.0
            break

    return {
        "has_streak": True,
        "name": streak_name,
        "count": streak_count,
        "single_price": single_price,
        "sum_price": round(single_price * streak_count, 2),
    }


def _checked_item_from_row(row) -> dict[str, object]:
    stickers = _parse_stickers(row[7])
    for sticker in stickers:
        if isinstance(sticker, dict):
            sticker_name = str(sticker.get("name") or "")
            sticker["market_url"] = _sticker_market_url(sticker_name)

    item_name = str(row[0] or "")
    price = row[2]
    stickers_price = row[3]
    try:
        ratio = (
            round(float(stickers_price) / float(price), 4)
            if price not in (None, 0, "")
            else 0
        )
    except (TypeError, ValueError, ZeroDivisionError):
        ratio = 0

    return {
        "item_name": item_name,
        "listing_id": str(row[1] or ""),
        "price": price,
        "stickers_price": stickers_price,
        "stickers_to_price_ratio": ratio,
        "float_value": row[4],
        "pattern_template": row[5],
        "profitable": bool(row[6]),
        "stickers": stickers,
        "streak": _streak_info(stickers),
        "market_url": _market_url(item_name) if item_name else "",
        "checked_at": str(row[8] or ""),
    }


def _bought_item_from_row(row) -> dict[str, object]:
    item_name = str(row[0] or "")
    return {
        "item_name": item_name,
        "listing_id": str(row[1] or ""),
        "price": "" if row[2] is None else str(row[2]),
        "stickers_price": "" if row[3] is None else str(row[3]),
        "date": str(row[4] or ""),
        "success": bool(row[5]),
        "status": "success" if row[5] else "failed",
        "error": str(row[6] or ""),
        "market_url": _market_url(item_name) if item_name else "",
    }


class SqliteItemsRepository:
    def __init__(self, db_path: str):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self) -> None:
        with self.lock:
            cur = self.db.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS TrackItems (
                    item_name TEXT NOT NULL,
                    item_url TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS Checked (
                    listing_id TEXT PRIMARY KEY
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS CheckedItems (
                    listing_id TEXT PRIMARY KEY,
                    item_name TEXT,
                    price REAL,
                    stickers_price REAL,
                    float_value REAL,
                    pattern_template TEXT,
                    profitable INTEGER,
                    stickers_json TEXT,
                    checked_at TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS BoughtItems (
                    item_name TEXT,
                    listing_id TEXT,
                    price REAL,
                    stickers_price REAL,
                    date TEXT,
                    success INTEGER DEFAULT 1,
                    error TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS StickerPrices (
                    name TEXT PRIMARY KEY,
                    price REAL
                )
                """
            )
            self._ensure_column("StickerPrices", "updated_at", "TEXT")
            self._ensure_column("BoughtItems", "success", "INTEGER DEFAULT 1")
            self._ensure_column("BoughtItems", "error", "TEXT")
            self._dedupe_track_items()
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_track_items_item_url
                ON TrackItems(item_url)
                """
            )
            self.db.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = [row[1] for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _dedupe_track_items(self) -> None:
        self.db.execute(
            """
            DELETE FROM TrackItems
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM TrackItems
                GROUP BY item_url
            )
            """
        )

    def _checked_column(self) -> str:
        with self.lock:
            columns = [row[1] for row in self.db.execute("PRAGMA table_info(Checked)").fetchall()]
            if "listing_id" in columns:
                return "listing_id"
            if "listingid" in columns:
                return "listingid"
            raise RuntimeError("Checked table does not have a listing id column")

    def _normalize_track_items(self, items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
        normalized: dict[str, str] = {}
        for name, url in items:
            clean_name = str(name).strip()
            clean_url = str(url).strip()
            if clean_name and clean_url:
                normalized.setdefault(clean_url, clean_name)
        return [(name, url) for url, name in normalized.items()]

    def get_track_items(self) -> list[dict[str, str]]:
        with self.lock:
            items = self.db.execute(
                "SELECT item_name, item_url FROM TrackItems ORDER BY item_name"
            ).fetchall()
            return [{item[0]: item[1]} for item in items]

    def count_track_items(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM TrackItems").fetchone()[0])

    def replace_track_items(self, items: Iterable[tuple[str, str]]) -> int:
        item_rows = self._normalize_track_items(items)
        with self.lock:
            self.db.execute("DELETE FROM TrackItems")
            self.db.executemany(
                "INSERT OR IGNORE INTO TrackItems (item_name, item_url) VALUES (?, ?)",
                item_rows,
            )
            self.db.commit()
            return self.db.execute("SELECT COUNT(*) FROM TrackItems").fetchone()[0]

    def add_track_items(self, items: Iterable[tuple[str, str]]) -> int:
        item_rows = self._normalize_track_items(items)
        with self.lock:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO TrackItems (item_name, item_url) VALUES (?, ?)",
                item_rows,
            )
            self.db.commit()
            return self.db.total_changes - before

    def add_to_checked(self, listing_id) -> None:
        column = self._checked_column()
        with self.lock:
            self.db.execute(
                f"INSERT OR IGNORE INTO Checked ({column}) VALUES (?)",
                (str(listing_id),),
            )
            self.db.commit()

    def add_checked_item_details(
        self,
        item_name,
        listing_id,
        price,
        stickers_price,
        float_value=None,
        pattern_template=None,
        stickers=None,
        profitable=False,
        checked_at=None,
    ) -> None:
        if isinstance(checked_at, datetime):
            checked_at = checked_at.isoformat(sep=" ", timespec="seconds")
        checked_at = str(checked_at or datetime.now().isoformat(sep=" ", timespec="seconds"))
        stickers_json = json.dumps(stickers or [], ensure_ascii=False)
        with self.lock:
            self.db.execute(
                """
                INSERT OR REPLACE INTO CheckedItems (
                    listing_id,
                    item_name,
                    price,
                    stickers_price,
                    float_value,
                    pattern_template,
                    profitable,
                    stickers_json,
                    checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(listing_id),
                    str(item_name or ""),
                    price,
                    stickers_price,
                    float_value,
                    None if pattern_template is None else str(pattern_template),
                    1 if profitable else 0,
                    stickers_json,
                    checked_at,
                ),
            )
            self.db.commit()

    def get_recent_checked_items(self, limit: int = 10) -> list[dict[str, object]]:
        with self.lock:
            rows = self.db.execute(
                """
                SELECT item_name, listing_id, price, stickers_price, float_value,
                       pattern_template, profitable, stickers_json, checked_at
                FROM CheckedItems
                ORDER BY checked_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        return [_checked_item_from_row(row) for row in rows]

    def get_checked_items(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        min_stickers_price: float | None = None,
        max_stickers_price: float | None = None,
        min_item_price: float | None = None,
        max_item_price: float | None = None,
        has_streak: bool | None = None,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        clauses = []
        params: list[object] = []
        if date_from:
            clauses.append("checked_at >= ?")
            params.append(str(date_from))
        if date_to:
            clauses.append("checked_at <= ?")
            params.append(str(date_to))
        if min_stickers_price is not None:
            clauses.append("stickers_price >= ?")
            params.append(float(min_stickers_price))
        if max_stickers_price is not None:
            clauses.append("stickers_price <= ?")
            params.append(float(max_stickers_price))
        if min_item_price is not None:
            clauses.append("price >= ?")
            params.append(float(min_item_price))
        if max_item_price is not None:
            clauses.append("price <= ?")
            params.append(float(max_item_price))

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            params.append(max(1, int(limit)))
        with self.lock:
            rows = self.db.execute(
                f"""
                SELECT item_name, listing_id, price, stickers_price, float_value,
                       pattern_template, profitable, stickers_json, checked_at
                FROM CheckedItems
                {where_sql}
                ORDER BY checked_at DESC
                {limit_sql}
                """,
                params,
            ).fetchall()

        items = [_checked_item_from_row(row) for row in rows]
        if has_streak is not None:
            items = [
                item
                for item in items
                if bool((item.get("streak") or {}).get("has_streak")) is has_streak
            ]
        return items

    def check(self, listing_id) -> bool:
        column = self._checked_column()
        with self.lock:
            return bool(
                self.db.execute(
                    f"SELECT 1 FROM Checked WHERE {column} = ?",
                    (str(listing_id),),
                ).fetchone()
            )

    def add_to_bought_items(
        self,
        item_name,
        listing_id,
        price,
        stickers_price,
        date,
        success=True,
        error="",
    ) -> None:
        if isinstance(date, datetime):
            date = date.isoformat(sep=" ", timespec="seconds")
        with self.lock:
            self.db.execute(
                """
                INSERT INTO BoughtItems (
                    item_name,
                    listing_id,
                    price,
                    stickers_price,
                    date,
                    success,
                    error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_name,
                    str(listing_id),
                    price,
                    stickers_price,
                    str(date),
                    1 if success else 0,
                    str(error or ""),
                ),
            )
            self.db.commit()

    def get_recent_bought_items(self, limit: int = 10) -> list[dict[str, str]]:
        with self.lock:
            rows = self.db.execute(
                """
                SELECT item_name, listing_id, price, stickers_price, date, success, error
                FROM BoughtItems
                ORDER BY date DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        return [_bought_item_from_row(row) for row in rows]

    def count_bought_items(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM BoughtItems").fetchone()[0])

    def count_sticker_prices(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM StickerPrices").fetchone()[0])

    def get_recent_sticker_prices(self, limit: int = 8) -> list[dict[str, str]]:
        with self.lock:
            rows = self.db.execute(
                """
                SELECT name, price, updated_at
                FROM StickerPrices
                ORDER BY COALESCE(updated_at, '') DESC, name
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [
            {
                "name": str(name or ""),
                "price": "" if price is None else str(price),
                "updated_at": str(updated_at or ""),
            }
            for name, price, updated_at in rows
        ]


class Items:
    def __init__(self, repository: SqliteItemsRepository):
        self.repository = repository

    def get_track_items(self):
        return self.repository.get_track_items()

    def count_track_items(self):
        return self.repository.count_track_items()

    def replace_track_items(self, items):
        return self.repository.replace_track_items(items)

    def add_track_items(self, items):
        return self.repository.add_track_items(items)

    def add_to_checked(self, listing_id):
        self.repository.add_to_checked(listing_id)

    def add_checked_item_details(self, *args, **kwargs):
        return self.repository.add_checked_item_details(*args, **kwargs)

    def get_recent_checked_items(self, limit=10):
        return self.repository.get_recent_checked_items(limit)

    def get_checked_items(self, **kwargs):
        return self.repository.get_checked_items(**kwargs)

    def check(self, listing_id) -> bool:
        return self.repository.check(listing_id)

    def add_to_bought_items(
        self,
        item_name,
        listing_id,
        price,
        stickers_price,
        date,
        success=True,
        error="",
    ):
        return self.repository.add_to_bought_items(
            item_name,
            listing_id,
            price,
            stickers_price,
            date,
            success=success,
            error=error,
        )

    def get_recent_bought_items(self, limit=10):
        return self.repository.get_recent_bought_items(limit)

    def count_bought_items(self):
        return self.repository.count_bought_items()

    def count_sticker_prices(self):
        return self.repository.count_sticker_prices()

    def get_recent_sticker_prices(self, limit=8):
        return self.repository.get_recent_sticker_prices(limit)
