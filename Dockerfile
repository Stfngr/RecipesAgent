FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install --no-install-recommends --yes tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 recipe-bot \
    && useradd --uid 10001 --gid recipe-bot --no-create-home --shell /usr/sbin/nologin recipe-bot

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache

USER recipe-bot

ENTRYPOINT ["recipe-bot"]
CMD ["--settings", "/etc/recipe-bot/settings.json", "--state", "/var/lib/recipe-bot/state.json"]
