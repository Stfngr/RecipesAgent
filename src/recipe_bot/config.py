import json
import os
import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Settings:
    recipes_per_day: int = 5
    start_time: str = "08:00"
    timezone: str = "Europe/Berlin"
    active_window_minutes: int = 60
    language: str = "en"
    trigger_codeword: str = "!bot"
    vegetarian_days: tuple[str, ...] = ()
    dessert_days_per_week: int = 2
    interaction_language: str = "de"
    sunday_leftovers: bool = False
    translation_language: str = "de"
    translation_attempts: int = 3
    translation_request_timeout_seconds: int = 30
    translation_job_timeout_minutes: int = 15

    def __post_init__(self):
        for name, low, high in (
            ("recipes_per_day", 1, 100),
            ("active_window_minutes", 1, 1439),
            ("dessert_days_per_week", 0, 7),
            ("translation_attempts", 1, 10),
            ("translation_request_timeout_seconds", 1, 3600),
            ("translation_job_timeout_minutes", 1, 1440),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer between {low} and {high}")
        if not isinstance(self.vegetarian_days, (list, tuple)) or any(
            not isinstance(day, str) or day not in WEEKDAYS for day in self.vegetarian_days
        ):
            raise ValueError("vegetarian_days must contain lowercase weekday names")
        if len(set(self.vegetarian_days)) != len(self.vegetarian_days):
            raise ValueError("vegetarian_days must not contain duplicates")
        object.__setattr__(self, "vegetarian_days", tuple(self.vegetarian_days))
        if not isinstance(self.start_time, str) or not re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", self.start_time
        ):
            raise ValueError("start_time must use HH:MM (24-hour time)")
        ZoneInfo(self.timezone)
        if self.language != "en":
            raise ValueError("Spoonacular recipes support English only; set language=en")
        if self.interaction_language not in ("en", "de"):
            raise ValueError("interaction_language must be en or de")
        if self.translation_language != "de":
            raise ValueError("translation_language must be de")
        if type(self.sunday_leftovers) is not bool:
            raise ValueError("sunday_leftovers must be true or false")
        if self.sunday_leftovers and self.dessert_days_per_week > 6:
            raise ValueError("dessert_days_per_week must be at most 6 with sunday_leftovers")
        if not isinstance(self.trigger_codeword, str) or not re.fullmatch(
            r"\S{1,32}", self.trigger_codeword
        ):
            raise ValueError("trigger_codeword must be 1-32 characters without whitespace")

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def start(self) -> time:
        return time.fromisoformat(self.start_time)


@dataclass(frozen=True)
class Credentials:
    telegram_token: str = field(repr=False)
    chat_id: int
    spoonacular_key: str = field(repr=False)
    dashboard_url: str | None = None
    dashboard_token: str | None = field(default=None, repr=False)
    libretranslate_url: str | None = None


def load_config(settings_path: Path, env_path: Path) -> tuple[Settings, Credentials]:
    names = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SPOONACULAR_API_KEY")
    # A service EnvironmentFile may be unreadable to the service user. Otherwise
    # load optional settings too, without overriding exported environment values.
    try:
        load_dotenv(env_path)
    except PermissionError:
        if not all(os.environ.get(name) for name in names):
            raise
    with settings_path.open(encoding="utf-8") as file:
        settings = Settings(**json.load(file))
    values = [os.environ.get(name, "").strip() for name in names]
    for name, value in zip(names, values):
        if not value:
            raise ValueError(f"Missing {name}")
    token, chat_id, key = values
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Invalid TELEGRAM_BOT_TOKEN format")
    if not re.fullmatch(r"-?[0-9]+", chat_id) or int(chat_id) == 0:
        raise ValueError("TELEGRAM_CHAT_ID must be a nonzero numeric chat ID")
    dashboard_url = os.environ.get("DASHBOARD_URL", "").strip() or None
    dashboard_token = os.environ.get("DASHBOARD_TOKEN", "").strip() or None
    if bool(dashboard_url) != bool(dashboard_token):
        raise ValueError("Set both DASHBOARD_URL and DASHBOARD_TOKEN, or neither")
    if dashboard_url:
        parsed = urlsplit(dashboard_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
            raise ValueError("DASHBOARD_URL must be an HTTP(S) origin without credentials")
        if not dashboard_token.isascii() or any(char.isspace() for char in dashboard_token):
            raise ValueError("Invalid DASHBOARD_TOKEN")
    libretranslate_url = os.environ.get("LIBRETRANSLATE_URL", "").strip() or None
    if libretranslate_url:
        parsed = urlsplit(libretranslate_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("Invalid LIBRETRANSLATE_URL port") from None
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or any(char.isspace() for char in libretranslate_url) or (port is not None and port == 0)):
            raise ValueError("LIBRETRANSLATE_URL must be an HTTP(S) origin without credentials")
    return settings, Credentials(token, int(chat_id), key, dashboard_url, dashboard_token, libretranslate_url)
