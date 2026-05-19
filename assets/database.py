from __future__ import annotations

import sqlite3
import threading
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
                CREATE TABLE IF NOT EXISTS BoughtItems (
                    item_name TEXT,
                    listing_id TEXT,
                    price REAL,
                    stickers_price REAL,
                    date TEXT
                )
                """
            )
            self._dedupe_track_items()
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_track_items_item_url
                ON TrackItems(item_url)
                """
            )
            self.db.commit()

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


class Items:
    def __init__(self, repository: SqliteItemsRepository):
        self.repository = repository

    def get_track_items(self):
        return self.repository.get_track_items()

    def replace_track_items(self, items):
        return self.repository.replace_track_items(items)

    def add_track_items(self, items):
        return self.repository.add_track_items(items)

    def add_to_checked(self, listing_id):
        self.repository.add_to_checked(listing_id)

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
