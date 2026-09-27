"""
web/server.py
--------------
سيرفر لوحة التحكم المدمج مع البوت (aiohttp — نفس عملية البوت).

هذا يعني أن أي عملية تسويها باللوحة (إضافة فلوس، تعديل إعدادات...)
تنفذ فورًا على نفس بيانات البوت — بدون تصدير ملفات أو إعادة تشغيل!

المزايا:
  - تسجيل دخول بكود الفريق (Dev/Founder/Team) أو بآيدي المالك + كلمة المرور
  - صلاحيات لكل رتبة (نفس صلاحيات البوت تمامًا)
  - إحصائيات مباشرة: عدد المستخدمين، إجمالي الفلوس، القروض، الأسهم
  - إدارة فلوس الأعضاء (إضافة لنفسه أو لغيره، سحب، تصفير)
  - إدارة أكواد الفريق (توليد/سحب)
  - تعديل كل إعدادات البوت مباشرة
  - سجل عمليات مباشر
"""

import os
import time
import secrets
import logging
import inspect
import hashlib
import math
import types
import typing
import asyncio
from pathlib import Path

from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.data_manager import (
    atomic_update_user, load_users, load_settings, save_settings,
    load_market, load_loans, save_loans, get_economy_stats, now_ts
)
from bot.utils import ranks
from bot.cogs.atria import atria_chat

logger = logging.getLogger("Vixen.Web")

DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"

SESSION_TTL = 7 * 24 * 3600  # أسبوع
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW = 300  # 5 دقائق
MAX_SESSIONS = 5000
MAX_ATTEMPT_IPS = 10000


class DashboardSessions:
    """إدارة جلسات تسجيل الدخول (بالذاكرة)."""

    def __init__(self):
        self.sessions = {}
        self.attempts = {}  # ip → [timestamps]

    def _prune_attempts(self, ip):
        now = time.time()
        attempts = [t for t in self.attempts.get(ip, []) if now - t < ATTEMPT_WINDOW]
        if attempts:
            self.attempts[ip] = attempts
        else:
            self.attempts.pop(ip, None)

    def too_many_attempts(self, ip) -> bool:
        self._prune_attempts(ip)
        return len(self.attempts.get(ip, [])) >= MAX_ATTEMPTS

    def record_attempt(self, ip):
        if ip not in self.attempts and len(self.attempts) >= MAX_ATTEMPT_IPS:
            self.attempts.pop(next(iter(self.attempts)))
        self.attempts.setdefault(ip, []).append(time.time())

    def create(self, user_id, rank: str, name: str = "", virtual: bool = False, code: str = "") -> str:
        now = time.time()
        expired = [token for token, value in self.sessions.items() if value.get("expires", 0) <= now]
        for expired_token in expired:
            self.sessions.pop(expired_token, None)
        while len(self.sessions) >= MAX_SESSIONS:
            self.sessions.pop(next(iter(self.sessions)))
        token = secrets.token_hex(32)
        self.sessions[token] = {
            "user_id": str(user_id) if user_id else "0",
            "rank": rank,
            "name": name,
            "virtual": virtual,   # جلسة بكود غير مربوط بآيدي ديسكورد
            "code": code,
            "expires": now + SESSION_TTL,
        }
        return token

    def get(self, token: str):
        session = self.sessions.get(token)
        if not session:
            return None
        if time.time() > session["expires"]:
            del self.sessions[token]
            return None
        return session


SESSIONS = DashboardSessions()


