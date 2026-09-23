# Daily Recipe Telegram Bot

[English below](#english)

Leichter Python-Dienst für tägliche Rezeptauswahl in einem Telegram-Chat. Ein
interner `asyncio`-Scheduler startet das Tagesmenü; Telegram Long Polling nimmt
Auswahl- und erneute Versandbefehle entgegen. Kein Cron, eingehender Port,
Webhook-Server oder LLM erforderlich; lokale Übersetzung über Ollama ist optional.

## Überblick

- Standardmäßig fünf zufällige Hauptgerichte um 08:00 Uhr in `Europe/Berlin`.
- Auswahl mit `!bot 2`, Auslassen mit `!bot 0`; erste gültige Auswahl gewinnt.
- Feste vegetarische Wochentage, Dessertquote und Sonntagreste sind konfigurierbar.
- Ohne Ollama bleiben Rezepttitel, Zutaten und Anweisungen Englisch. Optional
  übersetzt ein eigener Ollama-Container sie ins Deutsche, auch fürs Dashboard.
  Bot-Bedientexte sind unabhängig davon Deutsch oder Englisch.
- Ausgewähltes Rezeptbild wird vor den Details gesendet, wenn verfügbar.
- Zustand, Abrufbudget und Telegram-Outbox werden atomar persistiert.

## Voraussetzungen

Unterstützt wird ein Linux-Host mit ARM64-Architektur. Veröffentlichte Images
sind `linux/arm64`. Docker ist der offizielle Betriebsweg. Lokal werden Python
3.10+, `tzdata`, Telegram-Bot-Token, numerische Chat-ID und Spoonacular-API-Key
benötigt.

Direkte Laufzeitabhängigkeiten sind `httpx` und `python-dotenv`.

## Lokale Entwicklung und Tests

```bash
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
cp settings.json.example settings.json
chmod 600 .env
.venv/bin/python -m unittest discover -s tests -v
```

`.env` enthält Zugangsdaten; `settings.json` enthält Regeln. `TELEGRAM_CHAT_ID`
ist numerisch, bei Gruppen negativ. Umgebungsvariablen haben Vorrang vor `.env`.

```bash
.venv/bin/recipe-bot --send-test-message
```

Dieser Befehl sendet eine Telegram-Testnachricht ohne Spoonacular-Aufruf oder
Zustandsdatei. Normaler lokaler Start ist `.venv/bin/recipe-bot`; nicht parallel
zu einem Container mit demselben Telegram-Token ausführen.

## Konfiguration

`settings.json.example` definiert 1-100 Rezepte, 1-1439 aktive Minuten, 0-7
Desserttage, `vegetarian_days`, Startzeit, IANA-Zeitzone und
`interaction_language`. `language` muss `en` sein. Mit
`sunday_leftovers=true` sind höchstens sechs Desserttage zulässig.
`translation_language` muss `de` sein. Für die qualitätsorientierte
Pi-Übersetzung gelten `qwen3:4b`, 3 Versuche je Abschnitt, 900 Sekunden je
Übersetzung einschließlich lokaler Prüfung und 60 Minuten Gesamtfrist je
Übersetzungsauftrag als Standardwerte. Diese Werte sind über `translation_model`,
`translation_attempts`, `translation_request_timeout_seconds` und
`translation_job_timeout_minutes` konfigurierbar. Bei einer bestehenden
`settings.json` explizit gesetzte ältere Werte (z. B. `qwen3:1.7b`, 300
Sekunden oder 15 Minuten) bei Bedarf selbst anpassen; Standardwerte ersetzen
keine expliziten Einstellungen. **Fehlt `OLLAMA_URL` in `.env` oder ist leer,
werden keine Ollama-Anfragen gesendet; alle Rezeptinhalte bleiben wie bisher
Englisch.** `interaction_language` steuert weiterhin feste Bot-Nachrichten.

`<trigger_codeword> resend` sendet den heutigen Menü-, Auswahl- oder
Auslasszustand erneut. Es ruft Spoonacular nicht auf. Versandfehler blockieren
Auswahlbefehle nicht; bis zu 32 erneute Sendungen warten im Speicher und gehen
bei einem Neustart verloren.

## Telegram einrichten

1. Bot über BotFather erstellen und zum Zielchat hinzufügen.
2. Gruppen-Privatsphäre mit `/setprivacy` deaktivieren oder Bot zum Admin machen.
3. Senden erlauben und numerische `chat.id` über `getUpdates` ermitteln.
4. Token nie in URLs, Shell-History, Logs oder Versionsverwaltung ablegen.

## Deployment mit Docker

### Veröffentlichte Images

Jeder Push nach `main` führt Tests aus und veröffentlicht ein ARM64-Image:

```text
ghcr.io/stfngr/recipesagent:latest
ghcr.io/stfngr/recipesagent:main
ghcr.io/stfngr/recipesagent:sha-<commit>
```

Nach erstem Workflow-Lauf GitHub **Packages** auf **Public** setzen. SHA-Tags
sind unveränderlich. `latest` und `main` werden nur vom Build des beim letzten
Check aktuellen `main`-Commits aktualisiert.

### Vorbereitung für `docker run` oder Compose

Docker Engine auf dem 64-Bit-Linux-ARM64-Host installieren; für Compose
zusätzlich das Compose-Plugin. **Kein Repository-Klon nötig:** Das Image
enthält die Anwendung. Konfigurationsdateien auf dem Host nur bei der
Erstinstallation anlegen, sonst würden bestehende Zugangsdaten überschrieben.
`bot-network` bleibt auch für das optionale Dashboard erhalten:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
if ! sudo docker network inspect bot-network >/dev/null 2>&1; then
  sudo docker network create bot-network
fi
sudo install -d -m 700 /etc/recipe-bot
sudo install -m 600 /dev/null /etc/recipe-bot/.env
sudo install -m 600 -o 10001 -g 10001 /dev/null /etc/recipe-bot/settings.json
sudo install -d -o 10001 -g 10001 -m 700 /var/lib/recipe-bot
```

Für `docker run` mit Übersetzung zuerst den optionalen Ollama-Container im
nächsten Abschnitt starten und das Modell laden. Bei Compose stattdessen
**nur** das Compose-Profil im Abschnitt „Alternative mit Docker Compose“
verwenden. Anschließend Zugangsdaten und Einstellungen bearbeiten. Werte in
`/etc/recipe-bot/.env` **ohne Anführungszeichen** als `KEY=value` eintragen;
`docker run --env-file` entfernt Anführungszeichen nicht. Bei vorhandenen
`.env`-Dateien Anführungszeichen gegebenenfalls entfernen. Ohne Ollama
`OLLAMA_URL=` belassen; mit `docker run` lautet der Wert
`OLLAMA_URL=http://recipe-ollama:11434`, mit Compose
`OLLAMA_URL=http://ollama:11434`.

```bash
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
```

In `.env` die Platzhalter durch echte Zugangsdaten ersetzen und optionale
Werte leer lassen, wenn die Integration nicht genutzt wird:

```env
TELEGRAM_BOT_TOKEN=123456:replace-with-real-bot-token
TELEGRAM_CHAT_ID=-1001234567890
SPOONACULAR_API_KEY=replace-with-real-api-key
DASHBOARD_URL=
DASHBOARD_TOKEN=
OLLAMA_URL=
```

In `settings.json` mindestens `{}` eintragen; alle Einstellungen haben
Standardwerte. Eine leere Datei ist **kein** gültiges JSON. Bei Bedarf die
Werte aus dem Konfigurationsabschnitt anpassen.

### Optional: Ollama vor dem Bot starten

Auf dem Pi 4 mit 8 GB RAM ein 64-Bit-Betriebssystem verwenden. Ollama läuft
in einem separaten Container ohne veröffentlichten Host-Port. Das Modell bleibt
im benannten Docker-Volume erhalten:

```bash
sudo docker pull ollama/ollama:latest
sudo docker volume create recipe-ollama
sudo docker run -d \
  --name recipe-ollama \
  --restart unless-stopped \
  --network bot-network \
  -e OLLAMA_NUM_PARALLEL=1 \
  -e OLLAMA_MAX_LOADED_MODELS=1 \
  -v recipe-ollama:/root/.ollama \
  ollama/ollama:latest
sudo docker exec recipe-ollama ollama list && \
  sudo docker exec recipe-ollama ollama pull qwen3:4b
```

Wenn `ollama list` noch keinen Server erreicht, kurz warten und nur die letzte
`docker exec ... ollama list && docker exec ... ollama pull`-Zeile wiederholen;
`ollama pull` erst nach erfolgreicher Verbindung ausführen. Maximal ein Modell
gleichzeitig im Speicher und nur eine parallele Anfrage begrenzen den
Speicherbedarf, garantieren aber weder freie RAM-Reserven noch Antwortzeiten.
Optional `qwen3:8b` mit `sudo docker exec recipe-ollama ollama pull qwen3:8b`
laden und in `settings.json` `"translation_model": "qwen3:8b"` setzen; das
Modell benötigt etwa 5,2 GB RAM **zuzüglich** Ollama-, Kontext- und
Systemspeicher. Auf einem Pi 4 mit 8 GB kann das zu Speicherdruck, Swap oder
Abbrüchen führen; Qualität, RAM und Laufzeit vor Betrieb mit echten Rezepten
prüfen. Bot nach Änderung an `settings.json` neu starten.

### Bot starten und prüfen

```bash
sudo docker run -d \
  --name recipe-bot \
  --restart unless-stopped \
  --network bot-network \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file /etc/recipe-bot/.env \
  --mount type=bind,src=/etc/recipe-bot/settings.json,dst=/etc/recipe-bot/settings.json,readonly \
  --mount type=bind,src=/var/lib/recipe-bot,dst=/var/lib/recipe-bot \
  ghcr.io/stfngr/recipesagent:latest
sudo docker ps --filter name=recipe-bot
sudo docker logs -f recipe-bot
```

`docker logs -f` bleibt geöffnet; mit Strg+C nur die Log-Anzeige beenden.

### Konfiguration ändern, aktualisieren und Rollback

`docker restart` übernimmt Änderungen an `/etc/recipe-bot/.env` **nicht**:
`--env-file` wird nur beim Erstellen des Containers eingelesen. Nach jeder
Änderung an `.env` den Bot-Container stoppen und entfernen:

```bash
sudo docker stop recipe-bot
sudo docker rm recipe-bot
```

Danach den Bot mit dem `docker run`-Befehl oben neu erstellen. Änderungen an
`settings.json` erfordern mindestens einen Neustart des Bots.

Für ein Image-Update zuerst das neue Image ziehen, dann den Bot stoppen und
**vor dem Entfernen oder Starten der neuen Version** den Zustand sichern:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker stop recipe-bot
if sudo test -f /var/lib/recipe-bot/state.json; then
  backup="/var/lib/recipe-bot/state.json.backup-$(date +%Y%m%dT%H%M%S)"
  sudo install -m 600 -o 10001 -g 10001 /var/lib/recipe-bot/state.json "$backup"
fi
sudo docker rm recipe-bot
```

Danach den vollständigen `docker run`-Befehl oben erneut ausführen. Der
Zustands-Bind-Mount und `state.json.lock` bleiben bestehen; niemals zwei
Bot-Instanzen mit demselben Telegram-Token gleichzeitig starten.

**Rollback auf ein älteres Image ist eine Zustandswiederherstellung.** Dieses
Release migriert `state.json` beim Start auf Version 8; ältere Images
mit State-Version 7 oder 6 können die migrierte Datei nicht lesen. Vor dem
ersten Start des neuen Images im gestoppten Zustand ein Backup der alten
State-Version anlegen (Befehl oben). Für Rollback den Bot stoppen, das
**passende, vor der Migration erstellte** Backup zurückkopieren, den Bot
entfernen und das alte `sha-<commit>`-Image ziehen. Danach denselben
`docker run`-Befehl mit dem alten SHA-Tag statt `latest` ausführen. Die
Wiederherstellung verwirft alle Zustandsänderungen seit dem Backup; den
aktuellen Zustand vorher gesondert sichern, wenn er noch benötigt wird.
Platzhalter für Backup-Zeitstempel und SHA im folgenden Beispiel vor
Ausführung ersetzen:

```bash
backup=/var/lib/recipe-bot/state.json.backup-REPLACE_WITH_TIMESTAMP
image=ghcr.io/stfngr/recipesagent:sha-REPLACE_WITH_SHA
sudo test -f "$backup" && sudo docker pull "$image" && sudo docker stop recipe-bot && \
  sudo install -m 600 -o 10001 -g 10001 "$backup" /var/lib/recipe-bot/state.json && \
  sudo docker rm recipe-bot
```

Danach den `docker run`-Befehl von „Bot starten und prüfen“ mit `"$image"`
statt `ghcr.io/stfngr/recipesagent:latest` ausführen. Für ein Version-7-Image
ist ein Backup aus State-Version 7 nötig, für ein Version-6-Image eines aus
Version 6. Ein Version-8-Backup ist für beide nicht geeignet.

### Alternative mit Docker Compose

Nur die Compose-Datei in ein Verzeichnis auf dem Pi herunterladen; dort alle
folgenden Compose-Befehle ausführen. Das vollständige Repository ist nicht
nötig:

```bash
curl --fail --location --silent --show-error \
  --output compose.yaml \
  https://raw.githubusercontent.com/Stfngr/RecipesAgent/main/compose.yaml
sudo docker compose -f compose.yaml config --no-env-resolution -q
```

`compose.yaml` startet denselben Bot mit identischen Mounts, Sicherheitsoptionen
und dem externen `bot-network`. Compose **statt** `docker run` verwenden;
vor dem Wechsel vorhandene manuelle Container `recipe-bot` und gegebenenfalls
`recipe-ollama` stoppen und entfernen. Bind-Mount und benanntes Ollama-Volume
bleiben dabei erhalten. Das Netzwerk und die Konfiguration aus der
Erstinstallation müssen bereits existieren.

Ohne Übersetzung `OLLAMA_URL=` belassen und nur den Bot starten:

```bash
sudo docker compose -f compose.yaml pull recipe-bot
sudo docker compose -f compose.yaml up -d recipe-bot
```

Für Ollama **statt** des manuellen Ollama-Containers das Profil aktivieren.
Erst Ollama hochfahren, erfolgreiche Erreichbarkeit mit `ollama list` prüfen,
danach das Modell laden. In `/etc/recipe-bot/.env` für Compose
`OLLAMA_URL=http://ollama:11434` setzen, **nicht** `recipe-ollama`:

```bash
sudo docker compose -f compose.yaml --profile translation pull ollama
sudo docker compose -f compose.yaml --profile translation up -d ollama
sudo docker compose -f compose.yaml --profile translation exec ollama ollama list && \
  sudo docker compose -f compose.yaml --profile translation exec ollama ollama pull qwen3:4b && \
  sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot
```

Optional für `qwen3:8b` statt `qwen3:4b` erst
`sudo docker compose -f compose.yaml --profile translation exec ollama ollama pull qwen3:8b`
ausführen und `"translation_model": "qwen3:8b"` in `settings.json` setzen.
Die RAM-Warnung im Ollama-Abschnitt gilt auch für Compose. Wenn `ollama list`
zu früh ausgeführt wurde, vor `ollama pull` erneut prüfen.
Compose übernimmt Änderungen an `.env` durch
`sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot`;
`docker compose restart` reicht nicht. Für ein Image-Update:

```bash
sudo docker compose -f compose.yaml pull recipe-bot
sudo docker compose -f compose.yaml stop recipe-bot
if sudo test -f /var/lib/recipe-bot/state.json; then
  backup="/var/lib/recipe-bot/state.json.backup-$(date +%Y%m%dT%H%M%S)"
  sudo install -m 600 -o 10001 -g 10001 /var/lib/recipe-bot/state.json "$backup"
fi
sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot
```

Für Rollback auf eine alte State-Version das passende Backup wiederherstellen
und das alte Image auswählen (Platzhalter ersetzen):

```bash
backup=/var/lib/recipe-bot/state.json.backup-REPLACE_WITH_TIMESTAMP
image=ghcr.io/stfngr/recipesagent:sha-REPLACE_WITH_SHA
sudo test -f "$backup" && sudo RECIPE_BOT_IMAGE="$image" docker compose -f compose.yaml pull recipe-bot && \
  sudo docker compose -f compose.yaml stop recipe-bot && \
  sudo install -m 600 -o 10001 -g 10001 "$backup" /var/lib/recipe-bot/state.json && \
  sudo RECIPE_BOT_IMAGE="$image" docker compose -f compose.yaml up -d --force-recreate recipe-bot
```

`RECIPE_BOT_IMAGE` bei späteren Compose-Aufrufen mit altem Image ebenfalls
setzen, sonst gilt wieder `latest`. Rollback verwirft seit dem Backup
entstandene Zustandsänderungen; den aktuellen Zustand bei Bedarf zuvor
separat sichern.

## Betrieb und Daten

`state.json` wird atomar ersetzt und synchronisiert. Beschädigter oder
inkompatibler Zustand stoppt den Dienst; Zustand nicht löschen, weil Wochenlimits
und Tagesmenü verloren gehen können. Telegram-Zustellung ist mindestens einmal:
bei unklarer Antwort sind Duplikate möglich.

Maximal drei kostenpflichtige Rezeptabrufe pro lokalem Tag werden persistiert.
Fehlerhafte oder unvollständige Batches, doppelte Rezepte und verletzte
Vegetarisch-Filter werden abgewiesen. Telegram-Rate-Limits werden über alle
Sendepfade geteilt; Auswahl und Timeout-Verarbeitung laufen während der Sperre
weiter.

## Integrationen

### Home Dashboard

Das separate Projekt `home-dashboard` zeigt das zuletzt ausgewählte Rezept im
Heimnetz. Beide Werte in `/etc/recipe-bot/.env` setzen oder beide leer lassen:

```env
DASHBOARD_URL=http://home-dashboard-api:8000
DASHBOARD_TOKEN=derselbe-token-wie-im-home-dashboard
```

Container im gemeinsamen `bot-network` starten. Neue manuelle und automatische
Auswahl erzeugen einen persistenten Dashboard-Auftrag.
Kandidaten, Auslassen und Restesonntage ersetzen das angezeigte Rezept nicht.
Fehler blockieren Telegram nicht; neuere Auswahl ersetzt ältere offene Updates.

### Optionale Übersetzung mit Ollama auf dem Raspberry Pi 4

Einrichtung und Container-Reihenfolge stehen im Docker-Deployment-Abschnitt.
Innerhalb des Bot-Containers zeigt `localhost` **nicht** auf Ollama.
`qwen3:4b` ist der Qualitäts-Standard für den Pi 4 mit 8 GB;
`qwen3:8b` ist nur eine speicherintensivere Option (etwa 5,2 GB RAM allein
für das Modell). Qualität, Speicherbedarf und Laufzeit mit echten Rezepten
prüfen; für produktiven Betrieb getestete Image-Version fixieren.

Vor der Menüausgabe werden Titel in kleinen Gruppen übersetzt. Nach Auswahl
übersetzt der Bot ausschließlich das ausgewählte Rezept: kurze Rezepte mit
Zutaten und Schritten gemeinsam, lange in Abschnitten mit Rezepttitel und
Zutaten als Kontext. Telegram-Polling läuft weiter. Wenn Spoonacular brauchbare
metrische Mengen zu einer Zutat liefert, bilden diese die Grundlage für die
deutsche Übersetzung; andernfalls bleibt die originale Mengenangabe erhalten.
Explizite Fahrenheit-Angaben in den Schritten werden vorher in Celsius
umgerechnet. Die englischen Originaldaten bleiben für den Rückfall erhalten. Quellen, URLs und Lizenzangaben bleiben
original. Titel, Bildunterschrift, Zutaten, Zubereitung und Dashboard-Payload
verwenden dieselbe endgültige Fassung. Lokale Prüfung auf Form, Vollständigkeit
und Mengen-/Zahlenkonsistenz kann offensichtliche Fehler erkennen, aber keine
perfekte semantische Übersetzung garantieren. Übersetzungsfortschritt,
Versuchszähler und Frist überstehen Neustarts. Bei Verbindungsfehlern oder
fehlgeschlagener Prüfung folgen begrenzte Neuversuche; nach Ausschöpfung der
Versuche oder Frist gehen englische Originaltitel bzw. das gesamte englische
Originalrezept mit Hinweis in der gewählten Bediensprache an Telegram (und das
Originalrezept ans Dashboard). Ein späterer `resend` sendet dieselbe endgültige
Fassung; Übersetzungsfehler
lösen keinen erneuten kostenpflichtigen Spoonacular-Abruf aus. Während der
Rezeptübersetzung kann bereits eine deutsche Statusmeldung erscheinen.

Zum Ausschalten `OLLAMA_URL=` setzen (oder Eintrag entfernen) und den
Bot-Container neu erstellen; dann gibt es **keinen** Verbindungsversuch zu
Ollama und keine Übersetzungswartezeit. Bereits gespeicherte ausstehende
Übersetzungsaufträge fallen ohne Ollama auf Englisch zurück; bereits fertige
Nachrichten bleiben unverändert.

## Alternative: systemd

`systemd/recipe-bot.service` bleibt als alternative Linux-Unit im Repository.
Docker und systemd niemals gleichzeitig mit demselben Telegram-Token ausführen.
Die Docker-Anleitung ist der gepflegte Betriebsweg.

## English

Lightweight Python service for daily recipe selection in a Telegram chat. An
internal `asyncio` scheduler starts the daily menu; Telegram long polling handles
selection and resend commands. No cron, inbound port, webhook server, or LLM
required; local translation via Ollama is optional.

## Overview

- Five random main-course recipes at 08:00 in `Europe/Berlin` by default.
- Select with `!bot 2`, skip with `!bot 0`; the first valid selection wins.
- Fixed vegetarian days, dessert quota, and Sunday leftovers are configurable.
- Without Ollama, recipe content remains English. An optional separate Ollama
  container translates titles and the selected recipe to German for Telegram
  and the dashboard; bot interaction text can independently be German or English.
- Selected recipe images are sent before details when available.
- State, fetch budget, and Telegram outbox are atomically persisted.

## Requirements

Supported deployment target: Linux ARM64 host. Published images are
`linux/arm64`; Docker is the supported runtime. Local development needs Python
3.10+, `tzdata`, Telegram bot token, numeric chat ID, and Spoonacular API key.

## Local Development And Tests

```bash
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
cp settings.json.example settings.json
chmod 600 .env
.venv/bin/python -m unittest discover -s tests -v
```

Use `recipe-bot --send-test-message` to test Telegram without Spoonacular or a
state file. Do not run local and container instances with the same token.

## Configuration

`settings.json.example` defines recipe count, active window, dessert quota,
vegetarian days, start time, timezone, and interaction language. `language` must
be `en`; `sunday_leftovers=true` permits at most six dessert days.
`OLLAMA_URL` in `.env` enables optional translation; omitted or empty means
**no translation requests or delay** and English recipe content. Translation
settings in `settings.json` default to German, `qwen3:4b`, three attempts per
batch, 900 seconds per translation including local verification and 60 minutes per job to
allow for slower Pi inference. Existing explicit settings (including older
model and deadline values) are not overridden by new defaults.

## Telegram Setup

Create the bot with BotFather, disable group privacy or make it admin, allow it
to send messages, and obtain numeric `chat.id` from `getUpdates`. Never expose
tokens in URLs, shell history, logs, or version control.

## Deployment With Docker

### Published Images

Every `main` push tests and publishes:

```text
ghcr.io/stfngr/recipesagent:latest
ghcr.io/stfngr/recipesagent:main
ghcr.io/stfngr/recipesagent:sha-<commit>
```

Make the GitHub Package public after first publication. SHA tags are immutable;
`latest` and `main` are promoted only from the current `main` commit.

### Preparation For `docker run` Or Compose

Install Docker Engine on the Linux ARM64 host (also the Compose plugin when
using Compose). **No repository checkout is needed.** Pull the image before
creating the host configuration files, as shown in the German installation
section. Create `bot-network` if needed and install empty files only once:
repeating `install` would overwrite credentials. Fill `/etc/recipe-bot/.env`
with Telegram token, numeric chat ID and Spoonacular API key; use unquoted
`KEY=value` lines because `docker run --env-file` does not remove quotes.
Write `{}` to `/etc/recipe-bot/settings.json` for all defaults; an empty file
is not valid JSON. The state directory must be owned by UID/GID 10001. Choose
either `docker run` or Compose, never both. To translate, start Ollama with the
chosen deployment method and pull the model before starting the bot. Set
`OLLAMA_URL=http://recipe-ollama:11434` with `docker run`,
`OLLAMA_URL=http://ollama:11434` with Compose, or leave it empty.

### Start And Inspect

Use the German `docker run` command above. Check `docker ps` before following
the logs with `docker logs -f` (Ctrl+C stops following, not the container).

### Update And Roll Back

Changing `.env` requires **recreating** the container; `docker restart` does
not reload `--env-file`. To update, pull the new image, stop the bot, back up
`state.json`, remove the container and rerun the start command. Back up the
state **before** starting the new image. This release migrates state to
version 8 at startup. Older version-7 and version-6 images cannot read it.
To roll back, stop the bot and restore the pre-migration backup matching the
old image's state version (7 or 6) before starting it. A version-8 backup is
not compatible with either older image. This discards state changes made since
the backup. The German section contains exact commands.

### Optional Docker Compose

Download only `compose.yaml` from
`https://raw.githubusercontent.com/Stfngr/RecipesAgent/main/compose.yaml` with
`curl`, as shown in the German Compose section. Run all Compose commands from
the directory containing the downloaded file; no clone is required.
`compose.yaml` is an alternative to `docker run`, not an additional bot. Before
switching, stop and remove any manually started bot and Ollama containers; the
state bind mount and named Ollama volume remain. The external `bot-network` and
host configuration must exist first. Without translation, use
`sudo docker compose -f compose.yaml up -d recipe-bot` and leave `OLLAMA_URL`
empty. With translation, start Ollama using the `translation` profile, confirm
`ollama list` succeeds, pull `qwen3:4b` with
`sudo docker compose -f compose.yaml --profile translation exec ollama ollama pull qwen3:4b`,
then set `OLLAMA_URL=http://ollama:11434` and recreate the bot. Use
`sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot` after
`.env` changes; `docker compose restart` does not reload environment variables.
For updates, stop the bot and back up state after pulling the new image and
before recreating it. For older-image rollback, restore a compatible state
backup and select the old image using `RECIPE_BOT_IMAGE` as shown above.

## Operations And Data

State is atomically replaced and fsynced. Never delete corrupt state as a fix:
weekly limits and menus may be lost. Telegram delivery is at-least-once.
Three paid recipe fetch attempts per local day are persisted. Shared Telegram
cooldowns do not block selection or deadline processing.

## Integrations

### Home Dashboard

Set both `DASHBOARD_URL=http://home-dashboard-api:8000` and
`DASHBOARD_TOKEN`, then run the container on external `bot-network`. Manual and
automatic selections are persisted for dashboard delivery; failures never block
Telegram and newer selections replace pending older updates.

### Optional Ollama translation on Raspberry Pi 4

Use a 64-bit OS and 8 GB RAM. Run Ollama before starting the bot, following
the German Docker commands. Wait for `ollama list` to succeed before pulling
the default `qwen3:4b`:

```bash
sudo docker exec recipe-ollama ollama list && \
  sudo docker exec recipe-ollama ollama pull qwen3:4b
```

For Compose, check connectivity and pull the same model:

```bash
sudo docker compose -f compose.yaml --profile translation exec ollama ollama list && \
  sudo docker compose -f compose.yaml --profile translation exec ollama ollama pull qwen3:4b
```

Both containers share `bot-network`; Ollama exposes no host port and stores
models in the `recipe-ollama` volume. Both deployment examples limit parallel
requests and loaded models to one (`OLLAMA_NUM_PARALLEL=1`,
`OLLAMA_MAX_LOADED_MODELS=1`); this does not guarantee available RAM or
inference speed. Set `OLLAMA_URL` to `http://recipe-ollama:11434` with
`docker run`, or `http://ollama:11434` with Compose. Recreate the bot after
changing `.env`. Optionally pull `qwen3:8b` using the same command with its
tag, then set `"translation_model": "qwen3:8b"` in `settings.json` and restart
the bot. That model needs about 5.2 GB RAM plus Ollama, context and OS memory;
on an 8 GB Pi it may trigger swapping or OOM. Test quality, memory use and
latency on actual recipes rather than assuming better results.

Menu titles are translated before sending. After selection only the chosen
recipe is translated: shorter recipes translate ingredients and instructions
together, longer ones in batches with title and ingredients as context. When
Spoonacular provides usable metric ingredient measures, those are the basis
for German translation; otherwise the original quantities remain. Explicit
Fahrenheit temperatures in steps are converted to Celsius first. English
source data is retained for fallback; sources, URLs and licensing stay
unchanged. Local checks for shape, completeness and number/quantity consistency
can catch obvious errors, not guarantee semantic accuracy. Progress, retries
and deadline survive restarts. After three failed attempts per batch (including
failed validation) or the job deadline, the bot sends English menu titles or
the entire English original recipe to Telegram with a notice in the configured
interaction language, and the original recipe to the dashboard. No additional
Spoonacular fetch occurs; `resend` reuses the stored final text. Removing
`OLLAMA_URL` and recreating the bot disables all new translation attempts
immediately.

## Alternative: systemd

`systemd/recipe-bot.service` remains an alternative Linux unit. Never operate it
alongside Docker with the same Telegram token. Docker is the maintained path.
