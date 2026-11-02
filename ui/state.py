"""Small deterministic helpers shared by the interface and its tests."""

from __future__ import annotations

import hashlib
import json

LIVE = {"QUEUED", "PENDING", "RUNNING", "PLANNING"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "REJECTED"}
STATUS_LABELS = {
    "QUEUED": "排队中",
    "PENDING": "等待执行",
    "RUNNING": "执行中",
    "AWAITING_PLAN": "等待计划审批",
    "AWAITING_REVIEW": "等待报告审批",
    "COMPLETED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}


def submission_key(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_events(existing: list[dict], incoming: list[dict]) -> list[dict]:
    rows = {row["id"]: row for row in existing + incoming}
    return [rows[k] for k in sorted(rows)][-500:]


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
