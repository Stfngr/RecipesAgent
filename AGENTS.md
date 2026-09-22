# Recipe Bot

Python 3.10+ service that selects daily Spoonacular recipes for a Telegram chat, with durable state and optional Home Dashboard updates.

- Dependencies/build: `python -m pip install .`
- Tests: `python -m unittest discover -s tests -v`
- Docker is maintained deployment path; published images target `linux/arm64`.
- Read applicable guidance before changing related code:
  [architecture](docs/agent-guides/architecture.md) |
  [reliability](docs/agent-guides/reliability.md) |
  [integrations](docs/agent-guides/integrations.md) |
  [testing](docs/agent-guides/testing.md) |
  [deployment](docs/agent-guides/deployment.md)
