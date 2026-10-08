import re
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union, Set
import discord
from discord.ext import commands, tasks
from log_handler import logger

ENV_FILE = Path(__file__).parent / ".env"
_ENV_LOCK = asyncio.Lock()

ADMIN_IDS: Set[int] = {
    388683129658933259,
}

PROTECTED_COMMANDS: Set[str] = {
    "admin",
    "gm", "player", "gmcheck", "pinfo"
}

_SETTINGS_CACHE: Optional[Dict[str, Any]] = None
_ENV_MTIME: float = 0.0

class CommandDisabledError(commands.CheckFailure):
    def __init__(self, message: str = "This command is temporarily disabled by admin."):
        self.message = message
        super().__init__(self.message)

def normalize_command_name(name: str) -> str:
    if not name:
        return ""
    name = name.strip().lower()
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

async def get_command_settings(force_reload: bool = False) -> Dict[str, Any]:
    global _SETTINGS_CACHE, _ENV_MTIME
    current_mtime = ENV_FILE.stat().st_mtime if ENV_FILE.exists() else 0.0

    if not force_reload and _SETTINGS_CACHE is not None and current_mtime == _ENV_MTIME:
        return _SETTINGS_CACHE

    disabled_list: List[str] = []
    allowed_users: List[int] = list(ADMIN_IDS)

    if ENV_FILE.exists():
        try:
            content = await asyncio.to_thread(ENV_FILE.read_text, encoding="utf-8")
            _ENV_MTIME = current_mtime
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
    global _SETTINGS_CACHE, _ENV_MTIME
    _SETTINGS_CACHE = settings

    disabled_str = ", ".join(settings.get("disabled_commands", []))
    allowed_str = ", ".join(str(uid) for uid in settings.get("allowed_users", list(ADMIN_IDS)))

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

        for key, val in remaining.items():
            new_lines.append(f'{key}="{val}"')

        tmp_path = ENV_FILE.with_suffix(".tmp")
        tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        tmp_path.replace(ENV_FILE)

    async with _ENV_LOCK:
        await asyncio.to_thread(_atomic_env_write)
        if ENV_FILE.exists():
            _ENV_MTIME = ENV_FILE.stat().st_mtime

async def is_user_authorized(bot: commands.Bot, user: Union[discord.User, discord.Member]) -> bool:
    if not user:
        return False
    if user.id in ADMIN_IDS:
        return True

    try:
        settings = await get_command_settings()
        allowed_users = settings.get("allowed_users", [])
        if user.id in allowed_users:
            return True
    except Exception as exc:
        logger.error(f"Error checking allowed_users authorization: {exc}")

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

    first_word = cmd_name.split(" ")[0]
    if first_word in PROTECTED_COMMANDS:
        return False

    disabled_list = settings.get("disabled_commands", [])
    for d in disabled_list:
        d_norm = normalize_command_name(d)
        if not d_norm:
            continue
        if cmd_name == d_norm or cmd_name.startswith(d_norm + " "):
            return True

    return False

async def disable_command(command_name: str, bot: Optional[commands.Bot] = None) -> Tuple[bool, str]:
    normalized = normalize_command_name(command_name)
    if not normalized:
        return False, "Invalid or empty command name."

    if normalized in ("all", "*"):
        if not bot:
            return False, "Cannot disable all commands without bot context."
        all_cmds = [cmd.name for cmd in bot.commands if cmd.name not in PROTECTED_COMMANDS and not cmd.hidden]
        if not all_cmds:
            return False, "No manageable commands found to disable."

        settings = await get_command_settings(force_reload=True)
        current_disabled = set(settings.get("disabled_commands", []))
        newly_disabled = [cmd for cmd in all_cmds if cmd not in current_disabled]
        if not newly_disabled:
            return False, "All manageable commands are already disabled."

        current_disabled.update(all_cmds)
        settings["disabled_commands"] = sorted(list(current_disabled))
        for _aid in ADMIN_IDS:
            if _aid not in settings.setdefault("allowed_users", []):
                settings["allowed_users"].append(_aid)

        await save_command_settings(settings)
        logger.info(f"All commands ({len(all_cmds)}) have been DISABLED by administrator.")
        return True, f"All manageable commands ({len(all_cmds)}) have been disabled across all servers."

    first_word = normalized.split(" ")[0]
    if first_word in PROTECTED_COMMANDS:
        return False, f"`{normalized}` is a protected system command and cannot be disabled."

    settings = await get_command_settings(force_reload=True)
    disabled = settings.setdefault("disabled_commands", [])

    for d in disabled:
        d_norm = normalize_command_name(d)
        if d_norm == normalized:
            return False, f"`{normalized}` is already disabled."
        if normalized != d_norm and normalized.startswith(d_norm + " "):
            return False, f"`{normalized}` is already restricted because its parent command `{d_norm}` is disabled."

    # If disabling a parent command, prune redundant subcommands under it
    disabled = [d for d in disabled if not (d != normalized and d.startswith(normalized + " "))]
    disabled.append(normalized)
    settings["disabled_commands"] = sorted(disabled)
    for _aid in ADMIN_IDS:
        if _aid not in settings.setdefault("allowed_users", []):
            settings["allowed_users"].append(_aid)

    await save_command_settings(settings)
    logger.info(f"Command '{normalized}' has been DISABLED by administrator.")
    return True, f"`{normalized}` has been successfully disabled."