def _json_response(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json_response({"ok": False, "error": message}, status)


def get_session_from_request(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return SESSIONS.get(auth[7:].strip())


def require_permission(request: web.Request, permission: str):
    """يرجع الجلسة إذا كانت صالحة وتملك الصلاحية، وإلا يرفع خطأ HTTP."""
    session = get_session_from_request(request)
    if not session:
        raise web.HTTPUnauthorized(reason="انتهت الجلسة — سجل دخول من جديد")
    if permission:
        if session.get("virtual"):
            if permission not in ranks.DEFAULT_PERMISSIONS.get(session.get("rank", ""), []):
                raise web.HTTPForbidden(reason="ما عندك صلاحية لهذه العملية برتبتك الحالية")
        else:
            uid = int(session["user_id"]) if session["user_id"].isdigit() else 0
            if not ranks.has_permission(uid, permission):
                raise web.HTTPForbidden(reason="ما عندك صلاحية لهذه العملية برتبتك الحالية")
    return session


def _session_has(session, permission: str) -> bool:
    if session.get("virtual"):
        return permission in ranks.DEFAULT_PERMISSIONS.get(session.get("rank", ""), [])
    uid = int(session["user_id"]) if session["user_id"].isdigit() else 0
    return ranks.has_permission(uid, permission)


def resolve_name(bot, user_id) -> str:
    """يحاول يجيب اسم العضو من كاش البوت."""
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return "؟"
    if bot and uid:
        user = bot.get_user(uid)
        if user:
            return user.name
        for guild in getattr(bot, "guilds", []):
            member = guild.get_member(uid)
            if member:
                return member.display_name
    s = str(user_id)
    return f"عضو {s[-4:]}" if len(s) >= 4 else "عضو"


def _refresh_overdue(loans_data) -> None:
    now = now_ts()
    changed = False
    for loan in loans_data["loans"]:
        if loan["status"] == "active" and loan["due"] < now:
            loan["status"] = "overdue"
            changed = True
    if changed:
        save_loans(loans_data)


# ---------------------------------------------------------------------------
# الصفحات الثابتة
# ---------------------------------------------------------------------------

async def serve_index(request: web.Request) -> web.Response:
    index_path = DASHBOARD_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="<h1>ملف الداشبورد غير موجود</h1>", content_type="text/html")
    return web.FileResponse(index_path)


# ---------------------------------------------------------------------------
# API: تسجيل الدخول
# ---------------------------------------------------------------------------

async def api_login(request: web.Request) -> web.Response:
    ip = request.remote or "؟"
    if SESSIONS.too_many_attempts(ip):
        return _error("محاولات كثيرة خاطئة — انتظر 5 دقائق وحاول مجددًا", 429)

    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")

    method = body.get("method", "code")
    settings = load_settings()

    if method == "code":
        code = (body.get("code") or "").strip().upper()
        entry = ranks.list_codes().get(code)
        if not entry:
            SESSIONS.record_attempt(ip)
            return _error("الكود غير صحيح — تأكد من كتابته", 401)
        if entry.get("revoked"):
            SESSIONS.record_attempt(ip)
            return _error("هذا الكود تم سحبه", 403)

        rank = entry.get("rank")
        if rank not in ranks.RANK_LEVELS:
            return _error("بيانات كود غير صالحة", 403)
        used_by = entry.get("used_by")
        if used_by:
            try:
                user_id = int(used_by)
            except (TypeError, ValueError, OverflowError):
                return _error("بيانات كود غير صالحة", 403)
            if user_id < 0:
                token = SESSIONS.create(0, rank, name=f"كود …{code[-4:]}", virtual=True, code=code)
                perms = ranks.DEFAULT_PERMISSIONS.get(rank, [])
                limit = ranks.DEFAULT_DAILY_LIMITS.get(rank, 0)
            else:
                token = SESSIONS.create(user_id, rank, name=resolve_name(None, user_id))
                perms = ranks.get_permissions(user_id)
                limit = ranks.get_daily_limit(user_id)
        else:
            # اربط الكود بهوية Dashboard غير سالبة الهوية البشرية قبل إنشاء الجلسة.
            principal_id = -(secrets.randbelow((1 << 62) - 1) + 1)
            activation = ranks.activate_code(code, principal_id)
            if not activation.get("ok"):
                return _error(activation.get("message", "تعذر تفعيل الكود."), 409)
            token = SESSIONS.create(0, rank, name=f"كود …{code[-4:]}", virtual=True, code=code)
            perms = ranks.DEFAULT_PERMISSIONS.get(rank, [])
            limit = ranks.DEFAULT_DAILY_LIMITS.get(rank, 0)

        return _json_response({
            "ok": True,
            "token": token,
            "rank": rank,
            "rank_name": ranks.RANK_NAMES_AR.get(rank, rank),
            "permissions": perms,
            "daily_limit": limit,
            "owner": False,
        })

    elif method == "owner":
        owner_id = str(body.get("owner_id") or "").strip()
        password = str(body.get("password") or "")
        dash_cfg = settings.get("dashboard", {})
        real_owner = ranks.get_owner_id()

        if not real_owner:
            return _error("ما تم ضبط OWNER_ID بملف .env — راجع ملف .env.example", 400)
        if owner_id != str(real_owner):
            SESSIONS.record_attempt(ip)
            return _error("الآيدي ما يطابق آيدي المالك المسجل", 401)
        if password != (os.getenv("DASHBOARD_PASSWORD") or dash_cfg.get("password", "")):
            SESSIONS.record_attempt(ip)
            return _error("كلمة المرور غير صحيحة", 401)

        token = SESSIONS.create(real_owner, "owner", name="المالك")
        return _json_response({
            "ok": True,
            "token": token,
            "rank": "owner",
            "rank_name": ranks.RANK_NAMES_AR["owner"],
            "permissions": ranks.ALL_PERMISSIONS,
            "daily_limit": 0,
            "owner": True,
        })

    return _error("طريقة دخول غير معروفة")


async def api_me(request: web.Request) -> web.Response:
    session = get_session_from_request(request)
    if not session:
        return _error("جلسة غير صالحة", 401)

    rank = session["rank"]
    if session.get("virtual"):
        perms = ranks.DEFAULT_PERMISSIONS.get(rank, [])
        limit = ranks.DEFAULT_DAILY_LIMITS.get(rank, 0)
        used_today = 0
    else:
        uid = int(session["user_id"]) if session["user_id"].isdigit() else 0
        perms = ranks.get_permissions(uid) if uid else []
        limit = ranks.get_daily_limit(uid) if uid else 0
        used_today = ranks.get_today_usage(uid) if uid else 0

    return _json_response({
        "ok": True,
        "rank": rank,
        "rank_name": ranks.RANK_NAMES_AR.get(rank, rank),
        "permissions": perms,
        "daily_limit": limit,
        "used_today": used_today,
        "owner": rank == "owner",
        "name": session.get("name", ""),
    })


# ---------------------------------------------------------------------------
# API: الإحصائيات المباشرة
# ---------------------------------------------------------------------------

async def api_stats(request: web.Request) -> web.Response:
    require_permission(request, "view_stats")
    settings = load_settings()
    stats = get_economy_stats()

    loans_data = load_loans()
    _refresh_overdue(loans_data)
    active_loans = [l for l in loans_data["loans"] if l["status"] in ("active", "overdue")]
    loans_total = sum(l["total"] - l["paid"] for l in active_loans)

    market = load_market()
    stocks_listed = sum(e.get("price", 0) for e in market.get("prices", {}).values())
    invested = 0
    for u in load_users().values():
        for sym, holding in (u.get("stocks") or {}).items():
            price = market.get("prices", {}).get(sym, {}).get("price", holding.get("avg_cost", 0))
            invested += price * holding.get("qty", 0)

    bot = request.app.get("bot")
    bot_info = {}
    if bot:
        try:
            latency = getattr(bot, "latency", float("nan"))
            latency_ms = round(latency * 1000) if math.isfinite(latency) else None
            bot_info = {
                "name": str(bot.user) if bot.user else "…",
                "latency_ms": latency_ms,
                "guilds": len(bot.guilds),
                "connected": bool(bot.user and bot.is_ready()),
            }
        except Exception:
            bot_info = {}

    return _json_response({
        "ok": True,
        "ts": now_ts(),
        "users": stats["total_users"],
        "total_wallet": stats["total_wallet"],
        "total_bank": stats["total_bank"],
        "total_money": stats["total_money"],
        "total_earned": stats["total_earned"],
        "active_loans": len(active_loans),
        "loans_total": loans_total,
        "stocks_listed_value": round(stocks_listed, 2),
        "invested_value": int(invested),
        "staff_count": len(ranks.list_staff()),
        "codes_available": sum(1 for c in ranks.list_codes().values()
                               if not c.get("revoked") and not c.get("used_by")),
        "bot": bot_info,
        "currency_symbol": settings.get("bot", {}).get("currency_symbol", "🪙"),
    })


# ---------------------------------------------------------------------------
# API: الأعضاء
# ---------------------------------------------------------------------------

async def api_users(request: web.Request) -> web.Response:
    require_permission(request, "view_users")
    bot = request.app.get("bot")
    query = (request.query.get("q") or "").strip().lower()

    users = load_users()
    market = load_market()

    results = []
    for uid, data in users.items():
        name = resolve_name(bot, uid)
        if query and query not in name.lower() and query not in uid:
            continue
        stocks_value = 0
        for sym, holding in (data.get("stocks") or {}).items():
            price = market.get("prices", {}).get(sym, {}).get("price", holding.get("avg_cost", 0))
            stocks_value += price * holding.get("qty", 0)
        results.append({
            "id": uid,
            "name": name,
            "wallet": data.get("wallet", 0),
            "bank": data.get("bank", 0),
            "total": data.get("wallet", 0) + data.get("bank", 0),
            "stocks_value": int(stocks_value),
            "level": data.get("level", 0),
            "job": data.get("job", ""),
            "rank": ranks.get_rank(int(uid)),
        })
        if len(results) >= 200:
            break

    results.sort(key=lambda u: u["total"], reverse=True)
    return _json_response({"ok": True, "users": results})


async def api_members(request: web.Request) -> web.Response:
    require_permission(request, "view_users")
    bot = request.app.get("bot")
    guild = bot.guilds[0] if bot and bot.guilds else None
    if not guild:
        return _json_response({"ok": True, "members": [], "next": None})
    q=(request.query.get("q") or "").strip().lower()
    try: limit=max(1,min(100, int(request.query.get("limit", "100"))))
    except ValueError: limit=100
    try: after=int(request.query.get("after", "0"))
    except ValueError: after=0
    members=sorted(guild.members,key=lambda m:m.id)
    out=[]
    for m in members:
        if m.id <= after or m.bot: continue
        hay=f"{m.display_name} {m.name} {m.id}".lower()
        if q and q not in hay: continue
        out.append({"id":str(m.id),"name":m.display_name,"username":m.name,"avatar":m.display_avatar.url,"bot":m.bot})
        if len(out)>=limit: break
    nxt=out[-1]["id"] if len(out)==limit else None
    return _json_response({"ok":True,"members":out,"next":nxt})


class _DashboardPermissions:
    administrator = False

class DashboardAuthor:
    def __init__(self, user_id: int, name: str, rank: str):
        self.id = int(user_id)
        self.name = name or f"Dashboard {rank}"
        self.display_name = self.name
        self.bot = False
        self.guild_permissions = _DashboardPermissions()
        self.mention = self.name
        self.rank = rank
    def __str__(self):
        return self.name

class DashboardContext:
    def __init__(self, bot, guild, author, channel, session=None):
        self.bot=bot; self.guild=guild; self.author=author; self.channel=channel; self.interaction=None; self.message=None
        self.dashboard_session=session
        self.sent=[]
    async def send(self, content=None, **kwargs):
        text=content
        if text is None and kwargs.get("embed") is not None:
            text=getattr(kwargs["embed"],"description",None) or getattr(kwargs["embed"],"title",None) or "تم التنفيذ."
        self.sent.append(str(text or "تم التنفيذ."))
        return text


def _command_parameter_meta(command, name, annotation):
    """Extract Discord application-command metadata, including choices."""
    param = getattr(command, "_params", {}).get(name) if hasattr(command, "_params") else None
    choices = []
    if param is not None:
        for choice in getattr(param, "choices", []) or []:
            choices.append({"name": choice.name, "value": choice.value})
    typ = getattr(param, "type", None) if param is not None else None
    return typ, choices

def _all_commands(bot):
    out=[]
    if bot is None:
        return out
    def walk(container, prefix=""):
        children = container.get_commands() if hasattr(container, "get_commands") else getattr(container, "commands", [])
        for c in children:
            path=f"{prefix}.{c.name}" if prefix else c.name
            nested=getattr(c,"commands",None)
            if nested:
                walk(c,path)
                continue
            try: sig=inspect.signature(c.callback)
            except Exception: continue
            args=[]
            for n,p in sig.parameters.items():
                if n in ("self","ctx") or p.kind in (p.VAR_POSITIONAL,p.VAR_KEYWORD): continue
                annotation=getattr(p,"annotation",str)
                if annotation is inspect._empty: annotation=str
                discord_type, choices = _command_parameter_meta(c,n,annotation)
                args.append({
                    "name":n,
                    "type":getattr(annotation,"__name__",str(annotation)),
                    "discord_type":getattr(discord_type,"name",str(discord_type or "")),
                    "required":p.default is inspect._empty,
                    "default":None if p.default is inspect._empty else p.default,
                    "choices":choices,
                })
            out.append({
                "name": path,
                "description": getattr(c, "description", "") or "",
                "args": args,
                "cog": getattr(getattr(c, "cog", None), "qualified_name", ""),
                "aliases": list(getattr(c, "aliases", []) or []),
            })
    for cog in bot.cogs.values(): walk(cog)
    return sorted(out,key=lambda x:x["name"])


def _find_command(bot, path: str):
    """Find a leaf command inside the same loaded Cog tree used by the bot.

    Cog instances expose ``get_commands`` but not ``get_command``.  The old
    dashboard executor called the latter on the Cog, which made every command
    appear in the catalog but fail with "الأمر غير موجود" when executed.
    """
    normalized = str(path or "").strip().lstrip("/")
    normalized = normalized.replace("/", ".").replace(" ", ".")
    parts = [part for part in normalized.split(".") if part]
    if not parts or bot is None:
        return None

    def find_child(container, name):
        children = (
            container.get_commands()
            if hasattr(container, "get_commands")
            else getattr(container, "commands", [])
        )
        wanted = name.lower()
        for child in children or []:
            names = [getattr(child, "name", ""), getattr(child, "qualified_name", "")]
            names.extend(getattr(child, "aliases", []) or [])
            if any(str(candidate).lower() == wanted for candidate in names):
                return child
        return None

    for cog in bot.cogs.values():
        current = find_child(cog, parts[0])
        if current is None:
            continue
        for part in parts[1:]:
            current = find_child(current, part)
            if current is None:
                break
        if current is not None and not getattr(current, "commands", None):
            return current
    return None


def _resolve_arg(bot, guild, value, typ, command_param=None):
    # app_commands.Choice must be reconstructed before invoking the callback.
    choices = list(getattr(command_param, "choices", []) or []) if command_param is not None else []
    if choices:
        wanted = str(value)
        for choice in choices:
            if str(choice.value) == wanted or str(choice.name) == wanted:
                return app_commands.Choice(name=choice.name, value=choice.value)
        raise ValueError(f"قيمة غير صالحة: {value}")

    # ``Optional[discord.Member]`` and the newer ``discord.Member | None``
    # both need to be reduced to their concrete Discord type before lookup.
    origin = typing.get_origin(typ)
    if origin in (typing.Union, types.UnionType):
        candidates = [item for item in typing.get_args(typ) if item is not type(None)]
        if len(candidates) == 1:
            typ = candidates[0]
    name=getattr(typ,"__name__","")
    text=str(value).strip()
    if name in ("Member","User") or (isinstance(typ, type) and issubclass(typ, (discord.Member, discord.User))):
        cleaned=text.strip("<@!>")
        if not cleaned.isdigit(): raise ValueError("Member/User ID غير صالح")
        uid=int(cleaned)
        user=guild.get_member(uid) or bot.get_user(uid)
        if user is None: raise ValueError("العضو غير موجود")
        return user
    if name=="Role" or (isinstance(typ, type) and issubclass(typ, discord.Role)):
        role=guild.get_role(int(text));
        if role is None: raise ValueError("الرتبة غير موجودة")
        return role
    if name in ("TextChannel","VoiceChannel","CategoryChannel") or (
        isinstance(typ, type) and issubclass(typ, discord.abc.GuildChannel)
    ):
        channel=guild.get_channel(int(text));
        if channel is None: raise ValueError("القناة غير موجودة")
        return channel
    if typ is bool or name=="bool": return text.lower() in ("1","true","yes","on","نعم","صح")
    if typ is int or name=="int": return int(text)
    if typ is float or name=="float": return float(text)
    return value


async def api_commands(request: web.Request) -> web.Response:
    require_permission(request, "view_stats")
    return _json_response({"ok":True,"commands":_all_commands(request.app.get("bot"))})


async def api_command_execute(request: web.Request) -> web.Response:
    session=require_permission(request, "view_stats")
    try:
        body=await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")
    path=str(body.get("command") or "").strip().lstrip("/").replace("/", ".").replace(" ", ".")
    if not path: return _error("command مطلوب")
    bot=request.app.get("bot"); guild=bot.guilds[0] if bot and bot.guilds else None
    if not guild: return _error("البوت غير متصل بسيرفر",503)
    uid=int(session.get("user_id") or 0)
    if session.get("virtual"):
        digest=int(hashlib.sha256(session.get("code", "").encode()).hexdigest()[:15], 16)
        principal_id=-(digest or 1)
        ranks.register_dashboard_principal(principal_id, session.get("rank", "team"))
        author=DashboardAuthor(principal_id, session.get("name", "Dashboard Staff"), session.get("rank", "team"))
    else:
        author=guild.get_member(uid)
        if not author: return _error("عضو الجلسة غير موجود في السيرفر",403)
    channel=guild.system_channel or (guild.text_channels[0] if guild.text_channels else None)
    ctx=DashboardContext(bot,guild,author,channel,session=session)
    cmd=None
    # Resolve the same loaded command object used by Discord.
    cmd = _find_command(bot, path)
    # Accept a stale client path or a Discord-style qualified name without
    # weakening the catalog: resolution still only walks loaded leaf commands.
    if cmd is None:
        compact = ".".join(part for part in path.split(".") if part)
        cmd = _find_command(bot, compact)
    if cmd is None: return _error("الأمر غير موجود",404)
    try:
        ctx.command = cmd
        if hasattr(cmd,"can_run") and not await cmd.can_run(ctx): return _error("ما عندك صلاحية لهذا الأمر",403)
        sig=inspect.signature(cmd.callback); raw=body.get("args") or {}; call=[]
        for n,p in sig.parameters.items():
            if n in ("self","ctx"): continue
            if p.default is inspect._empty and n not in raw: return _error(f"المعامل {n} مطلوب")
            if n in raw:
                param_meta = getattr(cmd, "_params", {}).get(n) if hasattr(cmd, "_params") else None
                call.append(_resolve_arg(bot,guild,raw[n],getattr(p,"annotation",str),param_meta))
            elif p.default is not inspect._empty: call.append(p.default)
        if any(v is None for v in call): return _error("تعذر العثور على أحد المعاملات",404)
        # مهلة زمنية قصوى 25 ثانية لأي أمر: بعض الأوامر (مثل lockdown) تتعامل
        # مع عدد كبير من القنوات/الأعضاء وقد تتأخر بسبب Discord rate limits.
        # بدون هذه المهلة، الطلب يبقى معلّقًا في المتصفح بلا أي رد.
        try:
            await asyncio.wait_for(cmd.callback(getattr(cmd,"cog",None), ctx, *call), timeout=25)
        except asyncio.TimeoutError:
            logger.warning(
                "Dashboard command timed out: /%s by %s",
                path,
                getattr(author, "name", session.get("name", "unknown")),
            )
            return _error("تجاوز الأمر المهلة الزمنية (25 ثانية) — قد يكون لا يزال يعمل بالخلفية، تحقق من سجل التدقيق بعد قليل", 504)
        message = "\n".join(ctx.sent[-3:]) or "تم التنفيذ."
        actor_name = getattr(author, "name", session.get("name", "Dashboard"))
        try:
            ranks.log_action(
                getattr(author, "id", 0),
                actor_name,
                "dashboard_command",
                f"تنفيذ /{path}",
                "dashboard",
            )
        except Exception:
            # لا نحول عملية Discord الناجحة إلى فشل HTTP بسبب تعطل سجل التدقيق.
            logger.exception("Could not persist dashboard audit log for /%s", path)
        logger.info("Dashboard command executed: /%s by %s", path, actor_name)
        return _json_response({"ok":True,"message":message})
    except (discord.Forbidden,discord.HTTPException):
        logger.warning(
            "Dashboard command rejected by Discord: /%s by %s",
            path,
            getattr(author, "name", session.get("name", "unknown")),
        )
        return _error("Discord رفض تنفيذ العملية.",403)
    except commands.CheckFailure:
        logger.warning(
            "Dashboard command denied: /%s by %s",
            path,
            getattr(author, "name", session.get("name", "unknown")),
        )
        return _error("ما عندك صلاحية لهذا الأمر.", 403)
    except (TypeError, ValueError):
        return _error("المعاملات غير صالحة.", 400)
    except Exception:
        logger.exception("Dashboard command execution failed: /%s", path)
        return _error("تعذر تنفيذ الأمر. راجع سجل البوت للتفاصيل.", 500)


async def api_atria(request: web.Request) -> web.Response:
    require_permission(request, "view_stats")
    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")
    prompt = body.get("prompt")
    mode = body.get("mode", "chat")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        return _error("اكتب نصاً صالحاً لا يتجاوز 4000 حرف.")
    if mode not in {"chat", "moderation"}:
        return _error("وضع Atria غير صالح.")
    system = (
        "You are Vixen Discord EDR assistant. Help with Discord administration, moderation, security and bot operations. Be concise and safe."
        if mode == "chat" else
        "You are a Discord safety moderator. Analyze the supplied text and return JSON only with action: allow, warn, timeout, or ban, plus a short reason. Do not invent facts."
    )
    try:
        answer = await atria_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            1400,
        )
        return _json_response({"ok": True, "answer": answer[:4000]})
    except (RuntimeError, ValueError):
        logger.exception("Dashboard Atria request failed")
        return _error("تعذر الوصول إلى خدمة Atria الآن.", 502)

