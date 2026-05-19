import os
import pickle
import requests
from typing import Optional, Protocol
from aiohttp import CookieJar
from aiohttp.client import ClientSession
from requests import Request

from assets.steampy_compat import apply_steampy_compat

apply_steampy_compat()

from steampy.client import SteamClient


class ISteamSession(Protocol):
    def login(self, username, password):
        pass

    def save_cookies_session(self, path):
        pass

    def load_cookie_session(self, path):
        pass

    def is_alive(self):
        pass


class ISteamClient(Protocol):
    def login(self, username, password, steam_guard_file, api_key=""):
        pass

    def get_session(self):
        pass


class SteamPyClient(ISteamClient):
    def __init__(self):
        self._client = None

    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        steam_guard_file: Optional[str] = None,
        api_key: str = "",
        login_cookies: Optional[str] = None,
    ):
        from steampy.client import SteamClient

        self._client = SteamClient(
            api_key=api_key,
            username=username,
            password=password,
            steam_guard=steam_guard_file,
            login_cookies=login_cookies,
        )
        self._client.login()

    def get_session(self):
        return self._client._session

    def get_client(self):
        return self._client

    def is_alive(self):
        if self._client is None:
            return False
        return self._client.is_session_alive()


class SteamSession(ISteamSession):
    def __init__(self, client, username, password, path_to_mafile):
        self.client = client
        self.session = None
        self.username = username
        self.password = password
        self.path_to_mafile = path_to_mafile

    def login(self):
        self.client.login(self.username, self.password, self.path_to_mafile)

    def get_session(self):
        return self.client.get_session()

    def save_cookies_session(self, path):
        # Сохранение cookie в файл
        os.makedirs(path, exist_ok=True)
        res_path = os.path.join(path, self.username)
        with open(res_path, "wb") as f:
            pickle.dump(self.client.get_session().cookies, f)

    def save_client(self, path):
        os.makedirs(path, exist_ok=True)
        # Сохранение клиента SteamPy в файл
        res_path = os.path.join(path, self.username + "_client")
        with open(res_path, "wb") as f:
            pickle.dump(self.client, f)

    def load_cookie_session(self, path):
        # Загрузка cookie из файла
        res_path = os.path.join(path, self.username)
        if self.username not in os.listdir(path):
            raise Exception("No file for load. Try save_session first.")
        with open(res_path, "rb") as f:
            self.session = pickle.load(f)

    def load_client(self, path):
        # Загрузка клиента SteamPy из файла
        res_path = os.path.join(path, self.username)
        if self.username not in os.listdir(path):
            raise Exception("No file for load. Try save_session first.")
        with open(res_path, "rb") as f:
            self.session = pickle.load(f)

    def is_alive(self):
        # Проверка активности сессии
        url = "https://steamcommunity.com/market/"
        response = self.session.get(url)
        return self.username in response.text


class AsyncSteamSession(ISteamSession):
    def __init__(
        self,
        client: ISteamClient,
        username: str,
        password: str,
        path_to_mafile: str,
        api_key: str,
        login_cookies: dict = None,
    ):
        self.client: SteamPyClient = client
        self.sync_session: requests.Session
        self.async_session: ClientSession
        self.api_key = api_key
        self.login_cookies = login_cookies
        self.username = username
        self.password = password
        self.path_to_mafile = path_to_mafile

    def login(self):
        self.client.login(
            username=self.username,
            password=self.password,
            steam_guard_file=self.path_to_mafile,
            api_key=self.api_key,
            login_cookies=self.login_cookies,
        )

    def _cookie_header_for_url(self, url: str) -> str:
        sync_session = self.client.get_session()
        prepared_request = sync_session.prepare_request(Request("GET", url))
        return prepared_request.headers.get("Cookie", "")

    def get_async_session(self, url: str = "https://steamcommunity.com/"):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        cookie_header = self._cookie_header_for_url(url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        return ClientSession(headers=headers, cookie_jar=CookieJar(unsafe=True))

    def get_session(self):
        return self.client.get_session()

    def get_client(self):
        return self.client.get_client()

    def save_cookies_session(self, path):
        os.makedirs(path, exist_ok=True)
        # Сохранение cookie в файл
        res_path = os.path.join(path, self.username)
        with open(res_path, "wb") as f:
            pickle.dump(self.client.get_session().cookies, f)

    def save_client(self, path):
        os.makedirs(path, exist_ok=True)
        # Сохранение клиента SteamPy в файл
        res_path = os.path.join(path, self.username + "_client")
        with open(res_path, "wb") as f:
            pickle.dump(self.client, f)

    def load_cookie_session(self, path):
        # Загрузка cookie из файла

        res_path = os.path.join(path, self.username)
        if self.username not in os.listdir(path):
            raise Exception("No file for load. Try save_session first.")
        with open(res_path, "rb") as f:
            self.client.get_session().cookies.update(pickle.load(f))

    def load_client(self, path):
        # Загрузка клиента SteamPy из файла
        res_path = os.path.join(path, self.username + "_client")
        if self.username + "_client" not in os.listdir(path):
            raise Exception("No file for load. Try save_session first.")
        with open(res_path, "rb") as f:
            loaded_client = pickle.load(f)

        if isinstance(loaded_client, SteamClient):
            wrapper = SteamPyClient()
            wrapper._client = loaded_client
            self.client = wrapper
        else:
            self.client = loaded_client

    def is_alive(self):
        # Проверка активности сессии
        # url = "https://steamcommunity.com/market/"
        # response = await self.async_session.get(url)
        # return self.username in await response.text()
        try:
            return self.client.is_alive()
        except Exception:
            return False
