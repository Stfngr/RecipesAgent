import asyncio
from datetime import date, datetime
import logging
import random
import time

from .api import APIError, Spoonacular, Telegram
from .config import Settings
from .recipes import details, parse_selection, summary
from .state import Session, StateStore, week_key

log = logging.getLogger(__name__)


class RecipeService:
    def __init__(self, settings: Settings, store: StateStore, recipes: Spoonacular,
                 telegram: Telegram, clock=time.time, rng=None):
        self.settings = settings
        self.store = store
        self.state = store.load()
        self.recipes = recipes
        self.telegram = telegram
        self.clock = clock
        self.rng = rng or random.Random()
        self.lock = asyncio.Lock()
        self.retry_at = 0.0

    def local_now(self):
        return datetime.fromtimestamp(self.clock(), self.settings.zone)

    def reset_week(self):
        if self.state.reset_week(self.local_now().date()):
            self.store.save(self.state)
            log.info("Weekly counters reset: %s", self.state.week)

    async def tick(self):
        async with self.lock:
            self.reset_week()
            if self.clock() < self.retry_at:
                return
            session = self.state.session
            if session and session.phase == "active" and self.clock() >= session.deadline:
                self.resolve(self.rng.randrange(len(session.recipes)), automatic=True)
            if session and session.phase in ("announcing", "delivering"):
                await self.deliver()
            now = self.local_now()
            if (self.state.session is None or self.state.session.phase == "done") and (
                self.state.session is None or self.state.session.day < now.date().isoformat()
            ) and now.time() >= self.settings.start:
                await self.create_session()
                await self.deliver()

    async def create_session(self):
        day = self.local_now().date().isoformat()
        if self.state.fetch_day != day:
            self.state.fetch_day = day
            self.state.fetch_attempts = 0
            self.state.fetch_retry_at = 0
        if self.state.fetch_attempts >= 3 or self.clock() < self.state.fetch_retry_at:
            return
        # Persist the budget before each paid request, including across process restarts.
        self.state.fetch_attempts += 1
        self.state.fetch_retry_at = self.clock() + 300
        self.store.save(self.state)
        vegetarian_only = self.state.meat_recipes_chosen_this_week >= self.settings.meat_days_per_week
        try:
            recipes = await self.recipes.recipes(self.settings.recipes_per_day, vegetarian_only)
        except APIError as error:
            self.state.fetch_retry_at = self.clock() + max(300, error.retry_after)
            if error.status in (401, 402, 403):
                self.state.fetch_attempts = 3
            self.store.save(self.state)
            if self.state.fetch_attempts == 3:
                log.error("Daily recipe request budget exhausted; skipping today")
            raise
        self.reset_week()
        now = self.local_now()
        if now.date().isoformat() != day:
            log.warning("Recipe fetch crossed midnight; skipping stale menu")
            return
        needed = max(0, self.settings.dessert_days_per_week - self.state.dessert_days_used_this_week)
        remaining = 7 - now.weekday()
        dessert = needed > 0 and (needed >= remaining or self.rng.random() < needed / remaining)
        self.state.session = Session(
            day=now.date().isoformat(), recipes=recipes, dessert=dessert,
            trigger=self.settings.trigger_codeword, language=self.settings.interaction_language,
            window_minutes=self.settings.active_window_minutes,
            outbox=summary(recipes, dessert, self.settings.interaction_language,
                           self.settings.trigger_codeword, self.settings.active_window_minutes),
        )
        self.state.dessert_days_used_this_week += int(dessert)
        self.store.save(self.state)
        log.info("Daily session created: %s", self.state.session.day)

    async def deliver(self):
        session = self.state.session
        if session is None or session.phase not in ("announcing", "delivering"):
            return
        while session.next_message < len(session.outbox):
            await self.telegram.send(session.outbox[session.next_message])
            session.next_message += 1
            self.store.save(self.state)
        if session.phase == "announcing":
            session.opened_at = self.clock()
            session.deadline = session.opened_at + session.window_minutes * 60
            session.phase = "active"
            log.info("Selection window active")
        else:
            session.phase = "done"
            log.info("Recipe delivered; session inactive")
        self.store.save(self.state)

    def resolve(self, index: int, automatic: bool):
        session = self.state.session
        recipe = session.recipes[index]
        session.selected_index = index
        session.phase = "delivering"
        session.outbox = details(recipe, session.language, automatic)
        session.next_message = 0
        # Quotas belong to the menu date, not the eventual delivery/recovery date.
        if not recipe.vegetarian and week_key(date.fromisoformat(session.day)) == self.state.week:
            self.state.meat_recipes_chosen_this_week += 1
        self.store.save(self.state)
        log.info("%s selection: recipe %s", "Timeout" if automatic else "User", recipe.id)

    async def handle_update(self, update: dict):
        message = update.get("message", {})
        if message.get("chat", {}).get("id") != self.telegram.chat_id:
            return
        text = message.get("text")
        if not isinstance(text, str) or message.get("from", {}).get("is_bot"):
            return
        async with self.lock:
            session = self.state.session
            if not session or session.phase != "active" or not text.startswith(session.trigger):
                return
            self.reset_week()
            if self.clock() >= session.deadline:
                self.resolve(self.rng.randrange(len(session.recipes)), automatic=True)
                return
            # Telegram dates have one-second precision. Reject queued commands from older sessions.
            sent_at = message.get("date", 0)
            if not int(session.opened_at) <= sent_at < session.deadline:
                return
            index = parse_selection(text, session.trigger, len(session.recipes))
            if index is not None:
                self.resolve(index, automatic=False)

    async def scheduler(self):
        while True:
            try:
                await self.tick()
            except APIError as error:
                self.retry_at = self.clock() + error.retry_after
                log.warning("%s; retry in %.0fs", error, error.retry_after)
            await asyncio.sleep(1)

    async def listen(self):
        offset = None
        while True:
            try:
                updates = await self.telegram.updates(offset)
                for update in updates:
                    await self.handle_update(update)
                    offset = update["update_id"] + 1
            except APIError as error:
                log.warning("%s; poll retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)

    async def run(self):
        # Existing webhook must not compete with long polling. Retain pending messages for recovery.
        while True:
            try:
                await self.telegram.call("deleteWebhook", drop_pending_updates=False)
                break
            except APIError as error:
                log.warning("%s; startup retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)
        tasks = [asyncio.create_task(self.scheduler()), asyncio.create_task(self.listen())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
