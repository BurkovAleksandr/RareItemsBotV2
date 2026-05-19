"""Market purchase module."""

from assets.session import AsyncSteamSession
from assets.steam_market_client import SteamMarketClient
from steampy.models import Currency, GameOptions


class BuyModule:
    def __init__(self, steam_client: AsyncSteamSession, auto_confirm: bool = True):
        self.steam_session: AsyncSteamSession = steam_client
        self.auto_confirm = auto_confirm

    def buy_item(self, item_name, market_id, price, fee):
        client = self.steam_session.get_client()
        market_client = SteamMarketClient(client)
        return market_client.buy_listing(
            market_name=item_name,
            listing_id=str(market_id),
            total=int(price),
            fee=int(fee),
            game=GameOptions.CS,
            currency=Currency.RUB,
            auto_confirm=self.auto_confirm,
        )