async def api_money(request: web.Request) -> web.Response:
    session = require_permission(request, "")  # الصلاحية تتحدد حسب العملية تحت
    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")

    virtual = session.get("virtual", False)
    actor_id = int(session["user_id"]) if (not virtual and session["user_id"].isdigit()) else 0
    user_id = str(body.get("user_id") or "").strip()
    action = body.get("action")          # add / remove / reset
    mode = body.get("mode", "wallet")    # wallet / bank
    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        return _error("المبلغ لازم يكون رقمًا")

    if isinstance(body.get("amount", 0), bool) or not user_id.isdigit() or int(user_id) <= 0:
        return _error("آيدي العضو غير صالح")
    if mode not in {"wallet", "bank"}:
        return _error("نوع الرصيد غير صالح")

    settings = load_settings().get("economy", {})
    try:
        starting_balance = int(settings["starting_balance"])
        max_wallet = int(settings["max_wallet"])
        max_bank = int(settings["max_bank"])
        if min(max_wallet, max_bank) < 0:
            raise ValueError("invalid balance caps")
    except (KeyError, TypeError, ValueError, OverflowError):
        logger.exception("Invalid Dashboard economy settings")
        return _error("إعدادات الاقتصاد غير صالحة.", 503)

    bot = request.app.get("bot")
    if virtual:
        actor_name = session.get("name") or f"كود …{session.get('code', '')[-4:]}"
    else:
        actor_name = resolve_name(bot, actor_id)

    if action == "add":
        if not _session_has(session, "add_money"):
            return _error("ما عندك صلاحية إضافة فلوس", 403)
        if amount <= 0:
            return _error("المبلغ لازم يكون أكبر من صفر")
        if virtual:
            usage_key = f"code_{session.get('code', '؟')}"
            limit = ranks.DEFAULT_DAILY_LIMITS.get(session["rank"], 0)
        else:
            limit = ranks.get_daily_limit(actor_id)
            usage_key = actor_id
        if not ranks.reserve_daily_quota(usage_key, amount, limit):
            remaining = ranks.remaining_daily_quota(usage_key)
            return _error(f"تجاوزت حدك اليومي — المتبقي لك: {remaining or 0}", 403)

        outcome = {}
        balance_key = "bank" if mode == "bank" else "wallet"
        cap = max_bank if mode == "bank" else max_wallet

        def add_money(user):
            try:
                balance = max(0, int(user.get(balance_key, 0)))
                earned = max(0, int(user.get("total_earned", 0)))
            except (TypeError, ValueError, OverflowError):
                outcome["invalid"] = True
                return False
            credited = min(amount, max(0, cap - balance))
            if credited <= 0:
                outcome["full"] = True
                return False
            user[balance_key] = balance + credited
            if balance_key == "wallet":
                user["total_earned"] = earned + credited
            outcome.update({"credited": credited, "balance": balance + credited})
            return True

        try:
            new_data = atomic_update_user(int(user_id), add_money, starting_balance)
        except (OSError, TypeError, ValueError, OverflowError):
            ranks.adjust_daily_usage(usage_key, -amount)
            logger.exception("Dashboard money addition failed actor=%s target=%s", actor_id, user_id)
            return _error("تعذر حفظ الإضافة المالية. حاول لاحقًا.", 500)
        if outcome.get("full") or outcome.get("invalid"):
            ranks.adjust_daily_usage(usage_key, -amount)
            return _error("رصيد العضو وصل إلى الحد الأقصى أو أن بياناته غير صالحة.")
        if outcome["credited"] < amount:
            ranks.adjust_daily_usage(usage_key, outcome["credited"] - amount)
        try:
            ranks.log_action(actor_id, actor_name, "add_money",
                             f"أضاف {outcome['credited']} لـ {user_id} ({'بنك' if mode == 'bank' else 'محفظة'})", "dashboard")
        except (OSError, TypeError, ValueError):
            logger.exception("Could not write Dashboard money audit log")
        logger.info("dashboard_add_money actor=%s target=%s amount=%s", actor_id, user_id, outcome["credited"])
        return _json_response({"ok": True, "wallet": new_data["wallet"], "bank": new_data["bank"]})

    elif action == "remove":
        if not _session_has(session, "remove_money"):
            return _error("ما عندك صلاحية سحب فلوس", 403)
        if amount <= 0:
            return _error("المبلغ لازم يكون أكبر من صفر")
        balance_key = "bank" if mode == "bank" else "wallet"
        outcome = {}

        def remove_money(user):
            try:
                balance = max(0, int(user.get(balance_key, 0)))
            except (TypeError, ValueError, OverflowError):
                outcome["invalid"] = True
                return False
            removed = min(amount, balance)
            user[balance_key] = balance - removed
            outcome["removed"] = removed
            return True

        try:
            new_data = atomic_update_user(int(user_id), remove_money, starting_balance)
        except (OSError, TypeError, ValueError, OverflowError):
            logger.exception("Dashboard money removal failed actor=%s target=%s", actor_id, user_id)
            return _error("تعذر حفظ السحب المالي. حاول لاحقًا.", 500)
        if outcome.get("invalid"):
            return _error("رصيد العضو غير صالح.")
        try:
            ranks.log_action(actor_id, actor_name, "remove_money",
                             f"سحب {outcome['removed']} من {user_id} ({'بنك' if mode == 'bank' else 'محفظة'})", "dashboard")
        except (OSError, TypeError, ValueError):
            logger.exception("Could not write Dashboard money audit log")
        logger.info("dashboard_remove_money actor=%s target=%s amount=%s", actor_id, user_id, outcome["removed"])
        return _json_response({"ok": True, "wallet": new_data["wallet"], "bank": new_data["bank"]})

    elif action == "reset":
        if not _session_has(session, "reset_user"):
            return _error("ما عندك صلاحية التصفير", 403)
        try:
            new_data = atomic_update_user(
                int(user_id), lambda user: user.update({"wallet": 0, "bank": 0})
            )
        except (OSError, TypeError, ValueError, OverflowError):
            logger.exception("Dashboard balance reset failed actor=%s target=%s", actor_id, user_id)
            return _error("تعذر تصفير الرصيد. حاول لاحقًا.", 500)
        ranks.log_action(actor_id, actor_name, "reset_user", f"تصفير رصيد {user_id}", "dashboard")
        return _json_response({"ok": True, "wallet": new_data["wallet"], "bank": new_data["bank"]})

    return _error("عملية غير معروفة")


