import asyncio
from dataclasses import replace
from datetime import date, datetime
import logging
import random
import time

from .api import APIError, Spoonacular, Telegram
from .config import Settings, WEEKDAYS
from .dashboard import Dashboard, selection_update
from .recipes import Recipe, details, is_resend_command, is_skip_command, parse_selection, skipped, summary
from .state import Session, StateStore
from .translation import metric_temperatures

log = logging.getLogger(__name__)


class RecipeService:
    def __init__(self, settings: Settings, store: StateStore, recipes: Spoonacular,
                 telegram: Telegram, clock=time.time, rng=None, dashboard: Dashboard | None = None,
                 translator=None):
        self.settings = settings
        self.store = store
        self.state = store.load()
        self.recipes = recipes
        self.telegram = telegram
        self.clock = clock
        self.rng = rng or random.Random()
        self.lock = asyncio.Lock()
        self.retry_at = 0.0
        self.telegram_retry_at = 0.0
        self.send_lock = asyncio.Lock()
        self.resends = asyncio.Queue(maxsize=32)
        self.dashboard = dashboard
        self.dashboard_changed = asyncio.Event()
        self.translator = translator
        self.translation_changed = asyncio.Event()

    def record_telegram_failure(self, error: APIError):
        if error.status != 400:
            self.telegram_retry_at = max(self.telegram_retry_at, self.clock() + error.retry_after)

    async def send_telegram(self, method, *args):
        delay = self.telegram_retry_at - self.clock()
        if delay > 0:
            raise APIError("Telegram cooldown", retry_after=delay)
        # Never wait for another network request while holding the session lock.
        if self.send_lock.locked():
            raise APIError("Telegram sender busy", retry_after=1)
        async with self.send_lock:
            try:
                await method(*args)
            except APIError as error:
                self.record_telegram_failure(error)
                raise

    def local_now(self):
        return datetime.fromtimestamp(self.clock(), self.settings.zone)

    def reset_week(self):
        if self.state.reset_week(self.local_now().date()):
            self.store.save(self.state)
            log.info("Weekly counters reset: %s", self.state.week)

    async def tick(self):
        async with self.lock:
            self.reset_week()
            session = self.state.session
            if session and session.phase == "active" and self.clock() >= session.deadline:
                self.resolve(self.rng.randrange(len(session.recipes)), automatic=True)
            if self.clock() < self.retry_at:
                return
            if session and session.phase in ("announcing", "translating_recipe", "delivering", "skipping"):
                await self.deliver()
            now = self.local_now()
            if (self.state.session is None or self.state.session.phase in ("done", "skipped")) and (
                self.state.session is None or self.state.session.day < now.date().isoformat()
            ) and now.time() >= self.settings.start and self.state.sunday_leftovers_day != now.date().isoformat():
                if self.settings.sunday_leftovers and now.weekday() == 6:
                    await self.send_sunday_leftovers(now.date().isoformat())
                else:
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
        vegetarian_only = WEEKDAYS[date.fromisoformat(day).weekday()] in self.settings.vegetarian_days
        try:
            recipes = await self.recipes.recipes(self.settings.recipes_per_day, vegetarian_only)
        except APIError as error:
            self.state.fetch_retry_at = self.clock() + max(300, error.retry_after)
            if error.status in (401, 402, 403):
                self.state.fetch_attempts = 3
            self.store.save(self.state)
            await self.notify_fetch_failure(error)
            if self.state.fetch_attempts == 3:
                log.error("Daily recipe request budget exhausted; skipping today")
            raise
        self.reset_week()
        now = self.local_now()
        if now.date().isoformat() != day:
            log.warning("Recipe fetch crossed midnight; skipping stale menu")
            return
        needed = max(0, self.settings.dessert_days_per_week - self.state.dessert_days_used_this_week)
        remaining = 7 - now.weekday() - int(self.settings.sunday_leftovers)
        dessert = needed > 0 and (needed >= remaining or self.rng.random() < needed / remaining)
        self.state.session = Session(
            day=now.date().isoformat(), recipes=recipes, dessert=dessert,
            trigger=self.settings.trigger_codeword, language=self.settings.interaction_language,
            window_minutes=self.settings.active_window_minutes,
            outbox=summary(recipes, dessert, self.settings.interaction_language,
                           self.settings.trigger_codeword, self.settings.active_window_minutes),
            phase="translating_menu" if self.translator else "announcing",
            translation_deadline=(self.clock() + self.settings.translation_job_timeout_minutes * 60
                                  if self.translator else 0),
        )
        self.state.dessert_days_used_this_week += int(dessert)
        self.store.save(self.state)
        if self.translator:
            self.translation_changed.set()
        log.info("Daily session created: %s", self.state.session.day)

    async def notify_fetch_failure(self, error: APIError):
        exhausted = self.state.fetch_attempts == 3
        if self.settings.interaction_language == "de":
            outcome = "Keine weiteren Versuche heute." if exhausted else "Neuer Versuch in mindestens 5 Minuten."
        else:
            outcome = "No more attempts today." if exhausted else "Retrying in at least 5 minutes."
        alert = (f"Rezeptabruf fehlgeschlagen" + (f" (HTTP/API {error.status})" if error.status else "")
                 if self.settings.interaction_language == "de" else str(error))
        try:
            await self.send_telegram(self.telegram.send, f"{alert}. {outcome}")
        except APIError as notification_error:
            log.warning("Could not send recipe failure alert: %s", notification_error)

    async def send_sunday_leftovers(self, day: str):
        await self.send_telegram(
            self.telegram.send,
            ("Heute kochen wir mit Resten aus dem Kühlschrank oder bestellen etwas :)"
             if self.settings.interaction_language == "de" else
             "Today we cook with leftovers from the fridge or order something :)"),
        )
        self.state.sunday_leftovers_day = day
        self.store.save(self.state)
        log.info("Sunday leftovers message sent: %s", day)

    async def deliver(self):
        session = self.state.session
        if session is None or session.phase not in ("announcing", "translating_recipe", "delivering", "skipping"):
            return
        if session.phase == "delivering" and not session.photo_delivered:
            await self.send_selected_photo(session)
            session.photo_delivered = True
            self.store.save(self.state)
        while session.next_message < len(session.outbox):
            await self.send_telegram(self.telegram.send, session.outbox[session.next_message])
            session.next_message += 1
            self.store.save(self.state)
        if session.phase == "translating_recipe":
            return
        if session.phase == "announcing":
            session.opened_at = self.clock()
            session.deadline = session.opened_at + session.window_minutes * 60
            session.phase = "active"
            log.info("Selection window active")
        else:
            session.phase = "done" if session.phase == "delivering" else "skipped"
            log.info("%s; session inactive", "Recipe delivered" if session.phase == "done" else "Selection skipped")
        self.store.save(self.state)

    def resolve(self, index: int, automatic: bool):
        session = self.state.session
        recipe = session.recipes[index]
        session.selected_index = index
        session.selected_automatic = automatic
        if len(session.translated_titles) == len(session.recipes):
            session.selected_title = session.translated_titles[index]
        session.translation_attempts = 0
        session.translation_retry_at = 0
        if self.translator:
            session.phase = "translating_recipe"
            session.translation_deadline = self.clock() + self.settings.translation_job_timeout_minutes * 60
            session.outbox = ["Rezept ausgewählt. Übersetzung läuft." if session.language == "de"
                              else "Recipe selected. Translation in progress."]
            session.next_message = 0
        else:
            self.prepare_selected(session, recipe)
        self.store.save(self.state)
        if self.dashboard and session.phase == "delivering":
            self.dashboard_changed.set()
        if session.phase == "translating_recipe":
            self.translation_changed.set()
        log.info("%s selection: recipe %s", "Timeout" if automatic else "User", recipe.id)

    def selected_recipe(self, session: Session) -> Recipe:
        recipe = session.recipes[session.selected_index]
        if session.translation_fallback or not session.translated_ingredients or not session.translated_instructions:
            return recipe
        return replace(recipe, title=session.selected_title,
                       ingredients=session.translated_ingredients,
                       instructions=session.translated_instructions)

    def prepare_selected(self, session: Session, recipe: Recipe, fallback=False):
        session.phase = "delivering"
        session.outbox = details(recipe, session.language, session.selected_automatic)
        if fallback:
            message = ("Übersetzung derzeit nicht verfügbar. Hier ist das englische Original."
                       if session.language == "de" else "Translation unavailable. English original follows.")
            session.outbox = [message, *session.outbox]
        session.next_message = 0
        if self.dashboard:
            update = selection_update(recipe, session.day, session.selected_automatic, self.clock(),
                                      self.state.dashboard_updated_at)
            self.state.dashboard_pending = update
            self.state.dashboard_updated_at = update["updated_at"]

    def finish_translation(self, session: Session, fallback: bool):
        if session.phase == "translating_menu":
            if fallback:
                session.translated_titles = []
                menu = list(session.recipes)
            else:
                menu = [replace(recipe, title=title) for recipe, title in
                        zip(session.recipes, session.translated_titles)]
            session.outbox = summary(menu, session.dessert, session.language,
                                     session.trigger, session.window_minutes)
            if fallback and self.translator:
                message = ("Übersetzung derzeit nicht verfügbar. Rezepttitel bleiben Englisch."
                           if session.language == "de" else "Translation unavailable. Recipe titles remain English.")
                session.outbox = [message, *session.outbox]
            session.phase = "announcing"
        else:
            session.translation_fallback = fallback
            recipe = session.recipes[session.selected_index] if fallback else self.selected_recipe(session)
            self.prepare_selected(session, recipe, fallback and self.translator is not None)
        session.translation_retry_at = 0
        session.translation_attempts = 0
        self.store.save(self.state)
        if self.dashboard and session.phase == "delivering":
            self.dashboard_changed.set()

    def translation_batch(self, session: Session):
        if session.phase == "translating_menu":
            start = len(session.translated_titles)
            return "translated_titles", [recipe.title for recipe in session.recipes[start:start + 5]]
        recipe = session.recipes[session.selected_index]
        if not session.selected_title:
            return "selected_title", [recipe.title]
        ingredients = recipe.metric_ingredients or recipe.ingredients
        instructions = [metric_temperatures(step) for step in recipe.instructions]
        if not session.translated_ingredients and not session.translated_instructions:
            texts = [*ingredients, *instructions]
            if sum(map(len, texts)) <= 3200:
                return "translated_recipe", texts
        for field, source in (("translated_ingredients", ingredients),
                              ("translated_instructions", instructions)):
            start = len(getattr(session, field))
            if start < len(source):
                batch = []
                for text in source[start:start + 4]:
                    if batch and sum(map(len, batch)) + len(text) > 2000:
                        break
                    batch.append(text)
                return field, batch
        return None, []

    async def translation_worker(self):
        while True:
            async with self.lock:
                session = self.state.session
                if session is None or session.phase not in ("translating_menu", "translating_recipe"):
                    self.translation_changed.clear()
                    wait = None
                    batch = None
                elif (self.clock() >= session.translation_deadline or self.translator is None
                      or session.translation_attempts >= self.settings.translation_attempts):
                    self.finish_translation(session, fallback=True)
                    continue
                elif self.clock() < session.translation_retry_at:
                    wait = min(session.translation_retry_at, session.translation_deadline) - self.clock()
                    self.translation_changed.clear()
                    batch = None
                else:
                    field, texts = self.translation_batch(session)
                    if not texts:
                        self.finish_translation(session, fallback=False)
                        continue
                    session.translation_attempts += 1
                    self.store.save(self.state)
                    batch = (session, session.phase, field, texts)
                    wait = 0
            if batch is None:
                if wait is None:
                    await self.translation_changed.wait()
                else:
                    try:
                        await asyncio.wait_for(self.translation_changed.wait(), timeout=wait)
                    except asyncio.TimeoutError:
                        pass
                continue
            session, phase, field, texts = batch
            try:
                translated = await asyncio.wait_for(
                    self.translator.translate(texts),
                    timeout=min(self.settings.translation_request_timeout_seconds,
                                max(0.001, session.translation_deadline - self.clock())),
                )
            except (APIError, asyncio.TimeoutError) as error:
                if isinstance(error, asyncio.TimeoutError):
                    error = APIError("LibreTranslate timeout")
                async with self.lock:
                    if self.state.session is session and session.phase == phase:
                        log.warning("%s; translation attempt %s", error, session.translation_attempts)
                        if (session.translation_attempts >= self.settings.translation_attempts
                                or self.clock() >= session.translation_deadline):
                            self.finish_translation(session, fallback=True)
                        else:
                            session.translation_retry_at = self.clock() + 30 * session.translation_attempts
                            self.store.save(self.state)
                continue
            async with self.lock:
                if self.state.session is session and session.phase == phase:
                    if field == "selected_title":
                        session.selected_title = translated[0]
                    elif field == "translated_recipe":
                        count = len(session.recipes[session.selected_index].ingredients)
                        session.translated_ingredients.extend(translated[:count])
                        session.translated_instructions.extend(translated[count:])
                    else:
                        getattr(session, field).extend(translated)
                    session.translation_attempts = 0
                    session.translation_retry_at = 0
                    if not self.translation_batch(session)[1]:
                        self.finish_translation(session, fallback=False)
                    else:
                        self.store.save(self.state)

    def skip(self):
        session = self.state.session
        session.phase = "skipping"
        session.outbox = skipped(session.language)
        session.next_message = 0
        self.store.save(self.state)
        log.info("User skipped selection")

    def resend_menu(self, session: Session):
        photo_session = session if session.phase in ("delivering", "done") else None
        self.enqueue_resend(list(session.outbox), photo_session)

    def enqueue_resend(self, messages: list[str], photo_session: Session | None = None):
        try:
            self.resends.put_nowait((messages, photo_session))
        except asyncio.QueueFull:
            log.warning("Resend queue full; ignoring additional request")

    async def retry_resend(self, operation, *args):
        while True:
            try:
                await operation(*args)
                return
            except APIError as error:
                log.warning("%s; resend retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)

    async def resend_worker(self):
        while True:
            messages, photo_session = await self.resends.get()
            try:
                if photo_session:
                    await self.retry_resend(self.send_selected_photo, photo_session)
                for message in messages:
                    await self.retry_resend(self.send_telegram, self.telegram.send, message)
                log.info("Requested output resent")
            finally:
                self.resends.task_done()

    async def send_selected_photo(self, session: Session):
        recipe = self.selected_recipe(session)
        if not recipe.image_url or session.photo_skipped:
            return
        try:
            await self.send_telegram(self.telegram.send_photo, recipe.image_url, recipe.title)
        except APIError as error:
            # Telegram cannot fetch some external recipe images. Keep recipe text deliverable.
            if error.status != 400:
                raise
            session.photo_skipped = True
            self.store.save(self.state)
            log.warning("Recipe photo rejected; delivering text only")

    def notify_no_menu(self):
        message = ("Heute ist keine Rezeptauswahl verfuegbar." if self.settings.interaction_language == "de"
                   else "No recipe menu is available today.")
        self.enqueue_resend([message])

    async def handle_update(self, update: dict):
        message = update.get("message", {})
        if message.get("chat", {}).get("id") != self.telegram.chat_id:
            return
        text = message.get("text")
        if not isinstance(text, str) or message.get("from", {}).get("is_bot"):
            return
        async with self.lock:
            session = self.state.session
            trigger = session.trigger if session else self.settings.trigger_codeword
            if not text.startswith(trigger):
                return
            self.reset_week()
            if is_resend_command(text, trigger):
                if session and session.day == self.local_now().date().isoformat():
                    self.resend_menu(session)
                else:
                    self.notify_no_menu()
                return
            if not session or session.phase != "active":
                return
            if self.clock() >= session.deadline:
                self.resolve(self.rng.randrange(len(session.recipes)), automatic=True)
                return
            # Telegram dates have one-second precision. Reject queued commands from older sessions.
            sent_at = message.get("date", 0)
            if not int(session.opened_at) <= sent_at < session.deadline:
                return
            if is_skip_command(text, session.trigger):
                self.skip()
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
                self.record_telegram_failure(error)
                log.warning("%s; poll retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)

    async def publish_dashboard(self):
        # Snapshot identity prevents an old acknowledgement from clearing a newer
        # selection. No network await holds the session lock.
        pending = self.state.dashboard_pending
        if self.dashboard is None or pending is None:
            return
        await self.dashboard.publish(pending)
        if self.state.dashboard_pending is pending:
            self.state.dashboard_pending = None
            self.store.save(self.state)

    async def dashboard_worker(self):
        while True:
            self.dashboard_changed.clear()
            try:
                await self.publish_dashboard()
            except APIError as error:
                log.warning("%s; dashboard retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)
                continue
            if self.state.dashboard_pending is None:
                await self.dashboard_changed.wait()

    async def run(self):
        # Existing webhook must not compete with long polling. Retain pending messages for recovery.
        while True:
            try:
                await self.telegram.call("deleteWebhook", drop_pending_updates=False)
                break
            except APIError as error:
                self.record_telegram_failure(error)
                log.warning("%s; startup retry in %.0fs", error, error.retry_after)
                await asyncio.sleep(error.retry_after)
        tasks = [asyncio.create_task(self.scheduler()), asyncio.create_task(self.listen()),
                 asyncio.create_task(self.resend_worker())]
        if self.dashboard:
            tasks.append(asyncio.create_task(self.dashboard_worker()))
        if self.translator or (self.state.session and self.state.session.phase in
                               ("translating_menu", "translating_recipe")):
            tasks.append(asyncio.create_task(self.translation_worker()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
