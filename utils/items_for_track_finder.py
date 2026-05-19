from __future__ import annotations

import argparse
from pathlib import Path
from pprint import pprint
from urllib.parse import quote


EXTERIORS = [
    "(Factory New)",
    "(Minimal Wear)",
    "(Field-Tested)",
    "(Well-Worn)",
    "(Battle-Scarred)",
]


def get_items_from_file(file_path: str | Path) -> list[str]:
    return [
        line.strip()
        for line in Path(file_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def get_all_exteriors_for_item(item: str) -> list[str]:
    return [f"{item} {exterior}" for exterior in EXTERIORS]


def build_url(item_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(item_name)}"


def build_track_items(items: list[str], expand_exteriors: bool = False) -> list[dict[str, str]]:
    names: list[str] = []
    for item in items:
        names.extend(get_all_exteriors_for_item(item) if expand_exteriors else [item])
    return [{name: build_url(name)} for name in names]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Steam market tracking URLs from item names")
    parser.add_argument("--input", default="items_raw.txt", help="Text file with one item name per line")
    parser.add_argument("--expand-exteriors", action="store_true", help="Add all CS2 exterior variants")
    args = parser.parse_args()

    pprint(build_track_items(get_items_from_file(args.input), expand_exteriors=args.expand_exteriors))


if __name__ == "__main__":
    main()