# ---------------------------------------------------------------------------
# API: الفريق والأكواد
# ---------------------------------------------------------------------------

async def api_staff(request: web.Request) -> web.Response:
    require_permission(request, "view_stats")
    bot = request.app.get("bot")

    staff = []
    for uid, entry in ranks.list_staff().items():
        staff.append({
            "id": uid,
            "name": resolve_name(bot, uid),
            "rank": entry.get("rank"),
            "rank_name": ranks.RANK_NAMES_AR.get(entry.get("rank", ""), entry.get("rank", "")),
            "code": entry.get("code", ""),
            "activated_at": entry.get("activated_at", 0),
            "added_today": ranks.get_today_usage(int(uid)),
        })

    codes = []
    for code, entry in ranks.list_codes().items():
        codes.append({
            "code": code,
            "rank": entry.get("rank"),
            "rank_name": ranks.RANK_NAMES_AR.get(entry.get("rank", ""), entry.get("rank", "")),
            "created_at": entry.get("created_at", 0),
            "used_by": entry.get("used_by"),
            "used_by_name": resolve_name(bot, entry["used_by"]) if entry.get("used_by") else None,
            "revoked": entry.get("revoked", False),
            "note": entry.get("note", ""),
        })
    codes.sort(key=lambda c: c["created_at"], reverse=True)

    return _json_response({
        "ok": True,
        "staff": staff,
        "codes": codes,
        "permissions_meta": {
            "all": ranks.ALL_PERMISSIONS,
            "defaults": ranks.DEFAULT_PERMISSIONS,
            "daily_limits": ranks.DEFAULT_DAILY_LIMITS,
            "owner_id": ranks.get_owner_id(),
        },
    })


