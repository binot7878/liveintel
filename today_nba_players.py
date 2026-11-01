"""NBA-only entry point for the validated ESPN event-date ingestion pipeline."""
import argparse
import datetime
from daily_espn_pipeline import SLATE_ZONE, get_db, process_league


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=datetime.date.fromisoformat,
                        default=datetime.datetime.now(SLATE_ZONE).date())
    args = parser.parse_args()
    conn = get_db()
    try:
        result = process_league(conn, 'NBA', 'basketball/nba', args.date.isoformat())
    finally:
        conn.close()
    return 1 if result['error_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
