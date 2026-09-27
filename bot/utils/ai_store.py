"""Persistent, separated storage for AI moderation events and chat context."""

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vixen_ai.sqlite3"


class AIStore:
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ai_moderation_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    detected_event TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    confidence REAL,
                    action_requested TEXT,
                    action_executed TEXT,
                    result TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_moderation_guild_time
                    ON ai_moderation_logs(guild_id, timestamp DESC);
                CREATE TABLE IF NOT EXISTS ai_action_reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_action_reservations
                    ON ai_action_reservations(guild_id, timestamp);
                CREATE TABLE IF NOT EXISTS ai_chat_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_chat_context
                    ON ai_chat_messages(guild_id, channel_id, message_id DESC);
                CREATE TABLE IF NOT EXISTS warnings (
                    warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_target
                    ON warnings(guild_id, user_id, warning_id DESC);
                """
            )
            self._initialized = True
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def log_moderation(
        self,
        guild_id,
        channel_id,
        user_id,
        detected_event,
        classification,
        action_requested,
        action_executed,
        result,
        reason,
        confidence=None,
    ):
        if confidence is not None:
            confidence = max(0.0, min(1.0, float(confidence)))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO ai_moderation_logs (
                    guild_id, channel_id, user_id, detected_event, classification,
                    confidence, action_requested, action_executed, result, reason, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(guild_id), int(channel_id), int(user_id),
                    str(detected_event)[:100], str(classification)[:100], confidence,
                    str(action_requested or "")[:100], str(action_executed or "")[:100],
                    str(result)[:100], str(reason)[:1000], time.time(),
                ),
            )
            return cursor.lastrowid

    def recent_moderation(self, guild_id, limit=100):
        limit = max(1, min(500, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM ai_moderation_logs WHERE guild_id = ?
                   ORDER BY log_id DESC LIMIT ?""",
                (int(guild_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_chat_message(self, guild_id, channel_id, user_id, role, content):
        if role not in {"user", "assistant"}:
            raise ValueError("Invalid AI chat role")
        text = str(content).strip()
        if not text or len(text) > 4000:
            raise ValueError("Invalid AI chat content")
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO ai_chat_messages (
                    guild_id, channel_id, user_id, role, content, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (int(guild_id), int(channel_id), int(user_id), role, text, time.time()),
            )
            return cursor.lastrowid

    def recent_chat(self, guild_id, channel_id, limit=10):
        limit = max(1, min(50, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT role, content FROM ai_chat_messages
                   WHERE guild_id = ? AND channel_id = ?
                   ORDER BY message_id DESC LIMIT ?""",
                (int(guild_id), int(channel_id), limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def recent_chat_entries(self, guild_id, channel_id, limit=100):
        limit = max(1, min(500, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT message_id, guild_id, channel_id, user_id, role, content, timestamp
                   FROM ai_chat_messages WHERE guild_id = ? AND channel_id = ?
                   ORDER BY message_id DESC LIMIT ?""",
                (int(guild_id), int(channel_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_warning(self, guild_id, user_id, moderator_id, reason):
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO warnings(guild_id, user_id, moderator_id, reason, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (int(guild_id), int(user_id), int(moderator_id), str(reason)[:1000], time.time()),
            )
            return cursor.lastrowid

    def count_warnings(self, guild_id, user_id):
        with self._lock, self._connection() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            ).fetchone()[0]

    def recent_warnings(self, guild_id, user_id, limit=20):
        limit = max(1, min(100, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT warning_id, guild_id, user_id, moderator_id, reason, timestamp
                   FROM warnings WHERE guild_id = ? AND user_id = ?
                   ORDER BY warning_id DESC LIMIT ?""",
                (int(guild_id), int(user_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_guild_warnings(self, guild_id, limit=100, user_id=None, moderator_id=None):
        limit = max(1, min(500, int(limit)))
        filters = ["guild_id = ?"]
        params = [int(guild_id)]
        if user_id is not None:
            filters.append("user_id = ?")
            params.append(int(user_id))
        if moderator_id is not None:
            filters.append("moderator_id = ?")
            params.append(int(moderator_id))
        params.append(limit)
        query = (
            "SELECT warning_id, guild_id, user_id, moderator_id, reason, timestamp "
            "FROM warnings WHERE " + " AND ".join(filters) + " ORDER BY warning_id DESC LIMIT ?"
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def clear_warnings(self, guild_id, user_id):
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
                (int(guild_id), int(user_id)),
            )
            return cursor.rowcount

    def prune_chat(self, guild_id, retention_days):
        days = max(1, min(3650, int(retention_days)))
        cutoff = time.time() - days * 86400
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_chat_messages WHERE guild_id = ? AND timestamp < ?",
                (int(guild_id), cutoff),
            )
            return cursor.rowcount

    def reserve_moderation_action(self, guild_id, maximum, now=None):
        limit = max(1, min(1000, int(maximum)))
        current = time.time() if now is None else float(now)
        cutoff = current - 3600
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM ai_action_reservations WHERE timestamp <= ?",
                (cutoff,),
            )
            count = connection.execute(
                "SELECT COUNT(*) FROM ai_action_reservations WHERE guild_id = ? AND timestamp > ?",
                (int(guild_id), cutoff),
            ).fetchone()[0]
            if count >= limit:
                return False
            connection.execute(
                "INSERT INTO ai_action_reservations(guild_id, timestamp) VALUES (?, ?)",
                (int(guild_id), current),
            )
            return True


ai_store = AIStore()