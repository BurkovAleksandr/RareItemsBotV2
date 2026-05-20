from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


DEFAULT_STEPS = [
    ("bot_start", "Bot start"),
    ("parser_session", "Parser session"),
    ("buyer_session", "Buyer session"),
    ("proxy_setup", "Proxy check"),
    ("sticker_prices", "Sticker prices"),
    ("track_items", "Track items"),
    ("bot_loop", "Market loop"),
]


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


@dataclass
class StepEvent:
    at: str
    status: str
    message: str


@dataclass
class StepState:
    id: str
    label: str
    status: str = "pending"
    detail: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    events: list[StepEvent] = field(default_factory=list)


class RuntimeStatus:
    def __init__(self, recent_limit: int = 30):
        self.lock = threading.RLock()
        self.recent_limit = recent_limit
        self.steps: dict[str, StepState] = {}
        self.recent_checked: deque[dict[str, Any]] = deque(maxlen=recent_limit)
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.steps = {step_id: StepState(step_id, label) for step_id, label in DEFAULT_STEPS}
            self.recent_checked.clear()

    def start_step(self, step_id: str, label: str | None = None, detail: str = "") -> None:
        with self.lock:
            step = self._step(step_id, label)
            step.status = "active"
            step.detail = detail
            step.started_at = step.started_at or _now()
            step.finished_at = None
            self._event(step, "active", detail or "started")

    def update_step(self, step_id: str, detail: str, status: str = "active") -> None:
        with self.lock:
            step = self._step(step_id)
            step.status = status
            step.detail = detail
            self._event(step, status, detail)

    def finish_step(self, step_id: str, detail: str = "done", status: str = "success") -> None:
        with self.lock:
            step = self._step(step_id)
            step.status = status
            step.detail = detail
            step.finished_at = _now()
            self._event(step, status, detail)

    def fail_step(self, step_id: str, detail: str) -> None:
        self.finish_step(step_id, detail=detail, status="error")

    def skip_step(self, step_id: str, detail: str) -> None:
        self.finish_step(step_id, detail=detail, status="skipped")

    def add_checked_item(self, item: dict[str, Any]) -> None:
        with self.lock:
            record = dict(item)
            record.setdefault("checked_at", _now())
            self.recent_checked.appendleft(record)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "steps": [self._step_to_dict(step) for step in self.steps.values()],
                "recent_checked": list(self.recent_checked),
            }

    def _step(self, step_id: str, label: str | None = None) -> StepState:
        if step_id not in self.steps:
            self.steps[step_id] = StepState(step_id, label or step_id.replace("_", " ").title())
        elif label:
            self.steps[step_id].label = label
        return self.steps[step_id]

    def _event(self, step: StepState, status: str, message: str) -> None:
        if not message:
            return
        step.events.append(StepEvent(_now(), status, message))
        if len(step.events) > self.recent_limit:
            del step.events[: len(step.events) - self.recent_limit]

    def _step_to_dict(self, step: StepState) -> dict[str, Any]:
        data = asdict(step)
        data["events"] = [asdict(event) for event in step.events[-8:]]
        return data
