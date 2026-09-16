# Daily Recipe Telegram Bot

[English below](#english)

Leichter Python-Hintergrunddienst fuer taegliche Rezeptauswahl in einem
Telegram-Chat. Ein interner `asyncio`-Scheduler startet das Tagesmenue;
Telegram Long Polling nimmt Auswahl- und erneute Versandbefehle entgegen. Kein
Cron, eingehender Port, Webhook-Server oder LLM erforderlich.

## Verhalten

- Standardmaessig fuenf zufaellige Hauptgerichte um 08:00 Uhr in
  `Europe/Berlin`.
- Im konfigurierten Chat mit `!bot 2` auswaehlen oder mit `!bot 0` das heutige
  Essen auslassen. Andere Chats, Bot-Nachrichten, bearbeitete Nachrichten,
  sonstige Unterhaltung, fehlerhafte Zahlen und Nachrichten ausserhalb des
  Auswahlfensters werden ignoriert.
- Die erste gueltige Auswahl gewinnt; nach Ablauf wird gleichverteilt zufaellig
  ausgewaehlt.
- Feste vegetarische Wochentage wie Montag und Freitag konfigurierbar. An diesen
  Tagen werden nur vegetarische Rezepte angefordert und jedes zurueckgelieferte
  Vegetarisch-Flag wird geprueft. Fisch und Meeresfruechte sind nicht
  vegetarisch. Unbekannte Klassifikationen werden abgewiesen.
- Dessert ist nur `JA/NEIN` (oder `YES/NO`), kein separates Rezept. Die
  Wahrscheinlichkeit ist ungenutzte Desserttage / verbleibende Tage; gegen Ende
  der Woche wird die Nutzung erzwungen.
- Dessertzaehler verwenden ISO-Jahr und -Woche und werden montags in der
  konfigurierten Zeitzone zurueckgesetzt. Ausfallzeiten und Jahreswechsel werden
  beim Start behandelt. Zaehler gehoeren zum Menue-Datum.
- `sunday_leftovers` auf `true` setzen, um sonntags keine Rezepte anzufordern
  und stattdessen `Heute kochen wir mit Resten aus dem Kühlschrank oder bestellen etwas :)`
  zu senden.
- Rezepttitel, Zutaten und Anweisungen bleiben **Englisch**, sofern keine
  optionalen Lara-Translate-Zugangsdaten konfiguriert sind. Bot-Nachrichten
  unterstuetzen Deutsch und Englisch unabhaengig davon.

## Voraussetzungen

Linux, Python 3.10+, Zeitzonendatenbank (`tzdata`), Telegram-Bot-Token und
numerische Chat-ID sowie Spoonacular-API-Schluessel. Lara-Translate-
Zugangsdaten sind fuer deutsche Rezeptuebersetzungen optional. Raspberry Pi OS
Lite mit Python 3.11+ wird empfohlen.

Laufzeitabhaengigkeiten sind nur `httpx` und `python-dotenv` samt deren
Abhaengigkeiten. Direkte Aufrufe der Telegram Bot API vermeiden ein zusaetzliches
Framework und einen Scheduler. Das SPEC-Ziel von weniger als 50 MB RSS muss auf
dem tatsaechlichen Pi mit realistischen Rezepten und Netzwerkverkehr gemessen
werden; es ist keine garantierte oder hart erzwungene Speichergrenze.

## Lokale Einrichtung

Aus diesem Projektverzeichnis:

```bash
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
cp settings.json.example settings.json
chmod 600 .env
```

`.env` mit echten Zugangsdaten und `settings.json` mit den gewuenschten Regeln
konfigurieren. `TELEGRAM_CHAT_ID` muss eine numerische ID sein (bei Gruppen
negativ), kein Gruppenname. Umgebungsvariablen haben Vorrang vor `.env`.

`LARA_ACCESS_KEY_ID` und `LARA_ACCESS_KEY_SECRET` beide setzen, um Rezepttitel,
Zutaten und Anweisungen von Englisch nach Deutsch zu uebersetzen. Beide nicht
setzen, um englische Rezeptinhalte zu behalten. Lara-Zugangsdaten werden in den
API-Einstellungen von Lara Translate erstellt; niemals einen der Werte einchecken.
Nur einen der beiden Werte zu setzen ist ein Konfigurationsfehler.

```bash
.venv/bin/recipe-bot
```

Mit Ctrl+C beenden. Ein zweiter Prozess mit derselben Zustandsdatei scheitert an
der Sperre. Niemals mehrere Instanzen mit demselben Telegram-Token ausfuehren,
auch nicht mit unterschiedlichen Zustandsdateien.

Telegram-Versand testen, ohne Spoonacular aufzurufen oder den Scheduler zu
starten:

```bash
.venv/bin/recipe-bot --send-test-message
```

Dies sendet `Recipe bot Telegram test successful.` an den konfigurierten Chat
und beendet sich. Es erstellt oder liest keine Zustandsdatei.

Tests benoetigen keine Zugangsdaten und fuehren keine Live-API-Aufrufe aus:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Konfiguration

Siehe `settings.json.example`. Grenzen: 1-100 Rezepte, 1-1439 aktive Minuten,
0-7 Desserttage, Liste `vegetarian_days` mit kleingeschriebenen Wochentagen wie
`["monday", "friday"]`, Startzeit `HH:MM` und IANA-Zeitzone wie
`Europe/Berlin`. `language` muss `en` sein; `interaction_language` kann `de`
oder `en` sein. `trigger_codeword` unterscheidet Gross-/Kleinschreibung, darf
keinen Whitespace enthalten und ist bis zu 32 Zeichen lang. `sunday_leftovers`
muss `true` oder `false` sein und ist standardmaessig `false`.

Nach Aenderungen an den Einstellungen neu starten. Bestehende Sitzungen behalten
ihren urspruenglichen Trigger, Sprache, Kandidaten, Dessertentscheidung und
Zeitlimit.

Mit `<trigger_codeword> resend` (zum Beispiel `!bot resend`) kann jedes
Gruppenmitglied das aktuelle heutige Menue, das ausgewaehlte Rezept oder die
Auslassbestaetigung erneut senden. Der Befehl ruft Spoonacular nie auf,
verbraucht keinen Abrufversuch und oeffnet oder aendert die Auswahl nicht.

Die Tagesplanung verwendet lokale Wandzeit; Auswahlzeiten verwenden vergangene
Sekunden. Eine nicht vorhandene Fruehlingszeit startet zur ersten spaeteren
Ortszeit. Wiederholte Herbstzeiten erzeugen nicht zwei Sitzungen fuer ein Datum.
Startet der Dienst nach heutiger Startzeit ohne Sitzung, beginnt das heutige
Menue sofort mit vollem Fenster. Verpasste fruehere Daten werden nicht
nachgeholt. Eine unvollstaendige aeltere Sitzung wird aufgeloest und ausgeliefert,
bevor ein heutiges Menue entsteht. Ein Rezeptabruf ueber Mitternacht wird
verworfen und nicht dem naechsten Menue berechnet.

## Telegram einrichten

1. Mit BotFather einen Bot erstellen und zur Gruppe hinzufuegen.
2. **Gruppen-Privatsphaere deaktivieren** mit BotFather `/setprivacy` oder Bot
   zum Gruppenadministrator machen. `!bot` ist kein Telegram-Slash-Befehl und
   wird mit aktivierter Privatsphaere nicht verlaesslich zugestellt. Nach einer
   Aenderung Bot bei Bedarf entfernen und erneut hinzufuegen.
3. Bot die Berechtigung zum Senden geben. Gruppe oder privaten Chat verwenden,
   keinen Broadcast-Kanal ohne separaten Interaktionsmechanismus.
4. Numerische Chat-ID aus `chat.id` einer Nachricht in `getUpdates` ermitteln,
   waehrend der Dienst gestoppt ist. Keine Token-haltigen URLs oder
   Befehlsverlaeufe weitergeben.

Beim Start entfernt der Dienst vorhandene Webhooks fuer diesen Bot und behaelt
ausstehende Updates. Dediziertes Token verwenden und nicht mit einer anderen
Bot-Anwendung teilen.

## Raspberry-Pi-Bereitstellung

### Docker

Jeder Push nach `main` fuehrt Tests aus und veroeffentlicht ein ARM64-Image fuer
einen `aarch64`-Pi in GitHub Container Registry:

```text
ghcr.io/stfngr/recipesagent:latest
ghcr.io/stfngr/recipesagent:main
ghcr.io/stfngr/recipesagent:sha-<commit>
```

Nach erstem Workflow-Lauf auf der Repository-Seite **Packages** Paket
`recipesagent` auf **Public** setzen. Dann kann der Pi das Image ohne
GitHub-Zugangsdaten laden. Der unveraenderliche SHA-Tag erlaubt gezieltes
Rollback.

Docker Engine nach offizieller Anleitung fuer Pi OS installieren. Dann
Host-Konfiguration und persistenten Zustand anlegen:

```bash
sudo install -d -m 700 /etc/recipe-bot
sudo install -m 600 .env.example /etc/recipe-bot/.env
sudo install -m 600 settings.json.example /etc/recipe-bot/settings.json
sudo chown 10001:10001 /etc/recipe-bot/settings.json
sudo install -d -o 10001 -g 10001 -m 700 /var/lib/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
```

Container starten:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker run -d \
  --name recipe-bot \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file /etc/recipe-bot/.env \
  --mount type=bind,src=/etc/recipe-bot/settings.json,dst=/etc/recipe-bot/settings.json,readonly \
  --mount type=bind,src=/var/lib/recipe-bot,dst=/var/lib/recipe-bot \
  ghcr.io/stfngr/recipesagent:latest
```

Logs und Status pruefen:

```bash
sudo docker logs -f recipe-bot
sudo docker ps --filter name=recipe-bot
```

Nach jedem veroeffentlichten Image explizit aktualisieren. Der State-Bind-Mount
bleibt erhalten:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker rm -f recipe-bot
sudo docker run -d \
  --name recipe-bot \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file /etc/recipe-bot/.env \
  --mount type=bind,src=/etc/recipe-bot/settings.json,dst=/etc/recipe-bot/settings.json,readonly \
  --mount type=bind,src=/var/lib/recipe-bot,dst=/var/lib/recipe-bot \
  ghcr.io/stfngr/recipesagent:latest
```

Fuer ein Rollback `latest` in Pull- und Run-Befehlen durch einen
veroeffentlichten `sha-<commit>`-Tag ersetzen. Docker- und systemd-Bereitstellung
nicht gleichzeitig mit demselben Telegram-Token betreiben: Beide pollen Telegram.

### Systemd

OS-Pakete installieren (diese Befehle benoetigen Administratorrechte):

```bash
sudo apt update
sudo apt install git python3 python3-venv tzdata
sudo useradd --system --user-group --home-dir /var/lib/recipe-bot --no-create-home recipe-bot
sudo git clone https://github.com/Stfngr/RecipesAgent.git /opt/recipe-bot
sudo install -d -m 700 /etc/recipe-bot
sudo python3 -m venv /opt/recipe-bot/.venv
sudo /opt/recipe-bot/.venv/bin/pip install /opt/recipe-bot
sudo install -m 600 /opt/recipe-bot/.env.example /etc/recipe-bot/.env
sudo install -m 600 /opt/recipe-bot/settings.json.example /etc/recipe-bot/settings.json
sudo chown recipe-bot:recipe-bot /etc/recipe-bot/settings.json
sudo chmod 755 /etc/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
sudo install -m 644 /opt/recipe-bot/systemd/recipe-bot.service /etc/systemd/system/recipe-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now recipe-bot
```

Systemd liest die nur fuer root lesbare Umgebungsdatei und uebergibt die
Zugangsdaten an den Dienst. Die Anwendung muss diese Datei nicht selbst lesen.
Das persistente Zustandsverzeichnis des Dienstes wird automatisch von systemd
erstellt und besessen. Anwendungscode und Zugangsdaten bleiben fuer den
Dienstbenutzer schreibgeschuetzt.

Telegram-Versand auf dem Pi testen, ohne Spoonacular aufzurufen oder den Scheduler
zu starten:

```bash
/opt/recipe-bot/.venv/bin/recipe-bot \
  --settings /etc/recipe-bot/settings.json \
  --env-file /etc/recipe-bot/.env \
  --send-test-message
```

Der Befehl sendet eine Testnachricht, erstellt oder liest keine Zustandsdatei und
beendet sich.

Anwendungscode aktualisieren, waehrend der Dienst gestoppt ist:

```bash
sudo systemctl stop recipe-bot
sudo git -C /opt/recipe-bot pull --ff-only
sudo /opt/recipe-bot/.venv/bin/pip install /opt/recipe-bot
sudo systemctl restart recipe-bot
```

`--ff-only` verhindert, dass der Pi versehentlich einen Merge-Commit erzeugt.

```bash
sudo systemctl start recipe-bot
sudo systemctl stop recipe-bot
sudo systemctl restart recipe-bot
sudo systemctl status recipe-bot
sudo systemctl disable recipe-bot
journalctl -u recipe-bot -f
systemctl show recipe-bot -p MemoryCurrent -p MainPID
ps -o pid,rss,cmd -p "$(systemctl show recipe-bot -p MainPID --value)"
```

`disable` deaktiviert nur den Start beim Booten; `disable --now` stoppt auch.
RSS wird in KiB angegeben. Speicher mit echten API-Antworten auf gewuenschten
32-Bit- und 64-Bit-Pi-OS-Images pruefen; grosse Kandidatenzahlen benoetigen mehr
Speicher. Logging geht zu journald, nie in eine separate Anwendungslogdatei.
Journald-Aufbewahrung oder fluechtigen Speicher bei Bedenken wegen SD-Karten-
Schreibzugriffen auf OS-Ebene konfigurieren.

## Wiederherstellung und Fehlerverhalten

`state.json` wird nur bei Zustandswechsel atomar ersetzt und synchronisiert.
Ein beschaedigter oder inkompatibler Zustand stoppt den Prozess, statt Quoten
stillschweigend zurueckzusetzen. Dienst stoppen und ein bekannt gutes Backup
wiederherstellen; das Loeschen des Zustands vergisst auch Wochenlimits und kann
ein doppeltes Tagesmenue erzeugen.

Sitzungskandidaten und ausgehende Nachrichten werden persistiert. Ein Neustart
setzt eine aktive Frist fort oder waehlt nach abgelaufenem Fenster sofort eine
Alternative. Befehle muessen waehrend des aktiven Fensters verarbeitet werden:
Auch ein zuvor gesendeter Befehl, der erst nach Ablauf eintrifft, setzt die
Fallback-Auswahl nicht ausser Kraft. Auswahl und Quotenwechsel werden zusammen
vor Zustellung gespeichert; Wiederholungen waehlen nicht erneut und belasten
Quoten nicht doppelt. Die Dessertquote wird beim Speichern des Tagesmenues
reserviert; Wiederholungen koennen keinen weiteren Desserttag verbrauchen.
Offline-Tage koennen die gewuenschte Dessertfrequenz nicht garantieren.

Ausgehende Nachrichten haben persistente Zustellcursor. Telegram besitzt keinen
Idempotenzschluessel fuer `sendMessage`: Ein Absturz oder eine unsichere Antwort,
nachdem Telegram eine Nachricht angenommen hat, aber vor lokaler Bestaetigung,
kann die Nachricht bei Wiederholung duplizieren. Logische Auswahl und Zaehler
sind idempotent; Telegram-Zustellung ist mindestens einmalig.

Netzwerkfehler werden mit Verzogerung wiederholt; Telegram-Ratenlimits beachten
`retry_after`. Fehlgeschlagene Spoonacular-Anfragen und Lara-Uebersetzungen senden
einen sicheren Telegram-Hinweis mit Dienst, optionalem HTTP/API-Status und
Wiederholungsergebnis. Wenn Telegram selbst nicht verfuegbar ist, wird der
Hinweisfehler protokolliert und ersetzt den urspruenglichen Fehler nicht.
Hoechstens **drei kostenpflichtige Rezeptabrufversuche pro lokalem Tag** werden
ueber Neustarts hinweg persistiert, mit mindestens fuenf Minuten Abstand zwischen
fehlgeschlagenen Versuchen. Authentifizierungs- und Quotenfehler stoppen
Rezeptabrufe fuer dieses Datum. Unvollstaendige Batches (auch fehlende
Anweisungen), doppelte Rezepte oder verletzte Vegetarisch-Filter werden
abgewiesen; es gibt keinen Fallback, der Ernaehrungsvorgaben abschwaecht.
Fehlgeschlagene Tage werden protokolliert und am naechsten Tag wiederholt, ohne
Menuequoten zu verbrauchen.

Wenn Lara-Zugangsdaten konfiguriert sind, wird jeder Rezeptkandidat uebersetzt,
bevor das Menue gespeichert oder angekuendigt wird. Ein Lara-Fehler erzeugt kein
Menue und folgt demselben Wiederholungspfad wie ein Rezeptabruf-Fehler; der
englische Fallback ist absichtlich deaktiviert. Uebersetzungswiederholungen
benoetigen einen neuen Spoonacular-Abruf und verwenden deshalb dasselbe Tageslimit
von drei Abrufen.

Telegram-Sendefehler behalten die ausstehende Outbox und blockieren neue Sitzungen,
bis die Zustellung gelingt. Berechtigungs-, Token- oder Chatfehler erfordern daher
Bedienereingriff: Zugangsdaten oder Chatberechtigungen korrigieren, dann neu
starten. Journald auf wiederholte HTTP/API-Statuscodes ueberwachen.
Konfigurations-/Zustandsfehler protokollieren nur den Ausnahmetyp, um
Zugangsdaten nicht preiszugeben. HTTP-Anfrage-URL-Logging ist aus gleichem Grund
deaktiviert. Weist Telegram ein ausgewaehltes Rezeptbild mit HTTP 400 ab,
speichert der Bot das Bild als uebersprungen und liefert dennoch Rezepttext;
alle anderen Telegram-Fehler werden wiederholt.

## SPEC-Korrekturen

Vom Nutzer bestaetigte Entscheidungen und aktuelle API-Dokumentation haben
Vorrang vor der urspruenglichen SPEC:

- Spoonacular unterstuetzt keinen deutschen Rezeptabruf, daher wird kein
  irrefuehrender `language=de`-Request gesendet. Optionale Lara-Translate-
  Integration uebersetzt Rezeptinhalte bei Konfiguration nach Deutsch; sonst
  bleiben sie Englisch.
- Der aktuelle Random-Endpunkt verwendet `include-tags`, nicht das veraltete
  `tags` der SPEC.
- Der Hauptgericht-Filter schliesst reine Dessertmenues aus; das
  Vegetarisch-Flag wird lokal geprueft.
- Zufallsrezept-Antworten enthalten bereits vollstaendige Details; keine zweite
  Anfrage erforderlich.
- Dessert ist nur ein Indikator; Fisch und Meeresfruechte zaehlen als Fleisch.
- Fortlaufender systemd-Dienst mit internem Scheduler, kein Cron.
- Zustand enthaelt ISO-Jahr, Tagessitzung, ausgehenden Zustellfortschritt,
  Abrufbudget und absolute Frist ueber das Drei-Feld-Beispiel der SPEC hinaus.

API-Referenzen: [Zufallsrezepte](https://spoonacular.com/food-api/docs#Get-Random-Recipes),
[Sprachunterstuetzung](https://spoonacular.com/food-api/faq?faq-id=19),
[Telegram-Privatsphaere](https://core.telegram.org/bots/features#privacy-mode).

## English

One lightweight Python process, supervised by systemd. An internal asyncio scheduler
starts a daily menu; Telegram long polling receives selection and resend commands.
No cron, inbound port, webhook server, or LLM needed.

## Behavior

- Five random main-course recipes at 08:00 Europe/Berlin by default.
- Select using `!bot 2`, or skip today's meal with `!bot 0`, in the configured
  chat. Other chats, bot messages, edited messages, chatter, malformed numbers,
  and inactive-window messages are ignored.
- First valid selection wins; expiry selects uniformly at random.
- Configure fixed vegetarian weekdays such as Monday and Friday. On those days,
  only vegetarian recipes are requested and every returned vegetarian flag is
  verified. Seafood and fish are not vegetarian. Unknown classification is rejected.
- Dessert is only `JA/NEIN` (or `YES/NO`), not a separate recipe. Probability is
  unused dessert days / remaining days, with forced use near week's end.
- Dessert counters use ISO year and week and reset Monday in the configured timezone.
  Downtime and New Year are handled on startup. Counts belong to the menu date.
- Set `sunday_leftovers` to `true` to skip recipe requests on Sunday and send
  `Heute kochen wir mit Resten aus dem Kühlschrank oder bestellen etwas :)` instead.
- Recipe titles, ingredients, and instructions remain **English** unless optional
  Lara Translate credentials are configured. Bot messages support German and English
  independently.

## Requirements

Linux, Python 3.10+, timezone database (`tzdata`), Telegram bot token and numeric
chat ID, Spoonacular API key. Lara Translate credentials are optional for German
recipe translation. Raspberry Pi OS Lite with Python 3.11+ recommended.

Runtime dependencies are only `httpx` and `python-dotenv` (plus their dependencies).
Direct Telegram Bot API calls avoid an additional framework and scheduler.
The SPEC's sub-50 MB RSS target must be measured on the actual Pi under realistic
recipes and network traffic; it is not a guaranteed or hard-enforced memory limit.

## Local Setup

From this project directory:

```bash
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
cp settings.json.example settings.json
chmod 600 .env
```

Configure `.env` with real credentials and `settings.json` with desired rules.
`TELEGRAM_CHAT_ID` must be a numeric ID (negative for groups), not a group name.
Environment variables take precedence over `.env`.

Set both `LARA_ACCESS_KEY_ID` and `LARA_ACCESS_KEY_SECRET` to translate recipe
titles, ingredients, and instructions from English to German. Leave both unset to
keep English recipe content. Lara credentials are created in Lara Translate's API
settings; never commit either value. Setting only one is a configuration error.

```bash
.venv/bin/recipe-bot
```

Stop with Ctrl+C. Launching another process against the same state file fails
with a lock error. Never run multiple instances with the same Telegram token,
even if they use different state files.

Test Telegram delivery without calling Spoonacular or starting the scheduler:

```bash
.venv/bin/recipe-bot --send-test-message
```

This sends `Recipe bot Telegram test successful.` to the configured chat and
exits. It does not create or read the state file.

Tests need no credentials and make no live API calls:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Configuration

See `settings.json.example`. Limits: 1-100 recipes, 1-1439 active minutes,
0-7 dessert days, `vegetarian_days` list of lowercase weekday names such as
`["monday", "friday"]`, `HH:MM` start time, IANA timezone such as `Europe/Berlin`.
`language` must be `en`; `interaction_language` can be `de` or `en`.
`trigger_codeword` is case-sensitive, without whitespace, up to 32 characters.
`sunday_leftovers` must be `true` or `false`; it defaults to `false`.
Restart after changing settings. Existing sessions retain their original trigger,
language, candidates, dessert decision, and deadline.

Use `<trigger_codeword> resend` (for example, `!bot resend`) to resend today's
current menu, selected recipe, or skip confirmation. Any group member may use it.
It never calls Spoonacular, does not consume a fetch attempt, and does not reopen
or change selection.

Daily scheduling uses local wall time; selection duration uses elapsed seconds.
A nonexistent spring-forward start time runs at the first later local time.
Repeated autumn times cannot create two sessions for one date.
If started after today's start time without a session, today's menu starts
immediately with a full window. Missed earlier dates are not replayed.
An unfinished older session is resolved/delivered before creating today's menu.
A recipe request crossing midnight is discarded, not charged to the next menu.

## Telegram Setup

1. Create bot with BotFather and add it to your group.
2. **Disable group privacy mode** using BotFather `/setprivacy`, or make bot a
   group administrator. `!bot` is not a Telegram slash command and is not reliably
   delivered with privacy mode enabled. Remove and re-add bot if needed after
   changing privacy mode.
3. Give bot permission to send messages. Use a group or private chat, not a
   broadcast channel without a separate interaction mechanism.
4. Obtain numeric chat ID from a message's `chat.id` in `getUpdates` while this
   service is stopped. Do not share token-bearing URLs or command history.

Startup removes any existing webhook for this bot, retaining pending updates.
Use a dedicated token; do not share it with another bot application.

## Raspberry Pi Deployment

### Docker

Every push to `main` runs tests and publishes an ARM64 image for an `aarch64` Pi
to GitHub Container Registry:

```text
ghcr.io/stfngr/recipesagent:latest
ghcr.io/stfngr/recipesagent:main
ghcr.io/stfngr/recipesagent:sha-<commit>
```

After first workflow run, open the repository's **Packages** page, select the
`recipesagent` container package, and set its visibility to **Public**. The Pi
can then pull it without GitHub credentials. The immutable SHA tag supports a
specific rollback.

Install Docker Engine using Docker's official instructions for your Pi OS, then
create the host configuration and persistent state directory:

```bash
sudo install -d -m 700 /etc/recipe-bot
sudo install -m 600 .env.example /etc/recipe-bot/.env
sudo install -m 600 settings.json.example /etc/recipe-bot/settings.json
sudo chown 10001:10001 /etc/recipe-bot/settings.json
sudo install -d -o 10001 -g 10001 -m 700 /var/lib/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
```

Start container:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker run -d \
  --name recipe-bot \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file /etc/recipe-bot/.env \
  --mount type=bind,src=/etc/recipe-bot/settings.json,dst=/etc/recipe-bot/settings.json,readonly \
  --mount type=bind,src=/var/lib/recipe-bot,dst=/var/lib/recipe-bot \
  ghcr.io/stfngr/recipesagent:latest
```

Inspect logs and status:

```bash
sudo docker logs -f recipe-bot
sudo docker ps --filter name=recipe-bot
```

On each pushed image, update explicitly. The state bind mount remains intact:

```bash
sudo docker pull ghcr.io/stfngr/recipesagent:latest
sudo docker rm -f recipe-bot
sudo docker run -d \
  --name recipe-bot \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --env-file /etc/recipe-bot/.env \
  --mount type=bind,src=/etc/recipe-bot/settings.json,dst=/etc/recipe-bot/settings.json,readonly \
  --mount type=bind,src=/var/lib/recipe-bot,dst=/var/lib/recipe-bot \
  ghcr.io/stfngr/recipesagent:latest
```

To roll back, replace `latest` in the pull and run commands with a published
`sha-<commit>` tag. Do not run Docker deployment and systemd deployment at the
same time: both poll Telegram using same bot token.

### Systemd

Install OS packages (these commands require administrator access):

```bash
sudo apt update
sudo apt install git python3 python3-venv tzdata
sudo useradd --system --user-group --home-dir /var/lib/recipe-bot --no-create-home recipe-bot
sudo git clone https://github.com/Stfngr/RecipesAgent.git /opt/recipe-bot
sudo install -d -m 700 /etc/recipe-bot
sudo python3 -m venv /opt/recipe-bot/.venv
sudo /opt/recipe-bot/.venv/bin/pip install /opt/recipe-bot
sudo install -m 600 /opt/recipe-bot/.env.example /etc/recipe-bot/.env
sudo install -m 600 /opt/recipe-bot/settings.json.example /etc/recipe-bot/settings.json
sudo chown recipe-bot:recipe-bot /etc/recipe-bot/settings.json
sudo chmod 755 /etc/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
sudo install -m 644 /opt/recipe-bot/systemd/recipe-bot.service /etc/systemd/system/recipe-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now recipe-bot
```

Systemd reads the root-only environment file and passes credentials to service.
Application need not read that file itself. The service's persistent state
directory is created and owned automatically by systemd. Application code and
credentials stay read-only to the service user.

Test Telegram delivery from the Pi without calling Spoonacular or starting the
service scheduler:

```bash
/opt/recipe-bot/.venv/bin/recipe-bot \
  --settings /etc/recipe-bot/settings.json \
  --env-file /etc/recipe-bot/.env \
  --send-test-message
```

The command sends one test message, does not create or read the state file, and
exits.

Update application code with the service stopped:

```bash
sudo systemctl stop recipe-bot
sudo git -C /opt/recipe-bot pull --ff-only
sudo /opt/recipe-bot/.venv/bin/pip install /opt/recipe-bot
sudo systemctl restart recipe-bot
```

`--ff-only` prevents the Pi from creating an accidental merge commit.

```bash
sudo systemctl start recipe-bot
sudo systemctl stop recipe-bot
sudo systemctl restart recipe-bot
sudo systemctl status recipe-bot
sudo systemctl disable recipe-bot
journalctl -u recipe-bot -f
systemctl show recipe-bot -p MemoryCurrent -p MainPID
ps -o pid,rss,cmd -p "$(systemctl show recipe-bot -p MainPID --value)"
```

`disable` only disables boot startup; use `disable --now` to also stop.
RSS is reported in KiB. Validate memory with real API responses on both desired
32-bit/64-bit Pi OS images; large candidate counts use more memory.
Logging goes to journald, never a separate application log file. Configure
journald retention/volatile storage at OS level if SD-card writes are a concern.

## Recovery And Failure Semantics

`state.json` is atomically replaced and fsynced only when state changes. Corrupt
or incompatible state stops the process rather than silently resetting quotas.
Stop service and restore a known-good backup when recovering state; deleting state
also forgets weekly limits and can create a duplicate daily menu.

Session candidates and outgoing messages are persisted. Restart resumes an active
deadline or immediately falls back for an expired window. Commands must be
processed while the window is active: even a previously sent command arriving
after expiry does not override fallback. Selection and quota changes are saved
together before delivery, so retries never choose again or double-charge quota.
Dessert quota is reserved when the daily session is saved; retries cannot consume
another dessert day. Offline days cannot be guaranteed to meet dessert frequency.

Outgoing messages have persistent delivery cursors. Telegram has no idempotency
key for `sendMessage`: a crash or uncertain response after Telegram accepts a
message, but before local acknowledgement, can duplicate that message on retry.
Logical selections and counters are idempotent; Telegram delivery is at-least-once.

Network errors retry with delay; Telegram rate limits honor `retry_after`.
Failed Spoonacular requests and Lara translations send a safe Telegram alert with
the service, optional HTTP/API status, and retry outcome. If Telegram itself is
unavailable, the alert failure is logged and does not replace the original retry.
At most **three paid recipe fetch attempts per local day**, persisted across
restarts, with at least five minutes between failed attempts. Auth/quota errors
stop recipe fetches for that date. Incomplete batches (including missing
instructions), duplicate recipes, or violated vegetarian filters are rejected;
there is no fallback that weakens dietary constraints. Failed days are logged
and retried the next day without consuming menu quotas.

When Lara credentials are configured, every recipe candidate is translated before
the menu is saved or announced. A Lara failure creates no menu and follows the same
retry path as a recipe-fetch failure; English fallback is deliberately disabled.
Translation retries require a fresh Spoonacular request and therefore use the same
three-attempt daily fetch budget.

Telegram send failures retain the pending outbox and block newer sessions until
delivery succeeds. Permission/token/chat errors therefore require operator action:
correct credentials/chat permissions, then restart. Monitor journald for repeated
HTTP/API status codes. Configuration/state failures log only exception type to
avoid leaking credentials. HTTP request URL logging is disabled for the same reason.
If Telegram rejects a selected recipe image with HTTP 400, the bot records that
image as skipped and still delivers recipe text; all other Telegram errors retry.

## SPEC Corrections

User-approved choices and current API documentation supersede original SPEC:

- Spoonacular does not support German recipe retrieval, so no misleading
  `language=de` request is sent. Optional Lara Translate API integration translates
  recipe content to German when configured; otherwise it remains English.
- Current random endpoint uses `include-tags`, not the SPEC's legacy `tags`.
  Main-course filter excludes dessert-only menus; vegetarian flag is verified locally.
- Random recipe responses already contain full details; no second request needed.
- Dessert is indicator-only; fish and seafood count as meat.
- Continuous systemd service with internal scheduling, not cron.
- State includes ISO year, daily session, outgoing delivery progress, fetch budget,
  and absolute deadline beyond the SPEC's three-field example.

API references: [random recipes](https://spoonacular.com/food-api/docs#Get-Random-Recipes),
[language support](https://spoonacular.com/food-api/faq?faq-id=19),
[Telegram privacy](https://core.telegram.org/bots/features#privacy-mode).
