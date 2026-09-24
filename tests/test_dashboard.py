import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from recipe_bot.api import APIError
from recipe_bot.config import Settings, load_config
from recipe_bot.dashboard import Dashboard
from recipe_bot.service import RecipeService
from recipe_bot.state import State, StateStore
from test_bot import FakeRecipes, FakeTelegram


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(Path(self.tmp.name) / "state.json")
        self.now = 1790056800.0
        self.dashboard = AsyncMock()
        self.telegram = FakeTelegram()
        self.service = self.build()

    def build(self):
        return RecipeService(Settings(recipes_per_day=2), self.store, FakeRecipes(), self.telegram,
                             clock=lambda: self.now, dashboard=self.dashboard)

    async def select(self):
        await self.service.tick()
        await self.service.handle_update({"message": {
            "chat": {"id": self.telegram.chat_id}, "text": "!bot 1", "date": int(self.now)
        }})

    async def test_manual_selection_persisted_and_restart_delivers(self):
        await self.select()
        pending = self.store.load().dashboard_pending
        self.assertEqual(pending["payload"]["recipe"]["title"], "Recipe 1")
        self.service = self.build()
        await self.service.publish_dashboard()
        self.dashboard.publish.assert_awaited_once_with(pending)
        self.assertIsNone(self.store.load().dashboard_pending)
        self.assertEqual(self.store.load().dashboard_updated_at, pending["updated_at"])

    async def test_version_seven_pending_and_session_recipes_migrate(self):
        await self.select()
        path = self.store.path
        legacy = json.loads(path.read_text(encoding="utf-8"))
        pending = legacy["dashboard_pending"]
        legacy["version"] = 7
        for recipe in legacy["session"]["recipes"]:
            del recipe["metric_ingredients"]
        path.write_text(json.dumps(legacy), encoding="utf-8")
        migrated = self.store.load()
        self.assertEqual(migrated.version, 10)
        self.assertEqual(migrated.session.recipes[0].metric_ingredients, [])
        self.assertNotIn("metric_ingredients", migrated.dashboard_pending["payload"]["recipe"])
        self.assertEqual(migrated.dashboard_pending["updated_at"], pending["updated_at"])
        self.assertEqual(self.store.load().dashboard_pending, migrated.dashboard_pending)

    async def test_dashboard_pending_survives_a_later_skip(self):
        await self.select()
        pending = self.store.load().dashboard_pending
        self.now += 86400
        await self.service.tick()
        self.service.skip()
        self.assertEqual(self.store.load().dashboard_pending, pending)

    async def test_dashboard_failure_does_not_block_telegram(self):
        await self.select()
        self.dashboard.publish.side_effect = APIError("Home Dashboard")
        with self.assertRaises(APIError):
            await self.service.publish_dashboard()
        await self.service.tick()
        self.assertEqual(self.service.state.session.phase, "done")
        self.assertIsNotNone(self.store.load().dashboard_pending)
        self.assertIn("100 g rice", self.telegram.messages[-1])

    async def test_new_selection_survives_old_ack_and_clock_rollback(self):
        await self.select()
        old = self.service.state.dashboard_pending
        started, release = asyncio.Event(), asyncio.Event()

        async def publish(update):
            started.set()
            await release.wait()

        self.dashboard.publish.side_effect = publish
        task = asyncio.create_task(self.service.publish_dashboard())
        try:
            await started.wait()
            await self.service.tick()
            self.now += 86400
            await self.service.tick()
            self.now -= 86400 * 2
            self.service.resolve(1)
            newer = self.store.load().dashboard_pending
            self.assertGreater(datetime.fromisoformat(newer["updated_at"]),
                               datetime.fromisoformat(old["updated_at"]))
            release.set()
            await task
            self.assertEqual(self.store.load().dashboard_pending, newer)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_worker_retries_persisted_update(self):
        await self.select()
        done = asyncio.Event()
        attempts = 0

        async def publish(update):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise APIError("Home Dashboard", 503)
            done.set()

        self.dashboard.publish.side_effect = publish
        with patch("recipe_bot.service.asyncio.sleep", new_callable=AsyncMock):
            worker = asyncio.create_task(self.service.dashboard_worker())
            try:
                await asyncio.wait_for(done.wait(), 1)
                self.assertIsNone(self.store.load().dashboard_pending)
                self.assertEqual(attempts, 2)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_http_contract_and_redacted_errors(self):
        await self.select()
        pending = self.service.state.dashboard_pending

        def handler(request):
            self.assertEqual(request.method, "PUT")
            self.assertEqual(request.url.path, "/api/v1/state/recipe-bot/current-recipe")
            self.assertEqual(request.headers["Authorization"], "Bearer private-token")
            self.assertEqual(json.loads(request.content), pending)
            return httpx.Response(200, json={"applied": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await Dashboard(client, "http://home-dashboard-api:8000/", "private-token").publish(pending)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"applied": False})
        )) as client:
            with self.assertRaises(APIError):
                await Dashboard(client, "http://home-dashboard-api:8000", "private-token").publish(pending)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"detail": "private-token"})
        )) as client:
            with self.assertRaises(APIError) as raised:
                await Dashboard(client, "http://home-dashboard-api:8000", "private-token").publish(pending)
        self.assertNotIn("private-token", str(raised.exception))


class ConfigStateTests(unittest.TestCase):
    def test_optional_config_and_dotenv_with_exported_required_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "settings.json").write_text("{}")
            required = {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "-123",
                        "SPOONACULAR_API_KEY": "spoon-key"}
            with patch.dict("os.environ", required, clear=True):
                _, credentials = load_config(path / "settings.json", path / ".env")
                self.assertIsNone(credentials.dashboard_url)
            with patch.dict("os.environ", {**required, "DASHBOARD_URL": "http://dashboard:8000"}, clear=True):
                with self.assertRaises(ValueError):
                    load_config(path / "settings.json", path / ".env")
            (path / ".env").write_text("DASHBOARD_URL=http://dashboard:8000\nDASHBOARD_TOKEN=private-token\n")
            with patch.dict("os.environ", required, clear=True):
                _, credentials = load_config(path / "settings.json", path / ".env")
                self.assertEqual(credentials.dashboard_url, "http://dashboard:8000")
                self.assertNotIn("private-token", repr(credentials))

    def test_v5_migration_and_invalid_pending_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            legacy = asdict(State(dessert_days_used_this_week=2))
            legacy.pop("dashboard_pending")
            legacy.pop("dashboard_updated_at")
            legacy["version"] = 5
            path.write_text(json.dumps(legacy))
            state = StateStore(path).load()
            self.assertEqual(state.version, 10)
            self.assertEqual(state.dessert_days_used_this_week, 2)
            self.assertIsNone(state.dashboard_pending)
            corrupt = asdict(state)
            corrupt["dashboard_pending"] = {"updated_at": "invalid", "payload": {}}
            path.write_text(json.dumps(corrupt))
            with self.assertRaises(ValueError):
                StateStore(path).load()
