#!/usr/bin/env python
"""
cleanup_worker.py — Background worker to release expired seat holds.

Run this as a separate process alongside the Flask app:
    python cleanup_worker.py

Or add to crontab (every minute):
    * * * * * cd /path/to/stadi_soka && python cleanup_worker.py --once

In production, use a proper scheduler (Celery Beat, APScheduler, or a cron job).
This script uses a simple sleep loop suitable for development and small deployments.
"""

import sys
import time
import argparse
from datetime import datetime


def run_cleanup(app, interval_seconds=60, once=False):
    """Run expired seat hold cleanup on a fixed interval."""
    from seat_reservation import cleanup_expired_holds
    from models import SeatHold

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Cleanup worker started "
          f"(interval={interval_seconds}s, once={once})")

    while True:
        with app.app_context():
            try:
                released = cleanup_expired_holds()
                if released > 0:
                    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Released {released} expired seat hold(s)")
                # Also log total active holds for visibility
                active_holds = SeatHold.query.count()
                if active_holds > 0:
                    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Active holds remaining: {active_holds}")
            except Exception as e:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ERROR during cleanup: {e}", file=sys.stderr)

        if once:
            break
        time.sleep(interval_seconds)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Stadi Soka seat hold cleanup worker')
    parser.add_argument('--interval', type=int, default=60,
                        help='Cleanup interval in seconds (default: 60)')
    parser.add_argument('--once', action='store_true',
                        help='Run once and exit (useful for cron)')
    args = parser.parse_args()

    # Import app here to avoid circular imports
    from app import app
    run_cleanup(app, interval_seconds=args.interval, once=args.once)
