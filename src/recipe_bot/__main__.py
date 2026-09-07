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


def main():
    parser = argparse.ArgumentParser(description="Daily recipe Telegram bot")
    parser.add_argument("--settings", type=Path, default=Path("settings.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP libraries can include Telegram tokens in logged request URLs.
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    try:
        settings, credentials = load_config(args.settings, args.env_file)
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