async def api_staff_code(request: web.Request) -> web.Response:
    session = require_permission(request, "manage_codes")
    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")

    rank = body.get("rank", "")
    note = str(body.get("note", ""))[:100]
    actor_rank = session.get("rank", "")
    actor_level = 4 if actor_rank == "owner" else ranks.RANK_LEVELS.get(actor_rank, 0)
    if rank not in ranks.RANK_LEVELS:
        return _error("رتبة غير صالحة")
    if actor_rank != "owner" and ranks.RANK_LEVELS[rank] >= actor_level:
        return _error("لا يمكنك إنشاء كود لرتبة مساوية أو أعلى من رتبتك.", 403)
    try:
        count = max(1, min(int(body.get("count", 1)), 5))
    except (TypeError, ValueError):
        count = 1

    actor_id = int(session["user_id"]) if (not session.get("virtual") and session["user_id"].isdigit()) else 0
    codes = []
    for _ in range(count):
        code = ranks.generate_code(rank, actor_id, note)
        if code:
            codes.append(code)
    if not codes:
        return _error("رتبة غير صالحة")

    ranks.log_action(actor_id, session.get("name", "لوحة التحكم"), "generate_codes",
                     f"{len(codes)} كود رتبة {rank}", "dashboard")
    return _json_response({"ok": True, "codes": codes})


