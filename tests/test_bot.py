import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

from recipe_bot.api import APIError, Spoonacular, Telegram
from recipe_bot.__main__ import TEST_MESSAGE, main, send_test_message
from recipe_bot.config import Credentials, Settings, load_config
from recipe_bot.recipes import (Recipe, details, is_resend_command, is_restart_command, is_skip_command,
                                parse_selection, plain_text, skipped, split_message, summary)
from recipe_bot.service import RecipeService
from recipe_bot.state import Session, State, StateStore, week_key


def recipe(identity=1, vegetarian=True):
    return Recipe(identity, f"Recipe {identity}", vegetarian, ["100 g rice"], ["Cook rice."], 20)


class FakeTelegram:
    chat_id = -123

    def __init__(self):
        self.messages = []
        self.photos = []
        self.deliveries = []
        self.fail = False
        self.fail_messages = False
        self.fail_photos = False

    async def send(self, text):
        if self.fail or self.fail_messages:
            raise APIError("Telegram")
        self.messages.append(text)
        self.deliveries.append(("message", text))

    async def send_photo(self, image_url, caption):
        if self.fail or self.fail_photos:
            raise APIError("Telegram")
        self.photos.append((image_url, caption))
        self.deliveries.append(("photo", image_url, caption))


class FakeRecipes:
    def __init__(self):
        self.filters = []
        self.fail = None
        self.on_fetch = None

    async def recipes(self, count, vegetarian_only, extra_include_tags=()):
        self.filters.append(vegetarian_only)
        if self.on_fetch:
            self.on_fetch()
        if self.fail:
            raise self.fail
        return [recipe(index + 1, vegetarian_only or index != 0) for index in range(count)]


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

    async def resend(self):
        await self.service.handle_update(self.update("!bot resend"))
        worker = asyncio.create_task(self.service.resend_worker())
        try:
            await asyncio.wait_for(self.service.resends.join(), timeout=1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_schedule_and_selection(self):
        self.now -= 1
        await self.service.tick()
        self.assertIsNone(self.service.state.session)
        self.now += 1
        await self.service.tick()
        self.assertIn("Dessert heute: 😊", self.telegram.messages[0])
        self.assertEqual(self.service.state.session.phase, "active")
        await self.service.handle_update(self.update())
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("100 g rice", self.telegram.messages[-1])
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_recipe_failure_sends_final_alert(self):
        self.api.fail = APIError("Spoonacular", 500)
        with self.assertRaises(APIError):
            await self.service.tick()
        self.assertEqual(self.telegram.messages, [
            "Rezeptabruf fehlgeschlagen (HTTP/API 500). Keine weiteren Versuche heute."
        ])

    async def test_exhausted_recipe_budget_sends_final_alert(self):
        self.api.fail = APIError("Spoonacular", 402)
        with self.assertRaises(APIError):
            await self.service.tick()
        self.assertEqual(self.telegram.messages, [
            "Rezeptabruf fehlgeschlagen (HTTP/API 402). Keine weiteren Versuche heute."
        ])

    async def test_english_alert_and_sunday_message(self):
        self.settings = Settings(recipes_per_day=2, interaction_language="en", sunday_leftovers=True)
        self.service = self.build()
        await self.service.notify_fetch_failure(APIError("Spoonacular", 500))
        self.assertEqual(self.telegram.messages[-1],
                         "Spoonacular request failed (HTTP/API 500). No more attempts today.")
        self.now = datetime(2026, 9, 13, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        await self.service.tick()
        self.assertEqual(self.telegram.messages[-1],
                         "Today we cook with leftovers from the fridge or order something :)")

    async def test_alert_failure_does_not_replace_recipe_failure(self):
        self.api.fail = APIError("Spoonacular", 500)
        self.telegram.fail = True
        with self.assertRaisesRegex(APIError, "Spoonacular request failed"):
            await self.service.tick()
        self.assertIsNone(self.service.state.session)
        self.assertEqual(self.service.state.fetch_attempts, 1)

    async def test_filters_ignore_noise_and_stale_commands(self):
        await self.service.tick()
        for update in (self.update("chat"), self.update("!bot 00"), self.update("!bot 0 extra"),
                       self.update("!bot 3"),
                       self.update("!bot 1 extra"), self.update("!bot 1", chat=456),
                       self.update(sent_at=self.now - 60), {"edited_message": self.update()["message"]}):
            await self.service.handle_update(update)
        self.assertEqual(self.service.state.session.phase, "active")

    async def test_skip_command_ends_today_without_recipe(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 0"))
        self.assertEqual(self.service.state.session.phase, "skipping")
        self.assertIsNone(self.service.state.session.selected_index)
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "skipped")
        self.assertEqual(self.telegram.messages[-1], "Keine Auswahl fuer heute. Bis morgen.")
        self.assertEqual(len(self.api.filters), 1)
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)

    async def test_resend_rebuilds_current_menu_without_fetching(self):
        await self.service.tick()
        original_menu = self.telegram.messages.copy()
        await self.resend()
        self.assertEqual(self.telegram.messages, original_menu * 2)
        self.assertEqual(len(self.api.filters), 1)
        self.assertEqual(self.service.state.session.phase, "active")

    async def test_resend_failure_does_not_block_next_selection(self):
        self.settings = Settings(recipes_per_day=2)
        self.service = self.build()
        await self.service.tick()
        sleeping = asyncio.Event()
        release = asyncio.Event()

        async def sleep(delay):
            self.assertEqual(delay, 90)
            sleeping.set()
            await release.wait()

        self.telegram.send = AsyncMock(side_effect=[APIError("Telegram", 429, 90), None])
        async def updates(offset):
            if offset is None:
                return [dict(self.update("!bot resend"), update_id=10)]
            if offset == 11:
                await sleeping.wait()
                return [dict(self.update("!bot 2"), update_id=11)]
            raise asyncio.CancelledError

        self.telegram.updates = AsyncMock(side_effect=updates)
        with patch("recipe_bot.service.asyncio.sleep", side_effect=sleep):
            worker = asyncio.create_task(self.service.resend_worker())
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(self.service.listen(), timeout=1)
                self.assertEqual(self.telegram.updates.call_args.args, (12,))
                self.assertEqual(self.store.load().session.selected_index, 1)
                self.assertEqual(self.telegram.send.await_count, 1)
                with self.assertRaises(APIError):
                    await self.service.tick()
                self.assertEqual(self.telegram.send.await_count, 1)
                self.now += 90
                release.set()
                await asyncio.wait_for(self.service.resends.join(), timeout=1)
                self.assertEqual(self.telegram.send.await_count, 2)
                self.assertIn("Heutige Rezeptauswahl", self.telegram.send.call_args.args[0])
                self.assertEqual(self.store.load().session.selected_index, 1)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_inflight_resend_does_not_hold_selection_lock(self):
        await self.service.tick()
        started = asyncio.Event()
        release = asyncio.Event()

        async def send(text):
            started.set()
            await release.wait()

        self.telegram.send = AsyncMock(side_effect=send)
        await self.service.handle_update(self.update("!bot resend"))
        worker = asyncio.create_task(self.service.resend_worker())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.wait_for(self.service.handle_update(self.update("!bot 2")), timeout=1)
            with self.assertRaises(APIError):
                await asyncio.wait_for(self.service.tick(), timeout=1)
            self.assertEqual(self.store.load().session.selected_index, 1)
            self.assertEqual(self.telegram.send.await_count, 1)
            release.set()
            await asyncio.wait_for(self.service.resends.join(), timeout=1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_scheduled_failure_blocks_resends_photos_and_alerts(self):
        self.telegram.send = AsyncMock(side_effect=[APIError("Telegram", 429, 90), None])
        with self.assertRaises(APIError):
            await self.service.tick()
        await self.service.notify_fetch_failure(APIError("Spoonacular", 500))
        with self.assertRaises(APIError):
            await self.service.send_telegram(self.telegram.send, "resend")
        with self.assertRaises(APIError):
            await self.service.send_telegram(self.telegram.send_photo, "url", "caption")
        self.assertEqual(self.telegram.send.await_count, 1)
        self.assertEqual(self.telegram.photos, [])
        self.service.record_telegram_failure(APIError("Telegram", 429, 5))
        self.assertEqual(self.service.telegram_retry_at, self.now + 90)
        self.now += 90
        await self.service.tick()
        self.assertEqual(self.telegram.send.await_count, 2)

    async def test_selection_resolves_during_delivery_cooldown(self):
        await self.service.tick()
        self.service.retry_at = self.now + 90
        await self.service.handle_update(self.update("!bot 1"))
        self.assertEqual(self.store.load().session.phase, "delivering")

    async def test_resend_retry_keeps_completed_message_cursor(self):
        self.service.enqueue_resend(["first", "second"])
        self.telegram.send = AsyncMock(side_effect=[None, APIError("Telegram", 429, 90), None])

        async def sleep(delay):
            self.now += delay

        with patch("recipe_bot.service.asyncio.sleep", side_effect=sleep):
            worker = asyncio.create_task(self.service.resend_worker())
            try:
                await asyncio.wait_for(self.service.resends.join(), timeout=1)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual([call.args[0] for call in self.telegram.send.call_args_list],
                         ["first", "second", "second"])

    async def test_resend_queue_is_bounded(self):
        for _ in range(self.service.resends.maxsize + 1):
            await self.service.handle_update(self.update("!bot resend"))
        self.assertEqual(self.service.resends.qsize(), self.service.resends.maxsize)

    async def test_resend_after_selection_preserves_completed_session(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        selected_messages = self.telegram.messages[1:].copy()
        await self.resend()
        self.assertEqual(self.telegram.messages[-len(selected_messages):], selected_messages)
        self.assertIn("Ausgewaehltes Rezept: Recipe 1", self.telegram.messages[-1])
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertEqual(self.service.state.session.selected_index, 0)
        self.assertEqual(len(self.api.filters), 1)

    async def test_selected_recipe_photo_precedes_details_and_resends(self):
        await self.service.tick()
        self.service.state.session.recipes[0].image_url = "https://images.example/recipe.jpg"
        self.telegram.deliveries.clear()
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        self.assertEqual(self.telegram.deliveries[0], (
            "photo", "https://images.example/recipe.jpg", "Recipe 1"
        ))
        self.assertIn("Ausgewaehltes Rezept: Recipe 1", self.telegram.deliveries[1][1])
        await self.resend()
        self.assertEqual(self.telegram.deliveries[-2], (
            "photo", "https://images.example/recipe.jpg", "Recipe 1"
        ))
        self.assertIn("Ausgewaehltes Rezept: Recipe 1", self.telegram.deliveries[-1][1])

    async def test_recipe_without_image_sends_details_only(self):
        await self.service.tick()
        self.telegram.deliveries.clear()
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        self.assertFalse(self.telegram.photos)
        self.assertEqual(self.telegram.deliveries[0][0], "message")

    async def test_restart_after_photo_delivery_does_not_repeat_photo(self):
        await self.service.tick()
        self.service.state.session.recipes[0].image_url = "https://images.example/recipe.jpg"
        await self.service.handle_update(self.update("!bot 1"))
        self.telegram.fail_messages = True
        with self.assertRaises(APIError):
            await self.service.tick()
        self.assertEqual(len(self.telegram.photos), 1)
        self.assertTrue(self.service.state.session.photo_delivered)
        self.service = self.build()
        self.telegram.fail_messages = False
        await self.service.tick()
        self.assertEqual(len(self.telegram.photos), 1)
        self.assertEqual(self.service.state.session.phase, "done")

    async def test_rejected_photo_delivers_text_and_is_not_retried(self):
        await self.service.tick()
        self.service.state.session.recipes[0].image_url = "https://images.example/recipe.jpg"
        self.telegram.send_photo = AsyncMock(side_effect=APIError("Telegram sendPhoto", 400))
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertTrue(self.service.state.session.photo_skipped)
        self.assertIn("Ausgewaehltes Rezept: Recipe 1", self.telegram.messages[-1])
        self.service = self.build()
        await self.resend()
        self.assertEqual(self.telegram.send_photo.await_count, 1)

    async def test_resend_after_skip_preserves_skip_confirmation(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 0"))
        await self.service.tick()
        await self.resend()
        self.assertEqual(self.telegram.messages[-1], "Keine Auswahl fuer heute. Bis morgen.")
        self.assertEqual(self.service.state.session.phase, "skipped")
        self.assertEqual(len(self.api.filters), 1)

    async def test_resend_for_old_menu_reports_no_current_menu(self):
        await self.service.tick()
        self.now += 86400
        await self.resend()
        self.assertEqual(self.telegram.messages[-1], "Heute ist keine Rezeptauswahl verfuegbar.")
        self.assertEqual(len(self.api.filters), 1)

    async def test_failed_skip_delivery_recovers_after_restart(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 0"))
        self.telegram.fail = True
        with self.assertRaises(APIError):
            await self.service.tick()
        self.service = self.build()
        self.telegram.fail = False
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "skipped")
        self.assertEqual(self.telegram.messages[-1], "Keine Auswahl fuer heute. Bis morgen.")

    async def test_first_selection_wins(self):
        await self.service.tick()
        await asyncio.gather(self.service.handle_update(self.update()),
                             self.service.handle_update(self.update("!bot 2")))
        self.assertEqual(self.service.state.session.selected_index, 0)

    async def test_no_automatic_timeout_stays_active_indefinitely(self):
        await self.service.tick()
        self.now += 100000
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "active")
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertEqual(len(self.api.filters), 1)
        await self.service.handle_update(self.update("!bot 1"))
        self.assertEqual(self.service.state.session.selected_index, 0)

    async def test_restart_command_resends_menu_for_new_selection(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertEqual(self.service.state.session.selected_index, 0)
        await self.service.handle_update(self.update("!bot restart"))
        self.assertEqual(self.service.state.session.phase, "announcing")
        self.assertIsNone(self.service.state.session.selected_index)
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "active")
        self.assertEqual(self.telegram.messages[-1], self.telegram.messages[0])
        await self.service.handle_update(self.update("!bot 2"))
        self.assertEqual(self.service.state.session.selected_index, 1)
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("Recipe 2", self.telegram.messages[-1])
        self.assertEqual(len(self.api.filters), 1)

    async def test_restart_after_skip_allows_new_selection(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 0"))
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "skipped")
        await self.service.handle_update(self.update("!bot restart"))
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "active")
        await self.service.handle_update(self.update("!bot 1"))
        self.assertEqual(self.service.state.session.selected_index, 0)

    async def test_restart_command_ignored_when_nothing_to_restart(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot restart"))
        self.assertEqual(self.service.state.session.phase, "active")
        self.assertIsNone(self.service.state.session.selected_index)

    async def test_restart_command_ignored_for_old_menu(self):
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 1"))
        await self.service.tick()
        self.now += 86400
        await self.service.handle_update(self.update("!bot restart"))
        self.assertEqual(self.service.state.session.phase, "done")

    async def test_fixed_vegetarian_day_includes_seafood(self):
        self.settings = Settings(recipes_per_day=2, vegetarian_days=["monday"])
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.api.filters, [True])
        await self.service.handle_update(self.update())
        self.assertTrue(all(recipe.vegetarian for recipe in self.service.state.session.recipes))

    async def test_sunday_leftovers_sends_message_without_fetching(self):
        self.now = datetime(2026, 9, 13, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.settings = Settings(recipes_per_day=2, sunday_leftovers=True)
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(self.telegram.messages, [
            "Heute kochen wir mit Resten aus dem Kühlschrank oder bestellen etwas :)"
        ])
        self.assertEqual(self.api.filters, [])
        self.assertIsNone(self.service.state.session)
        self.assertEqual(self.service.state.sunday_leftovers_day, "2026-09-13")

    async def test_sunday_leftovers_is_delivered_once_across_restart(self):
        self.now = datetime(2026, 9, 13, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.settings = Settings(recipes_per_day=2, sunday_leftovers=True)
        self.service = self.build()
        await self.service.tick()
        self.service = self.build()
        await self.service.tick()
        self.assertEqual(len(self.telegram.messages), 1)
        self.assertEqual(self.api.filters, [])

    async def test_sunday_leftovers_disabled_sends_normal_menu(self):
        self.now = datetime(2026, 9, 13, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.settings = Settings(recipes_per_day=2, sunday_leftovers=False)
        self.service = self.build()
        await self.service.tick()
        self.assertIn("Heutige Rezeptauswahl:", self.telegram.messages[0])
        self.assertEqual(len(self.api.filters), 1)

    async def test_failed_details_delivery_does_not_reselect(self):
        await self.service.tick()
        await self.service.handle_update(self.update())
        self.telegram.fail = True
        with self.assertRaises(APIError):
            await self.service.tick()
        self.service = self.build()
        self.telegram.fail = False
        await self.service.tick()
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
        self.assertEqual(self.service.state.session.phase, "active")

    async def test_complete_week_uses_fixed_vegetarian_days(self):
        self.settings = Settings(recipes_per_day=2, vegetarian_days=["monday", "friday"])
        self.service = self.build()
        for _ in range(7):
            await self.service.tick()
            await self.service.handle_update(self.update())
            await self.service.tick()
            self.now += 86400
        self.assertEqual(self.service.state.dessert_days_used_this_week, 2)
        self.assertEqual(self.api.filters, [True, False, False, False, True, False, False])
        await self.service.tick()
        self.assertEqual(self.api.filters[-1], True)

    async def test_leftovers_week_meets_each_feasible_dessert_target(self):
        for target in range(7):
            with self.subTest(target=target):
                self.now = datetime(2026, 9, 7, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
                self.settings = Settings(recipes_per_day=2, sunday_leftovers=True,
                                         dessert_days_per_week=target)
                self.store = StateStore(Path(self.tmp.name) / f"state-{target}.json")
                self.service = self.build()
                # Defer optional desserts so every forced-allocation boundary is exercised.
                self.service.rng.random = lambda: 0.999
                fetch_count = len(self.api.filters)
                for day in range(7):
                    await self.service.tick()
                    if day < 6:
                        await self.service.handle_update(self.update())
                        await self.service.tick()
                    self.now += 86400
                self.assertEqual(self.service.state.dessert_days_used_this_week, target)
                self.assertEqual(len(self.api.filters) - fetch_count, 6)
                self.assertEqual(self.telegram.messages[-1],
                                 "Heute kochen wir mit Resten aus dem Kühlschrank oder bestellen etwas :)")

    async def test_dessert_never_exceeds_limit_and_sunday_forces(self):
        self.settings = Settings(recipes_per_day=2, dessert_days_per_week=0)
        self.service = self.build()
        await self.service.tick()
        self.assertFalse(self.service.state.session.dessert)
        await self.service.handle_update(self.update("!bot 0"))
        await self.service.tick()
        self.now += 6 * 86400
        self.settings = Settings(recipes_per_day=2, dessert_days_per_week=2)
        self.service = self.build()
        await self.service.tick()
        self.assertTrue(self.service.state.session.dessert)

    async def test_old_week_recovery_does_not_charge_new_week(self):
        self.now += 6 * 86400
        await self.service.tick()
        await self.service.handle_update(self.update("!bot 0"))
        await self.service.tick()
        self.now += 86400
        await self.service.tick()
        self.assertEqual(self.service.state.session.day, "2026-09-14")

    async def test_fetch_budget_persists_across_restart(self):
        self.api.fail = APIError("Spoonacular", 500)
        with self.assertRaises(APIError):
            await self.service.tick()
        self.service = self.build()
        self.now += 300
        await self.service.tick()
        self.assertEqual(len(self.api.filters), 1)
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
        self.settings = Settings(start_time="02:30", recipes_per_day=2)
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
        self.service.resend_worker = AsyncMock()
        with patch("recipe_bot.service.asyncio.sleep", new_callable=AsyncMock) as sleep:
            await self.service.run()
        sleep.assert_awaited_once_with(90)
        self.assertEqual(self.telegram.call.await_count, 2)
        self.assertEqual(self.service.telegram_retry_at, self.now + 90)

    async def test_polling_failure_blocks_sends_until_cooldown(self):
        self.telegram.updates = AsyncMock(side_effect=APIError("Telegram getUpdates", 429, 90))
        self.telegram.send = AsyncMock()
        with patch("recipe_bot.service.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.listen()
        with self.assertRaises(APIError):
            await self.service.send_telegram(self.telegram.send, "test")
        self.telegram.send.assert_not_awaited()
        self.now += 90
        await self.service.send_telegram(self.telegram.send, "test")
        self.telegram.send.assert_awaited_once_with("test")

    async def test_shutdown_cancels_resend_worker(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        self.telegram.call = AsyncMock(return_value=True)
        self.service.scheduler = AsyncMock()
        self.service.listen = AsyncMock()
        self.service.resend_worker = worker
        task = asyncio.create_task(self.service.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(stopped.is_set())

    async def test_listener_does_not_poll_outside_selection_window(self):
        self.telegram.updates = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.service.listen()
        self.telegram.updates.assert_awaited_once_with(None)


class UnitTests(unittest.TestCase):
    def test_cli_test_mode_sends_message_without_creating_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            credentials = Credentials("123:secret", -123, "secret-key")
            with patch("recipe_bot.__main__.load_config", return_value=(Settings(), credentials)), \
                 patch("recipe_bot.__main__.run_test_message", new_callable=AsyncMock) as send:
                main(["--state", str(state_path), "--send-test-message"])
            send.assert_awaited_once_with(credentials, "de")
            self.assertFalse(state_path.exists())

    def test_test_message_content(self):
        telegram = FakeTelegram()
        asyncio.run(send_test_message(telegram))
        self.assertEqual(telegram.messages, [TEST_MESSAGE])

    def test_config_validation(self):
        for kwargs in ({"recipes_per_day": 0}, {"recipes_per_day": True},
                        {"start_time": "8:00"},
                        {"vegetarian_days": ["monday", "monday"]},
                        {"vegetarian_days": ["Monday"]}, {"vegetarian_days": ["holiday"]},
                        {"vegetarian_days": "monday"}, {"dessert_days_per_week": -1},
                        {"trigger_codeword": "two words"}, {"language": "de"},
                        {"interaction_language": "fr"}, {"sunday_leftovers": 1},
                        {"sunday_leftovers": True, "dessert_days_per_week": 7},
                        {"additional_include_tags": ["italian", "italian"]},
                        {"additional_include_tags": ["Italian"]},
                        {"additional_include_tags": ["italian,vegan"]},
                        {"additional_include_tags": "italian"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_vegetarian_days_accepts_an_empty_json_list(self):
        self.assertEqual(Settings(vegetarian_days=[]).vegetarian_days, ())

    def test_additional_include_tags_accepts_valid_tags(self):
        settings = Settings(additional_include_tags=["italian", "gluten-free"])
        self.assertEqual(settings.additional_include_tags, ("italian", "gluten-free"))

    def test_year_aware_week_reset(self):
        self.assertEqual(week_key(datetime(2027, 1, 1).date()), "2026-W53")
        state = State(week="2025-W01", dessert_days_used_this_week=2)
        self.assertTrue(state.reset_week(datetime(2026, 1, 1).date()))
        self.assertEqual(state.dessert_days_used_this_week, 0)
        self.assertFalse(state.reset_week(datetime(2026, 1, 2).date()))

    def test_state_roundtrip_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            store.save(State(week="2026-W37", dessert_days_used_this_week=2))
            self.assertEqual(store.load().dessert_days_used_this_week, 2)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.load()

    def test_failed_atomic_replace_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save(State(dessert_days_used_this_week=1))
            with patch("recipe_bot.state.os.replace", side_effect=OSError), self.assertRaises(OSError):
                store.save(State(dessert_days_used_this_week=2))
            self.assertEqual(store.load().dessert_days_used_this_week, 1)

    def test_legacy_state_migrates_without_meat_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            legacy = {"week": "2026-W37", "meat_recipes_chosen_this_week": 2,
                       "dessert_days_used_this_week": 1, "session": None, "fetch_day": "",
                       "fetch_attempts": 0, "fetch_retry_at": 0, "version": 1}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = store.load()
            self.assertEqual(state.version, 10)
            self.assertEqual(state.dessert_days_used_this_week, 1)
            self.assertNotIn("meat_recipes_chosen_this_week", json.loads(path.read_text(encoding="utf-8")))

    def test_version_two_state_migrates_for_sunday_leftovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            legacy = {"week": "2026-W37", "dessert_days_used_this_week": 1, "session": None,
                      "fetch_day": "", "fetch_attempts": 0, "fetch_retry_at": 0, "version": 2}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.version, 10)
            self.assertEqual(state.sunday_leftovers_day, "")

    def test_version_three_session_migrates_photo_delivery_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "de", ["menu"])
            legacy = asdict(State(session=session))
            legacy["session"]["window_minutes"] = 60
            legacy["session"]["deadline"] = None
            legacy["version"] = 3
            del legacy["dashboard_pending"]
            del legacy["dashboard_updated_at"]
            del legacy["session"]["photo_delivered"]
            del legacy["session"]["photo_skipped"]
            del legacy["session"]["recipes"][0]["metric_ingredients"]
            for name in ("translated_titles", "translated_ingredients", "translated_instructions",
                         "translation_attempts", "translation_retry_at", "translation_deadline",
                         "translation_fallback", "selected_title"):
                del legacy["session"][name]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.version, 10)
            self.assertFalse(state.session.photo_delivered)
            self.assertFalse(state.session.photo_skipped)

    def test_version_four_session_migrates_photo_skip_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "de", ["menu"])
            legacy = asdict(State(session=session))
            legacy["session"]["window_minutes"] = 60
            legacy["session"]["deadline"] = None
            legacy["version"] = 4
            del legacy["dashboard_pending"]
            del legacy["dashboard_updated_at"]
            del legacy["session"]["photo_skipped"]
            del legacy["session"]["recipes"][0]["metric_ingredients"]
            for name in ("translated_titles", "translated_ingredients", "translated_instructions",
                         "translation_attempts", "translation_retry_at", "translation_deadline",
                         "translation_fallback", "selected_title"):
                del legacy["session"][name]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.version, 10)
            self.assertFalse(state.session.photo_skipped)

    def test_version_six_session_migrates_through_v8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "de", ["menu"])
            legacy = asdict(State(session=session))
            legacy["session"]["window_minutes"] = 60
            legacy["session"]["deadline"] = None
            legacy["version"] = 6
            del legacy["session"]["recipes"][0]["metric_ingredients"]
            for name in ("translated_titles", "translated_ingredients", "translated_instructions",
                         "translation_attempts", "translation_retry_at", "translation_deadline",
                         "translation_fallback", "selected_title"):
                del legacy["session"][name]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.version, 10)
            self.assertEqual(state.session.outbox, ["menu"])
            self.assertEqual(state.session.recipes[0].metric_ingredients, [])
            rewritten = json.loads(path.read_text(encoding="utf-8"))
            for name in ("window_minutes", "deadline", "selected_automatic"):
                self.assertNotIn(name, rewritten["session"])

    def test_version_seven_recipes_migrate_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = recipe()
            session = Session("2026-09-07", [original], False, "!bot", "en", ["menu"])
            legacy = asdict(State(session=session))
            legacy["version"] = 7
            del legacy["session"]["recipes"][0]["metric_ingredients"]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            store = StateStore(path)
            migrated = store.load()
            self.assertEqual(migrated.version, 10)
            self.assertEqual(migrated.session.recipes[0].ingredients, ["100 g rice"])
            self.assertEqual(migrated.session.recipes[0].metric_ingredients, [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["session"]["recipes"][0]
                             ["metric_ingredients"], [])
            self.assertEqual(store.load().session.recipes[0], migrated.session.recipes[0])

    def test_invalid_v7_recipe_does_not_rewrite_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "en", ["menu"])
            legacy = asdict(State(session=session))
            legacy["version"] = 7
            legacy["session"]["recipes"][0]["metric_ingredients"] = ["100 g rice"]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            original = path.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                StateStore(path).load()
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_metric_ingredients_survive_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            converted = Recipe.from_api({
                "id": 1, "title": "Carrots", "vegetarian": True,
                "extendedIngredients": [{"original": "2 cups chopped carrots", "measures": {
                    "metric": {"amount": 300, "unitShort": "g"}}}],
                "instructions": "Cook.",
            })
            session = Session("2026-09-07", [converted], False, "!bot", "en", ["menu"])
            store = StateStore(path)
            store.save(State(session=session))
            loaded = store.load().session.recipes[0]
            self.assertEqual(loaded.ingredients, ["2 cups chopped carrots"])
            self.assertEqual(loaded.metric_ingredients, ["300 g chopped carrots"])

    def test_recipe_metric_validation_and_v8_incomplete_state(self):
        for metric in ("100 g rice", [""], ["100 g rice", "200 g rice"], [3], None):
            with self.subTest(metric=metric), self.assertRaises(ValueError):
                Recipe(1, "Rice", True, ["100 g rice"], ["Cook."], metric_ingredients=metric)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "en", ["menu"])
            incomplete = asdict(State(session=session))
            del incomplete["session"]["recipes"][0]["metric_ingredients"]
            path.write_text(json.dumps(incomplete), encoding="utf-8")
            with self.assertRaises(ValueError):
                StateStore(path).load()

    def test_metric_ingredients_keep_originals_and_descriptive_tails(self):
        ingredients = [
            {"original": "2 cups finely chopped carrots", "measures": {
                "metric": {"amount": 300, "unitShort": "g"}}},
            {"original": "1/2 cup of fresh milk", "measures": {
                "metric": {"amount": 120.5, "unitShort": "ml"}}},
            {"original": "2 tbsp extra virgin olive oil", "measures": {
                "metric": {"amount": 30.0, "unitShort": "ml"}}},
            {"original": "2 Tbs. Dijon mustard", "measures": {
                "metric": {"amount": 30, "unitShort": "ml"}}},
            {"original": "1 onion, diced", "measures": {
                "metric": {"amount": 150, "unitShort": "g"}}},
            {"original": "salt to taste", "measures": {
                "metric": {"amount": 2, "unitShort": "g"}}},
        ]
        data = {"id": 1, "title": "Soup", "vegetarian": True,
                "extendedIngredients": ingredients, "instructions": "Simmer."}
        result = Recipe.from_api(data)
        self.assertEqual(result.ingredients, [item["original"] for item in ingredients])
        self.assertEqual(result.metric_ingredients, [
            "300 g finely chopped carrots", "120.5 ml of fresh milk",
            "30 ml extra virgin olive oil", "30 ml Dijon mustard", "1 onion, diced", "salt to taste",
        ])
        self.assertIn("2 cups finely chopped carrots", details(result, "en")[0])

    def test_untrusted_metric_values_fall_back_without_changing_english(self):
        cases = [
            (None, "2 cups rice"), ({"amount": 0, "unitShort": "g"}, "2 cups rice"),
            ({"amount": -1, "unitShort": "g"}, "2 cups rice"),
            ({"amount": float("inf"), "unitShort": "g"}, "2 cups rice"),
            ({"amount": float("nan"), "unitShort": "g"}, "2 cups rice"),
            ({"amount": True, "unitShort": "g"}, "2 cups rice"),
            ({"amount": "200", "unitShort": "g"}, "2 cups rice"),
            ({"amount": 200, "unitShort": "tbsp"}, "2 cups rice"),
            ({"amount": 200, "unitShort": "g extra"}, "2 cups rice"),
            ({"amount": 200, "unitShort": "g"}, "a cup rice"),
            ({"amount": 200, "unitShort": "g"}, "2 cups"),
        ]
        for metric, original in cases:
            with self.subTest(metric=metric, original=original):
                result = Recipe.from_api({
                    "id": 1, "title": "Rice", "vegetarian": True,
                    "extendedIngredients": [{"original": original, "measures": {"metric": metric}}],
                    "instructions": "Cook.",
                })
                self.assertEqual(result.ingredients, [original])
                self.assertEqual(result.metric_ingredients, [original])

    def test_partial_state_cannot_reset_counters(self):
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
        self.assertEqual(summary([recipe()], False, "de", "!bot"), [
            "Heutige Rezeptauswahl:\n\n"
            "1. Recipe 1\n\n"
            "Dessert heute: ☹️\n\n"
            "Auswahl: !bot <Nummer>\n"
            "!bot 0 fuer keine Auswahl"
        ])
        self.assertEqual(summary([recipe()], True, "en", "!bot"), [
            "Today's recipes:\n\n"
            "1. Recipe 1\n\n"
            "Dessert today: 😊\n\n"
            "Select: !bot <number>\n"
            "!bot 0 for no selection"
        ])
        self.assertIn("Ingredients:", details(recipe(), "en")[0])
        self.assertEqual(skipped("en"), ["No selection for today. See you tomorrow."])

    def test_parser_literal_trigger(self):
        self.assertEqual(parse_selection("!b.t 2", "!b.t", 2), 1)
        for text in ("!bat 2", " !b.t 2", "!b.t 0", "!b.t -1", "!b.t 2more", "!b.t 9999"):
            self.assertIsNone(parse_selection(text, "!b.t", 2))
        self.assertTrue(is_skip_command("!b.t 0", "!b.t"))
        for text in ("!b.t 00", "!b.t 0 ", "!b.t 0 extra", "!bat 0"):
            self.assertFalse(is_skip_command(text, "!b.t"))
        self.assertTrue(is_resend_command("!b.t resend", "!b.t"))
        for text in ("!b.t resend ", "!b.t resend now", "!bat resend"):
            self.assertFalse(is_resend_command(text, "!b.t"))
        self.assertTrue(is_restart_command("!b.t restart", "!b.t"))
        for text in ("!b.t restart ", "!b.t restart now", "!bat restart"):
            self.assertFalse(is_restart_command(text, "!b.t"))

    def test_credentials_not_in_repr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "-123",
                                           "SPOONACULAR_API_KEY": "secret-key"}):
                _, credentials = load_config(path, Path(directory) / ".env")
            self.assertNotIn("secret", repr(credentials))

class APITests(unittest.IsolatedAsyncioTestCase):
    def response_recipe(self, vegetarian=True):
        return {"id": 1, "title": "Rice", "vegetarian": vegetarian,
                "extendedIngredients": [{"original": "100 g rice"}],
                "instructions": "<p>Cook rice.</p>", "readyInMinutes": 20,
                "image": "https://images.example/rice.jpg"}

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
        self.assertEqual(recipes[0].image_url, "https://images.example/rice.jpg")

    async def test_extra_include_tags_are_appended(self):
        def handle(request):
            self.assertEqual(request.url.params["include-tags"], "main course,italian,gluten-free")
            return httpx.Response(200, json={"recipes": [self.response_recipe()]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await Spoonacular(client, "secret").recipes(1, False, ("italian", "gluten-free"))

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

    async def test_telegram_api_level_rate_limit(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "ok": False, "error_code": 429, "parameters": {"retry_after": 90}
            })
        )) as client:
            with self.assertRaises(APIError) as raised:
                await Telegram(client, "secret", -123).send("test")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.retry_after, 90)

    async def test_telegram_sends_photo_url_and_caption(self):
        def handle(request):
            self.assertTrue(request.url.path.endswith("/sendPhoto"))
            self.assertEqual(json.loads(request.content), {
                "chat_id": -123, "photo": "https://images.example/rice.jpg", "caption": "Rice"
            })
            return httpx.Response(200, json={"ok": True, "result": {}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await Telegram(client, "secret", -123).send_photo("https://images.example/rice.jpg", "Rice")

    async def test_telegram_errors_identify_operation_without_secrets(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"description": "Bad Request"})
        )) as client:
            with self.assertRaises(APIError) as raised:
                await Telegram(client, "secret", -123).updates(None)
        self.assertEqual(str(raised.exception), "Telegram getUpdates request failed (HTTP/API 400)")
        self.assertNotIn("secret", str(raised.exception))

    async def test_network_errors_hide_request_urls(self):
        def handle(request):
            raise httpx.ConnectError("https://api.telegram.org/botsecret", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(APIError) as raised:
                await Telegram(client, "secret", -123).send("test")
        self.assertNotIn("secret", str(raised.exception))

if __name__ == "__main__":
    unittest.main()
