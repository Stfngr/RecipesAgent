# Deployment

- Docker is maintained deployment path. Published container images support only `linux/arm64`.
- Keep container runtime non-root and compatible with read-only root filesystem. Persistent state belongs under `/var/lib/recipe-bot`; settings mount read-only from `/etc/recipe-bot/settings.json`.
- `systemd/recipe-bot.service` is alternative deployment only. Do not run Docker and systemd instances using same Telegram token.
- Preserve process single-instance locking through `state.json.lock`.
- Do not commit `.env`, `settings.json`, `state.json*`, or credentials.
- CI installs package and runs `python -m unittest discover -s tests -v` before publishing ARM64 image.
