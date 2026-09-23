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
`translation_language` muss `de` sein; Modell, höchstens 3 Versuche je Abschnitt
(Standard), HTTP-Timeout (300 Sekunden) und Auftragsfrist (15 Minuten) sind
über `translation_model`, `translation_attempts`,
`translation_request_timeout_seconds` und `translation_job_timeout_minutes`
konfigurierbar. **Fehlt `OLLAMA_URL` in `.env` oder ist leer, werden keine
Ollama-Anfragen gesendet; alle Rezeptinhalte bleiben wie bisher Englisch.**
`interaction_language` steuert weiterhin feste Bot-Nachrichten.

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

### Erstinstallation

Docker Engine mit Compose-Plugin auf dem Linux-ARM64-Host installieren. Dann
Konfiguration und persistenten Zustand anlegen:

```bash
sudo install -d -m 700 /etc/recipe-bot
sudo install -m 600 .env.example /etc/recipe-bot/.env
sudo install -m 600 settings.json.example /etc/recipe-bot/settings.json
sudo chown 10001:10001 /etc/recipe-bot/settings.json
sudo install -d -o 10001 -g 10001 -m 700 /var/lib/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
sudo docker network create bot-network
```

Wenn `bot-network` fürs Dashboard bereits existiert, Netzwerkanlage auslassen.

### Starten und Status prüfen

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
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
sudo docker logs -f recipe-bot
sudo docker ps --filter name=recipe-bot
```

### Aktualisieren und Rollback

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker rm -f recipe-bot
# Then rerun the preceding docker-run command with identical mounts and options.
```

Für Rollback `latest` im Pull- und Run-Befehl durch denselben veröffentlichten
`sha-<commit>`-Tag ersetzen. Bind-Mount für Zustand bleibt erhalten.
**Achtung:** Dieses Release migriert `state.json` auf Version 7. Ältere Images
mit State-Version 6 können den migrierten Zustand nicht laden; vor dem Update
ein Backup von `/var/lib/recipe-bot/state.json` für eventuelle Rollbacks anlegen.

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
DASHBOARD_URL="http://home-dashboard-api:8000"
DASHBOARD_TOKEN="derselbe-token-wie-im-home-dashboard"
```

Container im gemeinsamen `bot-network` starten. Neue manuelle und automatische
Auswahl erzeugen einen persistenten Dashboard-Auftrag.
Kandidaten, Auslassen und Restesonntage ersetzen das angezeigte Rezept nicht.
Fehler blockieren Telegram nicht; neuere Auswahl ersetzt ältere offene Updates.

### Optionale Übersetzung mit Ollama auf dem Raspberry Pi 4

64-Bit-Linux und mindestens 8 GB RAM empfohlen. Ollama läuft als **separater
Container** ohne veröffentlichten Host-Port, mit eigenem Modell-Volume. Beide
Container müssen im selben Docker-Netz sein:

```bash
sudo docker volume create recipe-ollama
sudo docker run -d \
  --name recipe-ollama \
  --restart unless-stopped \
  --network bot-network \
  -e OLLAMA_NUM_PARALLEL=1 \
  -v recipe-ollama:/root/.ollama \
  ollama/ollama:latest
sudo docker exec recipe-ollama ollama pull qwen3:1.7b
```

In `/etc/recipe-bot/.env` `OLLAMA_URL="http://recipe-ollama:11434"` setzen
und Bot mit denselben Mounts und `--network bot-network` neu starten. Innerhalb
des Bot-Containers zeigt `localhost` **nicht** auf Ollama. `qwen3:1.7b` ist
ein CPU-tauglicher Ausgangspunkt; Qualität und Laufzeit mit echten Rezepten
auf dem Pi prüfen. Für produktiven Betrieb getestete Image-Version fixieren.

Vor der Menüausgabe werden Titel in kleinen Gruppen übersetzt. Nach Auswahl
übersetzt der Bot ausschließlich das ausgewählte Rezept; währenddessen läuft
Telegram-Polling weiter. Titel, Bildunterschrift, Zutaten, Zubereitung und
Dashboard-Payload verwenden dieselbe gespeicherte Fassung. Zahlen und
Mengenangaben werden geprüft; Quellen, URLs und Lizenzangaben bleiben original.
Übersetzungsfortschritt, Versuchszähler und Frist überstehen Neustarts. Bei
Fehlern folgen begrenzte Neuversuche, dann englische Originaltitel bzw. das
gesamte englische Originalrezept mit deutschem Hinweis. Ein späterer `resend`
sendet dieselbe endgültige Fassung; Übersetzungsfehler lösen keinen erneuten
kostenpflichtigen Spoonacular-Abruf aus. Während der Rezeptübersetzung kann
bereits eine deutsche Statusmeldung erscheinen.

Zum Ausschalten `OLLAMA_URL=""` setzen (oder Eintrag entfernen) und Bot neu
starten; dann gibt es **keinen** Verbindungsversuch zu Ollama und keine
Übersetzungswartezeit. Bereits gespeicherte ausstehende Übersetzungsaufträge
fallen ohne Ollama auf Englisch zurück; bereits fertige Nachrichten bleiben
unverändert.

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
settings in `settings.json` default to German, `qwen3:1.7b`, three attempts per
batch, 300 seconds per request and 15 minutes per job.

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

### First Installation

Install Docker Engine with Compose plugin on the Linux ARM64 host, then create
`/etc/recipe-bot/.env`, `/etc/recipe-bot/settings.json`, and
`/var/lib/recipe-bot` exactly as shown in the German installation commands above.
Create `bot-network` unless it already exists.

### Start And Inspect

Use the German `docker pull` and `docker run` command above unchanged; they are
locale-independent. Inspect with `sudo docker logs -f recipe-bot` and
`sudo docker ps --filter name=recipe-bot`.

### Update And Roll Back

Pull `latest`, remove the container, and rerun it with the same options and
mounts. For rollback replace `latest` with one published `sha-<commit>` tag.
This release migrates `state.json` to version 7; older version-6 images cannot
read the migrated state. Back up the state file before upgrading if rollback
may be needed.

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

Run the separate Ollama container and pull `qwen3:1.7b` using the commands in
the German Ollama section above. A 64-bit OS and 8 GB RAM are recommended.
Both containers must share `bot-network`; Ollama does not expose a host port.
Set `OLLAMA_URL="http://recipe-ollama:11434"` in `/etc/recipe-bot/.env` and
restart the bot. The Ollama models reside in the `recipe-ollama` Docker volume.
Start with the small model and benchmark quality and speed on the Pi.

The bot translates menu titles before sending the menu and the chosen recipe
after selection. Progress, retries and deadline survive restarts. After three
failed attempts per batch or the job deadline, it sends the complete English
original (and English recipe data to the dashboard) with a notice. No additional
Spoonacular fetch occurs; `resend` reuses the stored final text. Removing
`OLLAMA_URL` and restarting disables all new translation attempts immediately.

## Alternative: systemd

`systemd/recipe-bot.service` remains an alternative Linux unit. Never operate it
alongside Docker with the same Telegram token. Docker is the maintained path.
