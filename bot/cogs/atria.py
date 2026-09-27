"""Atria Dawn integration: explicit AI chat plus optional moderation hooks."""
import os, aiohttp, discord, asyncio, json, datetime, time, logging
from discord.ext import commands
from discord import app_commands
from bot.utils.data_manager import load_settings

BASE="https://api.atria-asi.ai/v1/chat/completions"
MODEL="Atria-Dawn-Preview"
AI_SEMAPHORE = asyncio.Semaphore(3)
AI_RATE_LOCK = asyncio.Lock()
AI_MIN_INTERVAL = 0.35
_last_ai_call = 0.0
_http_session = None
logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024

async def _open_ai_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45, connect=5, sock_read=30),
            raise_for_status=False,
        )

async def _close_ai_session():
    global _http_session
    if _http_session is not None and not _http_session.closed:
        await _http_session.close()

async def atria_chat(messages, max_tokens=1200):
    global _last_ai_call
    key=os.getenv("ATRIA_API_KEY")
    if not key: raise RuntimeError("Atria service is not configured")
    if not isinstance(messages, list) or len(messages) > 20:
        raise ValueError("Invalid Atria request")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 2000:
        raise ValueError("Invalid Atria request")
    payload={"model":MODEL,"messages":messages,"max_tokens":max_tokens}
    async with AI_SEMAPHORE:
        async with AI_RATE_LOCK:
            wait=max(0.0, AI_MIN_INTERVAL-(time.monotonic()-_last_ai_call))
            if wait: await asyncio.sleep(wait)
            _last_ai_call=time.monotonic()
        await _open_ai_session()
        try:
            async with _http_session.post(
                BASE,
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                json=payload,
            ) as response:
                if response.content_type != "application/json":
                    raise RuntimeError("Atria service returned an invalid response")
                if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Atria service response is too large")
                raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("Atria service response is too large")
                data=json.loads(raw)
                if response.status>=400:
                    logger.warning("Atria API request failed with HTTP %s", response.status)
                    raise RuntimeError("Atria request failed")
                choices = data.get("choices") if isinstance(data, dict) else None
                first = choices[0] if isinstance(choices, list) and choices else {}
                message = first.get("message", {}) if isinstance(first, dict) else {}
                content=message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str) or not content:
                    raise RuntimeError("Atria service returned an invalid response")
                return content
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            logger.exception("Atria API request failed")
            raise RuntimeError("Atria service is temporarily unavailable") from None

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
        await ctx.defer()
        try:
            answer=await atria_chat([{"role":"system","content":"You are Vixen Discord EDR assistant. Be concise, safe, and operational."},{"role":"user","content":prompt}])
            await ctx.send(answer[:1900])
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
        cfg=load_settings().get("atria",{})
        if not isinstance(cfg, dict): return
        if not cfg.get("moderation_enabled",False): return
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
            verdict=await atria_chat([{"role":"system","content":"Moderate this Discord message. Reply JSON only: {\"action\":\"allow\"|\"warn\"|\"timeout\"|\"ban\",\"reason\":\"short\"}. Never ban/timeout based on guesses; use allow when evidence is insufficient."},{"role":"user","content":content}],400)
            compact=verdict.strip().removeprefix("```json").removesuffix("```").strip()
            try:
                result=json.loads(compact)
                if not isinstance(result, dict): raise ValueError("invalid result")
            except (json.JSONDecodeError, ValueError):
                logger.warning("Atria moderation returned invalid JSON guild=%s", message.guild.id)
                return
            action=str(result.get("action","allow")).lower()
            reason=str(result.get("reason","Atria moderation"))[:500]
            if action not in {"allow","warn","timeout","ban"}: action="allow"
            if action in {"timeout", "ban"}:
                bot_member = message.guild.me
                target = message.author
                if (
                    bot_member is None or target.id in {message.guild.owner_id, self.bot.user.id}
                    or target.top_role >= bot_member.top_role
                ):
                    logger.warning(
                        "Atria moderation action blocked by hierarchy guild=%s target=%s action=%s",
                        message.guild.id, target.id, action,
                    )
                    return
            if action=="timeout":
                await message.author.timeout(discord.utils.utcnow()+datetime.timedelta(minutes=max(1,min(60,int(cfg.get("timeout_minutes",10))))),reason=reason)
            elif action=="ban":
                await message.guild.ban(message.author,reason=reason,delete_message_seconds=0)
            elif action=="warn":
                await message.channel.send(f"⚠️ {message.author.mention}: {reason}",delete_after=8)
            logger.info(
                "atria_moderation guild=%s user=%s action=%s result=completed",
                message.guild.id, message.author.id, action,
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Atria moderation Discord action failed guild=%s user=%s", message.guild.id, message.author.id)
            return
        except (TypeError, ValueError, OverflowError, RuntimeError):
            logger.exception("Atria moderation failed guild=%s user=%s", message.guild.id, message.author.id)
            return

async def setup(bot): await bot.add_cog(Atria(bot))
