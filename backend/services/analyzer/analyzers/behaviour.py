"""Analyses 8, 9 et 10 — Séries, Fréquence, Overtrading.

Le journal connaît déjà les plus longues séries et le % de jours profitables.
Ce module ajoute ce qui manque pour comprendre le COMPORTEMENT :
- combien de temps il faut pour récupérer d'un creux ;
- si trader plus dans une journée dégrade le résultat (overtrading) ;
- ce que devient le trade qui suit immédiatement une perte (revenge trading).

Ce sont les questions que l'utilisateur se pose après une mauvaise semaine,
et auxquelles aucun écran actuel ne répond.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from services.analyzer.adapter import Dataset, TradeView
from services.analyzer import metrics as metrics_mod
from services.analyzer import reliability


# ── Analyse 8 : séries et récupération ──────────────────────────────────────

def series(dataset: Dataset, base: str = "gross") -> dict:
    views = dataset.views
    if not views:
        return {"base": base, "longest_win": 0, "longest_loss": 0, "alternations": 0,
                "drawdowns": [], "current_streak": None, "streak_performance": []}

    longest_win = longest_loss = current_win = current_loss = 0
    alternations = 0
    last_sign = 0
    for trade in views:
        sign = 1 if trade.is_win else (-1 if trade.is_loss else 0)
        if sign == 1:
            current_win += 1
            current_loss = 0
        elif sign == -1:
            current_loss += 1
            current_win = 0
        else:
            # Un breakeven interrompt les DEUX séries (convention de
            # compute_stats), mais ne compte pas comme une alternance.
            current_win = current_loss = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
        if sign != 0:
            if last_sign != 0 and sign != last_sign:
                alternations += 1
            last_sign = sign

    values = [t.value(base) for t in views]
    return {
        "base": base,
        "longest_win": longest_win,
        "longest_loss": longest_loss,
        "alternations": alternations,
        # Une alternance systématique (ratio proche de 1) signale un trading
        # sans continuité ; des séries longues signalent des régimes.
        "alternation_rate": round(alternations / max(len(views) - 1, 1) * 100, 1),
        "current_streak": _current_streak(views),
        "drawdowns": _drawdown_episodes(views, values),
        "streak_performance": _after_streak(views, base),
    }


def _current_streak(views: Sequence[TradeView]) -> Optional[dict]:
    kind = None
    count = 0
    for trade in reversed(views):
        sign = "win" if trade.is_win else ("loss" if trade.is_loss else None)
        if sign is None:
            break
        if kind is None:
            kind = sign
        if sign != kind:
            break
        count += 1
    return {"kind": kind, "length": count} if kind else None


def _drawdown_episodes(views: Sequence[TradeView], values: Sequence[float],
                       top: int = 5) -> List[dict]:
    """Épisodes de baisse de la courbe cumulée du groupe, avec leur
    récupération. « En cours » si le sommet n'a jamais été repris — c'est
    l'information la plus utile de la liste."""
    episodes: List[dict] = []
    peak = 0.0
    running = 0.0
    peak_index: Optional[int] = None
    trough = 0.0
    trough_index: Optional[int] = None

    for index, value in enumerate(values):
        running += value
        if running >= peak:
            if peak_index is not None and trough_index is not None and trough < peak:
                episodes.append(_episode(views, peak_index, trough_index, index, peak, trough))
            peak = running
            peak_index = index
            trough = running
            trough_index = None
        elif running < trough or trough_index is None:
            trough = running
            trough_index = index

    if peak_index is not None and trough_index is not None and trough < peak:
        episodes.append(_episode(views, peak_index, trough_index, None, peak, trough))

    episodes.sort(key=lambda e: e["depth"])
    return episodes[:top]


def _episode(views: Sequence[TradeView], peak_index: int, trough_index: int,
             recovery_index: Optional[int], peak: float, trough: float) -> dict:
    trough_trade = views[trough_index]
    recovered = recovery_index is not None
    trades_to_recover = (recovery_index - trough_index) if recovered else None
    days_to_recover = None
    if recovered:
        days_to_recover = (views[recovery_index].open_time - trough_trade.open_time).days
    return {
        "depth": round(trough - peak, 2),
        "from": views[peak_index].open_time.date().isoformat(),
        "trough": trough_trade.open_time.date().isoformat(),
        "recovered": recovered,
        "trades_to_recover": trades_to_recover,
        "days_to_recover": days_to_recover,
        "trades_in_drawdown": trough_index - peak_index,
    }