async def enable_command(command_name: str, bot: Optional[commands.Bot] = None) -> Tuple[bool, str]:
    normalized = normalize_command_name(command_name)
    if not normalized:
        return False, "Invalid or empty command name."

    if normalized in ("all", "*"):
        settings = await get_command_settings(force_reload=True)
        previously_disabled = settings.get("disabled_commands", [])
        count = len(previously_disabled)

        settings["disabled_commands"] = []
        for _aid in ADMIN_IDS:
            if _aid not in settings.setdefault("allowed_users", []):
                settings["allowed_users"].append(_aid)

        await save_command_settings(settings)
        if count > 0:
            logger.info(f"All commands ({count}) have been ENABLED by administrator. Previously disabled: {previously_disabled}")
            return True, f"All disabled commands ({count}) have been successfully re-enabled."
        else:
            logger.info("Command Guard reset: All commands are enabled and active.")
            return True, "All commands are active and unrestricted."

    settings = await get_command_settings(force_reload=True)
    disabled = settings.get("disabled_commands", [])

    # Identify matching disabled commands (exact or child subcommands)
    to_remove = set()
    for d in disabled:
        d_norm = normalize_command_name(d)
        if d_norm == normalized or d_norm.startswith(normalized + " "):
            to_remove.add(d)

    # Check if a parent command was disabled restricting this subcommand
    parent_removed = []
    if not to_remove:
        for d in disabled:
            d_norm = normalize_command_name(d)
            if normalized != d_norm and normalized.startswith(d_norm + " "):
                to_remove.add(d)
                parent_removed.append(d_norm)

    if not to_remove:
        return False, f"`{normalized}` is already enabled."

    settings["disabled_commands"] = sorted([d for d in disabled if d not in to_remove])
    for _aid in ADMIN_IDS:
        if _aid not in settings.setdefault("allowed_users", []):
            settings["allowed_users"].append(_aid)

    await save_command_settings(settings)
    logger.info(f"Command '{normalized}' has been ENABLED by administrator. Removed: {to_remove}")

    if parent_removed:
        return True, f"`{normalized}` has been successfully re-enabled (parent group `{parent_removed[0]}` was unrestricted)."
    elif len(to_remove) > 1:
        return True, f"`{normalized}` and its restricted subcommands have been successfully re-enabled."
    else:
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
    if user_id in ADMIN_IDS:
        return False, "Permanent admin IDs cannot be removed from the allowed list."

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

