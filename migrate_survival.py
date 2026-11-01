"""Explicit migration; refuses a funded fixed-stake ledger, preserves run IDs/baselines."""
import argparse,sqlite3
from challenge_ledger import initialize
from challenge_decisions import initialize_decisions
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--database',required=True);args=ap.parse_args()
    c=sqlite3.connect(args.database);c.row_factory=sqlite3.Row
    initialize(c);initialize_decisions(c)
    print('Survival schema ready; existing run IDs and baselines preserved')
