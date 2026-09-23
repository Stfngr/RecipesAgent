# Architecture

- Keep application code under `src/recipe_bot`; use standard-library `unittest`.
- Preserve asynchronous design: one shared `httpx.AsyncClient`, long polling, and `asyncio` workers.
- `RecipeService` owns session lifecycle, scheduling, command handling, delivery, retry coordination, and persisted state transitions.
- `Settings` and `Credentials` validate all configuration at process startup. Add configuration fields there, document them in `settings.json.example` or `.env.example`, and test invalid values.
- Spoonacular source content stays English. Optional LibreTranslate translation produces German recipe titles, ingredients and instructions; without `LIBRETRANSLATE_URL`, recipe content stays English and no translation is attempted.
- Support Telegram messages only from configured numeric chat ID. Parse only exact configured trigger commands.
- Telegram text must remain plain text. Split long messages through `split_message`; its 2,000-code-point chunks remain below Telegram's UTF-16 limit.
