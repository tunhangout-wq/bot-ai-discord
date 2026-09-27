"""Policy-checked Discord tools available to automatic AI moderation."""

import asyncio
import datetime
import logging

import discord

from bot.utils import ranks
from bot.utils.ai_store import ai_store
from bot.services.warnings import issue_warning

logger = logging.getLogger(__name__)


class ModerationToolLayer:
    def __init__(self, store=ai_store):
        self.store = store

    @staticmethod
    def _record(bot, guild_id, target_id, requested, result, reason):
        try:
            actor = getattr(bot, "user", None)
            ranks.log_action(
                getattr(actor, "id", 0),
                str(actor or "Vixen AI"),
                "ai_moderation",
                f"guild={guild_id} target={target_id} requested={requested} result={result} reason={reason[:300]}",
                "ai",
            )
        except Exception:
            logger.exception("Could not persist AI moderation audit event")

    async def _reserve_action(self, guild_id, maximum):
        return await asyncio.to_thread(self.store.reserve_moderation_action, guild_id, maximum)

    async def execute(self, bot, message, action, reason, policy, timeout_minutes=10):
        tool_names = {
            "warn": "warn_member",
            "timeout": "timeout_member",
            "ban": "ban_member",
        }
        tool = tool_names.get(action)
        guild = message.guild
        target = message.author
        reason = str(reason or "AI moderation")[:500]

        if not tool:
            return {"tool": None, "executed": False, "result": "ignored"}
        if not isinstance(policy, dict) or policy.get("enabled") is False or policy.get("kill_switch") is True:
            self._record(bot, guild.id, target.id, tool, "blocked_by_policy", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_policy"}
        allowed = policy.get("automatic_actions", ["warn", "timeout", "ban"])
        if not isinstance(allowed, list) or (action not in allowed and tool not in allowed):
            self._record(bot, guild.id, target.id, tool, "blocked_by_policy", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_policy"}
        if action == "ban" and policy.get("allow_ai_ban") is not True:
            self._record(bot, guild.id, target.id, tool, "blocked_by_policy", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_policy"}

        bot_member = guild.me
        bot_user_id = getattr(getattr(bot, "user", None), "id", None)
        if (
            bot_member is None
            or target.id in {guild.owner_id, bot_user_id}
            or target.top_role >= bot_member.top_role
        ):
            self._record(bot, guild.id, target.id, tool, "blocked_by_hierarchy", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_hierarchy"}

        permissions = bot_member.guild_permissions
        required_permission = {
            "timeout": "moderate_members",
            "ban": "ban_members",
        }.get(action)
        if required_permission and not getattr(permissions, required_permission, False):
            self._record(bot, guild.id, target.id, tool, "blocked_by_permissions", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_permissions"}
        if action == "warn":
            channel_permissions = message.channel.permissions_for(bot_member)
            if not channel_permissions.send_messages:
                self._record(bot, guild.id, target.id, tool, "blocked_by_permissions", reason)
                return {"tool": tool, "executed": False, "result": "blocked_by_permissions"}

        try:
            maximum = max(1, min(1000, int(policy.get("max_actions_per_hour", 20))))
        except (TypeError, ValueError, OverflowError):
            maximum = 20
        if not await self._reserve_action(guild.id, maximum):
            self._record(bot, guild.id, target.id, tool, "blocked_by_rate_limit", reason)
            return {"tool": tool, "executed": False, "result": "blocked_by_rate_limit"}

        try:
            if action == "warn":
                config = policy
                warning = await issue_warning(
                    guild,
                    target,
                    getattr(getattr(bot, "user", None), "id", 0),
                    "Vixen AI",
                    reason,
                    threshold=config.get("warning_threshold", 10),
                    timeout_minutes=config.get("warning_timeout_minutes", 60),
                    allow_threshold_timeout="timeout" in allowed or "timeout_member" in allowed,
                    reserve_escalation=lambda: self._reserve_action(guild.id, maximum),
                    source="ai",
                )
                await message.channel.send(
                    f"⚠️ {target.mention}: {reason} (warning #{warning['warning_id']})",
                    delete_after=8,
                )
                if warning["escalation"] == "timeout_applied":
                    self._record(bot, guild.id, target.id, "timeout_member", "success", reason)
                return {
                    "tool": tool,
                    "executed": True,
                    "result": "success",
                    "escalation": warning["escalation"],
                }
            elif action == "timeout":
                duration = max(1, min(60, int(timeout_minutes)))
                await target.timeout(
                    discord.utils.utcnow() + datetime.timedelta(minutes=duration),
                    reason=reason,
                )
            else:
                await guild.ban(target, reason=reason, delete_message_seconds=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            result = f"discord_error:{type(exc).__name__}"
            self._record(bot, guild.id, target.id, tool, result, reason)
            logger.warning("AI moderation tool failed guild=%s target=%s result=%s", guild.id, target.id, result)
            return {"tool": tool, "executed": False, "result": result}

        self._record(bot, guild.id, target.id, tool, "success", reason)
        logger.info("AI moderation tool executed guild=%s target=%s tool=%s", guild.id, target.id, tool)
        return {"tool": tool, "executed": True, "result": "success"}


moderation_tools = ModerationToolLayer()