def _after_streak(views: Sequence[TradeView], base: str) -> List[dict]:
    """Performance du trade qui SUIT une série de n pertes consécutives.

    Sert à objectiver le « revenge trading » : si le trade qui suit deux
    pertes est nettement moins bon que la moyenne, c'est mesuré, pas ressenti.
    """
    buckets: Dict[str, List[TradeView]] = {}
    consecutive_losses = 0
    for trade in views:
        label = "0 perte" if consecutive_losses == 0 else (
            f"{consecutive_losses} perte(s)" if consecutive_losses < 3 else "3+ pertes"
        )
        buckets.setdefault(label, []).append(trade)
        if trade.is_loss:
            consecutive_losses += 1
        elif trade.is_win:
            consecutive_losses = 0

    order = ["0 perte", "1 perte(s)", "2 perte(s)", "3+ pertes"]
    rows = [
        {"key": label, **metrics_mod.summarize(buckets[label], base, total_count=len(views))}
        for label in order if label in buckets
    ]
    return rows


# ── Analyse 9 : fréquence ───────────────────────────────────────────────────

def frequency(dataset: Dataset, base: str = "gross",
              thresholds: Optional[dict] = None) -> dict:
    """Performance selon le NOMBRE de trades pris dans la journée.

    Chaque trade est rangé dans la tranche correspondant au volume total de
    SA journée : « quand je prends 5 trades ou plus dans la journée, mon
    espérance est-elle la même que les jours où j'en prends 1 ou 2 ? »
    """
    views = dataset.views
    per_day: Dict[object, List[TradeView]] = {}
    for trade in views:
        per_day.setdefault(trade.day, []).append(trade)

    buckets: Dict[str, List[TradeView]] = {}
    for trades in per_day.values():
        label = _frequency_bucket(len(trades))
        buckets.setdefault(label, []).extend(trades)

    order = ["1 trade", "2-3 trades", "4-5 trades", "6+ trades"]
    rows = []
    for label in order:
        if label not in buckets:
            continue
        summary = metrics_mod.summarize(buckets[label], base, total_count=len(views))
        summary = reliability.annotate(
            summary, [t.r for t in buckets[label] if t.r is not None], thresholds
        )
        summary["key"] = label
        summary["days"] = sum(1 for trades in per_day.values() if _frequency_bucket(len(trades)) == label)
        rows.append(summary)

    day_counts = [len(trades) for trades in per_day.values()]
    return {
        "base": base,
        "rows": rows,
        "trading_days": len(per_day),
        "avg_per_day": round(sum(day_counts) / len(day_counts), 2) if day_counts else 0,
        "max_in_a_day": max(day_counts) if day_counts else 0,
        "weeks": len({(t.open_time.isocalendar()[0], t.open_time.isocalendar()[1]) for t in views}),
        "months": len({t.open_time.strftime("%Y-%m") for t in views}),
    }


def _frequency_bucket(count: int) -> str:
    if count <= 1:
        return "1 trade"
    if count <= 3:
        return "2-3 trades"
    if count <= 5:
        return "4-5 trades"
    return "6+ trades"


# ── Analyse 10 : overtrading ────────────────────────────────────────────────

def overtrading(dataset: Dataset, base: str = "gross",
                thresholds: Optional[dict] = None) -> dict:
    """Trois angles sur la même question : est-ce que j'en fais trop ?

    1. performance du n-ième trade du jour (1, 2, 3, 4+) ;
    2. performance selon l'état du jour à l'entrée (en gain / en perte) ;
    3. performance du trade qui suit immédiatement une perte, le même jour.
    """
    views = dataset.views

    rank_rows = []
    for label in ["1", "2", "3", "4+"]:
        trades = [t for t in views if _rank_label(t.rank_in_day) == label]
        if not trades:
            continue
        summary = metrics_mod.summarize(trades, base, total_count=len(views))
        summary = reliability.annotate(summary, [t.r for t in trades if t.r is not None], thresholds)
        summary["key"] = f"Trade n°{label}"
        rank_rows.append(summary)

    state_rows = []
    for label, predicate in (
        ("Jour en gain", lambda t: t.day_pnl_before > 0),
        ("Jour à zéro", lambda t: t.day_pnl_before == 0),
        ("Jour en perte", lambda t: t.day_pnl_before < 0),
    ):
        trades = [t for t in views if predicate(t)]
        if not trades:
            continue
        summary = metrics_mod.summarize(trades, base, total_count=len(views))
        summary["key"] = label
        state_rows.append(summary)

    after_loss = [t for t in views if t.after_loss]
    not_after_loss = [t for t in views if not t.after_loss]
    after = metrics_mod.summarize(after_loss, base, total_count=len(views)) if after_loss else None
    before = metrics_mod.summarize(not_after_loss, base, total_count=len(views)) if not_after_loss else None

    return {
        "base": base,
        "by_rank": rank_rows,
        "by_day_state": state_rows,
        "after_loss": after,
        "other_trades": before,
        "after_loss_delta": (
            round(after["expectancy"] - before["expectancy"], 2)
            if after and before else None
        ),
    }


def _rank_label(rank: int) -> str:
    return str(rank) if rank <= 3 else "4+"
