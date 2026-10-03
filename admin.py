import re
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union, Set
import discord
from discord import app_commands
from discord.ext import commands, tasks
from log_handler import logger

# =============================================================================
# SECTION 1: COMMAND GUARD CONFIGURATION & CONSTANTS
# =============================================================================

ENV_FILE = Path(__file__).parent / ".env"
_ENV_LOCK = asyncio.Lock()

# Permanent Developer ID: retains absolute bypass authority across all commands
DEVELOPER_ID = 388683129658933259

# Core administrative and emergency recovery commands that can NEVER be disabled
PROTECTED_COMMANDS: Set[str] = {
    "admin",
    "gm", "player", "gmcheck", "pinfo"
}

_SETTINGS_CACHE: Optional[Dict[str, Any]] = None


class CommandDisabledError(commands.CheckFailure):
    def __init__(self, message: str = "This command is temporarily disabled by admin."):
        self.message = message
        super().__init__(self.message)


def normalize_command_name(name: str) -> str:
    if not name:
        return ""
    name = name.strip().lower()
    # Handle command prefixes (e.g. "!mineria ", "!m ", "!", "/")
    for prefix in ["!mineria ", "!m ", "!", "/"]:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    name = re.sub(r"\s+", " ", name)
    return name


def extract_user_id(val: str) -> Optional[int]:
    if not val:
        return None
    cleaned = val.strip().replace("<@", "").replace("!", "").replace(">", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


# =============================================================================
# SECTION 2: ATOMIC .ENV PERSISTENCE ENGINE
# =============================================================================

async def get_command_settings(force_reload: bool = False) -> Dict[str, Any]:
    global _SETTINGS_CACHE
    if not force_reload and _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE

    disabled_list: List[str] = []
    allowed_users: List[int] = [DEVELOPER_ID]

    if ENV_FILE.exists():
        try:
            content = await asyncio.to_thread(ENV_FILE.read_text, encoding="utf-8")
            for raw_line in content.splitlines():
                stripped_line = raw_line.strip()
                if not stripped_line or stripped_line.startswith("#"):
                    continue
                if "=" in stripped_line:
                    key, val = stripped_line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    if key == "DISABLED_COMMANDS":
                        disabled_list = [c.strip().lower() for c in val.split(",") if c.strip()]
                    elif key == "ALLOWED_USERS":
                        for raw_uid in val.split(","):
                            u_str = raw_uid.strip()
                            if u_str.isdigit():
                                allowed_users.append(int(u_str))
        except Exception as exc:
            logger.error(f"Error reading .env for Command Guard settings: {exc}")

    allowed_users = sorted(list(set(allowed_users)))

    _SETTINGS_CACHE = {
        "disabled_commands": disabled_list,
        "allowed_users": allowed_users
    }
    return _SETTINGS_CACHE


async def save_command_settings(settings: Dict[str, Any]) -> None:
    global _SETTINGS_CACHE
    _SETTINGS_CACHE = settings

    disabled_str = ", ".join(settings.get("disabled_commands", []))
    allowed_str = ", ".join(str(uid) for uid in settings.get("allowed_users", [DEVELOPER_ID]))

    updates = {
        "DISABLED_COMMANDS": disabled_str,
        "ALLOWED_USERS": allowed_str
    }

    def _atomic_env_write() -> None:
        lines: List[str] = []
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

        remaining = dict(updates)
        new_lines: List[str] = []

        # Update existing keys in-place
        for line in lines:
            matched_key = None
            for key in remaining:
                if re.match(rf"^\s*{key}\s*=", line):
                    matched_key = key
                    break
            if matched_key:
                new_lines.append(f'{matched_key}="{remaining.pop(matched_key)}"')
            else:
                new_lines.append(line)

        # Append any new configuration keys
        for key, val in remaining.items():
            new_lines.append(f'{key}="{val}"')

        # Atomic file swap via temporary file
        tmp_path = ENV_FILE.with_suffix(".tmp")
        tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        tmp_path.replace(ENV_FILE)

    async with _ENV_LOCK:
        await asyncio.to_thread(_atomic_env_write)


# =============================================================================
# SECTION 3: AUTHORIZATION & COMMAND RESTRICTION LOGIC
# =============================================================================

async def is_user_authorized(bot: commands.Bot, user: Union[discord.User, discord.Member]) -> bool:
    if not user:
        return False
    # Developer ID retains absolute authorization
    if user.id == DEVELOPER_ID:
        return True

    # Check allowed_users list from .env
    try:
        settings = await get_command_settings()
        allowed_users = settings.get("allowed_users", [])
        if user.id in allowed_users:
            return True
    except Exception as exc:
        logger.error(f"Error checking allowed_users authorization: {exc}")

    # Check Discord application owner status
    try:
        if hasattr(bot, "is_owner") and await bot.is_owner(user):
            return True
    except Exception:
        pass

    return False


def is_command_disabled(cmd_name: str, settings: Dict[str, Any]) -> bool:
    cmd_name = normalize_command_name(cmd_name)
    if not cmd_name:
        return False

    # Handle protected system commands immunity
    first_word = cmd_name.split(" ")[0]
    if first_word in PROTECTED_COMMANDS:
        return False

    disabled_list = settings.get("disabled_commands", [])
    for d in disabled_list:
        d_norm = normalize_command_name(d)
        if cmd_name == d_norm or cmd_name.startswith(d_norm + " "):
            return True

    return False


async def disable_command(command_name: str) -> Tuple[bool, str]:
    normalized = normalize_command_name(command_name)
    if not normalized:
        return False, "Invalid or empty command name."

    first_word = normalized.split(" ")[0]
    if first_word in PROTECTED_COMMANDS:
        return False, f"`{normalized}` is a protected system command and cannot be disabled."

    settings = await get_command_settings(force_reload=True)
    disabled = settings.setdefault("disabled_commands", [])

    for d in disabled:
        if normalize_command_name(d) == normalized:
            return False, f"`{normalized}` is already disabled."

    disabled.append(normalized)
    if DEVELOPER_ID not in settings.setdefault("allowed_users", []):
        settings["allowed_users"].append(DEVELOPER_ID)

    await save_command_settings(settings)
    logger.info(f"Command '{normalized}' has been DISABLED by administrator.")
    return True, f"`{normalized}` has been successfully disabled."


async def enable_command(command_name: str) -> Tuple[bool, str]:
    normalized = normalize_command_name(command_name)
    if not normalized:
        return False, "Invalid or empty command name."

    settings = await get_command_settings(force_reload=True)
    disabled = settings.get("disabled_commands", [])

    matched = [d for d in disabled if normalize_command_name(d) == normalized]
    if not matched:
        return False, f"`{normalized}` is already enabled."

    settings["disabled_commands"] = [d for d in disabled if normalize_command_name(d) != normalized]
    if DEVELOPER_ID not in settings.setdefault("allowed_users", []):
        settings["allowed_users"].append(DEVELOPER_ID)

    await save_command_settings(settings)
    logger.info(f"Command '{normalized}' has been ENABLED by administrator.")
    return True, f"`{normalized}` has been successfully re-enabled."



async def add_allowed_user(user_id: int) -> Tuple[bool, str]:
    settings = await get_command_settings(force_reload=True)
    allowed = settings.setdefault("allowed_users", [])
    if user_id in allowed:
        return False, f"<@{user_id}> (`{user_id}`) is already in the allowed list."

    allowed.append(user_id)
    await save_command_settings(settings)
    return True, f"<@{user_id}> (`{user_id}`) was successfully added to the bypass list."


async def remove_allowed_user(user_id: int) -> Tuple[bool, str]:
    if user_id == DEVELOPER_ID:
        return False, "The permanent developer ID cannot be removed from the allowed list."

    settings = await get_command_settings(force_reload=True)
    allowed = settings.get("allowed_users", [])
    if user_id not in allowed:
        return False, f"<@{user_id}> (`{user_id}`) was not found in the allowed list."

    settings["allowed_users"] = [uid for uid in allowed if uid != user_id]
    await save_command_settings(settings)
    return True, f"<@{user_id}> (`{user_id}`) was successfully removed from the bypass list."


def get_interaction_command_name(interaction: discord.Interaction) -> Optional[str]:
    if not interaction.data:
        return None
    data = interaction.data
    name = data.get("name")
    if not name:
        return None

    parts = [name]
    options = data.get("options", [])
    while options and isinstance(options, list):
        first_opt = options[0]
        if isinstance(first_opt, dict) and first_opt.get("type") in (1, 2):
            sub_name = first_opt.get("name", "")
            if sub_name:
                parts.append(sub_name)
            options = first_opt.get("options", [])
        else:
            break

    return " ".join(parts).strip()


async def global_command_check(ctx: commands.Context) -> bool:
    if not ctx.command:
        return True

    root_name = ctx.command.root_parent.name if ctx.command.root_parent else ctx.command.name
    if root_name in PROTECTED_COMMANDS:
        return True

    cmd_name = ctx.command.qualified_name
    settings = await get_command_settings()

    if is_command_disabled(cmd_name, settings):
        if not await is_user_authorized(ctx.bot, ctx.author):
            raise CommandDisabledError("This command is temporarily disabled by admin.")

    return True


async def global_interaction_check(interaction: discord.Interaction) -> bool:
    if interaction.type not in (discord.InteractionType.application_command, discord.InteractionType.autocomplete):
        return True

    cmd_name = get_interaction_command_name(interaction)
    if not cmd_name:
        return True

    first_word = cmd_name.split(" ")[0]
    if first_word in PROTECTED_COMMANDS:
        return True

    settings = await get_command_settings()
    if is_command_disabled(cmd_name, settings):
        if not await is_user_authorized(interaction.client, interaction.user):
            if interaction.type != discord.InteractionType.autocomplete:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "This command is temporarily disabled by admin.",
                        ephemeral=True
                    )
            return False

    return True


