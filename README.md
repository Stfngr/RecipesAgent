# Daily Recipe Telegram Bot

[English below](#english)

Leichter Python-Dienst für tägliche Rezeptauswahl in einem Telegram-Chat. Ein
interner `asyncio`-Scheduler startet das Tagesmenü; Telegram Long Polling nimmt
Auswahl- und erneute Versandbefehle entgegen. Kein Cron, eingehender Port,
Webhook-Server oder LLM erforderlich; Übersetzung über einen selbst gehosteten
LibreTranslate-Container ist optional.

## Überblick

- Standardmäßig fünf zufällige Hauptgerichte um 08:00 Uhr in `Europe/Berlin`.
- Auswahl mit `!bot 2`, Auslassen mit `!bot 0`; erste gültige Auswahl gewinnt.
- Feste vegetarische Wochentage, Dessertquote und Sonntagreste sind konfigurierbar.
- Ohne LibreTranslate bleiben Rezepttitel, Zutaten und Anweisungen Englisch.
  Optional übersetzt ein eigener LibreTranslate-Container sie ins Deutsche,
  auch fürs Dashboard. Bot-Bedientexte sind unabhängig davon Deutsch oder
  Englisch.
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

`settings.json.example` definiert 1-100 Rezepte, 0-7 Desserttage,
`vegetarian_days`, Startzeit, IANA-Zeitzone und `interaction_language`. Die
Auswahl bleibt zeitlich unbegrenzt aktiv, bis eine Nummer, `0` oder `restart`
gesendet wird. `additional_include_tags` ergänzt die an Spoonacular
gesendeten `include-tags` (z. B. `["italian"]`) um weitere Spoonacular-Tags in
Kleinschreibung; Standard ist eine leere Liste. `language` muss `en` sein. Mit
`sunday_leftovers=true` sind höchstens sechs Desserttage zulässig.
`translation_language` muss `de` sein. Für die LibreTranslate-Übersetzung
gelten 3 Versuche je Abschnitt, 30 Sekunden je Übersetzungsanfrage und 15
Minuten Gesamtfrist je Übersetzungsauftrag als Standardwerte. Diese Werte sind
über `translation_attempts`, `translation_request_timeout_seconds` und
`translation_job_timeout_minutes` konfigurierbar. Bei einer bestehenden
`settings.json` explizit gesetzte ältere Werte bei Bedarf selbst anpassen;
Standardwerte ersetzen keine expliziten Einstellungen. **Fehlt
`LIBRETRANSLATE_URL` in `.env` oder ist leer, werden keine
LibreTranslate-Anfragen gesendet; alle Rezeptinhalte bleiben wie bisher
Englisch.** `interaction_language` steuert weiterhin feste Bot-Nachrichten.

`<trigger_codeword> resend` sendet den heutigen Menü-, Auswahl- oder
Auslasszustand erneut. Es ruft Spoonacular nicht auf. Versandfehler blockieren
Auswahlbefehle nicht; bis zu 32 erneute Sendungen warten im Speicher und gehen
bei einem Neustart verloren.

`<trigger_codeword> restart` verwirft eine bereits getroffene Auswahl oder ein
Auslassen und sendet dieselbe Tagesliste erneut zur erneuten Auswahl. Es ruft
Spoonacular nicht auf und funktioniert nur für die heutige Auswahl, solange
sie bereits getroffen, in Übersetzung oder ausgelassen wurde.

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

Für `docker run` mit Übersetzung zuerst den optionalen LibreTranslate-Container
im nächsten Abschnitt starten. Bei Compose stattdessen **nur** das
Compose-Profil im Abschnitt „Alternative mit Docker Compose“ verwenden.
Anschließend Zugangsdaten und Einstellungen bearbeiten. Werte in
`/etc/recipe-bot/.env` **ohne Anführungszeichen** als `KEY=value` eintragen;
`docker run --env-file` entfernt Anführungszeichen nicht. Bei vorhandenen
`.env`-Dateien Anführungszeichen gegebenenfalls entfernen. Ohne LibreTranslate
`LIBRETRANSLATE_URL=` belassen; mit `docker run` lautet der Wert
`LIBRETRANSLATE_URL=http://recipe-libretranslate:5000`, mit Compose
`LIBRETRANSLATE_URL=http://libretranslate:5000`.

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
LIBRETRANSLATE_URL=
```

In `settings.json` mindestens `{}` eintragen; alle Einstellungen haben
Standardwerte. Eine leere Datei ist **kein** gültiges JSON. Bei Bedarf die
Werte aus dem Konfigurationsabschnitt anpassen.

### Optional: LibreTranslate vor dem Bot starten

Auf dem Pi 4 ein 64-Bit-Betriebssystem verwenden. LibreTranslate läuft in
einem separaten Container ohne veröffentlichten Host-Port; das Sprachmodell
für Englisch/Deutsch bleibt im benannten Docker-Volume erhalten:

```bash
sudo docker pull libretranslate/libretranslate:v1.9.6
sudo docker volume create recipe-libretranslate
sudo docker run -d \
  --name recipe-libretranslate \
  --restart unless-stopped \
  --network bot-network \
  -e LT_LOAD_ONLY=en,de \
  -e LT_DISABLE_WEB_UI=true \
  -e LT_THREADS=1 \
  -v recipe-libretranslate:/home/libretranslate/.local \
  libretranslate/libretranslate:v1.9.6
```

Der erste Start lädt die Sprachmodelle für `en` und `de` herunter, was je nach
Verbindung einige Minuten dauern kann; spätere Starts nutzen das Volume. Die
Version ist bewusst gepinnt statt `latest`: ARM64-Builds hatten in der
Vergangenheit bereits einen dokumentierten Absturz auf Raspberry-Pi-Hardware
(GitHub-Issue #424); vor einem Wechsel auf eine neuere Version auf dem
Ziel-Pi testen. `LT_THREADS` startet je Wert einen eigenen Worker-Prozess mit
eigener Modellkopie im Speicher; auf dem Pi bei `1` belassen, sonst steigt der
RAM-Bedarf pro zusätzlichem Worker deutlich.

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
Release migriert `state.json` beim Start auf Version 9; ältere Images
mit State-Version 8 oder 7 können die migrierte Datei nicht lesen. Vor dem
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
statt `ghcr.io/stfngr/recipesagent:latest` ausführen. Für ein Version-8-Image
ist ein Backup aus State-Version 8 nötig, für ein Version-7-Image eines aus
Version 7. Ein Version-9-Backup ist für beide nicht geeignet.

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
`recipe-libretranslate` stoppen und entfernen. Bind-Mount und benanntes
LibreTranslate-Volume bleiben dabei erhalten. Das Netzwerk und die
Konfiguration aus der Erstinstallation müssen bereits existieren.

Ohne Übersetzung `LIBRETRANSLATE_URL=` belassen und nur den Bot starten:

```bash
sudo docker compose -f compose.yaml pull recipe-bot
sudo docker compose -f compose.yaml up -d recipe-bot
```

Für LibreTranslate **statt** des manuellen LibreTranslate-Containers das
Profil aktivieren. In `/etc/recipe-bot/.env` für Compose
`LIBRETRANSLATE_URL=http://libretranslate:5000` setzen, **nicht**
`recipe-libretranslate`:

```bash
sudo docker compose -f compose.yaml --profile translation up -d libretranslate
sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot
```

Der erste Start von `libretranslate` lädt die Sprachmodelle herunter, was
einige Minuten dauern kann; der Bot degradiert währenddessen automatisch auf
englische Rezeptinhalte, bis der Container erreichbar ist. Die Pinning- und
RAM-Hinweise aus dem LibreTranslate-Abschnitt oben gelten auch für Compose.
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

Container im gemeinsamen `bot-network` starten. Jede Auswahl erzeugt einen
persistenten Dashboard-Auftrag.
Kandidaten, Auslassen und Restesonntage ersetzen das angezeigte Rezept nicht.
Fehler blockieren Telegram nicht; neuere Auswahl ersetzt ältere offene Updates.

### Optionale Übersetzung mit LibreTranslate auf dem Raspberry Pi 4

Einrichtung und Container-Reihenfolge stehen im Docker-Deployment-Abschnitt.
Innerhalb des Bot-Containers zeigt `localhost` **nicht** auf LibreTranslate.
Der Container lädt nur die Sprachpaare Englisch/Deutsch (`LT_LOAD_ONLY=en,de`);
Speicherbedarf und Laufzeit mit echten Rezepten prüfen und für produktiven
Betrieb eine getestete Image-Version fixieren.

Vor der Menüausgabe werden Titel in kleinen Gruppen übersetzt. Nach Auswahl
übersetzt der Bot ausschließlich das ausgewählte Rezept: kurze Rezepte mit
Zutaten und Schritten gemeinsam, lange in Abschnitten. Telegram-Polling läuft
weiter. Wenn Spoonacular brauchbare metrische Mengen zu einer Zutat liefert,
bilden diese die Grundlage für die deutsche Übersetzung; andernfalls bleibt
die originale Mengenangabe erhalten. Explizite Fahrenheit-Angaben in den
Schritten werden vorher in Celsius umgerechnet. Die englischen Originaldaten
bleiben für den Rückfall erhalten. Quellen, URLs und Lizenzangaben bleiben
original. Titel, Bildunterschrift, Zutaten, Zubereitung und Dashboard-Payload
verwenden dieselbe endgültige Fassung. Eine lokale Prüfung auf Form,
Vollständigkeit und Mengen-/Zahlenkonsistenz kann offensichtliche Fehler
erkennen, aber keine perfekte semantische Übersetzung garantieren.
Übersetzungsfortschritt, Versuchszähler und Frist überstehen Neustarts. Bei
Verbindungsfehlern oder ungültiger Antwort folgen begrenzte Neuversuche; nach
Ausschöpfung der Versuche oder Frist gehen englische Originaltitel bzw. das
gesamte englische Originalrezept mit Hinweis in der gewählten Bediensprache an
Telegram (und das Originalrezept ans Dashboard). Ein späterer `resend` sendet
dieselbe endgültige Fassung; Übersetzungsfehler lösen keinen erneuten
kostenpflichtigen Spoonacular-Abruf aus. Während der Rezeptübersetzung kann
bereits eine deutsche Statusmeldung erscheinen.

Zum Ausschalten `LIBRETRANSLATE_URL=` setzen (oder Eintrag entfernen) und den
Bot-Container neu erstellen; dann gibt es **keinen** Verbindungsversuch zu
LibreTranslate und keine Übersetzungswartezeit. Bereits gespeicherte
ausstehende Übersetzungsaufträge fallen ohne LibreTranslate auf Englisch
zurück; bereits fertige Nachrichten bleiben unverändert.

## Alternative: systemd

`systemd/recipe-bot.service` bleibt als alternative Linux-Unit im Repository.
Docker und systemd niemals gleichzeitig mit demselben Telegram-Token ausführen.
Die Docker-Anleitung ist der gepflegte Betriebsweg.

## English

Lightweight Python service for daily recipe selection in a Telegram chat. An
internal `asyncio` scheduler starts the daily menu; Telegram long polling handles
selection and resend commands. No cron, inbound port, webhook server, or LLM
required; translation via a self-hosted LibreTranslate container is optional.

## Overview

- Five random main-course recipes at 08:00 in `Europe/Berlin` by default.
- Select with `!bot 2`, skip with `!bot 0`; the first valid selection wins.
- Fixed vegetarian days, dessert quota, and Sunday leftovers are configurable.
- Without LibreTranslate, recipe content remains English. An optional separate
  LibreTranslate container translates titles and the selected recipe to German
  for Telegram and the dashboard; bot interaction text can independently be
  German or English.
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

`settings.json.example` defines recipe count, dessert quota, vegetarian days,
start time, timezone, and interaction language. Selection stays open
indefinitely until a number, `0`, or `restart` is sent.
`additional_include_tags` appends extra lowercase Spoonacular tags (e.g.
`["italian"]`) to the `include-tags` sent to Spoonacular; it defaults to an
empty list. `language` must be `en`; `sunday_leftovers=true` permits at most
six dessert days.
`LIBRETRANSLATE_URL` in `.env` enables optional translation; omitted or empty
means **no translation requests or delay** and English recipe content.
Translation settings in `settings.json` default to German, three attempts per
batch, 30 seconds per translation request, and 15 minutes per job. Existing
explicit settings are not overridden by new defaults.

`<trigger_codeword> restart` discards an already made selection or skip and
resends today's same recipe list for a new pick. It does not call
Spoonacular and only works once today's selection has been made, is
translating, or was skipped.

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
either `docker run` or Compose, never both. To translate, start LibreTranslate
with the chosen deployment method before starting the bot. Set
`LIBRETRANSLATE_URL=http://recipe-libretranslate:5000` with `docker run`,
`LIBRETRANSLATE_URL=http://libretranslate:5000` with Compose, or leave it empty.

### Start And Inspect

Use the German `docker run` command above. Check `docker ps` before following
the logs with `docker logs -f` (Ctrl+C stops following, not the container).

### Update And Roll Back

Changing `.env` requires **recreating** the container; `docker restart` does
not reload `--env-file`. To update, pull the new image, stop the bot, back up
`state.json`, remove the container and rerun the start command. Back up the
state **before** starting the new image. This release migrates state to
version 9 at startup. Older version-8 and version-7 images cannot read it.
To roll back, stop the bot and restore the pre-migration backup matching the
old image's state version (8 or 7) before starting it. A version-9 backup is
not compatible with either older image. This discards state changes made since
the backup. The German section contains exact commands.

### Optional Docker Compose

Download only `compose.yaml` from
`https://raw.githubusercontent.com/Stfngr/RecipesAgent/main/compose.yaml` with
`curl`, as shown in the German Compose section. Run all Compose commands from
the directory containing the downloaded file; no clone is required.
`compose.yaml` is an alternative to `docker run`, not an additional bot. Before
switching, stop and remove any manually started bot and LibreTranslate
containers; the state bind mount and named LibreTranslate volume remain. The
external `bot-network` and host configuration must exist first. Without
translation, use `sudo docker compose -f compose.yaml up -d recipe-bot` and
leave `LIBRETRANSLATE_URL` empty. With translation, start LibreTranslate using
the `translation` profile with
`sudo docker compose -f compose.yaml --profile translation up -d libretranslate`,
then set `LIBRETRANSLATE_URL=http://libretranslate:5000` and recreate the bot.
The first LibreTranslate start downloads language models, which can take a few
minutes; the bot falls back to English automatically until it is reachable.
Use `sudo docker compose -f compose.yaml up -d --force-recreate recipe-bot` after
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
`DASHBOARD_TOKEN`, then run the container on external `bot-network`. Every
selection is persisted for dashboard delivery; failures never block
Telegram and newer selections replace pending older updates.

### Optional LibreTranslate translation on Raspberry Pi 4

Use a 64-bit OS. Run LibreTranslate before starting the bot, following the
German Docker commands:

```bash
sudo docker pull libretranslate/libretranslate:v1.9.6
sudo docker volume create recipe-libretranslate
sudo docker run -d \
  --name recipe-libretranslate \
  --restart unless-stopped \
  --network bot-network \
  -e LT_LOAD_ONLY=en,de \
  -e LT_DISABLE_WEB_UI=true \
  -e LT_THREADS=1 \
  -v recipe-libretranslate:/home/libretranslate/.local \
  libretranslate/libretranslate:v1.9.6
```

For Compose, use the `translation` profile instead:

```bash
sudo docker compose -f compose.yaml --profile translation up -d libretranslate
```

Both containers share `bot-network`; LibreTranslate exposes no host port and
stores its language models in the `recipe-libretranslate` volume. The image
tag is pinned rather than `latest`: arm64 builds have had a documented crash
on Raspberry Pi hardware before (GitHub issue #424); test on the target Pi
before moving to a newer tag. `LT_THREADS` spawns one worker process per
thread, each holding its own copy of the loaded models in memory; keep it at
`1` on a Pi, since each additional worker adds a full model copy to RAM. The
first start downloads the `en`/`de` language models, which can take a few
minutes; the bot falls back to English automatically until LibreTranslate is
reachable. Set `LIBRETRANSLATE_URL` to `http://recipe-libretranslate:5000`
with `docker run`, or `http://libretranslate:5000` with Compose. Recreate the
bot after changing `.env`.

Menu titles are translated before sending. After selection only the chosen
recipe is translated: shorter recipes translate ingredients and instructions
together, longer ones in batches. When Spoonacular provides usable metric
ingredient measures, those are the basis for German translation; otherwise
the original quantities remain. Explicit Fahrenheit temperatures in steps are
converted to Celsius first. English source data is retained for fallback;
sources, URLs and licensing stay unchanged. Local checks for shape,
completeness and number/quantity consistency can catch obvious errors, not
guarantee semantic accuracy. Progress, retries and deadline survive restarts.
After three failed attempts per batch (including failed validation) or the
job deadline, the bot sends English menu titles or the entire English
original recipe to Telegram with a notice in the configured interaction
language, and the original recipe to the dashboard. No additional Spoonacular
fetch occurs; `resend` reuses the stored final text. Removing
`LIBRETRANSLATE_URL` and recreating the bot disables all new translation
attempts immediately.

## Alternative: systemd

`systemd/recipe-bot.service` remains an alternative Linux unit. Never operate it
alongside Docker with the same Telegram token. Docker is the maintained path.
