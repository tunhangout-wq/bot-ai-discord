"""Atria Dawn integration: explicit AI chat plus optional moderation hooks."""
import discord, json, datetime, logging
from discord.ext import commands
from discord import app_commands
from bot.utils.data_manager import load_settings
from bot.services.ai_provider import ai_provider
from bot.services.ai_chat import ai_chat_service
from bot.services.moderation_tools import moderation_tools
from bot.utils.ai_store import ai_store

logger = logging.getLogger(__name__)

async def _open_ai_session():
    await ai_provider.open()

async def _close_ai_session():
    await ai_provider.close()

async def atria_chat(messages, max_tokens=1200):
    return await ai_provider.complete(messages, max_tokens)

class Atria(commands.Cog):
    def __init__(self, bot): self.bot=bot

    async def cog_load(self):
        await _open_ai_session()

    async def cog_unload(self):
        await _close_ai_session()

    @commands.hybrid_group(name="ai", invoke_without_command=True, description="Atria Dawn AI")
    async def ai(self, ctx): await ctx.send("استخدم /ai chat لبدء محادثة مع Atria Dawn.")

    @ai.command(name="chat", description="محادثة مباشرة مع Atria Dawn")
    @app_commands.describe(prompt="رسالتك")
    async def chat(self, ctx, prompt: str):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            await ctx.send("اكتب رسالة صالحة لا تتجاوز 4000 حرف.", ephemeral=True)
            return
        settings = load_settings()
        ai_settings = settings.get("ai", {})
        chat_policy = ai_settings.get("chat", {}) if isinstance(ai_settings, dict) else {}
        if not isinstance(chat_policy, dict) or not chat_policy.get("enabled", False):
            await ctx.send("AI Chat غير مفعّل من إعدادات السيرفر.", ephemeral=True)
            return
        if ctx.guild is None or str(ctx.channel.id) not in ai_chat_service._allowed_channels(chat_policy):
            await ctx.send("هذا الأمر متاح فقط في قنوات AI Chat المسموحة.", ephemeral=True)
            return
        await ctx.defer()
        try:
            answer = await ai_chat_service.answer_command(
                self.bot, ctx.guild, ctx.channel, ctx.author, prompt, chat_policy
            )
            if answer is None:
                await ctx.send("تعذر تنفيذ الطلب بسبب حد الاستخدام. حاول بعد قليل.")
                return
            await ctx.send(answer)
        except (RuntimeError, ValueError):
            logger.exception("Atria chat failed user=%s", ctx.author.id)
            await ctx.send("❌ تعذر الوصول إلى خدمة Atria الآن.")

    @ai.command(name="moderate", description="فحص رسالة باستخدام Atria Dawn")
    @app_commands.describe(text="النص المراد فحصه")
    async def moderate(self, ctx, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            await ctx.send("أدخل نصاً صالحاً لا يتجاوز 2000 حرف.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        try:
            verdict=await atria_chat([{"role":"system","content":"You are a Discord safety moderator. Return JSON only with action one of allow, warn, timeout, ban and a short reason. Do not invent facts."},{"role":"user","content":text}],400)
            await ctx.send(verdict[:1900], ephemeral=True)
        except (RuntimeError, ValueError):
            logger.exception("Manual Atria moderation failed user=%s", ctx.author.id)
            await ctx.send("❌ تعذر الوصول إلى خدمة Atria الآن.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        settings = load_settings()
        cfg=settings.get("atria",{})
        ai_settings = settings.get("ai", {})
        moderation_policy = ai_settings.get("moderation", {}) if isinstance(ai_settings, dict) else {}
        chat_policy = ai_settings.get("chat", {}) if isinstance(ai_settings, dict) else {}
        if not isinstance(moderation_policy, dict):
            moderation_policy = {}
        if not isinstance(chat_policy, dict):
            chat_policy = {}
        if isinstance(chat_policy, dict):
            await ai_chat_service.handle_message(self.bot, message, chat_policy)
        if not isinstance(cfg, dict): return
        if moderation_policy.get("kill_switch", False): return
        if not moderation_policy.get("enabled", cfg.get("moderation_enabled", False)): return
        if "channels" in moderation_policy:
            channels = moderation_policy.get("channels")
            if not isinstance(channels, list) or str(message.channel.id) not in {str(value) for value in channels}:
                return
        detection_types = moderation_policy.get("detection_types", ["spam", "flood", "repeated_messages", "toxicity", "harassment", "suspicious", "raid_like"])
        if not isinstance(detection_types, list) or not detection_types:
            return
        # Full-message moderation is optional and configurable. A prefix allowlist remains available for low-cost mode.
        if cfg.get("moderation_mode", "all") == "prefix":
            raw_prefixes=cfg.get("moderation_prefixes", ["[mod]","!modcheck"])
            if not isinstance(raw_prefixes, list): return
            prefixes=tuple(p[:100] for p in raw_prefixes[:20] if isinstance(p, str) and p)
            if not prefixes: return
            if not message.content.lower().startswith(tuple(p.lower() for p in prefixes)): return
        if not message.content.strip(): return
        # لا نرسل الرسائل الضخمة أو رسائل الإدارة الداخلية إلى النموذج.
        content=message.content[:2000]
        try:
            types = ", ".join(str(value) for value in detection_types[:20])
            sensitivity = moderation_policy.get("sensitivity", "medium")
            prompt = (
                "Moderate this Discord message. Return JSON only with keys action, classification, event, confidence, reason. "
                "action must be allow, warn, timeout, or ban. classification must be one of the configured detection types. "
                "confidence must be a number from 0 to 1. Never invent facts; use allow when evidence is insufficient. "
                f"Configured detection types: {types}. Sensitivity: {sensitivity}."
            )
            verdict=await atria_chat([{"role":"system","content":prompt},{"role":"user","content":content}],400)
            compact=verdict.strip().removeprefix("```json").removesuffix("```").strip()
            try:
                decision=json.loads(compact)
                if not isinstance(decision, dict): raise ValueError("invalid result")
            except (json.JSONDecodeError, ValueError):
                logger.warning("Atria moderation returned invalid JSON guild=%s", message.guild.id)
                return
            action=str(decision.get("action","allow")).lower()
            reason=str(decision.get("reason","Atria moderation"))[:500]
            if action not in {"allow","warn","timeout","ban"}: action="allow"
            policy = dict(moderation_policy)
            policy.setdefault("allow_ai_ban", cfg.get("allow_ai_ban") is True)
            execution = await moderation_tools.execute(
                self.bot,
                message,
                action,
                reason,
                policy,
                timeout_minutes=moderation_policy.get("timeout_minutes", cfg.get("timeout_minutes", 10)),
            )
            confidence = decision.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                confidence = None
            try:
                classification = str(decision.get("classification", action))
                executed_tool = execution.get("tool") if execution.get("executed") else ""
                if executed_tool and execution.get("escalation") == "timeout_applied":
                    executed_tool += "+timeout_member"
                ai_store.log_moderation(
                    message.guild.id,
                    message.channel.id,
                    message.author.id,
                    str(decision.get("event", classification)),
                    classification,
                    execution.get("tool") or action,
                    executed_tool,
                    execution.get("result", "unknown"),
                    reason,
                    confidence,
                )
            except Exception:
                logger.exception("Could not persist AI moderation decision guild=%s", message.guild.id)
            logger.info(
                "atria_moderation guild=%s user=%s classification=%s tool=%s result=%s",
                message.guild.id,
                message.author.id,
                action,
                execution.get("tool"),
                execution.get("result"),
            )
        except (TypeError, ValueError, OverflowError, RuntimeError):
            logger.exception("Atria moderation failed guild=%s user=%s", message.guild.id, message.author.id)
            return

async def setup(bot): await bot.add_cog(Atria(bot))