# =============================================================================
# SECTION 4: DISCORD COG & COMMAND IMPLEMENTATIONS
# =============================================================================

class Admin(commands.Cog, name="Admin"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.presence_task.start()
        # Register global checks for both prefix commands and slash interactions
        self.bot.add_check(global_command_check)
        self.bot.tree.interaction_check = global_interaction_check

    def cog_unload(self) -> None:
        self.presence_task.cancel()
        self.bot.remove_check(global_command_check)
        if getattr(self.bot.tree, "interaction_check", None) == global_interaction_check:
            self.bot.tree.interaction_check = None

    # --- Presence Loop ---

    @tasks.loop(minutes=5)
    async def presence_task(self) -> None:
        try:
            activity = discord.Game(name="!m and !roll")
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as exc:
            logger.warning(f"Failed to update presence: {exc}")

    @presence_task.before_loop
    async def before_presence_task(self) -> None:
        while not self.bot.is_ready():
            await asyncio.sleep(1)


    # =========================================================================
    # PREFIX COMMANDS: !admin
    # =========================================================================

    @commands.group(name="admin", invoke_without_command=True)
    async def admin_group(self, ctx: commands.Context) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        embed = discord.Embed(
            title="Admin Panel",
            description=(
                "`!admin disable <command>` → Disables a command for all users.\n"
                "`!admin enable <command>` → Re-enables a disabled command.\n"
                "`!admin list` → Lists all disabled commands."
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @admin_group.command(name="disable")
    async def admin_disable(self, ctx: commands.Context, *, command_name: Optional[str] = None) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        if not command_name:
            await ctx.send("Usage: `!admin disable <command>` (e.g. `!admin disable char create`)")
            return

        success, message = await disable_command(command_name)
        if success:
            norm = normalize_command_name(command_name)
            embed = discord.Embed(
                title="Command Disabled",
                description=f"`{norm}` has been disabled across all servers.\nOnly authorized users can execute it.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @admin_group.command(name="enable")
    async def admin_enable(self, ctx: commands.Context, *, command_name: Optional[str] = None) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        if not command_name:
            await ctx.send("Usage: `!admin enable <command>` (e.g. `!admin enable char create`)")
            return

        success, message = await enable_command(command_name)
        if success:
            norm = normalize_command_name(command_name)
            embed = discord.Embed(
                title="Command Enabled",
                description=f"`{norm}` has been re-enabled across all servers for all users.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @admin_group.command(name="list")
    async def admin_list(self, ctx: commands.Context) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        settings = await get_command_settings()
        disabled = settings.get("disabled_commands", [])

        embed = discord.Embed(title="Disabled Commands", color=discord.Color.gold())
        if disabled:
            embed.description = "\n".join(f"• `{cmd}`" for cmd in disabled)
        else:
            embed.description = "*No commands are currently disabled.*"
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
