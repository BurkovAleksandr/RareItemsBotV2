from __future__ import annotations

import requests


class Currency:
    rates_ids = {
        1: "USD",
        2: "GBP",
        3: "EUR",
        4: "CHF",
        5: "RUB",
        6: "PLN",
        7: "BRL",
        8: "JPY",
        9: "NOK",
        10: "IDR",
        11: "MYR",
        12: "PHP",
        13: "SGD",
        14: "THB",
        15: "VND",
        16: "KRW",
        17: "TRY",
        18: "UAH",
        19: "MXN",
        20: "CAD",
        21: "AUD",
        22: "NZD",
        23: "CNY",
        24: "INR",
        25: "CLP",
        26: "PEN",
        27: "COP",
        28: "ZAR",
        29: "HKD",
        30: "TWD",
        31: "SAR",
        32: "AED",
        33: "SEK",
        34: "ARS",
        35: "ILS",
        36: "BYN",
        37: "KZT",
        38: "KWD",
        39: "QAR",
        40: "CRC",
        41: "UYU",
    }

    def __init__(self, api_key: str, default_currency: int = 5):
        self.api_key = api_key
        self.rates: dict | None = None
        self.DEFAULT_CURRENCY = default_currency

    def change_currency(
        self,
        price: float,
        start_currency_id: int,
        target_currency_id: int = -1,
    ) -> float:
        if self.rates is None:
            raise RuntimeError("Currency rates are not loaded")
        if target_currency_id == -1:
            target_currency_id = self.DEFAULT_CURRENCY

        start_currency_definition = self.rates_ids[start_currency_id % 100]
        target_currency_definition = self.rates_ids[target_currency_id % 100]
        start_currency_value = self.rates[start_currency_definition]
        target_currency_value = self.rates[target_currency_definition]
        return price * (target_currency_value / start_currency_value)

    def update_steam_currency_rates(self) -> dict:
        params = {
            "key": self.api_key,
            "format": "json",
            "amp": "",
            "appid": "1764030",
        }
        response = requests.get(
            "https://api.steampowered.com/ISteamEconomy/GetAssetPrices/v1/",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload["result"]["success"] is not True:
            raise RuntimeError(f"Steam currency rates request failed: {payload}")

        self.rates = payload["result"]["assets"][0]["prices"]
        return self.rates
