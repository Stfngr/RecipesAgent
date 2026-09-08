# Daily Recipe Telegram Bot

One lightweight Python process, supervised by systemd. An internal asyncio scheduler
starts a daily menu; Telegram long polling accepts the first valid selection during
the active window. No cron, inbound port, webhook server, or LLM needed.

## Behavior

- Five random main-course recipes at 08:00 Europe/Berlin by default.
- Select using `!bot 2` in the configured chat. Other chats, bot messages, edited
  messages, chatter, malformed numbers, and inactive-window messages are ignored.
- First valid selection wins; expiry selects uniformly at random.
- Seafood and fish count as meat: every recipe with `vegetarian: false` consumes
  one meat day. At the limit, query vegetarian recipes and verify every result's
  vegetarian flag. Unknown classification is rejected, never treated as safe.
- Dessert is only `JA/NEIN` (or `YES/NO`), not a separate recipe. Probability is
  unused dessert days / remaining days, with forced use near week's end.
- Counters use ISO year and week and reset Monday in the configured timezone.
  Downtime and New Year are handled on startup. Counts belong to the menu date.
- Recipe titles, ingredients, and instructions remain **English**. Bot messages
  support German and English independently.

## Requirements

Linux, Python 3.10+, timezone database (`tzdata`), Telegram bot token and numeric
chat ID, Spoonacular API key. Raspberry Pi OS Lite with Python 3.11+ recommended.

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
0-7 meat/dessert days, `HH:MM` start time, IANA timezone such as `Europe/Berlin`.
`language` must be `en`; `interaction_language` can be `de` or `en`.
`trigger_codeword` is case-sensitive, without whitespace, up to 32 characters.
Restart after changing settings. Existing sessions retain their original trigger,
language, candidates, dessert decision, and deadline.

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

Install OS packages (these commands require administrator access):

```bash
sudo apt update
sudo apt install python3 python3-venv tzdata
sudo useradd --system --user-group --home-dir /var/lib/recipe-bot --no-create-home recipe-bot
sudo install -d -m 755 /opt/recipe-bot
sudo install -d -m 700 /etc/recipe-bot
sudo python3 -m venv /opt/recipe-bot/.venv
sudo /opt/recipe-bot/.venv/bin/pip install .
sudo install -m 600 .env.example /etc/recipe-bot/.env
sudo install -m 600 settings.json.example /etc/recipe-bot/settings.json
sudo chown recipe-bot:recipe-bot /etc/recipe-bot/settings.json
sudo chmod 755 /etc/recipe-bot
sudoedit /etc/recipe-bot/.env
sudoedit /etc/recipe-bot/settings.json
sudo install -m 644 systemd/recipe-bot.service /etc/systemd/system/recipe-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now recipe-bot
```

Systemd reads the root-only environment file and passes credentials to service.
Application need not read that file itself. The service's persistent state
directory is created and owned automatically by systemd. Application code and
credentials stay read-only to the service user.

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
At most **three paid recipe fetch attempts per local day**, persisted across
restarts, with at least five minutes between failed attempts. Auth/quota errors
stop recipe fetches for that date. Incomplete batches (including missing
instructions), duplicate recipes, or violated vegetarian filters are rejected;
there is no fallback that weakens dietary constraints. Failed days are logged
and retried the next day without consuming menu quotas.

Telegram send failures retain the pending outbox and block newer sessions until
delivery succeeds. Permission/token/chat errors therefore require operator action:
correct credentials/chat permissions, then restart. Monitor journald for repeated
HTTP/API status codes. Configuration/state failures log only exception type to
avoid leaking credentials. HTTP request URL logging is disabled for the same reason.

## SPEC Corrections

User-approved choices and current API documentation supersede original SPEC:

- English recipe content for now; German UI retained. Spoonacular does not support
  German recipe retrieval, so no misleading `language=de` request is sent.
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