async def api_staff_revoke(request: web.Request) -> web.Response:
    session = require_permission(request, "manage_codes")
    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")

    code = (body.get("code") or "").strip().upper()
    entry = ranks.list_codes().get(code)
    if not isinstance(entry, dict):
        return _error("الكود غير موجود", 404)
    actor_rank = session.get("rank", "")
    actor_level = 4 if actor_rank == "owner" else ranks.RANK_LEVELS.get(actor_rank, 0)
    target_level = ranks.RANK_LEVELS.get(entry.get("rank", ""), 0)
    if actor_rank != "owner" and target_level >= actor_level:
        return _error("لا يمكنك سحب كود لرتبة مساوية أو أعلى من رتبتك.", 403)
    if ranks.revoke_code(code):
        actor_id = int(session["user_id"]) if (not session.get("virtual") and session["user_id"].isdigit()) else 0
        ranks.log_action(actor_id, session.get("name", "لوحة التحكم"), "revoke_code", f"سحب {code}", "dashboard")
        return _json_response({"ok": True})
    return _error("الكود غير موجود", 404)


async def api_staff_remove(request: web.Request) -> web.Response:
    session = require_permission(request, "manage_staff")
    try:
        body = await request.json()
    except Exception:
        return _error("طلب غير صالح")
    if not isinstance(body, dict):
        return _error("طلب غير صالح")

    user_id = str(body.get("user_id") or "").strip()
    if not user_id.isdigit() or int(user_id) <= 0:
        return _error("آيدي غير صالح")
    actor_rank = session.get("rank", "")
    target_rank = ranks.get_rank(int(user_id)) or ""
    actor_level = 4 if actor_rank == "owner" else ranks.RANK_LEVELS.get(actor_rank, 0)
    target_level = 4 if target_rank == "owner" else ranks.RANK_LEVELS.get(target_rank, 0)
    if target_rank and actor_rank != "owner" and target_level >= actor_level:
        return _error("لا يمكنك إزالة عضو رتبته مساوية أو أعلى من رتبتك.", 403)
    if ranks.remove_staff(int(user_id)):
        actor_id = int(session["user_id"]) if not session.get("virtual") and session["user_id"].isdigit() else 0
        ranks.log_action(actor_id, session.get("name", "لوحة التحكم"), "remove_staff", f"إزالة {user_id}", "dashboard")
        return _json_response({"ok": True})
    return _error("العضو ما عنده رتبة أو هو المالك", 400)


