import asyncio
from datetime import datetime
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

from recipe_bot.api import APIError, Lara, Spoonacular, Telegram
from recipe_bot.__main__ import TEST_MESSAGE, main, send_test_message
from recipe_bot.config import Credentials, Settings, load_config
from recipe_bot.recipes import Recipe, details, parse_selection, plain_text, split_message, summary
from recipe_bot.service import RecipeService
from recipe_bot.state import State, StateStore, week_key


def recipe(identity=1, vegetarian=True):
    return Recipe(identity, f"Recipe {identity}", vegetarian, ["100 g rice"], ["Cook rice."], 20)


class FakeTelegram:
    chat_id = -123

    def __init__(self):
        self.messages = []
        self.fail = False

    async def send(self, text):
        if self.fail:
            raise APIError("Telegram")
        self.messages.append(text)


class FakeRecipes:
    def __init__(self):
        self.filters = []
        self.fail = None
        self.on_fetch = None

    async def recipes(self, count, vegetarian_only):
        self.filters.append(vegetarian_only)
        if self.on_fetch:
            self.on_fetch()
        if self.fail:
            raise self.fail
        return [recipe(index + 1, vegetarian_only or index != 0) for index in range(count)]


class FakeTranslator:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def translate_recipes(self, recipes):
        self.calls.append(recipes)
        if self.fail:
            raise APIError("Lara", retry_after=300)
        return [Recipe(
            item.id, f"Deutsch {item.title}", item.vegetarian,
            [f"Deutsch {ingredient}" for ingredient in item.ingredients],
            [f"Deutsch {instruction}" for instruction in item.instructions],
            item.ready_minutes, item.prep_minutes, item.cooking_minutes, item.servings,
            item.source_url, item.source_name, item.license,
        ) for item in recipes]


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(Path(self.tmp.name) / "state.json")
        self.now = datetime(2026, 9, 7, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.telegram = FakeTelegram()
        self.api = FakeRecipes()
        self.settings = Settings(recipes_per_day=2)
        self.service = self.build()

    def build(self):
        return RecipeService(self.settings, self.store, self.api, self.telegram,
                             clock=lambda: self.now, rng=random.Random(1))

    def update(self, text="!bot 1", chat=-123, sent_at=None):
        return {"message": {"chat": {"id": chat}, "text": text,
                            "date": int(self.now if sent_at is None else sent_at)}}

    async def test_schedule_and_selection(self):
        self.now -= 1
        await self.service.tick()
        self.assertIsNone(self.service.state.session)
        self.now += 1
        await self.service.tick()
        self.assertIn("Dessert heute: JA", self.telegram.messages[0])
        self.assertEqual(self.service.state.session.phase, "active")
        await self.service.handle_update(self.update())
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 1)
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("100 g rice", self.telegram.messages[-1])
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_lara_translation_is_persisted_before_announcement(self):
        translator = FakeTranslator()
        self.service = RecipeService(self.settings, self.store, self.api, self.telegram, translator,
                                     clock=lambda: self.now, rng=random.Random(1))
        await self.service.tick()
        self.assertEqual(len(translator.calls), 1)
        self.assertIn("Deutsch Recipe 1", self.telegram.messages[0])
        await self.service.handle_update(self.update())
        await self.service.tick()
        self.assertIn("Deutsch 100 g rice", self.telegram.messages[-1])

    async def test_lara_failure_creates_no_session(self):
        translator = FakeTranslator()
        translator.fail = True
        self.service = RecipeService(self.settings, self.store, self.api, self.telegram, translator,
                                     clock=lambda: self.now, rng=random.Random(1))
        with self.assertRaises(APIError) as raised:
            await self.service.tick()
        self.assertEqual(str(raised.exception), "Lara request failed")
        self.assertIsNone(self.service.state.session)
        self.assertEqual(self.service.state.fetch_attempts, 1)

    async def test_filters_ignore_noise_and_stale_commands(self):
        await self.service.tick()
        for update in (self.update("chat"), self.update("!bot 0"), self.update("!bot 3"),
                       self.update("!bot 1 extra"), self.update("!bot 1", chat=456),
                       self.update(sent_at=self.now - 60), {"edited_message": self.update()["message"]}):
            await self.service.handle_update(update)
        self.assertEqual(self.service.state.session.phase, "active")

    async def test_first_selection_wins(self):
        await self.service.tick()
        await asyncio.gather(self.service.handle_update(self.update()),
                             self.service.handle_update(self.update("!bot 2")))
        self.assertEqual(self.service.state.session.selected_index, 0)
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 1)

    async def test_timeout_at_deadline_and_restart(self):
        await self.service.tick()
        deadline = self.service.state.session.deadline
        self.now += 10
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.service.state.session.deadline, deadline)
        self.now = deadline
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("Automatische Auswahl", self.telegram.messages[-1])
        self.assertEqual(len(self.api.filters), 1)

    async def test_late_command_cannot_win_race_with_timeout(self):
        await self.service.tick()
        self.now = self.service.state.session.deadline
        await self.service.handle_update(self.update())
        self.assertEqual(self.service.state.session.phase, "delivering")
        self.assertIn("Automatische Auswahl", self.service.state.session.outbox[0])

    async def test_vegetarian_quota_includes_seafood(self):
        self.service.state.reset_week(self.service.local_now().date())
        self.service.state.meat_recipes_chosen_this_week = 3
        await self.service.tick()
        self.assertEqual(self.api.filters, [True])
        await self.service.handle_update(self.update())
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 3)

    async def test_failed_details_delivery_does_not_reselect_or_recount(self):
        await self.service.tick()
        await self.service.handle_update(self.update())
        self.telegram.fail = True
        with self.assertRaises(APIError):
            await self.service.tick()
        self.service = self.build()
        self.telegram.fail = False
        await self.service.tick()
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 1)
        self.assertEqual(self.service.state.session.phase, "done")

    async def test_failed_announcement_recovers_same_dessert(self):
        self.telegram.fail = True
        with self.assertRaises(APIError):
            await self.service.tick()
        self.assertEqual(self.service.state.dessert_days_used_this_week, 1)
        self.service = self.build()
        self.telegram.fail = False
        self.now += 600
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)
        self.assertEqual(self.service.state.dessert_days_used_this_week, 1)
        self.assertEqual(self.service.state.session.deadline, self.now + 3600)

    async def test_complete_week_obeys_both_limits(self):
        for _ in range(7):
            await self.service.tick()
            await self.service.handle_update(self.update())
            await self.service.tick()
            self.now += 86400
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 3)
        self.assertEqual(self.service.state.dessert_days_used_this_week, 2)
        self.assertEqual(self.api.filters, [False] * 3 + [True] * 4)
        await self.service.tick()
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 0)

    async def test_dessert_never_exceeds_limit_and_sunday_forces(self):
        self.settings = Settings(recipes_per_day=2, dessert_days_per_week=0)
        self.service = self.build()
        await self.service.tick()
        self.assertFalse(self.service.state.session.dessert)
        self.now += 6 * 86400
        self.settings = Settings(recipes_per_day=2, dessert_days_per_week=2)
        self.service = self.build()
        await self.service.tick()
        self.assertTrue(self.service.state.session.dessert)

    async def test_old_week_recovery_does_not_charge_new_week(self):
        self.now += 6 * 86400
        await self.service.tick()
        self.now += 86400
        await self.service.tick()
        self.assertEqual(self.service.state.meat_recipes_chosen_this_week, 0)
        self.assertEqual(self.service.state.session.day, "2026-09-14")

    async def test_fetch_budget_persists_across_restart(self):
        self.api.fail = APIError("Spoonacular", 500)
        for _ in range(3):
            with self.assertRaises(APIError):
                await self.service.tick()
            self.service = self.build()
            self.now += 300
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 3)
        self.assertIsNone(self.service.state.session)
        self.assertEqual(self.service.state.dessert_days_used_this_week, 0)

    async def test_quota_failure_stops_paid_requests_for_day(self):
        self.api.fail = APIError("Spoonacular", 402)
        with self.assertRaises(APIError):
            await self.service.tick()
        self.now += 300
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_fetch_crossing_midnight_does_not_steal_next_menu(self):
        self.settings = Settings(start_time="23:59", recipes_per_day=2)
        self.now = datetime(2026, 9, 13, 23, 59, 55, tzinfo=self.settings.zone).timestamp()
        self.service = self.build()
        self.api.on_fetch = lambda: setattr(self, "now", self.now + 10)
        await self.service.tick()
        self.assertIsNone(self.service.state.session)
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_dst_repeated_hour_does_not_duplicate_menu(self):
        self.settings = Settings(start_time="02:30", active_window_minutes=10, recipes_per_day=2)
        self.now = datetime(2026, 10, 25, 2, 30, tzinfo=self.settings.zone).timestamp()
        self.service = self.build()
        await self.service.tick()
        self.now += 3600
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_partial_delivery_cursor_survives_restart(self):
        await self.service.tick()
        await self.service.handle_update(self.update())
        self.service.state.session.outbox = ["first", "second"]
        self.service.state.session.next_message = 1
        self.store.save(self.service.state)
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.telegram.messages[-1], "second")
        self.assertNotIn("first", self.telegram.messages)

    async def test_startup_honors_retry_after(self):
        self.telegram.call = AsyncMock(side_effect=[APIError("Telegram", 429, 90), True])
        self.service.scheduler = AsyncMock()
        self.service.listen = AsyncMock()
        with patch("recipe_bot.service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await self.service.run()
        sleep.assert_awaited_once_with(90)
        self.assertEqual(self.telegram.call.await_count, 2)


class UnitTests(unittest.TestCase):
    def test_cli_test_mode_sends_message_without_creating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            credentials = Credentials("123:secret", -123, "secret-key")
            with patch("recipe_bot.__main__.load_config", return_value=(Settings(), credentials)), \
                 patch("recipe_bot.__main__.run_test_message", new_callable=AsyncMock) as send:
                main(["--state", str(state_path), "--send-test-message"])
            send.assert_awaited_once_with(credentials)
            self.assertFalse(state_path.exists())

    def test_test_message_content(self):
        telegram = FakeTelegram()
        asyncio.run(send_test_message(telegram))
        self.assertEqual(telegram.messages, [TEST_MESSAGE])

    def test_config_validation(self):
        for kwargs in ({"recipes_per_day": 0}, {"recipes_per_day": True},
                       {"start_time": "8:00"}, {"active_window_minutes": 0},
                       {"meat_days_per_week": 8}, {"dessert_days_per_week": -1},
                       {"trigger_codeword": "two words"}, {"language": "de"},
                       {"interaction_language": "fr"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_year_aware_week_reset(self):
        self.assertEqual(week_key(datetime(2027, 1, 1).date()), "2026-W53")
        state = State(week="2025-W01", meat_recipes_chosen_this_week=3)
        self.assertTrue(state.reset_week(datetime(2026, 1, 1).date()))
        self.assertEqual(state.meat_recipes_chosen_this_week, 0)
        self.assertFalse(state.reset_week(datetime(2026, 1, 2).date()))

    def test_state_roundtrip_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            store.save(State(week="2026-W37", meat_recipes_chosen_this_week=2))
            self.assertEqual(store.load().meat_recipes_chosen_this_week, 2)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.load()

    def test_failed_atomic_replace_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save(State(meat_recipes_chosen_this_week=1))
            with patch("recipe_bot.state.os.replace", side_effect=OSError), self.assertRaises(OSError):
                store.save(State(meat_recipes_chosen_this_week=2))
            self.assertEqual(store.load().meat_recipes_chosen_this_week, 1)

    def test_partial_state_cannot_reset_quotas(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"version": 1, "week": "2026-W37"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                StateStore(path).load()

    def test_html_and_long_message_format(self):
        self.assertEqual(plain_text("<ol><li>Mix &amp; stir</li><li>Cook</li></ol>"), "Mix & stir\nCook")
        chunks = split_message("\U0001f600" * 5000)
        self.assertEqual("".join(chunks), "\U0001f600" * 5000)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in chunks))
        self.assertIn("Dessert today: NO", summary([recipe()], False, "en", "!bot", 60)[0])
        self.assertIn("Ingredients:", details(recipe(), "en", False)[0])

    def test_parser_literal_trigger(self):
        self.assertEqual(parse_selection("!b.t 2", "!b.t", 2), 1)
        for text in ("!bat 2", " !b.t 2", "!b.t 0", "!b.t -1", "!b.t 2more", "!b.t 9999"):
            self.assertIsNone(parse_selection(text, "!b.t", 2))

    def test_credentials_not_in_repr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "-123",
                                           "SPOONACULAR_API_KEY": "secret-key"}):
                _, credentials = load_config(path, Path(directory) / ".env")
            self.assertNotIn("secret", repr(credentials))

    def test_lara_credentials_are_optional_but_must_be_a_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            values = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "-123",
                      "SPOONACULAR_API_KEY": "spoon-key"}
            with patch.dict("os.environ", values, clear=True):
                _, credentials = load_config(path, Path(directory) / ".env")
            self.assertIsNone(credentials.lara_access_key_id)
            with patch.dict("os.environ", {**values, "LARA_ACCESS_KEY_ID": "lara-id"}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(path, Path(directory) / ".env")
            with patch.dict("os.environ", {**values, "LARA_ACCESS_KEY_ID": "lara-id",
                                            "LARA_ACCESS_KEY_SECRET": "lara-secret"}, clear=True):
                _, credentials = load_config(path, Path(directory) / ".env")
            self.assertEqual(credentials.lara_access_key_id, "lara-id")
            self.assertNotIn("lara-secret", repr(credentials))


class APITests(unittest.IsolatedAsyncioTestCase):
    def response_recipe(self, vegetarian=True):
        return {"id": 1, "title": "Rice", "vegetarian": vegetarian,
                "extendedIngredients": [{"original": "100 g rice"}],
                "instructions": "<p>Cook rice.</p>", "readyInMinutes": 20}

    async def test_current_query_and_cached_details(self):
        def handle(request):
            self.assertEqual(request.url.params["include-tags"], "main course,vegetarian")
            self.assertNotIn("language", request.url.params)
            self.assertNotIn("apiKey", request.url.params)
            self.assertEqual(request.headers["x-api-key"], "secret")
            return httpx.Response(200, json={"recipes": [self.response_recipe()]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            recipes = await Spoonacular(client, "secret").recipes(1, True)
        self.assertEqual(recipes[0].instructions, ["Cook rice."])

    async def test_unsafe_missing_or_incomplete_recipes_rejected(self):
        invalid = [self.response_recipe(False), {**self.response_recipe(), "vegetarian": None},
                   {**self.response_recipe(), "instructions": ""}]
        for item in invalid:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"recipes": [item]})
            )) as client:
                with self.assertRaises(APIError):
                    await Spoonacular(client, "secret").recipes(1, True)

    async def test_telegram_plain_text_and_rate_limit(self):
        def handle(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["chat_id"], -123)
            self.assertNotIn("parse_mode", payload)
            return httpx.Response(429, json={"parameters": {"retry_after": 90}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(APIError) as raised:
                await Telegram(client, "secret", -123).send("test")
        self.assertEqual(raised.exception.retry_after, 90)
        self.assertNotIn("secret", str(raised.exception))

    async def test_network_errors_hide_request_urls(self):
        def handle(request):
            raise httpx.ConnectError("https://api.telegram.org/botsecret", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(APIError) as raised:
                await Telegram(client, "secret", -123).send("test")
        self.assertNotIn("secret", str(raised.exception))

    async def test_lara_translates_recipe_content(self):
        class Translator:
            def translate(self, values, **kwargs):
                if kwargs != {"source": "en-US", "target": "de-DE", "content_type": "text/plain",
                              "no_trace": True}:
                    raise AssertionError("Unexpected Lara translation request")
                return type("Result", (), {"translation": [f"Deutsch {value}" for value in values]})()

        translated = await Lara("id", "secret", Translator()).translate_recipes([recipe()])
        self.assertEqual(translated[0].title, "Deutsch Recipe 1")
        self.assertEqual(translated[0].ingredients, ["Deutsch 100 g rice"])
        self.assertEqual(translated[0].instructions, ["Deutsch Cook rice."])

    async def test_lara_rejects_invalid_or_failed_translations(self):
        class Translator:
            def translate(self, values, **kwargs):
                return type("Result", (), {"translation": ["only one value"]})()

        with self.assertRaises(APIError) as raised:
            await Lara("id", "secret", Translator()).translate_recipes([recipe()])
        self.assertEqual(str(raised.exception), "Lara request failed")


if __name__ == "__main__":
    unittest.main()
