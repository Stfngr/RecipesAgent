from dataclasses import asdict, dataclass, field, fields
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import tempfile

from .recipes import Recipe


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class Session:
    day: str
    recipes: list[Recipe]
    dessert: bool
    trigger: str
    language: str
    window_minutes: int
    outbox: list[str]
    phase: str = "announcing"
    next_message: int = 0
    opened_at: float | None = None
    deadline: float | None = None
    selected_index: int | None = None

    def __post_init__(self):
        date.fromisoformat(self.day)
        if not self.recipes or not all(isinstance(r, Recipe) for r in self.recipes):
            raise ValueError("Invalid session recipes")
        if len({r.id for r in self.recipes}) != len(self.recipes):
            raise ValueError("Duplicate recipe IDs")
        if type(self.dessert) is not bool or self.language not in ("de", "en"):
            raise ValueError("Invalid session settings")
        if not isinstance(self.trigger, str) or not re.fullmatch(r"\S{1,32}", self.trigger):
            raise ValueError("Invalid session trigger")
        if type(self.window_minutes) is not int or not 1 <= self.window_minutes <= 1439:
            raise ValueError("Invalid session window")
        if self.phase not in ("announcing", "active", "delivering", "skipping", "done", "skipped"):
            raise ValueError("Invalid session phase")
        if not isinstance(self.outbox, list) or not self.outbox or not all(
            isinstance(message, str) and 0 < len(message) <= 2000 for message in self.outbox
        ):
            raise ValueError("Invalid session messages")
        if type(self.next_message) is not int or not 0 <= self.next_message <= len(self.outbox):
            raise ValueError("Invalid delivery cursor")
        if self.phase != "announcing":
            for value in (self.opened_at, self.deadline):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Invalid session deadline")
            if self.deadline <= self.opened_at:
                raise ValueError("Deadline must follow opening time")
        if self.phase in ("delivering", "done"):
            if type(self.selected_index) is not int or not 0 <= self.selected_index < len(self.recipes):
                raise ValueError("Invalid selected recipe")
        elif self.selected_index is not None:
            raise ValueError("Unresolved session cannot have a selection")


@dataclass
class State:
    week: str = ""
    dessert_days_used_this_week: int = 0
    session: Session | None = None
    fetch_day: str = ""
    fetch_attempts: int = 0
    fetch_retry_at: float = 0
    version: int = field(default=2)

    def __post_init__(self):
        if self.version != 2:
            raise ValueError("Unsupported state version")
        if self.week and not re.fullmatch(r"[0-9]{4}-W[0-9]{2}", self.week):
            raise ValueError("Invalid state week")
        if type(self.dessert_days_used_this_week) is not int or not 0 <= self.dessert_days_used_this_week <= 7:
            raise ValueError("Invalid weekly counter")
        if self.fetch_day:
            date.fromisoformat(self.fetch_day)
        if type(self.fetch_attempts) is not int or not 0 <= self.fetch_attempts <= 3:
            raise ValueError("Invalid fetch attempt count")
        if type(self.fetch_retry_at) not in (int, float) or not math.isfinite(self.fetch_retry_at):
            raise ValueError("Invalid fetch retry time")

    def reset_week(self, day: date) -> bool:
        week = week_key(day)
        if self.week == week:
            return False
        self.week = week
        self.dessert_days_used_this_week = 0
        return True


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> State:
        if not self.path.exists():
            return State()
        with self.path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError("Incomplete or unsupported state file")
        legacy_fields = {item.name for item in fields(State)} | {"meat_recipes_chosen_this_week"}
        migrated = data.get("version") == 1 and set(data) == legacy_fields
        if migrated:
            meat_counter = data["meat_recipes_chosen_this_week"]
            if type(meat_counter) is not int or not 0 <= meat_counter <= 7:
                raise ValueError("Invalid weekly counter")
            data.pop("meat_recipes_chosen_this_week")
            data["version"] = 2
        if set(data) != {item.name for item in fields(State)}:
            raise ValueError("Incomplete or unsupported state file")
        if data.get("session") is not None:
            session = data["session"]
            if not isinstance(session, dict) or set(session) != {item.name for item in fields(Session)}:
                raise ValueError("Incomplete or unsupported session")
            session["recipes"] = [Recipe(**recipe) for recipe in session["recipes"]]
            data["session"] = Session(**session)
        state = State(**data)
        if migrated:
            self.save(state)
        return state

    def save(self, state: State) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", delete=False) as file:
                temporary = file.name
                json.dump(asdict(state), file, ensure_ascii=True, allow_nan=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