# ---------------------------------------------------------------------------
# API: الإعدادات
# ---------------------------------------------------------------------------

async def api_settings_get(request: web.Request) -> web.Response:
    require_permission(request, "manage_settings")
    return _json_response({"ok": True, "settings": load_settings()})


async def api_settings_save(request: web.Request) -> web.Response:
    require_permission(request, "manage_settings")
    try:
        body = await request.json()
    except Exception:
        return _error("JSON غير صالح")
    settings = body.get("settings")
    if not isinstance(settings, dict):
        return _error("بنية الإعدادات غير صالحة")

    save_settings(settings)
    ranks.log_action(0, "لوحة التحكم", "save_settings", "تعديل الإعدادات من اللوحة", "dashboard")
    return _json_response({"ok": True})


# ---------------------------------------------------------------------------
# API: السجل والسوق
# ---------------------------------------------------------------------------

async def api_logs(request: web.Request) -> web.Response:
    require_permission(request, "view_logs")
    logs = sorted(ranks.load_logs(), key=lambda l: l.get("ts", 0), reverse=True)[:100]
    return _json_response({"ok": True, "logs": logs})


async def api_market(request: web.Request) -> web.Response:
    require_permission(request, "view_stats")
    cfg = load_settings().get("stocks", {})
    market = load_market()
    stocks = []
    for entry in cfg.get("symbols", []):
        sym = entry["symbol"].upper()
        data = market.get("prices", {}).get(sym, {"price": entry.get("base_price", 100), "prev": 0, "history": []})
        change = ((data["price"] - data["prev"]) / data["prev"] * 100) if data.get("prev") else 0
        stocks.append({
            "symbol": sym,
            "name": entry.get("name", ""),
            "emoji": entry.get("emoji", "📈"),
            "price": data.get("price", 0),
            "change": round(change, 2),
            "history": data.get("history", [])[-24:],
        })

    loans_data = load_loans()
    _refresh_overdue(loans_data)
    loans = [{
        "id": l["id"],
        "lender": l["lender"],
        "borrower": l["borrower"],
        "principal": l["principal"],
        "total": l["total"],
        "paid": l["paid"],
        "status": l["status"],
        "due": l["due"],
    } for l in loans_data["loans"][-50:]]

    return _json_response({"ok": True, "stocks": stocks, "loans": loans, "last_update": market.get("last_update", 0)})


