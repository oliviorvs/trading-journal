"""Métriques d'un GROUPE de trades.

Règle R4 : aucune métrique n'est redéfinie. Les formules ci-dessous sont
celles de `services/stats.compute_stats`, appliquées à un sous-ensemble —
la Vue d'ensemble, elle, appelle directement `compute_stats` pour garantir
la parité au centime près (voir analyzers/performance.py).

Voir docs/definitions-metriques.md pour les définitions figées.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from services.analyzer.adapter import TradeView

NOT_SET = "Non renseigné"


def summarize(trades: Sequence[TradeView], base: str = "gross",
              total_count: Optional[int] = None) -> dict:
    """Métriques complètes d'un groupe.

    `base` (décision D1) ne change QUE les montants en $ — la classification
    gagnant / perdant reste sur le brut, ce qui garde le win rate identique
    à celui du Dashboard quelle que soit la base choisie.
    """
    count = len(trades)
    if not count:
        return _empty(total_count)

    # UNE SEULE passe. La version précédente enchaînait une dizaine de
    # compréhensions de liste (gagnants, perdants, valeurs, sommes, R,
    # drawdown, extrêmes) : chacune reparcourait le groupe, et cette fonction
    # est appelée une fois par groupe de chaque dimension. Le benchmark du
    # §14 a montré que c'était le principal coût de l'écran complet à
    # 100 000 trades. Les résultats sont identiques — seul le nombre de
    # parcours change.
    net_base = base == "net"
    wins_count = losses_count = 0
    sum_wins = sum_losses = pnl = 0.0
    best = worst = None
    r_values: List[float] = []
    running = peak = 0.0
    dd_money = 0.0
    r_running = r_peak = 0.0
    dd_r = 0.0

    for trade in trades:
        value = trade.net if net_base else trade.profit
        pnl += value
        # Classification TOUJOURS sur le brut, quelle que soit la base
        # (voir docs/definitions-metriques.md) : c'est ce qui garde le win
        # rate identique à celui du Dashboard.
        if trade.profit > 0:
            wins_count += 1
            sum_wins += value
        elif trade.profit < 0:
            losses_count += 1
            sum_losses += value

        if best is None or value > best:
            best = value
        if worst is None or value < worst:
            worst = value

        running += value
        if running > peak:
            peak = running
        if running - peak < dd_money:
            dd_money = running - peak

        if trade.r is not None:
            r_values.append(trade.r)
            r_running += trade.r
            if r_running > r_peak:
                r_peak = r_running
            if r_running - r_peak < dd_r:
                dd_r = r_running - r_peak

    breakeven = count - wins_count - losses_count
    sum_losses_abs = abs(sum_losses)
    avg_win = sum_wins / wins_count if wins_count else 0.0
    avg_loss = sum_losses_abs / losses_count if losses_count else 0.0
    win_rate_frac = wins_count / count
    loss_rate_frac = losses_count / count
    avg_r = sum(r_values) / len(r_values) if r_values else None
    if not r_values:
        dd_r = None

    result = {
        "trades": count,
        "wins": wins_count,
        "losses": losses_count,
        "breakeven": breakeven,
        "win_rate": round(win_rate_frac * 100, 1),
        "loss_rate": round(loss_rate_frac * 100, 1),
        "pnl": round(pnl, 2),
        "avg_pnl": round(pnl / count, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        # Non défini s'il n'y a aucune perte — `null`, jamais l'infini ni une
        # valeur arbitraire (comportement de compute_stats).
        "profit_factor": round(sum_wins / sum_losses_abs, 2) if sum_losses_abs else None,
        "expectancy": round(win_rate_frac * avg_win - loss_rate_frac * avg_loss, 2),
        "avg_r": round(avg_r, 2) if avg_r is not None else None,
        "expectancy_r": round(avg_r, 2) if avg_r is not None else None,
        # La couverture R accompagne TOUJOURS un R moyen : sans elle, « +0,8R »
        # sur 3 trades renseignés parmi 50 est trompeur (§6 du cahier).
        "r_coverage": round(len(r_values) / count * 100, 1),
        "r_count": len(r_values),
        "drawdown_money": round(dd_money, 2),
        "drawdown_r": round(dd_r, 2) if dd_r is not None else None,
        "best": round(best, 2) if best is not None else None,
        "worst": round(worst, 2) if worst is not None else None,
    }
    if total_count:
        result["pct_of_total"] = round(count / total_count * 100, 1)
    return result


def _empty(total_count: Optional[int]) -> dict:
    empty = {
        "trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
        "win_rate": 0.0, "loss_rate": 0.0, "pnl": 0.0, "avg_pnl": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": None, "expectancy": 0.0,
        "avg_r": None, "expectancy_r": None, "r_coverage": 0.0, "r_count": 0,
        "drawdown_money": 0.0, "drawdown_r": None, "best": None, "worst": None,
    }
    if total_count:
        empty["pct_of_total"] = 0.0
    return empty


def _cumulative_drawdown(values: Sequence[float]) -> float:
    """Plus grande baisse pic → creux de la somme cumulée.

    SANS capital de référence et SANS mouvements de capital : ce n'est PAS le
    drawdown du compte (`stats.max_drawdown`), c'est celui de la séquence de
    résultats du groupe. Libellé distinct dans l'interface pour cette raison.
    Toujours ≤ 0.
    """
    running = peak = 0.0
    worst = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return worst


def group_by(trades: Sequence[TradeView], key: Callable[[TradeView], Optional[str]],
             base: str = "gross", total_count: Optional[int] = None,
             order: Optional[Sequence[str]] = None,
             sort_by: str = "pnl") -> List[dict]:
    """Regroupe et résume. Les trades sans valeur tombent sous
    « Non renseigné » (convention de `by_exit_reason` dans le journal) :
    ils sont VISIBLES, jamais silencieusement écartés — c'est souvent le
    groupe le plus instructif au début."""
    buckets: Dict[str, List[TradeView]] = {}
    for trade in trades:
        value = key(trade)
        buckets.setdefault(value if value else NOT_SET, []).append(trade)

    total = total_count if total_count is not None else len(trades)
    rows = [
        {"key": name, **summarize(group, base, total)}
        for name, group in buckets.items()
    ]
    if order:
        rank = {name: i for i, name in enumerate(order)}
        rows.sort(key=lambda r: (rank.get(r["key"], len(rank)), r["key"]))
    elif sort_by == "key":
        rows.sort(key=lambda r: r["key"])
    else:
        rows.sort(key=lambda r: r.get(sort_by) or 0, reverse=True)
    return rows


def group_by_multi(trades: Sequence[TradeView],
                   key: Callable[[TradeView], Sequence[str]],
                   base: str = "gross",
                   total_count: Optional[int] = None) -> List[dict]:
    """Regroupement quand un trade peut appartenir à PLUSIEURS groupes
    (émotions, erreurs, tags multiples).

    Conséquence assumée : la somme des `n` dépasse le nombre de trades, et
    `pct_of_total` ne totalise pas 100 %. L'interface l'indique — c'est le
    prix des étiquettes multiples, et c'est moins trompeur que de n'en
    retenir arbitrairement qu'une.
    """
    buckets: Dict[str, List[TradeView]] = {}
    for trade in trades:
        values = key(trade)
        if not values:
            buckets.setdefault(NOT_SET, []).append(trade)
            continue
        for value in values:
            buckets.setdefault(value, []).append(trade)

    total = total_count if total_count is not None else len(trades)
    rows = [{"key": name, **summarize(group, base, total)} for name, group in buckets.items()]
    rows.sort(key=lambda r: r["trades"], reverse=True)
    return rows


def best_and_worst(rows: Sequence[dict], min_n: int = 3) -> dict:
    """Meilleur / pire groupe par expectancy $, en ignorant les groupes trop
    petits pour vouloir dire quoi que ce soit."""
    eligible = [r for r in rows if r["trades"] >= min_n and r["key"] != NOT_SET]
    if not eligible:
        return {"best": None, "worst": None}
    return {
        "best": max(eligible, key=lambda r: r["expectancy"])["key"],
        "worst": min(eligible, key=lambda r: r["expectancy"])["key"],
    }
