# SPEC.md: Automated Daily Recipe Telegram Bot

## 1. Executive Summary
The goal of this project is to build an autonomous, resource-efficient Telegram bot running on a Raspberry Pi 3 Model B. The bot fetches daily recipe suggestions from the Spoonacular API based on predefined weekly constraints (fixed vegetarian weekdays, dessert frequency), presents a summary list to a Telegram chat along with a simple **YES/NO** indicator for dessert, listens for a user selection using a specific activation trigger/codeword, and falls back to an automatic selection if a timeout occurs.

---

## 2. Technical Stack & Deployment Constraints
* **Language:** Python 3.10+
* **Target Hardware:** Raspberry Pi 3 Model B (1GB RAM)
* **OS Environment:** Raspberry Pi OS Lite (64-bit / 32-bit)
* **Frameworks/Libraries:** 
  * `python-telegram-bot` (or `telebot` / `aiogram` for lightweight async handling)
  * `requests` / `httpx`
  * `python-dotenv`
  * `APScheduler` or native `cron` / `asyncio` loop for time-based triggers
* **Recipe API:** Spoonacular REST API
* **Language Support:** English & German for chat interactions; Recipe content fetched in German.

---

## 3. Configuration & State Management

### 3.1 Environment File (`.env`)
Stores credentials and sensitive deployment details:
```env
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
TELEGRAM_CHAT_ID="your_telegram_chat_id_or_group_id"
SPOONACULAR_API_KEY="your_spoonacular_api_key"
```

### 3.2 Settings File (`settings.json`)
Stores application parameters that govern business logic:
```json
{
  "recipes_per_day": 5,
  "start_time": "08:00",
  "active_window_minutes": 60,
  "language": "de",
  "trigger_codeword": "!bot",
  "vegetarian_days": ["monday", "friday"],
  "dessert_days_per_week": 2,
  "interaction_language": "de"
}
```

### 3.3 Application State Persistence (`state.json`)
Tracks weekly limits and reset timers across restarts:
```json
{
  "current_week_number": 36,
  "dessert_days_used_this_week": 0
}
```

---

## 4. Business Logic & Weekly Rules

### 4.1 State Reset
* Every Monday at 00:00, the system resets `dessert_days_used_this_week` to `0`.

### 4.2 Meat Constraint Logic
* `vegetarian_days` contains lowercase weekday names. For a configured day, the Spoonacular query filter must strictly append `tags=vegetarian` to force 100% vegetarian suggestions.
* Each returned recipe on a configured vegetarian day must have `vegetarian: true`. Fish and seafood are not vegetarian.

### 4.3 Dessert Indicator Logic (YES / NO)
* The bot evaluates whether dessert is available today based on remaining allowed days:
  * **Days Remaining in Week:** `7 - Current Day Index (0 = Mon, 6 = Sun)`
  * **Dessert Days Needed:** `dessert_days_per_week - dessert_days_used_this_week`
* If `Dessert Days Needed > 0`:
  * Calculate probability or randomly decide if today features dessert (`true` or `false`).
  * Force dessert to `true` if `Days Remaining <= Dessert Days Needed`.
* If dessert is selected for today (`true`), increment `dessert_days_used_this_week` and output **"Dessert heute: JA"** in the daily message. Otherwise, output **"Dessert heute: NEIN"**.

---

## 5. System Workflow & Lifecycle

```
[Start Time Reached]
        │
        ▼
[Check Weekly Constraints & Reset State if New Week]
        │
        ▼
[Fetch X Recipes from Spoonacular (German)]
        │
        ▼
[Send Summary List & Dessert Status (YES/NO) to Telegram]
        │
        ▼
[Set Bot Status: ACTIVE] ◄─── (Listens ONLY to trigger_codeword)
        │
        ├─────────────────────────────────────────┐
        ▼                                         ▼
[Valid Selection Received]               [Timeout Reached]
        │                                         │
        ▼                                         ▼
[Extract Chosen Recipe]                  [Randomly Select Recipe]
        │                                         │
        └────────────────────┬────────────────────┘
                             │
                             ▼
              [Fetch & Send Full Recipe Details]
                             │
                             ▼
                   [Set Bot Status: INACTIVE]
```

### 5.1 Stage 1: Scheduled Trigger
1. At `start_time` (e.g., 08:00), the bot initializes a new selection session.
2. Evaluates fixed vegetarian-day and dessert constraints for the current day.
3. Calls Spoonacular API (`/recipes/random`) fetching `recipes_per_day` items in German (`language=de`).
4. Format and post the summary list to Telegram:
   > 🤖 **Heutige Rezeptauswahl:**
   > 1. Gemüselasagne
   > 2. Rindergulasch mit Rotkohl
   > 3. Linsensalat mit Feta
   > 
   > 🍨 **Dessert heute:** JA
   > 
   > *Wählt ein Rezept mit: `!bot 2`* (Aktiv für 60 Minuten)

### 5.2 Stage 2: Active Listening Window
* The bot enters an **ACTIVE** listening state for `active_window_minutes`.
* **Filtering:** To minimize background noise, the bot parses group messages **only** if they start with the `trigger_codeword` (e.g., `!bot 2` or `!rezept 1`). All other chat chatter is completely ignored.
* If a valid input (`!bot <number>`) is received from any user in the chat:
  1. Lock selection to `<number>`.
  2. Transition to Stage 3 immediately.
* A valid `!bot 0` skips today's meal. The bot sends a confirmation message and remains inactive until the next day's `start_time`.

### 5.3 Stage 3: Fallback & Final Resolution
* **Timeout Fallback:** If `active_window_minutes` elapses without a valid trigger command, the bot logs a timeout event and selects a recipe uniformly at random from the day's candidate list.
* The bot posts the full recipe details (ingredients, prep time, step-by-step instructions) to the channel.
* The bot enters an **INACTIVE** state until the next day's `start_time`.

---

## 6. API & Integration Details

### Spoonacular API Requests
* **Endpoint:** `GET https://api.spoonacular.com/recipes/random`
* **Query Parameters:**
  * `apiKey`: `{SPOONACULAR_API_KEY}`
  * `number`: `{recipes_per_day}`
  * `language`: `de`
  * `tags`: `vegetarian` (conditionally appended for configured vegetarian weekdays)

---

## 7. Non-Functional Requirements & Performance
* **Memory Footprint:** Must run reliably under 50 MB RAM (ideal for Pi 3B with 1GB total RAM).
* **Process Management:** Packaged with a `systemd` service file (`recipe-bot.service`) to auto-restart on failure or reboot.
* **Logging:** Minimal file-based or `stdout` logging via `systemd-journald` to prevent SD-card wear out.
