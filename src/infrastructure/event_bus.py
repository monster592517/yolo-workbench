# -*- coding: utf-8 -*-
"""Event bus - simple pub/sub for decoupled communication"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

Callback = Callable[..., None]


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: Dict[str, List[Callback]] = {}

    def subscribe(self, event: str, callback: Callback):
        with self._lock:
            self._subs.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callback):
        with self._lock:
            if event in self._subs:
                try: self._subs[event].remove(callback)
                except ValueError: pass

    def emit(self, event: str, **data):
        with self._lock:
            callbacks = list(self._subs.get(event, []))
        for cb in callbacks:
            try: cb(**data)
            except Exception: pass


_bus: EventBus = None

def get_event_bus() -> EventBus:
    global _bus
    if _bus is None: _bus = EventBus()
    return _bus
