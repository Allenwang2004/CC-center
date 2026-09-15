"""
The one-way channel from the watcher to every open browser tab.

Server-sent events: whoever is looking at the page subscribes, and anything that
changes the picture --- a scan finishing, a setting changing, a session starting
to wait for you --- gets pushed. Bounded queues, and a full one drops rather than
blocks: a tab that stopped reading must never hold up a scan.
"""

from __future__ import annotations

import queue
import threading


class Bus:
    """SSE 用: 誰都可以訂閱, 有事就推。"""

    def __init__(self):
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def emit(self, event, data):
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass


BUS = Bus()
