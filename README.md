# 🎲 Mineria Discord Bot

A specialized Discord bot for **Pathfinder Roleplaying Game (1st Edition)** — character management, dice rolling, and server registry tools.

## ⚡ Prefixes

| Prefix | Example |
|--------|---------|
| `!` | `!roll 2d20+5` |
| `!m ` | `!m roll 2d20+5` |
| `!mineria ` | `!mineria roll 2d20+5` |

---

## 🎲 Core Commands

| Command | Example | Description |
|---------|---------|-------------|
| `!roll <expr>` | `!roll 4d6k3` | Roll dice. Supports modifiers (`+5`), keep-highest (`k3`), multi-roll (`d20, d6`). |
| `!trait <cat>`| `!trait combat` | Suggest a random trait for a specific category. |
| `!drawback` | `!drawback` | Display a random character drawback. |

---

## 👤 Character Management

| Command | Example | Description |
|---------|---------|-------------|
| `!char create <race>` | `!char create Elf`| Start the character creation wizard. |
| `!char dr <stats>` | `!char dr 18 16 14` | Distribute rolled stats to your character. |
| `!char save <name>` | `!char save Varka` | Finalize and save the character. |
| `!char list` | `!char list` | List all registered characters you own. |
| `!char info [name]` | `!char info Varka` | Display the detailed character sheet. |
| `!char edit` | `!char edit`| Character management operations (also `rename`, `delete`). |
| `!kia <name>` | `!kia Varka` | Calculate starting XP for a new character (Death penalty). |
| `!mia <name>` | `!mia Varka` | Calculate starting XP for a new character (Missing penalty). |
| `!rec` | `!rec` | Toggle automatic class recommendations on/off. |

---

## 🛠️ Utility & Documents

| Command | Example | Description |
|---------|---------|-------------|
| `!d` / `!dup` | `!d` | **Duplicate Check**: Scans the XP Sheet for players violating the "1 Ranked + 1 Clerk" rule. |
| `!doc [name]` | `!doc rules.pdf` | List or download files from the `mineria_files/docs/` directory. |
| `!map [name]` | `!map city` | List or view map images from the `mineria_files/maps/` directory. |
| `!wiki` | `!wiki` | Show official Mineria & Pathfinder Wiki links. |
| `!help` / `!m` | `!help` | Displays the modern help menu. |

---

## ⚙️ Admin Commands (Owner Only)

| Command | Example | Description |
|---------|---------|-------------|
| `!backup` | `!backup` | Triggers an immediate manual zip backup of user data. |
| `!sync` | `!sync` | Syncs the Discord slash command tree. |
## ⚙️ Features

- **Duplicate Player Detection** — Enforces 1 Ranked + 1 Clerk rule via live Google Sheet data.
- **Character Creation** — Full suite to roll, allocate, and save characters.
- **Document & Map Serving** — Instantly download PDFs or view maps from the bot.
- **Automated Backups** — Daily/weekly backups of the `datas/` folder.
- **Robust Logging** — Detailed logs in `logs/mineria.log` with minimal chat spam.

---

## 🚀 Installation

```bash
# 1. Clone the repository
git clone <repo-url>

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file
echo DISCORD_TOKEN_TEST=your_token_here > .env

# 4. Run
python main.py
```

---

## 📁 Data Structure

| File/Dir | Description |
|----------|-------------|
| `datas/characters.json` | Saved user characters |
| `datas/races.json` | Race definitions & modifiers |
| `datas/classes.json` | Class definitions & primary stats |
| `datas/drawbacks.json` | Database of character drawbacks |
| `datas/traits.json` | Database of character traits |
| `mineria_files/docs/` | Documents served by `!doc` |
| `mineria_files/maps/` | Images served by `!map` |
| `logs/mineria.log` | Application log |
| `backups/` | Automated daily/weekly ZIP backups |