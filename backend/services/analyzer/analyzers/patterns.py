"""Analyse 14 — Patterns, avec garde-fous statistiques.

Profondeur MAXIMALE : 2 variables (§10 du cahier). Ce n'est pas une limite
technique, c'est une limite statistique : croiser une troisième variable
divise l'échantillon au point que chaque cellule ne contient plus que trois
ou quatre trades. Une « découverte » sur quatre trades n'en est pas une.

Trois garde-fous, tous obligatoires :
1. `n` et intervalle de confiance affichés sur CHAQUE ligne ;
2. statut plafonné quand l'intervalle de l'expectancy R contient 0 (D9) ;
3. avertissement de multiplicité sur le nombre de combinaisons testées.

Un pattern n'est JAMAIS promu en règle automatiquement (règle R8) : il faut
une validation explicite de l'utilisateur (voir playbook.py).
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from services.analyzer.adapter import Dataset, TradeView
from services.analyzer import metrics as metrics_mod
from services.analyzer import reliability
from services.analyzer.analyzers.dimensions import WEEKDAYS

# Croisements du MVP, repris du cahier. Chaque entrée : (libellé, extracteur).
_AXES: dict[str, Tuple[str, Callable[[TradeView], Optional[str]]]] = {
    "setup": ("Setup", lambda t: t.setup),
    "session": ("Session", lambda t: t.session),
    "symbol": ("Instrument", lambda t: t.symbol),
    "weekday": ("Jour", lambda t: WEEKDAYS[t.weekday]),
    "direction": ("Sens", lambda t: t.direction),
    "timeframe": ("Timeframe", lambda t: t.timeframe),
}

# Paires autorisées — volontairement énumérées plutôt que « toutes les
# combinaisons » : chaque paire ajoutée multiplie les tests, donc les faux
# positifs (voir l'avertissement de multiplicité).
_PAIRS = [
    ("setup", "session"),
    ("setup", "symbol"),
    ("setup", "weekday"),
    ("setup", "direction"),
    ("session", "symbol"),
    ("session", "weekday"),
    ("symbol", "direction"),
    ("setup", "timeframe"),
]


def patterns(dataset: Dataset, base: str = "gross", min_n: int = 10,
             thresholds: Optional[dict] = None) -> dict:
    views = dataset.views
    rows: List[dict] = []
    tested = 0
    # Référence calculée UNE FOIS. Elle l'était auparavant à l'intérieur de
    # `_row`, donc une fois par ligne retenue : à 20 000 trades et ~50 lignes,
    # cela représentait à lui seul l'essentiel du temps de l'écran Patterns
    # (3,2 s mesurées). Le benchmark du §14 est ce qui l'a mis au jour.
    baseline = metrics_mod.summarize(views, base)

    # 1. Variables seules (profondeur 1) — le point de comparaison.
    for axis_key, (axis_label, extractor) in _AXES.items():
        buckets = _bucket(views, extractor)
        for key, group in buckets.items():
            tested += 1
            if len(group) < min_n:
                continue
            rows.append(_row(f"{axis_label} = {key}", [axis_label], group,
                             views, baseline, base, thresholds))

    # 2. Croisements à 2 variables.
    for left, right in _PAIRS:
        left_label, left_extractor = _AXES[left]
        right_label, right_extractor = _AXES[right]
        combined = _bucket(
            views,
            lambda t, a=left_extractor, b=right_extractor: _combine(a(t), b(t)),
        )
        for key, group in combined.items():
            tested += 1
            if len(group) < min_n:
                continue
            rows.append(_row(
                f"{left_label} × {right_label} = {key}",
                [left_label, right_label], group, views, baseline, base, thresholds,
            ))

    # Tri par intérêt décroissant : expectancy R quand elle existe, sinon
    # expectancy $. Les lignes « non concluantes » restent visibles mais
    # descendent — elles sont l'information la plus fréquente, et la masquer
    # donnerait une fausse impression d'abondance de signaux.
    rows.sort(key=lambda r: (not r["inconclusive"], r.get("expectancy_r") or 0, r["expectancy"]),
              reverse=True)

    return {
        "base": base,
        "min_n": min_n,
        "baseline": baseline,
        "rows": rows,
        "multiplicity": reliability.multiplicity_note(tested),
        "note": "Une observation n'est pas une règle : aucun pattern n'est appliqué automatiquement.",
    }


def _combine(left: Optional[str], right: Optional[str]) -> Optional[str]:
    # Un croisement où l'une des deux variables est vide n'est pas exploitable :
    # « Setup non renseigné × Londres » n'apprend rien sur le setup.
    if not left or not right:
        return None
    return f"{left} · {right}"


def _bucket(views: Sequence[TradeView],
            extractor: Callable[[TradeView], Optional[str]]) -> dict:
    buckets: dict[str, List[TradeView]] = {}
    for trade in views:
        key = extractor(trade)
        if not key:
            continue
        buckets.setdefault(key, []).append(trade)
    return buckets


def _row(condition: str, axes: List[str], group: Sequence[TradeView],
         all_views: Sequence[TradeView], baseline: dict, base: str,
         thresholds: Optional[dict]) -> dict:
    summary = metrics_mod.summarize(group, base, total_count=len(all_views))
    summary = reliability.annotate(summary, [t.r for t in group if t.r is not None], thresholds)
    summary["condition"] = condition
    summary["axes"] = axes
    summary["depth"] = len(axes)

    # Écart à la référence : un win rate de 58 % n'est un signal que s'il se
    # détache de la moyenne générale de l'utilisateur.
    summary["delta_expectancy"] = round(summary["expectancy"] - baseline["expectancy"], 2)
    summary["delta_win_rate"] = round(summary["win_rate"] - baseline["win_rate"], 1)
    return summary
