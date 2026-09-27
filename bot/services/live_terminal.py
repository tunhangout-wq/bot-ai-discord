"""In-process fanout of real Python log records to authorized Dashboard clients."""

import asyncio
import collections
import datetime
import logging
import threading


class LiveTerminalHandler(logging.Handler):
    def __init__(self, history_limit=500):
        super().__init__(level=logging.INFO)
        self.history = collections.deque(maxlen=history_limit)
        self.queues = set()
        self.loop = None
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            event = {
                "timestamp": datetime.datetime.fromtimestamp(
                    record.created, datetime.timezone.utc
                ).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:4000],
            }
            with self._lock:
                self.history.append(event)
                loop = self.loop
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(self._publish, event)
        except Exception:
            self.handleError(record)

    def subscribe(self):
        self.loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=200)
        self.queues.add(queue)
        with self._lock:
            history = list(self.history)[-100:]
        for event in history:
            queue.put_nowait(event)
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)

    def _publish(self, event):
        for queue in tuple(self.queues):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


live_terminal = LiveTerminalHandler()


def install_live_terminal():
    root = logging.getLogger()
    if live_terminal not in root.handlers:
        root.addHandler(live_terminal)