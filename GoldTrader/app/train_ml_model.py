#!/usr/bin/env python3
"""
Offline trainer for the local ML win-probability advisor (see ml_advisor.py) -
joins logs/ml_snapshots.csv (one row per EXECUTED trade, logged by main.py's
run_once() via ml_advisor.log_snapshot()) against that trade's real closed
P&L from MT5's own history, and fits a scikit-learn classifier on the result,
persisted to logs/ml_win_probability_model.joblib.

Run this by hand (or from cron/Task Scheduler) periodically as more trades
accumulate - main.py's live evaluation loop only ever READS the persisted
model (see market_intel.build_feature_snapshot()'s "ml_win_probability" key);
it never trains one itself. Needs `pip install scikit-learn joblib` (see
requirements.txt) - main.py keeps running fine without either package
installed, this script just has nothing to do.

Usage:
    python train_ml_model.py                      # uses logs/ml_snapshots.csv
    python train_ml_model.py --min-samples 50      # require more labeled trades before training
    python train_ml_model.py --lookback-days 730   # how far back to query MT5 history
"""
from __future__ import annotations

import argparse
import logging

import ml_advisor
from config import AdvisorConfig

log = logging.getLogger(__name__)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--magic", type=int, default=20260921, help="must match config.py's AdvisorConfig.magic")
    parser.add_argument("--log-dir", default=AdvisorConfig().log_dir, dest="log_dir")
    parser.add_argument("--min-samples", type=int, default=30, dest="min_samples",
                        help="minimum labeled (real-P&L) trades required before training (default 30)")
    parser.add_argument("--lookback-days", type=int, default=365, dest="lookback_days")
    parser.add_argument("--login", type=int)
    parser.add_argument("--password")
    parser.add_argument("--server")
    parser.add_argument("--terminal-path", dest="terminal_path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    cfg = AdvisorConfig(symbol=args.symbol, magic=args.magic, log_dir=args.log_dir)

    import mt5_gateway as gw
    gw.connect(login=args.login, password=args.password, server=args.server,
              terminal_path=args.terminal_path)

    result = ml_advisor.train_model(cfg, min_samples=args.min_samples,
                                    lookback_days=args.lookback_days)
    if result["trained"]:
        print(f"Trained on {result['n_samples']} labeled trade(s) - cross-validated accuracy "
              f"{result['cv_accuracy'] * 100.0:.1f}% vs a {result['base_rate'] * 100.0:.1f}% base rate "
              "(always guessing the more common outcome).")
        if result["cv_accuracy"] <= result["base_rate"]:
            print("No measurable edge over the base rate yet - Claude is told the same numbers, so "
                  "it can discount the estimate; keep trading and retrain later.")
        print(f"Model saved to {cfg.log_dir}/{ml_advisor.MODEL_FILENAME} - "
              "main.py's live loop will pick it up on the next evaluation cycle.")
        return 0
    print(f"Not trained: {result['reason']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
