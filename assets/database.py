from __future__ import annotations

import sqlite3
import threading
import json
from datetime import datetime
from typing import Iterable


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
                    date TEXT
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

        result = []
        for row in rows:
            stickers = []
            if row[7]:
                try:
                    stickers = json.loads(row[7])
                except json.JSONDecodeError:
                    stickers = []
            result.append(
                {
                    "item_name": str(row[0] or ""),
                    "listing_id": str(row[1] or ""),
                    "price": row[2],
                    "stickers_price": row[3],
                    "float_value": row[4],
                    "pattern_template": row[5],
                    "profitable": bool(row[6]),
                    "stickers": stickers,
                    "checked_at": str(row[8] or ""),
                }
            )
        return result

    def check(self, listing_id) -> bool:
        column = self._checked_column()
        with self.lock:
            return bool(
                self.db.execute(
                    f"SELECT 1 FROM Checked WHERE {column} = ?",
                    (str(listing_id),),
                ).fetchone()
            )

    def add_to_bought_items(self, item_name, listing_id, price, stickers_price, date) -> None:
        if isinstance(date, datetime):
            date = date.isoformat(sep=" ", timespec="seconds")
        with self.lock:
            self.db.execute(
                """
                INSERT INTO BoughtItems (item_name, listing_id, price, stickers_price, date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_name, str(listing_id), price, stickers_price, str(date)),
            )
            self.db.commit()

    def get_recent_bought_items(self, limit: int = 10) -> list[dict[str, str]]:
        with self.lock:
            rows = self.db.execute(
                """
                SELECT item_name, listing_id, price, stickers_price, date
                FROM BoughtItems
                ORDER BY date DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        return [
            {
                "item_name": str(item_name or ""),
                "listing_id": str(listing_id or ""),
                "price": "" if price is None else str(price),
                "stickers_price": "" if stickers_price is None else str(stickers_price),
                "date": str(date or ""),
            }
            for item_name, listing_id, price, stickers_price, date in rows
        ]

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

    def check(self, listing_id) -> bool:
        return self.repository.check(listing_id)

    def add_to_bought_items(self, item_name, listing_id, price, stickers_price, date):
        return self.repository.add_to_bought_items(
            item_name,
            listing_id,
            price,
            stickers_price,
            date,
        )

    def get_recent_bought_items(self, limit=10):
        return self.repository.get_recent_bought_items(limit)

    def count_bought_items(self):
        return self.repository.count_bought_items()

    def count_sticker_prices(self):
        return self.repository.count_sticker_prices()

    def get_recent_sticker_prices(self, limit=8):
        return self.repository.get_recent_sticker_prices(limit)
