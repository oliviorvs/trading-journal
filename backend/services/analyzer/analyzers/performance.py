"""Analyse 1 — Vue d'ensemble.

Exigence de parité (§6 du cahier) : chaque KPI doit être IDENTIQUE, au même
arrondi, à `/api/performance/stats` pour les mêmes filtres. La façon la plus
sûre d'y arriver n'est pas de recalculer « à l'identique », c'est d'appeler
la MÊME fonction : `stats.compute_stats` sur les mêmes objets `Trade`. Ce qui
est propre à l'Analyzer (couverture R, expectancy en R, base nette) est
ajouté À CÔTÉ, jamais en remplacement.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from services import stats
from services.analyzer.adapter import Dataset
from services.analyzer import metrics as metrics_mod


def overview(dataset: Dataset, db: Session, base: str = "gross") -> dict:
    # `include_movements` : les dépôts / retraits neutralisent le drawdown,
    # mais ils n'appartiennent à aucun symbole — même règle que le router
    # /performance/stats quand un filtre symbole est posé.
    include_movements = not dataset.filters.get("symbol")
    journal = stats.compute_stats(list(dataset.trades), db, include_movements=include_movements)

    group = metrics_mod.summarize(dataset.views, base)
    r_values = [t.r for t in dataset.views if t.r is not None]

    return {
        # ── Bloc PARITÉ : tel que renvoyé par le journal, sans retouche ────
        "journal": journal,
        # ── Bloc ANALYZER : ce que le journal ne calcule pas ───────────────
        "analyzer": {
            "base": base,
            "trades": group["trades"],
            "win_rate": group["win_rate"],
            "loss_rate": group["loss_rate"],
            "pnl": group["pnl"],
            "profit_factor": group["profit_factor"],
            "expectancy": group["expectancy"],
            "expectancy_r": group["expectancy_r"],
            "avg_r": group["avg_r"],
            "avg_win": group["avg_win"],
            "avg_loss": group["avg_loss"],
            "r_coverage": group["r_coverage"],
            "r_count": group["r_count"],
            "best_r": round(max(r_values), 2) if r_values else None,
            "worst_r": round(min(r_values), 2) if r_values else None,
            # Somme des R : combien de « risques » ont été gagnés au total.
            "total_r": round(sum(r_values), 2) if r_values else None,
            "gross_pnl": round(sum(t.profit for t in dataset.views), 2),
            "net_pnl": round(sum(t.net for t in dataset.views), 2),
        },
        "filters": dataset.filters,
        "is_manual": dataset.is_manual,
    }
