"""
main.py
--------
نقطة تشغيل البوت الرئيسية.
يقوم بتحميل التوكن من ملف .env، تفعيل الـ Intents اللازمة،
تحميل جميع الـ Cogs (الوحدات)، وتشغيل سيرفر لوحة التحكم المدمج.
"""

import asyncio
import os
import sys
import logging
import time

import discord
from discord.ext import commands
from dotenv import load_dotenv

# يسمح بتشغيل الملف مباشرة من أي مكان بدون مشاكل استيراد
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.utils.data_manager import load_settings
from bot.web.server import start_web_server

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("Vixen")

settings = load_settings()
PREFIX = os.getenv("BOT_PREFIX") or settings.get("bot", {}).get("prefix", "!")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
bot._vixen_started_at = time.monotonic()

COGS = [
    "bot.cogs.economy",
    "bot.cogs.bank",
    "bot.cogs.gambling",
    "bot.cogs.shop",
    "bot.cogs.leaderboard",
    "bot.cogs.admin",
    "bot.cogs.general",
    "bot.cogs.control",
    "bot.cogs.atria",
    # --- النظام الاحترافي ---
    "bot.cogs.staff",     # الرتب الإدارية Dev/Founder/Team
    "bot.cogs.levels",    # الخبرة والمستويات
    "bot.cogs.stocks",    # الأسهم والاستثمار
    "bot.cogs.loans",     # القروض
    "bot.cogs.rob",       # السرقة
    "bot.cogs.career",    # الوظائف والبزنس
]


@bot.event
async def on_ready():
    logger.info(f"تم تسجيل الدخول باسم {bot.user} (ID: {bot.user.id})")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        logger.info(f"تمت مزامنة {len(synced)} أمر Slash.")
    except Exception as e:
        logger.error(f"فشل مزامنة الأوامر: {e}")

    bot_settings = load_settings().get("bot", {})
    presence = bot_settings.get("presence", {}) if isinstance(bot_settings, dict) else {}
    presence_type = presence.get("type", "playing") if isinstance(presence, dict) else "playing"
    presence_name = presence.get("name", f"{PREFIX}help | نظام العملات") if isinstance(presence, dict) else f"{PREFIX}help | نظام العملات"
    activity_types = {
        "playing": discord.ActivityType.playing,
        "listening": discord.ActivityType.listening,
        "watching": discord.ActivityType.watching,
    }
    if not isinstance(presence_type, str) or presence_type not in activity_types:
        presence_type = "playing"
    if not isinstance(presence_name, str) or not presence_name.strip():
        presence_name = f"{PREFIX}help | نظام العملات"
    activity = discord.Activity(type=activity_types.get(presence_type, discord.ActivityType.playing), name=presence_name)
    await bot.change_presence(activity=activity, status=discord.Status.online)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ ناقص معلومة: `{error.param.name}`")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ لازم تنتظر `{error.retry_after:.1f}` ثانية قبل ما تعيد المحاولة.")
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 ما عندك صلاحية تسوي هذا الأمر.")
        return
    logger.error(f"خطأ غير متوقع: {error}")
    await ctx.send("❌ صار خطأ غير متوقع أثناء تنفيذ الأمر.")


async def load_cogs():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            logger.info(f"تم تحميل: {cog}")
        except Exception as e:
            logger.error(f"فشل تحميل {cog}: {e}")


async def main():
    async with bot:
        await load_cogs()
        # تشغيل لوحة التحكم (نفس العملية — كل تعديل باللوحة يظهر فورًا بالبوت)
        dashboard_started = await start_web_server(bot)
        try:
            if not TOKEN or TOKEN == "ضع_التوكن_هنا":
                logger.error("لم يتم ضبط DISCORD_TOKEN! افتح ملف .env وضع التوكن الصحيح.")
                if dashboard_started:
                    logger.warning("لوحة التحكم تعمل بوضع الإعداد فقط حتى تتم إضافة DISCORD_TOKEN و OWNER_ID.")
                    await asyncio.Event().wait()
                return
            await bot.start(TOKEN)
        finally:
            runner = getattr(bot, "_dashboard_runner", None)
            if runner is not None:
                try:
                    await runner.cleanup()
                except Exception:
                    logger.exception("Could not close Dashboard server")
                finally:
                    delattr(bot, "_dashboard_runner")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت.")
