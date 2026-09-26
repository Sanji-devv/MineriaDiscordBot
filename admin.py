"""
Mineria Discord Bot - Administrative Suite & Command Guard
==========================================================
Provides dynamic command restriction (Command Guard), permission management,
slash command tree synchronization, and Discord presence management.
Persists all configuration atomically into the root .env file.
"""

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
    "cmd", "sync", "all",
    "d", "dup", "checkdup",
    "gm", "player", "oyuncu", "gmcheck", "pinfo",
    "best", "top", "mostmissions", "gorevler", "bestchar"
}

_SETTINGS_CACHE: Optional[Dict[str, Any]] = None


class CommandDisabledError(commands.CheckFailure):
    """Raised when a command invocation is rejected due to active Command Guard restriction."""
    def __init__(self, message: str = "This command is currently disabled by the developer."):
        self.message = message
        super().__init__(self.message)


def normalize_command_name(name: str) -> str:
    """
    Normalizes a command identifier by stripping bot prefixes, extra whitespace,
    and converting to lowercase.
    Example: "!mineria char create" -> "char create", "/roll" -> "roll"
    """
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
    """
    Extracts raw integer user ID from string or Discord mention format.
    Example: "<@!388683129658933259>" or "<@388683129658933259>" -> 388683129658933259
    """
    if not val:
        return None
    cleaned = val.strip().replace("<@", "").replace("!", "").replace(">", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


# =============================================================================
# SECTION 2: ATOMIC .ENV PERSISTENCE ENGINE
# =============================================================================

async def get_command_settings(force_reload: bool = False) -> Dict[str, Any]:
    """
    Reads Command Guard settings directly from the .env configuration file with in-memory caching.
    Returns:
        Dict with 'disabled_commands' (List[str]) and 'allowed_users' (List[int]).
    """
    global _SETTINGS_CACHE
    if not force_reload and _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE

    disabled_list: List[str] = []
    allowed_users: List[int] = [DEVELOPER_ID]

    if ENV_FILE.exists():
        try:
            content = await asyncio.to_thread(ENV_FILE.read_text, encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    if key == "DISABLED_COMMANDS":
                        disabled_list = [c.strip().lower() for c in val.split(",") if c.strip()]
                    elif key == "ALLOWED_USERS":
                        for uid in val.split(","):
                            uid = uid.strip()
                            if uid.isdigit():
                                allowed_users.append(int(uid))
        except Exception as exc:
            logger.error(f"Error reading .env for Command Guard settings: {exc}")

    # Fallback safety default
    if not disabled_list:
        disabled_list = ["char create"]
    allowed_users = sorted(list(set(allowed_users)))

    _SETTINGS_CACHE = {
        "disabled_commands": disabled_list,
        "allowed_users": allowed_users
    }
    return _SETTINGS_CACHE


async def save_command_settings(settings: Dict[str, Any]) -> None:
    """
    Persists Command Guard configuration directly into the .env file atomically.
    Preserves all existing environment variables, whitespace, and comments.
    """
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
    """Checks whether the user possesses bypass authority (Developer, Bot Owner, or in allowed_users)."""
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
    """Checks if a command or any of its parent groups is currently restricted."""
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
    """Disables a specific command and commits changes to .env."""
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
    """Re-enables a previously disabled command and commits changes to .env."""
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


def get_all_manageable_commands(bot: commands.Bot) -> List[str]:
    """Collects all unique manageable root command names from the bot, excluding protected ones."""
    cmds: Set[str] = set()
    if hasattr(bot, "commands"):
        for cmd in bot.commands:
            name = cmd.name.lower().strip()
            if name not in PROTECTED_COMMANDS and not name.startswith("cmd") and not name.startswith("all"):
                cmds.add(name)
    if hasattr(bot, "tree") and hasattr(bot.tree, "get_commands"):
        for app_cmd in bot.tree.get_commands():
            name = app_cmd.name.lower().strip()
            if name not in PROTECTED_COMMANDS and not name.startswith("cmd") and not name.startswith("all"):
                cmds.add(name)
    return sorted(list(cmds))


async def disable_all_commands(bot: commands.Bot) -> Tuple[bool, List[str], str]:
    """Disables all manageable commands in bulk and commits changes to .env."""
    all_cmds = get_all_manageable_commands(bot)
    if not all_cmds:
        return False, [], "No manageable commands found to restrict."

    settings = await get_command_settings(force_reload=True)
    current_disabled = set(settings.get("disabled_commands", []))
    for cmd in all_cmds:
        current_disabled.add(cmd)

    settings["disabled_commands"] = sorted(list(current_disabled))
    if DEVELOPER_ID not in settings.setdefault("allowed_users", []):
        settings["allowed_users"].append(DEVELOPER_ID)

    await save_command_settings(settings)
    logger.info(f"All commands ({len(all_cmds)}) have been DISABLED by administrator.")
    return True, all_cmds, f"A total of **{len(all_cmds)}** commands were successfully restricted."


async def enable_all_commands() -> Tuple[bool, List[str], str]:
    """Clears all command restrictions and commits changes to .env."""
    settings = await get_command_settings(force_reload=True)
    previously_disabled = settings.get("disabled_commands", [])
    if not previously_disabled:
        return False, [], "No commands are currently restricted. All commands are active."

    settings["disabled_commands"] = []
    if DEVELOPER_ID not in settings.setdefault("allowed_users", []):
        settings["allowed_users"].append(DEVELOPER_ID)

    await save_command_settings(settings)
    logger.info(f"All commands have been ENABLED by administrator. Previously disabled: {previously_disabled}")
    return True, previously_disabled, f"Restrictions successfully lifted from **{len(previously_disabled)}** commands."


async def add_allowed_user(user_id: int) -> Tuple[bool, str]:
    """Adds a Discord user ID to the bypass list in .env."""
    settings = await get_command_settings(force_reload=True)
    allowed = settings.setdefault("allowed_users", [])
    if user_id in allowed:
        return False, f"<@{user_id}> (`{user_id}`) is already in the allowed list."

    allowed.append(user_id)
    await save_command_settings(settings)
    return True, f"<@{user_id}> (`{user_id}`) was successfully added to the bypass list."


async def remove_allowed_user(user_id: int) -> Tuple[bool, str]:
    """Removes a Discord user ID from the bypass list in .env."""
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
    """Extracts qualified hierarchical command name from Discord application command interaction data."""
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
    """Global check predicate evaluated before executing any traditional prefix command."""
    if not ctx.command:
        return True

    root_name = ctx.command.root_parent.name if ctx.command.root_parent else ctx.command.name
    if root_name in PROTECTED_COMMANDS:
        return True

    cmd_name = ctx.command.qualified_name
    settings = await get_command_settings()

    if is_command_disabled(cmd_name, settings):
        if not await is_user_authorized(ctx.bot, ctx.author):
            raise CommandDisabledError("This command is currently disabled by the developer.")

    return True


async def global_interaction_check(interaction: discord.Interaction) -> bool:
    """Global check predicate evaluated before executing any application slash command or autocomplete."""
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
                        "This command is currently disabled by the developer.",
                        ephemeral=True
                    )
            return False

    return True


# =============================================================================
# SECTION 4: DISCORD COG & COMMAND IMPLEMENTATIONS
# =============================================================================

class Admin(commands.Cog, name="Admin"):
    """Cog handling Command Guard control, command sync, and background presence tasks."""

    cmd_slash_group = app_commands.Group(name="cmd", description="Command Guard management panel")
    all_slash_group = app_commands.Group(name="all", description="Bulk command restriction panel")

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
        """Periodic background task ensuring the bot displays active status and activity text."""
        try:
            activity = discord.Game(name="!m and !roll")
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as exc:
            logger.warning(f"Failed to update presence: {exc}")

    @presence_task.before_loop
    async def before_presence_task(self) -> None:
        await self.bot.wait_until_ready()

    # --- Sync Commands ---

    @commands.command(name="sync", hidden=True)
    @commands.is_owner()
    async def sync_tree(self, ctx: commands.Context) -> None:
        """Synchronizes application slash commands with Discord gateway (Owner only)."""
        msg = await ctx.send("Synchronizing application commands...")
        try:
            synced = await self.bot.tree.sync()
            await msg.edit(content=f"Successfully synchronized **{len(synced)}** commands globally.")
        except Exception as exc:
            await msg.edit(content=f"Synchronization failed: {exc}")

    @app_commands.command(name="sync", description="Sync slash commands globally (Owner only)")
    async def slash_sync(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message("This command is restricted to the bot owner.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"Successfully synchronized **{len(synced)}** application commands.")
        except Exception as exc:
            await interaction.followup.send(f"Synchronization failed: {exc}")

    # =========================================================================
    # PREFIX COMMANDS: !cmd
    # =========================================================================

    @commands.group(name="cmd", invoke_without_command=True)
    async def cmd_text_group(self, ctx: commands.Context) -> None:
        """Command Guard management panel."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        embed = discord.Embed(
            title="Command Management Panel",
            description=(
                "**Command Restriction & Enabling:**\n"
                "`!cmd disable <command>` -> Disables the specified command.\n"
                "`!cmd enable <command>` -> Re-enables the disabled command.\n"
                "`!cmd list` -> Lists disabled commands and authorized users.\n\n"
                "**Bypass Permissions:**\n"
                "`!cmd allow <id/@user>` -> Grants bypass permission for restricted commands.\n"
                "`!cmd disallow <id/@user>` -> Revokes bypass permission.\n\n"
                f"*Permanent Developer ID:* `{DEVELOPER_ID}`"
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @cmd_text_group.command(name="disable")
    async def cmd_disable(self, ctx: commands.Context, *, command_name: Optional[str] = None) -> None:
        """Disables a command for non-authorized users."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        if not command_name:
            await ctx.send("Please specify the command name to disable. E.g.: `!cmd disable char create`")
            return

        success, message = await disable_command(command_name)
        if success:
            norm = normalize_command_name(command_name)
            embed = discord.Embed(
                title="Command Disabled",
                description=f"`{norm}` has been disabled.\nOnly authorized users can execute it.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @cmd_text_group.command(name="enable")
    async def cmd_enable(self, ctx: commands.Context, *, command_name: Optional[str] = None) -> None:
        """Re-enables a previously disabled command."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        if not command_name:
            await ctx.send("Please specify the command name to enable. E.g.: `!cmd enable char create`")
            return

        success, message = await enable_command(command_name)
        if success:
            norm = normalize_command_name(command_name)
            embed = discord.Embed(
                title="Command Enabled",
                description=f"`{norm}` has been successfully re-enabled for all users.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @cmd_text_group.command(name="list")
    async def cmd_list(self, ctx: commands.Context) -> None:
        """Lists disabled commands and authorized users."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        settings = await get_command_settings()
        disabled = settings.get("disabled_commands", [])
        allowed = settings.get("allowed_users", [DEVELOPER_ID])

        embed = discord.Embed(title="Command Status & Permissions", color=discord.Color.gold())
        if disabled:
            cmd_lines = "\n".join(f"• `{cmd}`" for cmd in disabled)
            embed.add_field(name="Disabled Commands", value=cmd_lines, inline=False)
        else:
            embed.add_field(name="Disabled Commands", value="*No commands are currently disabled.*", inline=False)

        user_lines = "\n".join(f"• <@{uid}> (`{uid}`)" for uid in allowed)
        embed.add_field(name="Bypass Authorized Users", value=user_lines or f"• `{DEVELOPER_ID}`", inline=False)
        await ctx.send(embed=embed)

    @cmd_text_group.command(name="allow")
    async def cmd_allow(self, ctx: commands.Context, *, user_arg: Optional[str] = None) -> None:
        """Grants a user bypass permission for restricted commands."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        uid = extract_user_id(user_arg) if user_arg else None
        if not uid:
            await ctx.send("Please enter a valid user ID or mention. E.g.: `!cmd allow 388683129658933259`")
            return

        success, message = await add_allowed_user(uid)
        await ctx.send(message)

    @cmd_text_group.command(name="disallow")
    async def cmd_disallow(self, ctx: commands.Context, *, user_arg: Optional[str] = None) -> None:
        """Revokes a user's bypass permission."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        uid = extract_user_id(user_arg) if user_arg else None
        if not uid:
            await ctx.send("Please enter a valid user ID or mention. E.g.: `!cmd disallow 388683129658933259`")
            return

        success, message = await remove_allowed_user(uid)
        await ctx.send(message)

    # =========================================================================
    # SLASH COMMANDS: /cmd
    # =========================================================================

    @cmd_slash_group.command(name="disable", description="Disable a command (Administrators only)")
    @app_commands.describe(command="Command to disable (e.g. char create)")
    async def slash_cmd_disable(self, interaction: discord.Interaction, command: str) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, message = await disable_command(command)
        if success:
            norm = normalize_command_name(command)
            embed = discord.Embed(
                title="Command Disabled",
                description=f"`{norm}` has been disabled.\nOnly authorized users can execute it.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @slash_cmd_disable.autocomplete("command")
    async def slash_cmd_disable_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            settings = await get_command_settings()
            already_disabled = set(normalize_command_name(c) for c in settings.get("disabled_commands", []))

            all_cmds: Set[str] = set()
            for cmd in self.bot.walk_commands():
                qname = cmd.qualified_name.lower().strip()
                first = qname.split(" ")[0]
                if first not in PROTECTED_COMMANDS and qname not in already_disabled:
                    all_cmds.add(qname)

            curr_lower = current.lower().strip()
            choices = []
            for c in sorted(all_cmds):
                if not curr_lower or curr_lower in c:
                    choices.append(app_commands.Choice(name=c, value=c))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception:
            return []

    @cmd_slash_group.command(name="enable", description="Re-enable a disabled command")
    @app_commands.describe(command="Command to re-enable")
    async def slash_cmd_enable(self, interaction: discord.Interaction, command: str) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, message = await enable_command(command)
        if success:
            norm = normalize_command_name(command)
            embed = discord.Embed(
                title="Command Enabled",
                description=f"`{norm}` has been successfully re-enabled for all users.",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @slash_cmd_enable.autocomplete("command")
    async def slash_cmd_enable_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            settings = await get_command_settings()
            disabled = settings.get("disabled_commands", [])
            curr_lower = current.lower().strip()
            choices = []
            for c in disabled:
                if not curr_lower or curr_lower in c.lower():
                    choices.append(app_commands.Choice(name=c, value=c))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception:
            return []

    @cmd_slash_group.command(name="list", description="List disabled commands and authorized users")
    async def slash_cmd_list(self, interaction: discord.Interaction) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        settings = await get_command_settings()
        disabled = settings.get("disabled_commands", [])
        allowed = settings.get("allowed_users", [DEVELOPER_ID])

        embed = discord.Embed(title="Command Status & Permissions", color=discord.Color.gold())
        if disabled:
            cmd_lines = "\n".join(f"• `{cmd}`" for cmd in disabled)
            embed.add_field(name="Disabled Commands", value=cmd_lines, inline=False)
        else:
            embed.add_field(name="Disabled Commands", value="*No commands are currently disabled.*", inline=False)

        user_lines = "\n".join(f"• <@{uid}> (`{uid}`)" for uid in allowed)
        embed.add_field(name="Bypass Authorized Users", value=user_lines or f"• `{DEVELOPER_ID}`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cmd_slash_group.command(name="allow", description="Grant a user bypass permission for restricted commands")
    @app_commands.describe(user="User to grant permission")
    async def slash_cmd_allow(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, message = await add_allowed_user(user.id)
        await interaction.response.send_message(message, ephemeral=True)

    @cmd_slash_group.command(name="disallow", description="Revoke a user's bypass permission for restricted commands")
    @app_commands.describe(user="User to revoke permission from")
    async def slash_cmd_disallow(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, message = await remove_allowed_user(user.id)
        await interaction.response.send_message(message, ephemeral=True)

    # =========================================================================
    # PREFIX COMMANDS: !all
    # =========================================================================

    @commands.group(name="all", invoke_without_command=True)
    async def all_text_group(self, ctx: commands.Context) -> None:
        """Shows current restricted commands status and options."""
        await self._show_all_status(ctx)

    async def _show_all_status(self, ctx: commands.Context) -> None:
        """Renders the status embed of all currently restricted commands."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        settings = await get_command_settings()
        disabled = settings.get("disabled_commands", [])

        embed = discord.Embed(title="Restricted Commands Status", color=discord.Color.gold())
        if disabled:
            cmd_lines = "\n".join(f"• `{c}`" for c in disabled)
            embed.description = f"A total of **{len(disabled)}** commands are currently restricted:"
            embed.add_field(name="Disabled Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To enable all: !all enable | To restrict all: !all disable")
        else:
            embed.description = "No commands are currently restricted. All commands are active."
            embed.set_footer(text="To restrict all: !all disable")

        await ctx.send(embed=embed)

    @all_text_group.command(name="disable")
    async def all_disable(self, ctx: commands.Context) -> None:
        """Disables all manageable commands for non-authorized users."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        success, restricted_cmds, message = await disable_all_commands(self.bot)
        if success:
            embed = discord.Embed(
                title="All Commands Restricted",
                description=f"A total of **{len(restricted_cmds)}** commands were successfully disabled.\nOnly authorized users can execute them.",
                color=discord.Color.red()
            )
            cmd_lines = "\n".join(f"• `{c}`" for c in restricted_cmds)
            embed.add_field(name="Restricted Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To re-enable commands: !all enable")
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @all_text_group.command(name="enable")
    async def all_enable(self, ctx: commands.Context) -> None:
        """Re-enables all commands for everyone."""
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        success, unrestricted_cmds, message = await enable_all_commands()
        if success:
            embed = discord.Embed(
                title="All Commands Enabled",
                description=f"Restrictions removed from **{len(unrestricted_cmds)}** commands. All commands are active.",
                color=discord.Color.green()
            )
            cmd_lines = "\n".join(f"• `{c}`" for c in unrestricted_cmds)
            embed.add_field(name="Unrestricted Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To re-restrict commands: !all disable")
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @all_text_group.command(name="status", aliases=["list"])
    async def all_status(self, ctx: commands.Context) -> None:
        """Shows which commands are currently restricted."""
        await self._show_all_status(ctx)

    # =========================================================================
    # SLASH COMMANDS: /all
    # =========================================================================

    @all_slash_group.command(name="disable", description="Restrict all commands (Administrators only)")
    async def slash_all_disable(self, interaction: discord.Interaction) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, restricted_cmds, message = await disable_all_commands(self.bot)
        if success:
            embed = discord.Embed(
                title="All Commands Restricted",
                description=f"A total of **{len(restricted_cmds)}** commands were successfully disabled.\nOnly authorized users can execute them.",
                color=discord.Color.red()
            )
            cmd_lines = "\n".join(f"• `{c}`" for c in restricted_cmds)
            embed.add_field(name="Restricted Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To re-enable commands: /all enable")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @all_slash_group.command(name="enable", description="Re-enable all restricted commands")
    async def slash_all_enable(self, interaction: discord.Interaction) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        success, unrestricted_cmds, message = await enable_all_commands()
        if success:
            embed = discord.Embed(
                title="All Commands Enabled",
                description=f"Restrictions removed from **{len(unrestricted_cmds)}** commands. All commands are active.",
                color=discord.Color.green()
            )
            cmd_lines = "\n".join(f"• `{c}`" for c in unrestricted_cmds)
            embed.add_field(name="Unrestricted Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To re-restrict commands: /all disable")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @all_slash_group.command(name="status", description="List which commands are currently restricted")
    async def slash_all_status(self, interaction: discord.Interaction) -> None:
        if not await is_user_authorized(self.bot, interaction.user):
            await interaction.response.send_message("This command is restricted to authorized administrators.", ephemeral=True)
            return

        settings = await get_command_settings()
        disabled = settings.get("disabled_commands", [])

        embed = discord.Embed(title="Restricted Commands Status", color=discord.Color.gold())
        if disabled:
            cmd_lines = "\n".join(f"• `{c}`" for c in disabled)
            embed.description = f"A total of **{len(disabled)}** commands are currently restricted:"
            embed.add_field(name="Disabled Commands", value=cmd_lines, inline=False)
            embed.set_footer(text="To enable all: /all enable | To restrict all: /all disable")
        else:
            embed.description = "No commands are currently restricted. All commands are active."
            embed.set_footer(text="To restrict all: /all disable")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the Admin Cog."""
    await bot.add_cog(Admin(bot))
