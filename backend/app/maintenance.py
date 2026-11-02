"""One-off maintenance commands."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import structlog

from app.models.session import Session, SessionStatus
from research_engine import citation_rate

logger = structlog.get_logger()


@dataclass(frozen=True)
class BackfillPlan:
    """One row that would change, and what it would change to.

    Carries the row's index rather than the row so a caller can print the plan, count it,
    and decide — the dry run is the point, not a courtesy.
    """

    index: int
    rate: float


def plan_backfill(sessions: list[Session]) -> list[BackfillPlan]:
    """Which sessions can be measured from what is already stored, and to what.

    Pure, so the rules below are testable without a database — they are the whole of the
    command's judgement:

    - **COMPLETED only.** A draft is not a report; measuring one would publish a number
      for text no human approved, and it would change under the reader on approval.
    - **Never overwrite.** A rate recorded at finalize was computed against the live
      outcome; recomputing from a stored report is second-hand, so an existing value wins.
    - **No citations stays NULL.** `resolution_rate` returns `None` for a report with no
      markers, and that `None` is a finding, not a gap to fill.
    - **All-dangling records 0.0.** That *is* a measurement — the ⚠-chip case — and it
      has to be findable in History rather than hidden as unmeasured.
    """
    plans: list[BackfillPlan] = []
    for i, s in enumerate(sessions):
        if s.status != SessionStatus.COMPLETED:
            continue
        if s.citation_resolution_rate is not None:
            continue
        if not s.final_report:
            continue
        rate = citation_rate.resolution_rate(s.final_report, s.sources)
        if rate is None:
            continue
        plans.append(BackfillPlan(index=i, rate=rate))
    return plans


async def backfill_citation_rate(*, apply: bool) -> int:
    """Recompute the rate for completed sessions that never had one. Returns the count."""
    from sqlalchemy import select

    from app.db.base import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            rows = (
                (
                    await db.execute(
                        select(Session)
                        .where(
                            Session.status == SessionStatus.COMPLETED,
                            Session.citation_resolution_rate.is_(None),
                        )
                        .order_by(Session.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            plans = plan_backfill(list(rows))

            for plan in plans:
                row = rows[plan.index]
                print(f"{'apply ' if apply else 'would '} {row.id}  →  {plan.rate:.4f}")
                if apply:
                    row.citation_resolution_rate = plan.rate

            skipped = len(rows) - len(plans)
            print(
                f"\n{len(plans)} session(s) measurable, {skipped} left unmeasured "
                f"(no citable claims, or no stored report)."
            )
            if apply:
                await db.commit()
                print("Written.")
            else:
                print("Dry run — nothing written. Re-run with --apply.")
            return len(plans)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser(
        "backfill-citation-rate",
        help="Recompute citation_resolution_rate for completed sessions that lack one.",
    )
    backfill.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the command only prints what it would do.",
    )
    args = parser.parse_args(argv)

    if args.command == "backfill-citation-rate":
        asyncio.run(backfill_citation_rate(apply=args.apply))
    return 0


if __name__ == "__main__":  # pragma: no cover — entry point
    raise SystemExit(main())
