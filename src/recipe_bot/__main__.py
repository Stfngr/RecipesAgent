import argparse
import asyncio
import fcntl
import logging
from pathlib import Path
import signal

import httpx

from .api import Spoonacular, Telegram
from .config import load_config
from .service import RecipeService
from .state import StateStore

TEST_MESSAGE = "Recipe bot Telegram test successful."


async def run(settings, credentials, state_path):
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=4)) as client:
        service = RecipeService(
            settings, StateStore(state_path), Spoonacular(client, credentials.spoonacular_key),
            Telegram(client, credentials.telegram_token, credentials.chat_id),
        )
        task = asyncio.create_task(service.run())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            logging.info("Service stopped")


async def send_test_message(telegram: Telegram):
    await telegram.send(TEST_MESSAGE)


async def run_test_message(credentials):
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=4)) as client:
        await send_test_message(Telegram(client, credentials.telegram_token, credentials.chat_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Daily recipe Telegram bot")
    parser.add_argument("--settings", type=Path, default=Path("settings.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--send-test-message", action="store_true",
                        help="send a Telegram test message and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP libraries can include Telegram tokens in logged request URLs.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        settings, credentials = load_config(args.settings, args.env_file)
        if args.send_test_message:
            asyncio.run(run_test_message(credentials))
            logging.info("Telegram test message sent")
            return
        args.state.parent.mkdir(parents=True, exist_ok=True)
        with args.state.with_name(args.state.name + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            logging.info("Recipe bot starting; timezone=%s", settings.timezone)
            asyncio.run(run(settings, credentials, args.state))
    except Exception as error:
        # Report only exception type: third-party exceptions may contain secrets or chat content.
        logging.error("Startup/runtime failure (%s); check configuration, state and service access",
                      type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
