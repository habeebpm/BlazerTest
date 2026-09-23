"""
Optional local ML win-probability advisor - scikit-learn (pure CPU/local,
no cloud API and no GPU; nothing leaves the machine once `pip install
scikit-learn joblib` has run) trained on THIS system's own historical
evaluation snapshots plus real MT5-sourced trade outcomes.

Follows the exact same "informational context, never a hard gate" pattern
already established by market_intel.dxy_context()/consensus_context()/
recent_performance_summary(): its output is read-only context folded into
the snapshot Claude sees (see market_intel.build_feature_snapshot()'s
"ml_win_probability" key and claude_advisor.SYSTEM_PROMPT) - executor.gate()
knows nothing about it and never will. Every function here gracefully
no-ops (returns None, or a {"trained": False, "reason": ...} dict) rather
than raising whenever scikit-learn/joblib aren't installed, no model has
been trained yet, or there isn't enough labeled data - a bare `pip install`
skip, a fresh account with no trade history, or a corrupt model file must
never block or crash a live evaluation cycle.

Two halves:
  - log_snapshot() - called once per EXECUTED trade (see main.py's
    run_once()) to append this cycle's numeric feature vector, keyed by the
    same ticket log_decision()/log_trade() already use, to
    logs/ml_snapshots.csv. Never called for a rejected decision - there is
    no outcome to ever join a rejected evaluation to.
  - train_model() (offline; see train_ml_model.py's CLI wrapper) joins
    ml_snapshots.csv against MT5's real closed-trade P&L - the exact same
    ticket-based join calibration_report.py already uses - and fits a
    GradientBoostingClassifier(win=1/loss=0), persisting it to
    logs/ml_win_probability_model.joblib. win_probability_context() loads
    that persisted model (cached in-process, invalidated on file mtime
    change) and scores the CURRENT snapshot.

Honest limitation: like every other piece of this solution, its only
opinion is "what did trades that looked like this actually do", drawn from
this account's own history - it starts with zero signal on a fresh account
and only gets more useful as more trades accumulate (see train_model()'s
min_samples floor).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from config import AdvisorConfig

# executor is imported lazily inside the functions that need its _csv_path()/
# _append_row() helpers, not at module level: executor.py itself imports
# market_intel (for atr_sl_distance()), and market_intel imports this module
# (to fold win_probability_context() into build_feature_snapshot()) - a
# top-level `import executor` here would make that a circular import.

log = logging.getLogger(__name__)

MODEL_FILENAME = "ml_win_probability_model.joblib"
SNAPSHOT_FILENAME = "ml_snapshots.csv"


def extract_features(snapshot: dict, direction: str | None = None) -> dict:
    """Flattens build_feature_snapshot()'s nested dict (the same JSON
    already handed to Claude) into a flat {name: float} numeric feature
    vector a classifier can use - drops raw candles, timestamps, and
    free-text fields. Pure function, no I/O: every lookup has a neutral
    default, so this never raises even on a bare {} (see FEATURE_NAMES
    below, which is derived by calling this on an empty snapshot), the
    same "missing optional context degrades to a safe default" convention
    dxy_context()/consensus_context() use for the pieces they can't read.

    `direction` ("buy"/"sell", optional) folds in WHICH side was actually
    traded as its own feature (direction_is_buy) - most of the rest of this
    vector (RSI, ADX, structure trend, ...) is directionless market state,
    so without this a buy and a sell taken in a similar-looking market
    state but with opposite real outcomes would otherwise be
    indistinguishable to the classifier, diluting the signal exactly where
    it matters most (counter-trend/ambiguous setups). log_snapshot() always
    passes the real traded direction; win_probability_context() (called
    from build_feature_snapshot(), before Claude has picked a direction)
    scores the snapshot once per candidate direction instead - see its own
    docstring.
    """
    pi = snapshot.get("primary_indicators") or {}
    tb = snapshot.get("trend_bias") or {}
    smc = snapshot.get("smc") or {}
    sweep = smc.get("liquidity_sweep") or {}
    zone = smc.get("premium_discount") or {}
    structure = smc.get("market_structure") or {}
    order_blocks = smc.get("order_blocks") or {}
    bull_ob = order_blocks.get("bullish_order_block") or {}
    bear_ob = order_blocks.get("bearish_order_block") or {}
    last_event = structure.get("last_event") or {}
    candle = snapshot.get("last_closed_candle") or {}
    session = snapshot.get("session") or {}
    perf = snapshot.get("recent_performance") or {}
    dxy = snapshot.get("dxy") or {}
    consensus = snapshot.get("consensus") or {}
    hist_shape = pi.get("macd_hist_shape") or {}

    close = pi.get("close") or 0.0

    def pct_diff(a, b):
        return (a - b) / close if close else 0.0

    return {
        "rsi14": pi.get("rsi14", 50.0),
        "macd_hist": pi.get("macd_hist", 0.0),
        "macd_hist_prev": pi.get("macd_hist_prev", 0.0),
        "macd_hist_declining": 1.0 if hist_shape.get("declining_from_peak") else 0.0,
        "adx14": pi.get("adx14", 0.0),
        "plus_di": pi.get("plus_di", 0.0),
        "minus_di": pi.get("minus_di", 0.0),
        "di_gap": abs(pi.get("plus_di", 0.0) - pi.get("minus_di", 0.0)),
        "stoch_k": pi.get("stoch_k", 50.0),
        "stoch_d": pi.get("stoch_d", 50.0),
        "atr14": pi.get("atr14", 0.0),
        "bollinger_percent_b": pi.get("bollinger_percent_b", 0.5),
        "bollinger_bandwidth": pi.get("bollinger_bandwidth", 0.0),
        "ema20_vs_ema50_pct": pct_diff(pi.get("ema20", 0.0), pi.get("ema50", 0.0)),
        "ema50_vs_ema200_pct": pct_diff(pi.get("ema50", 0.0), pi.get("ema200", 0.0)),
        "trend_close_vs_ema200_pct": pct_diff(tb.get("close", 0.0), tb.get("ema200", 0.0)),
        "liquidity_swept": 1.0 if sweep.get("swept") else 0.0,
        "premium_discount_pct": zone.get("position_pct", 0.5),
        "structure_bullish": 1.0 if structure.get("trend") == "bullish" else 0.0,
        "structure_bearish": 1.0 if structure.get("trend") == "bearish" else 0.0,
        "bos_event": 1.0 if last_event.get("type") == "BOS" else 0.0,
        "choch_event": 1.0 if last_event.get("type") == "CHoCH" else 0.0,
        "price_inside_bullish_ob": 1.0 if bull_ob.get("price_inside_zone") else 0.0,
        "price_inside_bearish_ob": 1.0 if bear_ob.get("price_inside_zone") else 0.0,
        "fvg_count": float(len(smc.get("fair_value_gaps") or [])),
        "candle_body_pct": candle.get("body_pct_of_range", 0.0),
        "candle_bullish": 1.0 if candle.get("bullish") else 0.0,
        "hour_utc": float(session.get("hour_utc", 0)),
        "session_overlap": 1.0 if session.get("session_overlap") else 0.0,
        "recent_win_rate_pct": perf.get("win_rate_pct", 50.0) if perf.get("trade_count") else 50.0,
        "recent_trade_count": float(perf.get("trade_count", 0)),
        "dxy_change_pct_10bars": dxy.get("change_pct_last_10_bars", 0.0),
        "dxy_above_ema20": 1.0 if dxy.get("vs_ema20") == "above" else 0.0,
        "consensus_buy_positions": float(consensus.get("other_system_buy_positions", 0)),
        "consensus_sell_positions": float(consensus.get("other_system_sell_positions", 0)),
        "direction_is_buy": 1.0 if direction == "buy" else 0.0,
    }


# Single source of truth for column order (training and scoring must agree
# on it) - derived from extract_features() itself rather than hand-kept in
# sync with it, so the two can never silently drift apart.
FEATURE_NAMES: tuple = tuple(extract_features({}).keys())

SNAPSHOT_FIELDS = ["time", "ticket", "direction"] + list(FEATURE_NAMES)


def _model_path(cfg: AdvisorConfig) -> str:
    import executor
    return executor._csv_path(cfg, MODEL_FILENAME)


def log_snapshot(cfg: AdvisorConfig, snapshot: dict, direction: str, ticket) -> None:
    """Appends one row to logs/ml_snapshots.csv for a just-executed trade
    (dry-run or live), keyed by the same ticket log_decision()/log_trade()
    already use so train_model() can join it to that ticket's real MT5 P&L
    later - exactly calibration_report.py's own join. A dry-run ticket is
    still logged; it simply never matches a row in MT5's real closed-trade
    history at training time (same as an unmatched ticket there), so it's
    just never usable as a training example, not an error. Wrapped in
    try/except and never raises: this is called from the hot evaluation
    path (main.py's run_once()) right after a real order was already sent,
    so a logging problem here must never look like a failed trade.
    """
    try:
        import executor
        features = extract_features(snapshot, direction)
        row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "ticket": ticket, "direction": direction, **features}
        executor._append_row(executor._csv_path(cfg, SNAPSHOT_FILENAME), SNAPSHOT_FIELDS, row)
    except Exception as exc:
        log.warning("ml_advisor.log_snapshot: could not log this trade's feature snapshot (%s) - "
                    "continuing; this trade just won't be part of future ML training data.", exc)


def _load_snapshots(cfg: AdvisorConfig) -> list:
    # Reuses calibration_report.py's own load_csv() rather than
    # re-implementing "missing file -> []" here - one place owns that edge
    # case for every CSV this package reads back.
    import calibration_report
    import executor
    return calibration_report.load_csv(executor._csv_path(cfg, SNAPSHOT_FILENAME))


def train_model(cfg: AdvisorConfig, gateway=None, min_samples: int = 30,
                 lookback_days: int = 365) -> dict:
    """Offline training entry point (see train_ml_model.py) - joins
    logs/ml_snapshots.csv against MT5's real closed-trade P&L and fits a
    GradientBoostingClassifier on extract_features()'s numeric vector,
    persisting it to logs/ml_win_probability_model.joblib.

    `gateway` defaults to the real mt5_gateway module (lazy-imported, same
    convention as claude_advisor.build_client()) but can be a fake for
    testing without a live MT5 connection - see selftest.py.

    Returns {"trained": False, "reason": "..."} rather than raising for
    every "not ready yet" case (scikit-learn/joblib missing, no snapshots
    logged yet, an MT5 read failure, fewer than min_samples labeled
    examples, or every labeled example sharing one outcome - a classifier
    needs both a win and a loss to learn anything), and {"trained": True,
    "n_samples": N, "train_accuracy": ...} on success.
    """
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        import joblib
    except ImportError:
        return {"trained": False,
                "reason": "scikit-learn/joblib are not installed - `pip install scikit-learn "
                          "joblib` to enable the local ML win-probability advisor (see "
                          "requirements.txt)."}

    rows = _load_snapshots(cfg)
    if not rows:
        return {"trained": False,
                "reason": "no logged snapshots yet (logs/ml_snapshots.csv is empty or missing) - "
                          "needs at least one executed trade first."}

    if gateway is None:
        import mt5_gateway as gateway
    try:
        trades = gateway.recent_closed_trades(cfg.symbol, cfg.magic, count=5000,
                                              lookback_days=lookback_days)
    except Exception as exc:
        return {"trained": False, "reason": f"could not read MT5 trade history: {exc}"}

    pnl_by_ticket: dict = {}
    for t in trades:
        ticket = str(t["ticket"])
        pnl_by_ticket[ticket] = pnl_by_ticket.get(ticket, 0.0) + t["pnl_dollars"]

    X, y = [], []
    for row in rows:
        ticket = str(row.get("ticket", "")).strip()
        if not ticket or ticket not in pnl_by_ticket:
            continue
        pnl = pnl_by_ticket[ticket]
        if pnl == 0:
            continue  # scratch trade (closed flat) - no clean win/loss label to learn from
        try:
            X.append([float(row[name]) for name in FEATURE_NAMES])
        except (KeyError, ValueError):
            continue
        y.append(1 if pnl > 0 else 0)

    if len(X) < min_samples:
        return {"trained": False,
                "reason": f"only {len(X)} labeled trade(s) with real MT5 P&L so far - need at "
                          f"least {min_samples} before training a useful model."}
    if len(set(y)) < 2:
        return {"trained": False,
                "reason": "every labeled trade so far shares one outcome (all wins or all "
                          "losses) - a classifier needs both to learn anything yet."}

    model = GradientBoostingClassifier(random_state=0)
    model.fit(X, y)
    train_accuracy = float(model.score(X, y))
    os.makedirs(cfg.log_dir, exist_ok=True)
    joblib.dump({"model": model, "feature_names": FEATURE_NAMES, "n_samples": len(X)},
                _model_path(cfg))
    log.info("ml_advisor.train_model: trained on %d labeled trade(s) (train accuracy %.1f%%) -> %s",
              len(X), train_accuracy * 100.0, _model_path(cfg))
    return {"trained": True, "n_samples": len(X), "train_accuracy": round(train_accuracy, 3)}


# path -> (mtime, loaded_dict); invalidated whenever the file on disk
# changes (a fresh train_model() run), so a long-running main.py process
# picks up a newer model without needing a restart.
_model_cache: dict = {}


def _load_model(cfg: AdvisorConfig):
    path = _model_path(cfg)
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    cached = _model_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        import joblib
    except ImportError:
        return None
    try:
        loaded = joblib.load(path)
    except Exception as exc:
        log.warning("ml_advisor: could not load persisted model at %s (%s) - continuing without "
                    "it this cycle.", path, exc)
        return None
    _model_cache[path] = (mtime, loaded)
    return loaded


def win_probability_context(cfg: AdvisorConfig, snapshot: dict) -> dict | None:
    """Informational win-probability estimate from the locally-trained
    model (see train_model()/train_ml_model.py) - purely additional context
    folded into the snapshot Claude sees (see claude_advisor.SYSTEM_PROMPT),
    exactly like dxy_context()/consensus_context()/recent_performance_summary():
    executor.gate() never reads this, so a stale, wrong, or missing model
    can never itself block or force a trade. Returns None (Claude's prompt
    already treats a null "dxy"/"consensus" section as "ignore this
    entirely") whenever no model has been trained yet, scikit-learn/joblib
    aren't installed, or scoring this snapshot fails for any reason - never
    raises.

    Called from build_feature_snapshot() BEFORE Claude has picked a
    direction, so which side will actually be traded isn't known yet -
    unlike log_snapshot() (which always logs the real traded direction),
    this scores the SAME market state once per candidate direction (see
    extract_features()'s own `direction` parameter) and reports both, so
    Claude can weigh whichever side it's actually leaning toward.
    """
    loaded = _load_model(cfg)
    if loaded is None:
        return None
    try:
        model = loaded["model"]
        feature_names = loaded["feature_names"]

        def score(direction: str) -> float:
            features = extract_features(snapshot, direction)
            vector = [[features.get(name, 0.0) for name in feature_names]]
            return float(model.predict_proba(vector)[0][1])

        return {
            "win_probability_pct_buy": round(score("buy") * 100.0, 1),
            "win_probability_pct_sell": round(score("sell") * 100.0, 1),
            "trained_on_n_trades": loaded.get("n_samples"),
            "note": "locally-trained estimate from this system's own trade history, one per "
                    "candidate direction - informational only, weigh it like recent_performance, "
                    "never a hard rule",
        }
    except Exception as exc:
        log.warning("ml_advisor.win_probability_context: could not score this snapshot (%s) - "
                    "continuing without it.", exc)
        return None
