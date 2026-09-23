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

from recipe_bot.api import APIError
from recipe_bot.config import Settings, load_config
from recipe_bot.service import RecipeService
from recipe_bot.state import Session, State, StateStore
from recipe_bot.translation import OllamaTranslator, metric_temperatures
from test_bot import FakeRecipes, FakeTelegram, recipe


class TranslatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_contract_and_numeric_validation(self):
        calls = []
        def handler(request):
            self.assertEqual(str(request.url), "http://ollama:11434/api/chat")
            payload = json.loads(request.content)
            calls.append(payload)
            self.assertEqual(payload["model"], "qwen3:4b")
            self.assertFalse(payload["stream"])
            self.assertFalse(payload["think"])
            if len(calls) == 1:
                self.assertEqual(payload["format"]["properties"]["translations"]["minItems"], 2)
                self.assertEqual(json.loads(payload["messages"][1]["content"]), {
                    "kind": "ingredient", "context": "Rice pudding", "texts": ["Rice", "100 g rice"]
                })
                content = {"translations": ["Reis", "100 g Reis"]}
            else:
                self.assertEqual(json.loads(payload["messages"][1]["content"])["translations"],
                                 ["Reis", "100 g Reis"])
                content = {"valid": True}
            return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {
                "content": json.dumps(content)
            }})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OllamaTranslator(client, "http://ollama:11434", "qwen3:4b", 900).translate(
                ["Rice", "100 g rice"], kind="ingredient", context="Rice pudding"
            )
        self.assertEqual(result, ["Reis", "100 g Reis"])
        self.assertEqual(len(calls), 2)

    async def test_invalid_or_partial_responses_and_errors_are_safe(self):
        responses = [
            {"done": True, "message": {"content": '{"translations":["Reis"]}'}},
            {"done": False, "message": {"content": '{"translations":["Reis","200 g Reis"]}'}},
            {"done": True, "message": {"content": '{"translations":["Reis","200 g Reis"]}'}},
            {"done": True, "message": {"content": 'private-recipe-text'}},
        ]
        for data in responses:
            with self.subTest(data=data):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=data)
                )) as client:
                    with self.assertRaises(APIError) as raised:
                        await OllamaTranslator(client, "http://ollama:11434", "model", 3).translate(
                            ["Rice", "100 g rice"]
                        )
                self.assertNotIn("private-recipe-text", str(raised.exception))
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"error": "private-recipe-text"})
        )) as client:
            with self.assertRaises(APIError) as raised:
                await OllamaTranslator(client, "http://ollama:11434", "model", 3).translate(["Rice"])
        self.assertNotIn("private-recipe-text", str(raised.exception))

    async def test_quality_check_rejects_invented_protein(self):
        responses = iter([{"translations": ["Fischfilets anbraten."]}, {"valid": False}])
        def handler(request):
            return httpx.Response(200, json={"done": True, "message": {
                "content": json.dumps(next(responses))
            }})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(APIError):
                await OllamaTranslator(client, "http://ollama:11434", "qwen3:4b", 900).translate(
                    ["Fry the ham fillets."]
                )

    def test_metric_temperatures_only_convert_explicit_fahrenheit(self):
        self.assertEqual(metric_temperatures("Bake at 350°F for 20 min."), "Bake at 175 °C for 20 min.")
        self.assertEqual(metric_temperatures("Heat to 400 degrees F for 5 min."),
                         "Heat to 205 °C for 5 min.")
        self.assertEqual(metric_temperatures("Heat to 200 °C for 5 min."),
                         "Heat to 200 °C for 5 min.")


class TranslationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(Path(self.tmp.name) / "state.json")
        self.now = datetime(2026, 9, 7, 8, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
        self.settings = Settings(recipes_per_day=2, translation_attempts=2)
        self.api = FakeRecipes()
        self.telegram = FakeTelegram()
        self.dashboard = AsyncMock()
        self.translator = AsyncMock()
        async def translate(texts, **kwargs):
            return [f"DE {text}" for text in texts]
        self.translator.translate.side_effect = translate
        self.service = self.build()

    def build(self, translator=None):
        return RecipeService(self.settings, self.store, self.api, self.telegram,
                             clock=lambda: self.now, rng=random.Random(1), dashboard=self.dashboard,
                             translator=self.translator if translator is None else translator)

    async def wait_for(self, predicate):
        async def run():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(run(), 2)

    async def start_worker(self):
        worker = asyncio.create_task(self.service.translation_worker())
        self.addAsyncCleanup(self.stop_worker, worker)
        return worker

    async def stop_worker(self, worker):
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def menu(self):
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "translating_menu")
        self.assertEqual(self.telegram.messages, [])
        worker = await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "active")
        return worker

    async def select(self):
        await self.service.handle_update({"message": {
            "chat": {"id": self.telegram.chat_id}, "text": "!bot 1", "date": int(self.now)
        }})

    async def test_no_translator_sends_english_without_waiting(self):
        self.service = RecipeService(self.settings, self.store, self.api, self.telegram,
                                     clock=lambda: self.now, dashboard=self.dashboard)
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "active")
        await self.select()
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("Recipe 1", self.telegram.messages[0])
        self.assertIn("100 g rice", self.telegram.messages[-1])
        self.assertEqual(self.store.load().dashboard_pending["payload"]["recipe"]["title"], "Recipe 1")
        self.translator.translate.assert_not_awaited()

    async def test_translated_menu_recipe_caption_dashboard_and_resend(self):
        await self.menu()
        self.assertIn("DE Recipe 1", self.telegram.messages[0])
        self.assertNotIn("DE 100 g rice", self.telegram.messages[0])
        self.service.state.session.recipes[0].image_url = "https://images.example/a.jpg"
        await self.select()
        self.assertEqual(self.store.load().session.phase, "translating_recipe")
        self.assertIsNone(self.store.load().dashboard_pending)
        await self.wait_for(lambda: self.service.state.session.phase == "delivering")
        await self.service.tick()
        self.assertEqual(self.telegram.photos[-1][1], "DE Recipe 1")
        self.assertIn("DE 100 g rice", self.telegram.messages[-1])
        self.assertIn("DE Cook rice.", self.telegram.messages[-1])
        pending = self.store.load().dashboard_pending
        self.assertEqual(pending["payload"]["recipe"]["title"], "DE Recipe 1")
        self.assertEqual(pending["payload"]["recipe"]["ingredients"], ["DE 100 g rice"])
        self.assertEqual(self.api.filters, [False])
        self.service = self.build()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertEqual(self.service.selected_recipe(self.service.state.session).title, "DE Recipe 1")
        self.assertEqual(self.service.state.dashboard_pending, pending)
        self.service.resend_menu(self.service.state.session)
        resend = asyncio.create_task(self.service.resend_worker())
        try:
            await asyncio.wait_for(self.service.resends.join(), 1)
        finally:
            resend.cancel()
            await asyncio.gather(resend, return_exceptions=True)
        self.assertEqual(self.telegram.photos[-1][1], "DE Recipe 1")
        self.assertIn("DE 100 g rice", self.telegram.messages[-1])

    async def test_metric_recipe_translates_in_context_without_changing_english_fallback(self):
        converted = recipe()
        converted.ingredients = ["1 cup flour"]
        converted.metric_ingredients = ["125 g flour"]
        converted.instructions = ["Bake at 350 degrees F for 20 minutes."]
        self.api.recipes = AsyncMock(return_value=[converted, recipe(2)])
        await self.menu()
        await self.select()
        await self.wait_for(lambda: self.service.state.session.phase == "delivering")
        translated = self.translator.translate.await_args_list[-1]
        self.assertEqual(translated.args[0], ["125 g flour", "Bake at 175 °C for 20 minutes."])
        self.assertEqual(translated.kwargs["kind"], "recipe")
        self.assertIn("flour", translated.kwargs["context"])
        await self.service.tick()
        self.assertIn("DE 125 g flour", self.telegram.messages[-1])
        self.assertIn("DE Bake at 175 °C", self.telegram.messages[-1])
        self.assertEqual(self.store.load().session.recipes[0].ingredients, ["1 cup flour"])

    async def test_long_recipe_batches_keep_ingredient_context(self):
        worker = await self.menu()
        await self.stop_worker(worker)
        selected = self.service.state.session.recipes[0]
        selected.ingredients = ["1 cup flour", "1 cup sugar"]
        selected.metric_ingredients = ["125 g flour", "200 g sugar"]
        selected.instructions = ["Mix flour. " * 310, "Add sugar."]
        self.store.save(self.service.state)
        await self.select()
        session = self.service.state.session
        field, texts, kind, context = self.service.translation_batch(session)
        self.assertEqual((field, texts, kind),
                         ("translated_ingredients", ["125 g flour", "200 g sugar"], "ingredient"))
        self.assertIn("125 g flour", context)
        session.translated_ingredients = ["125 g Mehl", "200 g Zucker"]
        field, texts, kind, context = self.service.translation_batch(session)
        self.assertEqual(field, "translated_instructions")
        self.assertEqual(texts, ["Mix flour. " * 310])
        self.assertEqual(kind, "instruction")
        self.assertIn("Recipe 1", context)

    async def test_failed_metric_translation_returns_exact_english_original(self):
        converted = recipe()
        converted.ingredients = ["1 cup flour"]
        converted.metric_ingredients = ["125 g flour"]
        converted.instructions = ["Bake at 350 degrees F for 20 minutes."]
        self.api.recipes = AsyncMock(return_value=[converted, recipe(2)])
        await self.menu()
        self.translator.translate.side_effect = APIError("Ollama translation validation")
        await self.select()
        await self.wait_for(lambda: self.service.state.session.translation_retry_at > self.now)
        self.now += 31
        self.service.translation_changed.set()
        self.now += 61
        self.service.translation_changed.set()
        await self.wait_for(lambda: self.service.state.session.phase == "delivering")
        await self.service.tick()
        self.assertIn("englische Original", self.telegram.messages[-2])
        self.assertIn("1 cup flour", self.telegram.messages[-1])
        self.assertIn("350 degrees F", self.telegram.messages[-1])

    async def test_menu_retries_then_english_and_no_extra_fetch(self):
        self.translator.translate.side_effect = APIError("Ollama translation")
        await self.service.tick()
        worker = await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.translation_retry_at > self.now)
        self.assertEqual(self.store.load().session.translation_attempts, 1)
        await self.stop_worker(worker)
        self.service = self.build()
        await self.start_worker()
        self.now += 31
        self.service.translation_changed.set()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        await self.service.tick()
        self.assertEqual(self.translator.translate.await_count, 2)
        self.assertIn("Recipe 1", self.telegram.messages[-1])
        self.assertIn("Rezepttitel bleiben Englisch", self.telegram.messages[0])
        self.assertEqual(len(self.api.filters), 1)

    async def test_recipe_fallback_uses_english_everywhere(self):
        await self.menu()
        self.translator.translate.side_effect = APIError("Ollama translation")
        await self.select()
        await self.wait_for(lambda: self.service.state.session.translation_retry_at > self.now)
        self.now += 31
        self.service.translation_changed.set()
        await self.wait_for(lambda: self.service.state.session.phase == "delivering")
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertTrue(self.service.state.session.translation_fallback)
        self.assertIn("englische Original", self.telegram.messages[-2])
        self.assertIn("Recipe 1", self.telegram.messages[-1])
        self.assertNotIn("DE Recipe 1", self.telegram.messages[-1])
        self.assertEqual(self.store.load().dashboard_pending["payload"]["recipe"]["title"], "Recipe 1")

    async def test_disabling_ollama_resolves_pending_menu_without_requests(self):
        await self.service.tick()
        self.service = RecipeService(self.settings, self.store, self.api, self.telegram,
                                     clock=lambda: self.now, dashboard=self.dashboard)
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        await self.service.tick()
        self.assertIn("Recipe 1", self.telegram.messages[-1])
        self.assertNotIn("Übersetzung derzeit", self.telegram.messages[-1])
        self.translator.translate.assert_not_awaited()

    async def test_disabling_ollama_resolves_pending_recipe_without_requests(self):
        worker = await self.menu()
        await self.stop_worker(worker)
        await self.select()
        self.service = RecipeService(self.settings, self.store, self.api, self.telegram,
                                     clock=lambda: self.now, dashboard=self.dashboard)
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "delivering")
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIn("Recipe 1", self.telegram.messages[-1])
        self.assertEqual(self.store.load().dashboard_pending["payload"]["recipe"]["title"], "Recipe 1")
        self.translator.translate.assert_awaited_once()

    async def test_changed_model_falls_back_instead_of_mixing_translations(self):
        await self.service.tick()
        self.service.state.session.translated_titles.append("DE Recipe 1")
        self.store.save(self.service.state)
        self.settings = Settings(recipes_per_day=2, translation_model="different:1b")
        self.service = self.build()
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        self.assertEqual(self.service.state.session.translated_titles, [])
        self.translator.translate.assert_not_awaited()

    async def test_restart_exhausted_budget_does_not_retry(self):
        await self.service.tick()
        session = self.service.state.session
        session.translation_attempts = self.settings.translation_attempts
        self.store.save(self.service.state)
        self.service = self.build()
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        self.translator.translate.assert_not_awaited()
        await self.service.tick()
        self.assertIn("Recipe 1", self.telegram.messages[-1])

    async def test_job_deadline_falls_back_without_requests(self):
        await self.service.tick()
        self.now = self.service.state.session.translation_deadline
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        self.translator.translate.assert_not_awaited()

    async def test_recovery_only_retranslates_missing_chunk(self):
        self.settings = Settings(recipes_per_day=6)
        self.service = self.build()
        started, release = asyncio.Event(), asyncio.Event()
        async def translate(texts, **kwargs):
            if texts == ["Recipe 6"]:
                started.set()
                await release.wait()
            return [f"DE {text}" for text in texts]
        self.translator.translate.side_effect = translate
        await self.service.tick()
        worker = await self.start_worker()
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(self.store.load().session.translated_titles), 5)
        await self.stop_worker(worker)
        self.translator = AsyncMock()
        async def remaining(texts, **kwargs):
            return [f"DE {text}" for text in texts]
        self.translator.translate.side_effect = remaining
        self.service = self.build()
        await self.start_worker()
        await self.wait_for(lambda: self.service.state.session.phase == "announcing")
        self.translator.translate.assert_awaited_once_with(["Recipe 6"], kind="title", context="")
        self.assertEqual(len(self.api.filters), 1)

    async def test_translation_does_not_hold_selection_lock(self):
        await self.menu()
        started, release = asyncio.Event(), asyncio.Event()
        async def translate(texts, **kwargs):
            started.set()
            await release.wait()
            return [f"DE {text}" for text in texts]
        self.translator.translate.side_effect = translate
        await self.select()
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(self.service.handle_update({"message": {
                "chat": {"id": self.telegram.chat_id}, "text": "!bot resend", "date": int(self.now)
            }}), 1)
            self.assertEqual(self.service.state.session.selected_index, 0)
            self.assertEqual(self.service.state.session.phase, "translating_recipe")
            await asyncio.wait_for(self.service.tick(), 1)
            self.assertIn("Übersetzung läuft", self.telegram.messages[-1])
        finally:
            release.set()


