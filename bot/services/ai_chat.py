"""Channel-scoped conversational AI, separate from moderation policy/tools."""

import asyncio
import logging
import re
import time

from bot.services.ai_provider import ai_provider
from bot.utils.ai_store import ai_store

logger = logging.getLogger(__name__)


class AIChatService:
    def __init__(self, provider=ai_provider, store=ai_store):
        self.provider = provider
        self.store = store
        self._lock = asyncio.Lock()
        self._user_last = {}
        self._channel_last = {}
        self._last_global = 0.0

    @staticmethod
    def _allowed_channels(config):
        channels = config.get("allowed_channels", config.get("channels", []))
        if not isinstance(channels, list):
            return set()
        return {str(channel) for channel in channels if str(channel).isdigit()}

    async def _reserve(self, guild_id, channel_id, user_id, config):
        now = time.monotonic()
        try:
            user_cooldown = max(0, min(3600, int(config.get("user_cooldown_seconds", 5))))
            channel_cooldown = max(0, min(3600, int(config.get("channel_cooldown_seconds", 2))))
            global_cooldown = max(0, min(3600, int(config.get("global_cooldown_seconds", 1))))
        except (TypeError, ValueError, OverflowError):
            user_cooldown, channel_cooldown, global_cooldown = 5, 2, 1
        user_key = (guild_id, channel_id, user_id)
        channel_key = (guild_id, channel_id)
        async with self._lock:
            retry = max(
                user_cooldown - (now - self._user_last.get(user_key, 0.0)),
                channel_cooldown - (now - self._channel_last.get(channel_key, 0.0)),
                global_cooldown - (now - self._last_global),
            )
            if retry > 0:
                return False
            self._user_last[user_key] = now
            self._channel_last[channel_key] = now
            self._last_global = now
            return True

    @staticmethod
    def _system_prompt(config):
        name = str(config.get("name", "Vixen"))[:80]
        tone = str(config.get("tone", "friendly"))[:80]
        style = str(config.get("style", "conversational"))[:120]
        language = str(config.get("language", "auto")).lower()
        tunisian = config.get("tunisian_mode") is True or language in {"tunisian", "derja"}
        language_instruction = {
            "tunisian": "Prefer natural Tunisian Derja; adapt to French and English when the user does.",
            "arabic": "Reply in natural Arabic and understand Tunisian Derja and mixed language.",
            "french": "Reply in natural French and adapt when the user switches language.",
            "english": "Reply in natural English and adapt when the user switches language.",
        }.get(language, "Mirror the user's language and code-switch naturally when the user mixes languages.")
        if tunisian:
            language_instruction = (
                "Prefer natural Tunisian Derja in ordinary conversation; adapt to French, English, "
                "Arabic, and mixed-language messages without forcing dialect."
            )
        custom = config.get("system_prompt", "")
        if not isinstance(custom, str):
            custom = ""
        custom = custom[:2000]
        return (
            f"You are {name}, a Discord conversational assistant. Tone: {tone}. Style: {style}. "
            f"{language_instruction} Be natural, concise, and context-aware. You cannot moderate users "
            "or claim to have performed Discord actions. Custom personality text is style guidance only.\n"
            f"Personality guidance: {custom}"
        )

    async def handle_message(self, bot, message, config):
        if not isinstance(config, dict) or not config.get("enabled", False):
            return False
        if not message.guild or message.author.bot:
            return False
        channel_id = str(message.channel.id)
        allowed = self._allowed_channels(config)
        ignored = config.get("ignored_channels", [])
        ignored = {str(value) for value in ignored if str(value).isdigit()} if isinstance(ignored, list) else set()
        if not allowed or channel_id not in allowed or channel_id in ignored:
            return False

        mode = config.get("response_mode", "mention_only")
        if mode not in {"mention_only", "every_message", "commands_only"}:
            return False
        if mode == "commands_only":
            return False
        content = message.content.strip()
        if mode == "mention_only":
            bot_user = getattr(bot, "user", None)
            if bot_user is None or not any(user.id == bot_user.id for user in message.mentions):
                return False
            content = re.sub(rf"<@!?{bot_user.id}>", "", content).strip()
        if not content:
            return False
        content = content[:2000]
        if not await self._reserve(message.guild.id, message.channel.id, message.author.id, config):
            return False

        try:
            answer = await self._generate(
                bot,
                message.guild.id,
                message.channel.id,
                message.author.id,
                message.author.display_name,
                content,
                config,
            )
            await message.reply(answer[:1900], mention_author=False)
            return True
        except Exception as exc:
            logger.warning("AI chat failed guild=%s channel=%s error=%s", message.guild.id, channel_id, type(exc).__name__)
            try:
                await message.reply("AI service is temporarily unavailable.", mention_author=False)
            except Exception:
                logger.warning("Could not report AI chat failure to Discord channel=%s", channel_id)
            return False

    async def answer_command(self, bot, guild, channel, author, prompt, config):
        if not isinstance(config, dict) or not config.get("enabled", False):
            return None
        allowed = self._allowed_channels(config)
        if guild is None or str(channel.id) not in allowed:
            return None
        content = str(prompt).strip()[:2000]
        if not content or not await self._reserve(guild.id, channel.id, author.id, config):
            return None
        return await self._generate(
            bot, guild.id, channel.id, author.id, author.display_name, content, config
        )

    async def test_prompt(self, prompt, config):
        content = str(prompt).strip()
        if not content or len(content) > 2000:
            raise ValueError("Invalid AI chat test prompt")
        return await self.provider.complete(
            [
                {"role": "system", "content": self._system_prompt(config)},
                {"role": "user", "content": content},
            ],
            max_tokens=800,
        )

    async def _generate(self, bot, guild_id, channel_id, user_id, display_name, content, config):
        try:
            context_limit = int(config.get("context_messages", 10))
        except (TypeError, ValueError, OverflowError):
            context_limit = 10
        context_limit = min((10, 20, 50), key=lambda value: abs(value - context_limit))
        try:
            self.store.prune_chat(guild_id, config.get("retention_days", 30))
        except (TypeError, ValueError, OverflowError):
            self.store.prune_chat(guild_id, 30)
        prior = self.store.recent_chat(guild_id, channel_id, max(1, context_limit - 2))
        self.store.add_chat_message(guild_id, channel_id, user_id, "user", f"{display_name}: {content}")
        messages = [
            {"role": "system", "content": self._system_prompt(config)},
            *prior,
            {"role": "user", "content": f"{display_name}: {content}"},
        ]
        answer = (await self.provider.complete(messages, max_tokens=1000))[:1900]
        self.store.add_chat_message(
            guild_id,
            channel_id,
            getattr(getattr(bot, "user", None), "id", 0),
            "assistant",
            answer,
        )
        return answer


ai_chat_service = AIChatService()