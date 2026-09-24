from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

import httpx

from .api import APIError, request_json
from .recipes import Recipe


def selection_update(recipe: Recipe, day: str, now: float, previous: str) -> dict:
    timestamp = datetime.fromtimestamp(now, timezone.utc)
    if previous:
        timestamp = max(timestamp, datetime.fromisoformat(previous) + timedelta(microseconds=1))
    return {
        "updated_at": timestamp.isoformat(timespec="microseconds"),
        "payload": {"date": day,
                    "recipe": {key: value for key, value in asdict(recipe).items()
                               if key != "metric_ingredients"}},
    }


def validate_update(update: dict):
    if not isinstance(update, dict) or set(update) != {"updated_at", "payload"}:
        raise ValueError("Invalid dashboard update")
    timestamp = datetime.fromisoformat(update["updated_at"])
    if timestamp.tzinfo is None:
        raise ValueError("Dashboard timestamp must include timezone")
    payload = update["payload"]
    if not isinstance(payload, dict) or set(payload) != {"date", "recipe"}:
        raise ValueError("Invalid dashboard payload")
    date.fromisoformat(payload["date"])
    Recipe(**payload["recipe"])


class Dashboard:
    def __init__(self, client: httpx.AsyncClient, url: str, token: str):
        self.client = client
        self.url = url.rstrip("/") + "/api/v1/state/recipe-bot/current-recipe"
        self.token = token

    async def publish(self, update: dict):
        data = await request_json(
            self.client, "PUT", self.url, "Home Dashboard", json=update,
            headers={"Authorization": f"Bearer {self.token}"}, timeout=10,
        )
        if data.get("applied") is not True:
            raise APIError("Home Dashboard rejected update")
