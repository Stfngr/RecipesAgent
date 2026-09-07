import httpx
import math

from .recipes import Recipe


class APIError(Exception):
    """Safe error message without credentials, request URLs, or response bodies."""

    def __init__(self, service: str, status: int | None = None, retry_after: float = 60):
        self.status = status
        self.retry_after = max(1, retry_after) if math.isfinite(retry_after) else 60
        super().__init__(f"{service} request failed" + (f" (HTTP/API {status})" if status else ""))


async def request_json(client: httpx.AsyncClient, method: str, url: str, service: str, **kwargs):
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.RequestError:
        raise APIError(service) from None
    if not response.is_success:
        retry_after = 60
        try:
            retry_after = float(response.json().get("parameters", {}).get("retry_after", 60))
        except (ValueError, TypeError, AttributeError):
            pass
        raise APIError(service, response.status_code, retry_after)
    try:
        data = response.json()
    except ValueError:
        raise APIError(service) from None
    if not isinstance(data, dict):
        raise APIError(service)
    return data


class Spoonacular:
    def __init__(self, client: httpx.AsyncClient, key: str):
        self.client = client
        self.key = key

    async def recipes(self, count: int, vegetarian_only: bool) -> list[Recipe]:
        params = {"number": count, "include-tags": "main course"}
        if vegetarian_only:
            params["include-tags"] += ",vegetarian"
        data = await request_json(
            self.client, "GET", "https://api.spoonacular.com/recipes/random", "Spoonacular",
            headers={"x-api-key": self.key}, params=params,
        )
        try:
            recipes = [Recipe.from_api(item) for item in data["recipes"]]
            if len(recipes) != count or len({r.id for r in recipes}) != count:
                raise ValueError("Incomplete or duplicate candidates")
            if vegetarian_only and any(not r.vegetarian for r in recipes):
                raise ValueError("Vegetarian filter not honored")
        except (KeyError, ValueError, TypeError, AttributeError):
            raise APIError("Spoonacular recipe validation", retry_after=300) from None
        return recipes


class Telegram:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: int):
        self.client = client
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.chat_id = chat_id

    async def call(self, method: str, **payload):
        data = await request_json(
            self.client, "POST", self.base_url + method, "Telegram", json=payload,
            timeout=httpx.Timeout(45, connect=10),
        )
        if data.get("ok") is not True or "result" not in data:
            raise APIError("Telegram", data.get("error_code"))
        return data["result"]

    async def send(self, text: str):
        await self.call("sendMessage", chat_id=self.chat_id, text=text,
                        link_preview_options={"is_disabled": True})

    async def updates(self, offset: int | None):
        return await self.call("getUpdates", offset=offset, timeout=30, allowed_updates=["message"])
