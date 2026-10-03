# 🎲 Mineria Discord Bot

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py v2](https://img.shields.io/badge/discord.py-v2.0+-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Pathfinder 1e](https://img.shields.io/badge/System-Pathfinder%201e-DA251D)](https://www.d20pfsrd.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Mineria Discord Bot** is a feature-rich, high-performance companion bot tailored for **Pathfinder Roleplaying Game (1st Edition)** tabletop campaigns set within the custom **Mineria** universe.

---

## 🎯 Project Overview & Vision

Tabletop RPG campaigns—especially those powered by complex, rule-dense systems like **Pathfinder 1e**—require significant bookkeeping, mathematical validation, and frequent rule references. Managing race-dependent stat budgets, multi-die rolling with drop-lowest logic, trait/drawback selection, and live campaign experience point (XP) tracking can often slow down gameplay.

**Mineria Discord Bot** acts as an all-in-one digital companion designed to:
- **Automate Complex Stat Distribution:** Calculates stat point budgets dynamically based on racial power (`Dice Points = 41 - Race Points`), simulates multi-dice rolls keeping the top 3 (`4d6kh3`), and provides interactive Discord buttons (`BonusSelectView`) for flexible racial stat bonuses (`+2 to Any`).
- **Synchronize Live Campaign XP:** Ingests master campaign Google Sheets via non-blocking, one-way read-only CSV parsing to calculate character levels, total XP, and remaining XP to next level.
- **Serve Interactive Maps & Documents:** Renders campaign lore PDFs and tactical map images directly inside Discord embeds with button-driven pagination.
- **Streamline Roleplay & Build Crafting:** Filters thousands of traits and drawbacks by category and race keywords, supporting a 24-hour reroll cache.

---

## ⚡ Command Prefixes

The bot listens to the following prefixes:

| Prefix | Example Usage |
|---|---|
| `!` | `!roll 4d6kh3` |
| `!m ` | `!m char create Elf` |
| `!mineria ` | `!mineria doc rules.pdf` |

---

## 🌟 Core Modules & Key Features

### 1. 🧙‍♂️ Interactive Character Wizard (`character.py`)
- **Race Points Budgeting:** Point budget automatically adapts to racial power (`Budget = 41 - Race Points`, minimum floor of 18).
- **Stat Distribution Engine (`!char dr`):** Distribute dice counts across the 6 core stats (STR, DEX, CON, INT, WIS, CHA). The bot rolls $N$ d6 for each stat, drops the lowest dice (`~~1~~`), sums the highest 3, and applies racial modifiers.
- **Flexible Racial Bonus UI:** Interactive buttons (`+2 STR`, `+2 DEX`, etc.) appear for races with flexible bonuses, applying changes in real time.
- **Class Recommendation Engine:** Evaluates rolled stat profiles against class archetypes to suggest optimal class options (toggleable via `!rec`).
- **Character Persistence:** Save (`!char save`), list (`!char list`), inspect (`!char info`), rename, edit stats/classes, or delete saved character sheets stored atomically in `datas/characters.json`.

### 2. 📊 Live Campaign XP & Player Tracking (`utility.py`)
- Fetches live data from the campaign's Google Sheet using asynchronous HTTP requests with TTL caching.
- Computes replacement character starting XP and current levels via `!kia <name>`, `!retire <name>`, `!mia <name>`, and `!newchar <name>`.
- Detects roster rule violations (e.g., "1 Ranked + 1 Clerk" rule) via `!dup` / `!d`.
- Queries total GM sessions and GM history for any player across all active and archived characters via `!gm <player>`.
- Ranks a player's characters by total missions and games played via `!best <player>`.

### 3. 🎲 Advanced Dice Engine (`dice.py`)
- Full support for Pathfinder 1e and general TTRPG dice expressions:
  - Keep-highest: `4d6kh3` or `4d6k3`
  - Keep-lowest: `2d20kl1`
  - Inline arithmetic & modifiers: `1d20+7+1d4-2`
  - Multi-roll sequences: `d20, d6, 2d8`
- Visual feedback highlighting dropped dice (`~~2~~`) and natural criticals (1s and 20s).

### 4. 📜 Trait & Drawback Library (`traits.py`, `drawbacks.py`)
- **Category & Race Filtering:** Filter traits by category (Combat, Magic, Faith, Social, etc.) or race queries (`!trait combat`, `!trait Elf`).
- **24-Hour Reroll Cache:** Tracks user rolls for 24 hours to enable deliberate character crafting and reroll validation (`!trait reroll`).
- **Drawback Lookup:** Generates random character drawbacks with rich descriptive embeds (`!drawback`).

### 5. 🗺️ Document & Tactical Map Viewer (`documents.py`)
- Serves campaign PDFs and map imagery stored in `mineria_files/docs/` and `mineria_files/maps/`.
- Interactive page-turning buttons allow users to browse multi-page documents and maps without leaving Discord.

### 6. ⚙️ Administration, Security & Clean Logging (`admin.py`, `log_handler.py`, `error_handler.py`)
- **Concise Log Summaries:** Clean log format (`[Server/#channel] User: !cmd -> Status`) for minimal noise in console and `logs/mineria.log`.
- **Robust Exception Handling:** Catches transient Discord API exceptions (`discord.NotFound`, `discord.Forbidden`) to ensure high availability.

---

## 📋 Command Reference

### 🎲 Dice & Rule Lookups
| Command | Example | Description |
|---|---|---|
| `!roll <expr>` / `!r` | `!r 4d6kh3+2` | Roll dice with keep-highest, drop-lowest, and modifiers. |
| `!trait [category/race]` | `!trait combat` | Suggest a random trait matching category or race. |
| `!trait reroll` | `!trait reroll` | Reroll your last trait roll within 24 hours. |
| `!drawback` | `!drawback` | Fetch a random character drawback. |
| `!d20 <query>` | `!d20 Fireball` | Search rules and spells on D20PFSRD. |

### 👤 Character Management
| Command | Example | Description |
|---|---|---|
| `!char create <race>` | `!char create Human` | Initiate the character creation wizard for a race. |
| `!char dr <distribution>` | `!char dr 6 5 5 5 5 5` | Allocate dice to the 6 core stats and roll. |
| `!char save <name>` | `!char save "Aric Thorn"` | Save your rolled character sheet. |
| `!char list` | `!char list` | List all saved characters owned by your account. |
| `!char info [name]` | `!char info "Aric Thorn"` | Display full character stats and details. |
| `!char rename <old> <new>` | `!char rename Aric Aric II` | Rename an existing character. |
| `!char delete <name>` | `!char delete Aric` | Permanently delete a character sheet. |
| `!rec` | `!rec` | Toggle automatic class recommendations on/off. |

### 📊 Campaign & Analytics Commands
| Command | Example | Description |
|---|---|---|
| `!stats [user]` | `!stats` | Generate 4-panel visual stat distribution charts. |
| `!stats summary` | `!stats summary` | View numerical breakdown, score brackets, and records. |
| `!stats history` | `!stats history` | View recent distribution roll logs. |
| `!kia <name>` | `!kia Varka` | Calculate starting XP for a replacement after character death. |
| `!mia <name>` | `!mia Varka` | Calculate starting XP with Missing-in-Action penalties. |
| `!gm <player>` | `!gm "player name"` | View total GM sessions and GM history for a player across all characters. |

### 📁 Documents, Maps & General
| Command | Example | Description |
|---|---|---|
| `!doc [name]` | `!doc rules.pdf` | Browse or download campaign PDF documents. |
| `!map [name]` | `!map world_map` | Browse campaign tactical maps with page navigation. |
| `!wiki` | `!wiki` | Display official Mineria and Pathfinder Wiki links. |
| `!help` / `!m` | `!m` | Open the interactive bot command terminal. |

### 🔒 Admin Commands (Authorized Only)
| Command | Example | Description |
|---|---|---|
| `!admin disable <cmd>` | `!admin disable char create` | Restrict a command globally across all servers. |
| `!admin enable <cmd>` | `!admin enable char create` | Re-enable a command for all server members. |
| `!admin list` | `!admin list` | View all currently restricted commands. |

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).