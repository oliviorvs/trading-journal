"""Analyses 2 à 5 — Setups, Instruments, Sessions, Temporel.

Le journal a déjà des « Répartitions » (n, part, win rate, R moyen, P&L).
Ce module y ajoute ce qui manquait pour DÉCIDER : profit factor, expectancy
en $ ET en R, drawdown cumulé du groupe, statut de fiabilité, et — pour les
sessions — le meilleur et le pire setup DANS la session.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from services.analyzer.adapter import Dataset, TradeView
from services.analyzer import metrics as metrics_mod
from services.analyzer import reliability
from services.analyzer.sessions import session_labels

WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# Dimensions calculables SANS aucune nouvelle saisie (Lot 2). Chaque entrée
# est une fonction qui extrait la clé de regroupement d'un trade.
KEY_EXTRACTORS: dict[str, Callable[[TradeView], Optional[str]]] = {
    "setup": lambda t: t.setup,
    "playbook": lambda t: t.playbook,
    "symbol": lambda t: t.symbol,
    "session": lambda t: t.session,
    "direction": lambda t: t.direction,
    "timeframe": lambda t: t.timeframe,
    "exit_reason": lambda t: t.exit_reason,
    "weekday": lambda t: WEEKDAYS[t.weekday],
    "hour": lambda t: f"{t.hour:02d}h",
    "week": lambda t: f"{t.open_time.isocalendar()[0]}-S{t.open_time.isocalendar()[1]:02d}",
    "month": lambda t: t.open_time.strftime("%Y-%m"),
    "duration": lambda t: _duration_bucket(t),
    "risk_source": lambda t: t.risk_source,
}

# Dimensions à plusieurs valeurs par trade (tables de liaison / colonnes
# historiques) — traitées à part, voir psychology.py.
MULTI_DIMENSIONS = {"emotion", "error", "tag"}

# Dimensions dont l'ordre d'affichage est naturel (chronologique), pas par
# performance décroissante : trier « Lundi, Mercredi, Mardi » n'aide personne.
ORDERED = {"weekday": WEEKDAYS, "hour": [f"{h:02d}h" for h in range(24)]}
SORT_BY_KEY = {"week", "month"}


def _duration_bucket(trade: TradeView) -> Optional[str]:
    """Mêmes tranches que `stats.duration_bucket` — reprises telles quelles
    pour que l'Analyzer et la page Répartitions racontent la même histoire."""
    minutes = trade.duration_minutes
    if minutes is None:
        return None
    if minutes < 5:
        return "<5min"
    if minutes < 15:
        return "5-15min"
    if minutes < 60:
        return "15-60min"
    if minutes < 240:
        return "1-4h"
    return ">4h"


def available_dimensions() -> List[str]:
    return sorted(KEY_EXTRACTORS) + sorted(MULTI_DIMENSIONS)


def by_dimension(dataset: Dataset, name: str, base: str = "gross",
                 thresholds: Optional[dict] = None) -> dict:
    """Tableau d'une dimension, chaque ligne annotée de sa fiabilité."""
    if name in MULTI_DIMENSIONS:
        from services.analyzer.analyzers import psychology
        return psychology.by_multi_dimension(dataset, name, base, thresholds)

    extractor = KEY_EXTRACTORS.get(name)
    if extractor is None:
        raise KeyError(name)

    views = dataset.views
    order: Optional[Sequence[str]] = ORDERED.get(name)
    if name == "session":
        order = session_labels(list(dataset.session_windows))

    rows = metrics_mod.group_by(
        views, extractor, base,
        total_count=len(views),
        order=order,
        sort_by="key" if name in SORT_BY_KEY else "pnl",
    )
    rows = _annotate(rows, views, extractor, thresholds)

    payload = {
        "dimension": name,
        "base": base,
        "total_trades": len(views),
        "rows": rows,
        **metrics_mod.best_and_worst(rows),
    }
    if name == "session":
        payload["session_details"] = _session_details(views, base, thresholds)
    return payload


def _annotate(rows: List[dict], views: Sequence[TradeView],
              extractor: Callable[[TradeView], Optional[str]],
              thresholds: Optional[dict]) -> List[dict]:
    """Recalcule les R par groupe pour l'intervalle de confiance. Un second
    passage sur les trades, mais sans relecture de la base (R7)."""
    r_by_key: dict[str, List[float]] = {}
    for trade in views:
        if trade.r is None:
            continue
        key = extractor(trade) or metrics_mod.NOT_SET
        r_by_key.setdefault(key, []).append(trade.r)
    return [reliability.annotate(row, r_by_key.get(row["key"], []), thresholds) for row in rows]


def _session_details(views: Sequence[TradeView], base: str,
                     thresholds: Optional[dict]) -> List[dict]:
    """Pour chaque session : son meilleur et son pire setup.

    C'est la question à laquelle le journal ne sait pas répondre aujourd'hui
    (« ma session de Londres est bonne — mais avec quel setup ? »).
    """
    grouped: dict[str, List[TradeView]] = {}
    for trade in views:
        grouped.setdefault(trade.session, []).append(trade)

    details = []
    for session, trades in grouped.items():
        setups = metrics_mod.group_by(trades, lambda t: t.setup, base, total_count=len(trades))
        extremes = metrics_mod.best_and_worst(setups)
        details.append({
            "session": session,
            "trades": len(trades),
            "best_setup": extremes["best"],
            "worst_setup": extremes["worst"],
            "setups": setups[:5],
            "best_hour": _best_hour(trades, base),
        })
    details.sort(key=lambda d: d["trades"], reverse=True)
    return details


def _best_hour(trades: Sequence[TradeView], base: str) -> Optional[str]:
    hours = metrics_mod.group_by(trades, lambda t: f"{t.hour:02d}h", base, total_count=len(trades))
    eligible = [h for h in hours if h["trades"] >= 3]
    return max(eligible, key=lambda h: h["expectancy"])["key"] if eligible else None


def heatmap(dataset: Dataset, base: str = "gross") -> dict:
    """Carte de chaleur jour × heure.

    Le journal en a déjà une, mais en P&L uniquement. Celle-ci porte aussi le
    win rate, le R moyen et `n` : une case verte à +450 $ sur 2 trades et une
    case verte à +450 $ sur 40 trades ne veulent pas dire la même chose.
    """
    cells: dict[tuple, List[TradeView]] = {}
    for trade in dataset.views:
        cells.setdefault((trade.weekday, trade.hour), []).append(trade)

    data = []
    for (weekday, hour), trades in cells.items():
        summary = metrics_mod.summarize(trades, base)
        data.append({
            "weekday": weekday,
            "weekday_label": WEEKDAYS[weekday],
            "hour": hour,
            "trades": summary["trades"],
            "pnl": summary["pnl"],
            "win_rate": summary["win_rate"],
            "avg_r": summary["avg_r"],
            "expectancy": summary["expectancy"],
        })
    data.sort(key=lambda c: (c["weekday"], c["hour"]))
    return {"base": base, "cells": data}
