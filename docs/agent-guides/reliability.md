# Reliability And State

- Persist state through `StateStore.save`; it atomically replaces and fsyncs both file and parent directory.
- Persist an externally visible state transition before network delivery. Resume incomplete delivery after restart using `Session.next_message`, `photo_delivered`, and `photo_skipped`.
- Treat malformed, incomplete, or unsupported state as a startup failure. Do not silently reset or delete state.
- Maintain explicit state migrations when changing `State` or `Session` schemas. Bump `State.version`, accept only known prior shapes, and test every migration.
- Preserve paid Spoonacular request budget: store attempt count and retry time before every request; limit to three attempts per local day.
- Do not hold `RecipeService.lock` during network I/O. Preserve first-valid-selection-wins behavior.
- Telegram cooldown applies to all Telegram sends and polling failures. Selection and deadline resolution must still proceed during delivery cooldown.
- Dashboard delivery is durable and independent from Telegram: selection queues a persistent update; dashboard failures must not prevent Telegram delivery.
- Persist translation progress and retry budgets before Ollama I/O. Do not hold `RecipeService.lock` during translation calls. After exhaustion, use the complete English original for both Telegram and Dashboard.
