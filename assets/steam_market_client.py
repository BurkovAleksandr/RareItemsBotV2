from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from steampy import guard
from steampy.client import SteamClient
from steampy.exceptions import ApiException
from steampy.models import Currency, GameOptions


COMMUNITY_URL = "https://steamcommunity.com"
CONFIRMATION_URL = f"{COMMUNITY_URL}/mobileconf"


@dataclass
class MobileConfirmation:
    data_confid: str
    nonce: str
    creator_id: str | None = None
    type: int | None = None
    type_name: str | None = None
    headline: str | None = None
    summary: list[str] | None = None

    def matches(self, confirmation_id: str) -> bool:
        return self.data_confid == confirmation_id or self.creator_id == confirmation_id


@dataclass
class MarketPurchaseResult:
    success: bool
    listing_id: str
    total: int
    initial_response: dict[str, Any]
    confirmation_response: dict[str, Any] | None = None
    completion_response: dict[str, Any] | None = None

    @property
    def wallet_info(self) -> dict[str, Any] | None:
        response = self.completion_response or self.initial_response
        wallet_info = response.get("wallet_info") if isinstance(response, dict) else None
        return wallet_info if isinstance(wallet_info, dict) else None

    @property
    def wallet_balance(self) -> Decimal | None:
        wallet_info = self.wallet_info
        if not wallet_info or wallet_info.get("wallet_balance") is None:
            return None
        return Decimal(str(wallet_info["wallet_balance"])) / Decimal("100")


def build_buy_listing_data(
    session_id: str,
    currency: Currency | int,
    subtotal: int,
    fee: int,
    total: int,
    confirmation_id: str | int | None = None,
) -> dict[str, str]:
    currency_value = currency.value if hasattr(currency, "value") else currency
    return {
        "sessionid": session_id,
        "currency": str(currency_value),
        "subtotal": str(subtotal),
        "fee": str(fee),
        "total": str(total),
        "quantity": "1",
        "billing_state": "",
        "save_my_address": "0",
        "tradefee_tax": "0",
        "confirmation": str(confirmation_id or 0),
    }


def build_buy_listing_headers(market_name: str, game: GameOptions) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": COMMUNITY_URL,
        "Referer": f"{COMMUNITY_URL}/market/listings/{game.app_id}/{quote(market_name)}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    }


def extract_confirmation_id(payload: dict[str, Any]) -> str | None:
    confirmation = payload.get("confirmation")
    if isinstance(confirmation, dict) and confirmation.get("confirmation_id"):
        return str(confirmation["confirmation_id"])
    if payload.get("confirmation_id"):
        return str(payload["confirmation_id"])
    return None


def is_buy_success(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("wallet_info"), dict) and payload["wallet_info"].get(
        "success"
    ) == 1


