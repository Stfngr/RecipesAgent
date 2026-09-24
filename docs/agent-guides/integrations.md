# Integrations And Secrets

- Use `request_json` for HTTP integrations and expose failures as `APIError`.
- Never include tokens, API keys, full request URLs, response bodies, chat content, or third-party exception text in errors or logs.
- Keep Spoonacular API key in request header `x-api-key`; never place it in query parameters.
- Validate Spoonacular responses before persisting or sending them: exact candidate count, unique IDs, complete recipe fields, and vegetarian classification on vegetarian days.
- Spoonacular requests use `include-tags=main course`; append `,vegetarian` for configured vegetarian days.
- Maintain Telegram long polling. On startup call `deleteWebhook` with `drop_pending_updates=False`.
- Dashboard config is optional, but `DASHBOARD_URL` and `DASHBOARD_TOKEN` must be set together. Publish selections with authenticated `PUT /api/v1/state/recipe-bot/current-recipe`.
- PUT body is `{"updated_at": ..., "payload": {"date": ..., "recipe": {...}}}` (see `selection_update()` in `dashboard.py`) — this is a wire contract with the separate Home Dashboard service; changing its shape requires updating that service too.
- Preserve monotonic dashboard `updated_at` values, even if system clock moves backward.
- LibreTranslate is optional and accessed over the internal Docker network. `LIBRETRANSLATE_URL` missing or empty disables translation without requests. Validate its origin and never log recipe input or response bodies.
