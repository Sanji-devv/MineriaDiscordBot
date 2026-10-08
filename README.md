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
- **Stat Distribution Engine (`!char dr` / `!dr`):** Distribute dice counts across the 6 core stats (STR, DEX, CON, INT, WIS, CHA). The bot rolls $N$ d6 for each stat, drops the lowest dice (`~~1~~`), sums the highest 3, and applies racial modifiers.
- **Flexible Racial Bonus UI:** Interactive buttons (`+2 STR`, `+2 DEX`, etc.) appear for races with flexible bonuses, applying changes in real time.
- **Class Recommendation Engine:** Evaluates rolled stat profiles against class archetypes to suggest optimal class options (toggleable via `!rec open` / `!rec close`).
- **Character Persistence:** Save (`!char save`), list (`!char list`), inspect (`!char info`), rename (`!char rename`), edit stats/classes (`!char edit`), or delete (`!char delete`) saved character sheets stored atomically in `data/characters.json`.

### 2. 📊 Live Campaign XP & Player Tracking (`utility.py`)
- Fetches live data from the campaign's Google Sheet using asynchronous HTTP requests with TTL caching.
- Computes replacement character starting XP and current levels via `!kia <name>` (50% task XP) and `!mia <name>` (90% task XP).
- Queries total GM sessions and GM history for any player across all active and archived characters via `!gm <player>`.

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
- Interactive page-turning buttons allow users to browse multi-page documents and maps without leaving Discord (`!doc`, `!map`).

### 6. ⚙️ Administration & Command Guard Suite (`admin.py`, `log_handler.py`, `error_handler.py`)
- **Granular Command Guard:** Restrict root commands (e.g. `!admin disable roll`), specific subcommands (e.g. `!admin disable char create`), or all commands globally (`!admin disable all`).
- **Bypass Authorization:** Authorized administrators and users in the bypass list (`!admin allow <user>`) retain execution privileges even when commands are restricted.
- **Hierarchical Resolution:** Intelligent resolution ensures enabling or disabling parent groups cleanly manages child subcommands.
- **Concise Log Summaries:** Clean log format (`[Server/#channel] User: !cmd -> Status`) for minimal noise in console and `logs/mineria.log`.
- **Robust Exception Handling:** Catches transient Discord API exceptions (`discord.NotFound`, `discord.Forbidden`) and gracefully handles restricted command notices.

---

## 📋 Command Reference

### 🎲 Dice & Rules
| Command | Example | Description |
|---|---|---|
| `!roll <expr>` / `!r` | `!r 4d6kh3+2` | Roll dice with keep-highest, drop-lowest, and modifiers. |
| `!trait [category/race]` | `!trait combat` | Suggest a random trait matching category or race. |
| `!trait reroll` | `!trait reroll` | Reroll your last trait roll within 24 hours. |
| `!drawback` | `!drawback` | Fetch a random character drawback. |

### 👤 Character Wizard & Management
| Command | Example | Description |
|---|---|---|
| `!char create <race>` | `!char create Human` | Initiate the character creation wizard for a race. |
| `!char dr <stats>` / `!dr` | `!char dr 6 5 5 5 5 5` | Allocate dice counts to STR, DEX, CON, INT, WIS, CHA and roll. |
| `!char add <stat> <val>` | `!char add STR 2` | Add bonus points to a stat during creation. |
| `!char remove <stat> <val>` | `!char remove DEX 1` | Remove points from a stat during creation. |
| `!char cancel` | `!char cancel` | Cancel and discard active character creation session. |
| `!char save <name>` | `!char save "Aric Thorn"` | Finalize and save your character sheet. |
| `!char info [name]` | `!char info "Aric Thorn"` | Display full character stats and details. |
| `!char list` | `!char list` | List all saved characters owned by your account. |
| `!char edit class <name> <class>` | `!char edit class Aric Fighter` | Change a saved character's class. |
| `!char edit stat <name> <stat> <val>` | `!char edit stat Aric STR 18` | Modify a specific attribute score on a saved sheet. |
| `!char rename <old> <new>` | `!char rename Aric Aric II` | Rename an existing character. |
| `!char delete <name>` | `!char delete Aric` | Permanently delete a character sheet. |
| `!char kia <name>` | `!char kia Aric` | Calculate KIA replacement XP for a fallen saved character. |
| `!char mia <name>` | `!char mia Aric` | Calculate MIA replacement XP for a missing saved character. |
| `!rec open` / `!rec close` | `!rec open` | Enable or disable automatic class recommendations. |

### 📊 Campaign & Player Tracking
| Command | Example | Description |
|---|---|---|
| `!kia <name>` | `!kia Varka` | Calculate starting XP for a replacement after character death (50% Task XP). |
| `!mia <name>` | `!mia Varka` | Calculate starting XP with Missing-in-Action penalties (90% Task XP). |
| `!gm <player>` | `!gm "player name"` | View total GM sessions and GM history for a player across all characters. |

### 📁 Documents, Maps & General
| Command | Example | Description |
|---|---|---|
| `!doc [name]` | `!doc rules.pdf` | Browse or download campaign PDF documents. |
| `!doc list` | `!doc list` | List all available campaign PDF documents. |
| `!map [name]` | `!map world_map` | Browse campaign tactical maps with page navigation. |
| `!map list` | `!map list` | List all available tactical battlemaps. |
| `!wiki` | `!wiki` | Display official Mineria and Pathfinder Wiki links. |
| `!help` / `!m` | `!m` | Open the interactive bot command terminal. |

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).