def is_confirmation_success(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("success") is True


def _json_or_api_error(response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise ApiException(f"Steam returned a non-JSON response: {response.text[:300]}") from exc


class SteamMarketClient:
    def __init__(self, steam_client: SteamClient) -> None:
        self.client = steam_client

    def buy_listing(
        self,
        market_name: str,
        listing_id: str,
        total: int,
        fee: int,
        game: GameOptions = GameOptions.CS,
        currency: Currency = Currency.RUB,
        auto_confirm: bool = True,
        confirmation_timeout: float = 60,
        confirmation_interval: float = 2,
    ) -> MarketPurchaseResult:
        subtotal = int(total) - int(fee)
        initial_response = self._buy_listing_once(
            market_name=market_name,
            listing_id=listing_id,
            subtotal=subtotal,
            fee=int(fee),
            total=int(total),
            game=game,
            currency=currency,
        )
        if is_buy_success(initial_response):
            return MarketPurchaseResult(True, listing_id, int(total), initial_response)

        if not initial_response.get("need_confirmation"):
            raise ApiException(f'There was a problem buying this item. Message: {initial_response.get("message")}')

        confirmation_id = extract_confirmation_id(initial_response)
        if not confirmation_id:
            raise ApiException("Steam requires mobile confirmation but did not return confirmation_id")
        if not auto_confirm:
            raise ApiException(f"Steam requires mobile confirmation: {confirmation_id}")

        confirmation_response = self.confirm_mobile_confirmation(
            confirmation_id,
            timeout_seconds=confirmation_timeout,
            interval_seconds=confirmation_interval,
        )
        if not is_confirmation_success(confirmation_response):
            raise ApiException(f"Mobile confirmation failed: {confirmation_response}")

        completion_response = self.complete_confirmed_buy_listing(
            market_name=market_name,
            listing_id=listing_id,
            subtotal=subtotal,
            fee=int(fee),
            total=int(total),
            confirmation_id=confirmation_id,
            game=game,
            currency=currency,
            timeout_seconds=confirmation_timeout,
            interval_seconds=confirmation_interval,
        )
        return MarketPurchaseResult(
            True,
            listing_id,
            int(total),
            initial_response,
            confirmation_response=confirmation_response,
            completion_response=completion_response,
        )

    def complete_confirmed_buy_listing(
        self,
        market_name: str,
        listing_id: str,
        subtotal: int,
        fee: int,
        total: int,
        confirmation_id: str,
        game: GameOptions,
        currency: Currency,
        timeout_seconds: float,
        interval_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            payload = self._buy_listing_once(
                market_name=market_name,
                listing_id=listing_id,
                subtotal=subtotal,
                fee=fee,
                total=total,
                game=game,
                currency=currency,
                confirmation_id=confirmation_id,
            )
            if is_buy_success(payload):
                return payload

            if payload.get("need_confirmation"):
                next_confirmation_id = extract_confirmation_id(payload)
                if next_confirmation_id and next_confirmation_id != confirmation_id:
                    raise ApiException(f"Steam requested a new mobile confirmation: {next_confirmation_id}")

            if time.monotonic() >= deadline:
                raise ApiException(f"Timed out completing confirmed market purchase: {payload}")
            time.sleep(interval_seconds)

    def confirm_mobile_confirmation(
        self,
        confirmation_id: str,
        timeout_seconds: float = 60,
        interval_seconds: float = 2,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_confirmations: list[MobileConfirmation] = []

        while True:
            confirmations = self.fetch_mobile_confirmations()
            last_confirmations = confirmations
            for confirmation in confirmations:
                if confirmation.matches(confirmation_id):
                    return self.send_mobile_confirmation(confirmation, "allow")

            if time.monotonic() >= deadline:
                seen = [
                    {
                        "id": confirmation.data_confid,
                        "creator_id": confirmation.creator_id,
                        "type": confirmation.type,
                        "headline": confirmation.headline,
                    }
                    for confirmation in last_confirmations
                ]
                raise ApiException(f"Confirmation {confirmation_id} was not found. Seen confirmations: {seen}")
            time.sleep(interval_seconds)

    def fetch_mobile_confirmations(self) -> list[MobileConfirmation]:
        response = self.client._session.get(
            f"{CONFIRMATION_URL}/getlist",
            params=self._confirmation_params("list"),
            headers={"User-Agent": "Mozilla/5.0 Steam Client Bootstrapper/01"},
        )
        payload = _json_or_api_error(response)
        if payload.get("success") is not True:
            raise ApiException(f"Could not fetch mobile confirmations: {payload}")

        confirmations = []
        for item in payload.get("conf") or []:
            confirmations.append(
                MobileConfirmation(
                    data_confid=str(item.get("id") or item.get("confid")),
                    nonce=str(item.get("nonce") or item.get("key")),
                    creator_id=str(item["creator_id"]) if item.get("creator_id") else None,
                    type=item.get("type"),
                    type_name=item.get("type_name"),
                    headline=item.get("headline"),
                    summary=item.get("summary"),
                )
            )
        return confirmations

    def send_mobile_confirmation(self, confirmation: MobileConfirmation, action: str = "allow") -> dict[str, Any]:
        params = self._confirmation_params(action)
        params.update(
            {
                "op": action,
                "cid": confirmation.data_confid,
                "ck": confirmation.nonce,
            }
        )
        response = self.client._session.get(
            f"{CONFIRMATION_URL}/ajaxop",
            params=params,
            headers={"User-Agent": "Mozilla/5.0 Steam Client Bootstrapper/01"},
        )
        return _json_or_api_error(response)

    def _buy_listing_once(
        self,
        market_name: str,
        listing_id: str,
        subtotal: int,
        fee: int,
        total: int,
        game: GameOptions,
        currency: Currency,
        confirmation_id: str | int | None = None,
    ) -> dict[str, Any]:
        session_id = self.client._get_session_id()
        if not session_id:
            raise ApiException("Missing Steam sessionid cookie")

        data = build_buy_listing_data(session_id, currency, subtotal, fee, total, confirmation_id)
        response = self.client._session.post(
            f"{COMMUNITY_URL}/market/buylisting/{listing_id}",
            data=data,
            headers=build_buy_listing_headers(market_name, game),
        )
        return _json_or_api_error(response)

    def _confirmation_params(self, tag: str) -> dict[str, Any]:
        steam_guard = self.client.steam_guard or {}
        steam_id = str(steam_guard.get("steamid") or self.client.get_steam_id())
        identity_secret = steam_guard.get("identity_secret")
        if not identity_secret:
            raise ApiException("Missing identity_secret for mobile confirmation")

        timestamp = int(time.time())
        confirmation_key = guard.generate_confirmation_key(identity_secret, tag, timestamp).decode("ascii")
        return {
            "p": steam_guard.get("device_id") or guard.generate_device_id(steam_id),
            "a": steam_id,
            "k": confirmation_key,
            "t": timestamp,
            "m": "react",
            "tag": tag,
        }