# ---------------------------------------------------------------------------
# التشغيل
# ---------------------------------------------------------------------------

@web.middleware
async def error_middleware(request, handler):
    """يحول كل الأخطاء إلى استجابات JSON موحدة (تناسب الواجهة)."""
    try:
        return await handler(request)
    except web.HTTPException as e:
        if e.status >= 400:
            return web.json_response(
                {"ok": False, "error": e.reason or "خطأ في الطلب"},
                status=e.status,
            )
        raise
    except Exception as e:  # خطأ غير متوقع — نرجع JSON بدل صفحة خطأ نصية
        logger.exception(f"خطأ غير متوقع في {request.path}: {e}")
        return web.json_response(
            {"ok": False, "error": "صار خطأ غير متوقع بالسيرفر"},
            status=500,
        )


def create_web_app(bot) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["bot"] = bot

    app.router.add_get("/", serve_index)

    app.router.add_post("/api/login", api_login)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/users", api_users)
    app.router.add_get("/api/members", api_members)
    app.router.add_get("/api/commands", api_commands)
    app.router.add_post("/api/commands/execute", api_command_execute)
    app.router.add_post("/api/atria", api_atria)
    app.router.add_post("/api/money", api_money)
    app.router.add_get("/api/staff", api_staff)
    app.router.add_post("/api/staff/code", api_staff_code)
    app.router.add_post("/api/staff/revoke", api_staff_revoke)
    app.router.add_post("/api/staff/remove", api_staff_remove)
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_post("/api/settings", api_settings_save)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_get("/api/market", api_market)

    if (DASHBOARD_DIR / "assets").exists():
        app.router.add_static("/assets/", str(DASHBOARD_DIR / "assets"))

    return app


async def start_web_server(bot) -> bool:
    """يشغل سيرفر الداشبورد داخل نفس حلقة أحداث البوت."""
    cfg = load_settings().get("dashboard", {})
    if not cfg.get("enabled", True):
        logger.info("لوحة التحكم معطلة من الإعدادات (dashboard.enabled=false)")
        return False

    host = os.getenv("DASHBOARD_HOST") or cfg.get("host", "127.0.0.1")
    try:
        port = int(os.getenv("DASHBOARD_PORT") or cfg.get("port", 8080))
    except (TypeError, ValueError):
        port = 8080

    app = create_web_app(bot)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        bot._dashboard_runner = runner
        logger.info(f"🖥️ لوحة التحكم تعمل الآن على http://{host}:{port}")
        if not (os.getenv("DASHBOARD_PASSWORD") or cfg.get("password")):
            logger.warning("⚠️ DASHBOARD_PASSWORD غير مضبوط — دخول المالك معطل.")
        return True
    except OSError as e:
        logger.error(f"تعذر تشغيل لوحة التحكم على {host}:{port} — {e}")
        await runner.cleanup()
        return False
    except Exception:
        logger.exception("Could not start Dashboard server on %s:%s", host, port)
        await runner.cleanup()
        return False