class Admin(commands.Cog, name="Admin"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.presence_task.start()
        self.bot.add_check(global_command_check)
        self.bot.tree.interaction_check = global_interaction_check

    def cog_unload(self) -> None:
        self.presence_task.cancel()
        self.bot.remove_check(global_command_check)
        if getattr(self.bot.tree, "interaction_check", None) == global_interaction_check:
            self.bot.tree.interaction_check = None

    @tasks.loop(minutes=5)
    async def presence_task(self) -> None:
        try:
            activity = discord.Game(name="!m and !roll")
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as exc:
            logger.warning(f"Failed to update presence: {exc}")

    @presence_task.before_loop
    async def before_presence_task(self) -> None:
        try:
            if hasattr(self.bot, "wait_until_ready") and callable(self.bot.wait_until_ready):
                res = self.bot.wait_until_ready()
                if asyncio.iscoroutine(res):
                    await res
        except (RuntimeError, Exception):
            pass

        while hasattr(self.bot, "is_ready") and callable(self.bot.is_ready) and not self.bot.is_ready():
            await asyncio.sleep(1)

    @commands.group(name="admin", invoke_without_command=True)
    async def admin_group(self, ctx: commands.Context) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        embed = discord.Embed(
            title="Admin Panel",
            description=(
                "`!admin disable <command|all>` → Disables a command (or all commands) for all users.\n"
                "`!admin enable <command|all>` → Re-enables a disabled command (or all commands).\n"
                "`!admin list` → Lists all disabled commands.\n"
                "`!admin allow <user>` → Grants user command guard bypass authority.\n"
                "`!admin remove <user>` → Removes user from bypass list.\n"
                "`!admin users` → Lists all authorized bypass users."
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
            await ctx.send("Usage: `!admin disable <command|all>` (e.g. `!admin disable char create` or `!admin disable all`)")
            return

        success, message = await disable_command(command_name, bot=self.bot)
        if success:
            norm = normalize_command_name(command_name)
            desc = (
                "All manageable commands have been disabled across all servers.\nOnly authorized users can execute them."
                if norm == "all"
                else f"`{norm}` has been disabled across all servers.\nOnly authorized users can execute it."
            )
            embed = discord.Embed(
                title="Commands Disabled" if norm == "all" else "Command Disabled",
                description=desc,
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
            await ctx.send("Usage: `!admin enable <command|all>` (e.g. `!admin enable char create` or `!admin enable all`)")
            return

        success, message = await enable_command(command_name, bot=self.bot)
        if success:
            norm = normalize_command_name(command_name)
            desc = (
                "All commands have been re-enabled across all servers for all users."
                if norm in ("all", "*")
                else f"`{norm}` has been re-enabled across all servers for all users."
            )
            embed = discord.Embed(
                title="All Commands Enabled" if norm in ("all", "*") else "Command Enabled",
                description=desc,
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(message)

    @admin_group.command(name="list")
    async def admin_list(self, ctx: commands.Context, *, option: Optional[str] = None) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to authorized administrators.")
            return

        settings = await get_command_settings(force_reload=True)
        disabled = settings.get("disabled_commands", [])

        all_manageable = sorted([
            cmd.name for cmd in self.bot.commands
            if cmd.name not in PROTECTED_COMMANDS and not cmd.hidden
        ])

        opt_norm = option.strip().lower() if option else ""

        if opt_norm in ("all", "*", "full", "hepsi"):
            embed = discord.Embed(title="Bot Commands Guard Status", color=discord.Color.blue())
            lines = []
            for c_name in all_manageable:
                is_dis = is_command_disabled(c_name, settings)
                status_icon = "❌ Disabled" if is_dis else "✅ Active"
                lines.append(f"• `{c_name}` — {status_icon}")

            for d in disabled:
                if " " in d:
                    lines.append(f"• `{d}` — ❌ Disabled")

            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Total: {len(all_manageable)} manageable commands | Disabled: {len(disabled)}")
            await ctx.send(embed=embed)
            return

        if disabled:
            embed = discord.Embed(
                title="Restricted Commands",
                description=f"A total of **{len(disabled)}** command(s) are currently disabled for non-administrators:\n\n" + "\n".join(f"• `{cmd}`" for cmd in disabled),
                color=discord.Color.red()
            )
            embed.set_footer(text="Use `!admin enable <cmd|all>` to re-enable | View full status: `!admin list all`")
        else:
            embed = discord.Embed(
                title="Command Guard Status",
                description=(
                    "✅ **All commands are currently active and unrestricted.**\n\n"
                    "No commands are disabled across servers. All users can execute bot commands."
                ),
                color=discord.Color.green()
            )
            embed.add_field(
                name=f"Active Commands ({len(all_manageable)})",
                value=", ".join(f"`{c}`" for c in all_manageable),
                inline=False
            )
            embed.set_footer(text="To restrict a command: `!admin disable <cmd|all>` | Full status: `!admin list all`")

        await ctx.send(embed=embed)

    @admin_group.command(name="allow")
    async def admin_allow(self, ctx: commands.Context, *, user_arg: Optional[str] = None) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        if not user_arg:
            await ctx.send("Usage: `!admin allow <@user or user_id>`")
            return

        uid = extract_user_id(user_arg)
        if not uid:
            await ctx.send("Invalid user ID or mention.")
            return

        success, message = await add_allowed_user(uid)
        await ctx.send(message)

    @admin_group.command(name="remove")
    async def admin_remove(self, ctx: commands.Context, *, user_arg: Optional[str] = None) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        if not user_arg:
            await ctx.send("Usage: `!admin remove <@user or user_id>`")
            return

        uid = extract_user_id(user_arg)
        if not uid:
            await ctx.send("Invalid user ID or mention.")
            return

        success, message = await remove_allowed_user(uid)
        await ctx.send(message)

    @admin_group.command(name="users")
    async def admin_users(self, ctx: commands.Context) -> None:
        if not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to the developer.")
            return

        settings = await get_command_settings()
        allowed = settings.get("allowed_users", [])

        embed = discord.Embed(title="Authorized Bypass Users", color=discord.Color.blue())
        if allowed:
            embed.description = "\n".join(f"• <@{uid}> (`{uid}`)" for uid in allowed)
        else:
            embed.description = "*No bypass users configured.*"
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