class TranslationConfigTests(unittest.TestCase):
    def test_optional_url_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            required = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "-123",
                        "SPOONACULAR_API_KEY": "spoon-key"}
            with patch.dict("os.environ", required, clear=True):
                settings, credentials = load_config(path, Path(directory) / ".env")
                self.assertIsNone(credentials.ollama_url)
                self.assertEqual(settings.translation_model, "qwen3:4b")
                self.assertEqual(settings.translation_request_timeout_seconds, 900)
                self.assertEqual(settings.translation_job_timeout_minutes, 60)
            with patch.dict("os.environ", {**required, "OLLAMA_URL": "http://ollama:11434"}, clear=True):
                _, credentials = load_config(path, Path(directory) / ".env")
                self.assertEqual(credentials.ollama_url, "http://ollama:11434")
            for url in ("ftp://ollama", "http://user:pass@ollama", "http://ollama/api/chat",
                        "http://ollama?key=secret"):
                with self.subTest(url=url), patch.dict("os.environ", {**required, "OLLAMA_URL": url}, clear=True):
                    with self.assertRaises(ValueError):
                        load_config(path, Path(directory) / ".env")
        for kwargs in ({"translation_model": ""}, {"translation_model": "bad model"},
                       {"translation_attempts": 0}, {"translation_request_timeout_seconds": 0},
                       {"translation_job_timeout_minutes": -1}, {"translation_language": "fr"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_v6_session_migration_preserves_outbox_and_rejects_partial_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            session = Session("2026-09-07", [recipe()], False, "!bot", "de", 60, ["menu"])
            legacy = asdict(State(session=session))
            legacy["version"] = 6
            del legacy["session"]["recipes"][0]["metric_ingredients"]
            for field in ("translated_titles", "translated_ingredients", "translated_instructions",
                          "translation_attempts", "translation_retry_at", "translation_deadline",
                          "selected_automatic", "translation_fallback", "selected_title", "translation_model"):
                del legacy["session"][field]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.version, 8)
            self.assertEqual(state.session.outbox, ["menu"])
            self.assertEqual(state.session.phase, "announcing")
            legacy["session"].pop("photo_skipped")
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with self.assertRaises(ValueError):
                StateStore(path).load()
