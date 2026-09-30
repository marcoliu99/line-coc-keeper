"""Inspect or apply receipt-only legacy correction reconciliation.

Run with the bot stopped for --apply. Summary rebuild is separate and only
performed for approved corrections with a configured conversation provider.
"""
from __future__ import annotations

import argparse
import asyncio

from app.services import correction_summary, history_reconciliation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist exact-receipt annotations")
    parser.add_argument("--rebuild-summaries", action="store_true",
                        help="after --apply, use the configured provider to rebuild corrected summaries")
    args = parser.parse_args()
    if args.rebuild_summaries and not args.apply:
        parser.error("--rebuild-summaries requires --apply")
    groups = history_reconciliation.group_ids()
    totals = {"approved_receipts": 0, "newly_marked_log_entries": 0}
    for group_id in groups:
        result = history_reconciliation.reconcile_group(group_id, apply=args.apply)
        if args.rebuild_summaries and result["approved_receipts"]:
            asyncio.run(correction_summary.rebuild(group_id))
        for key in totals:
            totals[key] += result[key]
    print(f"groups={len(groups)} apply={args.apply} "
          f"approved_receipts={totals['approved_receipts']} "
          f"newly_marked_log_entries={totals['newly_marked_log_entries']}")


if __name__ == "__main__":
    main()
