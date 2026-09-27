# Vixen Discord EDR Control Center

نظام إدارة Discord فعلي يجمع بين Bot + Dashboard + Economy + Staff + Atria Dawn.

## التشغيل

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

املأ `.env`:

- `DISCORD_TOKEN`
- `OWNER_ID`
- `DASHBOARD_PASSWORD`
- `AI_API_KEY` مضبوط في `.env` لتشغيل Atria (يظل `ATRIA_API_KEY` مدعومًا للتوافق)
- `GUILD_ID` اختياري لتسريع مزامنة Slash Commands لسيرفر محدد

ثم:

```bash
python -m bot.main
```

Dashboard: `http://127.0.0.1:8080` افتراضيًا، أو المنفذ الموجود في `DASHBOARD_PORT` عند التشغيل المحلي.

## Control Center

كل أوامر Control Center التنفيذية متاحة كـ `/slash` وPrefix، وتشمل:

- Server: lockdown/unlockdown, name, icon, verification, slowmode, systemchannel, features
- Channel: create/delete/rename/topic/slowmode/clone/purge/lock/unlock
- Member: timeout/untimeout/kick/ban/unban/deafen/undeafen/mute/unmute/move/dm
- Role: create/delete/add/remove/rename/list
- Security: bans/invites/createinvite/deleteinvite/webhooks/deletewebhooks
- Utility: ping/members/emoji/sticker/event/events/prune/botinfo

الـDashboard يقرأ هذه الأوامر مباشرة من Cog ويعرض معاملات كل أمر ويستدعي نفس callback والصلاحيات، لذلك لا توجد أزرار mock.

## Lockdown

`/server lockdown` يحفظ حالة `@everyone` السابقة لكل قناة ويكمل عند `Forbidden/HTTPException` مع delay صغير لتقليل ضغط Discord. `unlockdown` يستعيد الحالة المحفوظة فقط.

## DM

إرسال DM يحتاج صلاحية Vixen `dm_members` فقط، وليس `moderate_members`. قائمة أعضاء الـDashboard تستخدم pagination بدل حد ثابت 200.

## Staff

`setrank` و`removestaff` يحترمان hierarchy: لا يمكن للعضو منح أو إزالة رتبة مساوية/أعلى من رتبته، والمالك محمي.

## Economy

حد الإضافة اليومي يحسب المبلغ الذي أُضيف فعليًا، مع احترام quota والحدود القصوى للمحفظة والبنك.

## Loans

`/loan` أصبح Hybrid Group: يعمل Slash وPrefix (`!loan request`, `!loan repay`, `!loan my`).

## Atria Dawn

التكامل يستخدم `AI_API_KEY` server-side فقط (`ATRIA_API_KEY` اسم قديم مدعوم). لا تضع المفتاح في JavaScript أو HTML أو Git.

Dashboard: صفحة Atria AI مع Chat وModeration يدوي وتلقائي قابل للتفعيل.
Bot: `/ai chat`.

يمكن تفعيل moderation من `settings.json` عبر `atria.moderation_enabled`. عند `moderation_mode=all` يفحص الرسائل الجديدة تلقائيًا، وعند `prefix` يفحص الرسائل التي تبدأ بالبادئة المحددة. كما يوجد `/ai moderate` للفحص اليدوي.

## حالة التشغيل على Replit

الـWorkflow يشغل البوت والـDashboard في عملية واحدة. إذا لم يتم ضبط `DISCORD_TOKEN` بعد، يبقى الـDashboard متاحًا في وضع الإعداد بدل أن تنتهي العملية؛ لا يمكن تنفيذ أوامر Discord أو تسجيل دخول المالك حتى تتم إضافة `DISCORD_TOKEN` و`OWNER_ID`.

## التنظيف والاختبار

النسخة النهائية لا تحتوي `__pycache__`, `*.pyc`, `.pytest_cache`, أو ملفات مؤقتة.

قبل التشغيل:

```bash
python -m compileall -q bot
```

ثم شغّل البوت واختبر Slash sync والـDashboard ضد سيرفر Discord فعلي.

## Dashboard command catalog
الـCommand Center يكتشف كل leaf commands من جميع الـCogs (وليس Control فقط) ويعرض المعاملات وينفذ الـcallback الحقيقي مع فحص Vixen permissions. المصدر الحالي يحتوي 117 leaf commands.

## Final Control Center / AI notes
- Dashboard login supports Owner credentials and one-use Vixen staff codes.
- Unbound staff-code sessions use a negative, isolated Dashboard principal; they are never treated as the Discord guild owner.
- Command Center discovers executable leaf commands from all loaded Cogs, not only the Control cog.
- Discord application-command metadata is exposed to the dashboard, including choices; the dashboard renders selects for choices and converts Discord objects such as Member/Role/Channel before invoking the same Cog callback.
- Atria Chat and Atria Moderation use `AI_API_KEY` server-side only (`ATRIA_API_KEY` remains a legacy fallback). Automatic moderation is policy-checked and rate-limited.
- Live Discord Gateway/API validation still requires running the project with a real Discord token, guild ID, and bot permissions.


### AI credentials
ضع المفتاح المدور في `AI_API_KEY` داخل `.env` المحلي فقط. لا تضع secrets في `.env.example` أو HTML أو JavaScript أو Git. يظل `ATRIA_API_KEY` مدعومًا كاسم قديم للتوافق.
