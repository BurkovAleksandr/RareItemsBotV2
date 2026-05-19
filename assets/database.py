from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable


class SqliteItemsRepository:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path)
        self._create_tables()

    def _create_tables(self) -> None:
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
        self.db.commit()

    def _checked_column(self) -> str:
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(Checked)").fetchall()]
        if "listing_id" in columns:
            return "listing_id"
        if "listingid" in columns:
            return "listingid"
        raise RuntimeError("Checked table does not have a listing id column")

    def get_track_items(self) -> list[dict[str, str]]:
        items = self.db.execute("SELECT item_name, item_url FROM TrackItems").fetchall()
        return [{item[0]: item[1]} for item in items]

    def replace_track_items(self, items: Iterable[tuple[str, str]]) -> int:
        item_rows = [(str(name), str(url)) for name, url in items if name and url]
        self.db.execute("DELETE FROM TrackItems")
        self.db.executemany(
            "INSERT INTO TrackItems (item_name, item_url) VALUES (?, ?)",
            item_rows,
        )
        self.db.commit()
        return len(item_rows)

    def add_track_items(self, items: Iterable[tuple[str, str]]) -> int:
        item_rows = [(str(name), str(url)) for name, url in items if name and url]
        self.db.executemany(
            "INSERT INTO TrackItems (item_name, item_url) VALUES (?, ?)",
            item_rows,
        )
        self.db.commit()
        return len(item_rows)

    def add_to_checked(self, listing_id) -> None:
        column = self._checked_column()
        self.db.execute(
            f"INSERT OR IGNORE INTO Checked ({column}) VALUES (?)",
            (str(listing_id),),
        )
        self.db.commit()

    def check(self, listing_id) -> bool:
        column = self._checked_column()
        return bool(
            self.db.execute(
                f"SELECT 1 FROM Checked WHERE {column} = ?",
                (str(listing_id),),
            ).fetchone()
        )

    def add_to_bought_items(self, item_name, listing_id, price, stickers_price, date) -> None:
        if isinstance(date, datetime):
            date = date.isoformat(sep=" ", timespec="seconds")
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
