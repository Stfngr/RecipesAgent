# Testing

- Run full suite: `python -m unittest discover -s tests -v`.
- Add focused tests for every behavior change, including failure and restart behavior when state, network delivery, scheduling, or concurrency changes.
- Use `unittest.IsolatedAsyncioTestCase` for async service tests.
- Use fakes for `RecipeService` collaborators and `httpx.MockTransport` to verify HTTP request and response contracts.
- Keep tests deterministic: inject clock and RNG into `RecipeService`; do not use live Telegram, Spoonacular, or dashboard services.
- Test redaction whenever adding error paths that process credentials or external HTTP failures.
