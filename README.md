# Mineria Discord Bot

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-v2.0%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![System](https://img.shields.io/badge/System-Pathfinder%201e-DA251D)](https://www.d20pfsrd.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Mineria Discord Bot** is a high-performance companion bot built for tabletop roleplaying campaigns running on the **Pathfinder Roleplaying Game (1st Edition)** ruleset in the custom **Mineria** setting.

---

## 🛠️ Technology Stack & Programming Language

* **Primary Language:** **Python 3.10+**
* **Discord Framework:** `discord.py` (v2.0+) using modern Cogs, UI Components, and Application (Slash) Commands
* **Asynchronous Networking & I/O:** `aiohttp` (non-blocking Google Sheets CSV ingestion) and `aiofiles` (atomic asynchronous file storage)
* **Configuration:** `python-dotenv` for environment management

---

## 🎲 What It Can Do

The bot provides automated tools and utilities to assist players and Game Masters during campaign play:

* **Character Wizard & Point Distribution:**
  * Interactive character creation with race-specific dice point calculations (`41 - Race Points`).
  * Dynamic attribute rolling (`!char dr` / `/dr`) with drop-lowest dice pools and racial modifier application.
  * Interactive UI buttons for flexible racial bonuses (`+2 to Any`) and automated class recommendations.
  * Complete character sheet management (save, inspect, list, edit, rename, and delete).

* **Polyhedral Dice Engine:**
  * Advanced dice rolling (`!roll` / `/roll`) supporting keep-highest (`4d6kh3`), keep-lowest (`2d20kl1`), drop dice, inline arithmetic, and multi-roll sequences with highlighted kept and dropped dice.

* **Traits & Drawbacks System:**
  * Filter and draw from over 1,300 campaign traits categorized by theme (Combat, Magic, Nobility, Hunter, Nature, etc.) or race.
  * 24-hour reroll window (`!trait reroll`) with duplicate prevention.
  * Random drawback lookups (`!drawback`) with direct rulebook reference links.

* **Campaign Documents & Tactical Battlemaps:**
  * Access campaign rulebooks, PDFs, and tactical battlemap images directly inside Discord (`!doc`, `!map`).

* **Live Campaign XP & Player Progression:**
  * Asynchronously fetches live campaign progress from Google Sheets with in-memory caching.
  * Calculates starting XP and levels for replacement characters (`!kia`, `!mia`).
  * Queries player GM session counts and character histories (`!gm`).

* **Administrative Guard & Security:**
  * Granular command restriction engine (`!admin disable`, `!admin enable`, `!admin only`) with developer-only authentication and customizable user bypass permissions.

---

## 🚀 Quick Start

### Prerequisites
* Python 3.10 or newer
* A registered Discord Bot application with Message Content and Server Members intents enabled.

### Installation & Run

1. Clone the repository:
   ```bash
   git clone https://github.com/Sanji-devv/MineriaDiscordBot.git
   cd MineriaDiscordBot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure your `.env` file in the root directory:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   XP_SHEET_URL=your_google_sheets_csv_url_here
   ```

4. Launch the bot:
   ```bash
   python main.py
   ```