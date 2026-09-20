"""Analyses 6 et 7 — Émotions et Erreurs.

C'est le gain le plus rapide du chantier. Le journal collecte `emotion` et
`error_tag` depuis toujours, les rend configurables dans Réglages… et ne les
analyse NULLE PART : ni dans l'API, ni à l'écran, ni dans le PDF. Seul un
compteur d'usage existe. Tout ce qui suit fonctionne donc dès le Lot 2, sans
une seule nouvelle saisie (voir la compatibilité ascendante de l'adaptateur).

Spécificité des erreurs : on ne mesure pas seulement leur fréquence mais
leur COÛT — la somme des résultats des trades concernés, en $ et en R, et
l'écart avec les trades sans erreur. « Le FOMO m'a coûté 1 240 $ sur 14
trades » est actionnable ; « FOMO : 14 » ne l'est pas.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from services.analyzer.adapter import Dataset, TradeView
from services.analyzer import metrics as metrics_mod
from services.analyzer import reliability

# Valeurs conventionnelles signifiant « pas d'erreur » — elles servent de
# groupe de RÉFÉRENCE, pas de catégorie d'erreur.
NO_ERROR_KEYS = {"aucune", "aucun", "none", "rien"}

_MULTI_EXTRACTORS = {
    "emotion": lambda t: t.emotions,
    "error": lambda t: t.errors,
    "tag": lambda t: t.tags,
}


def by_multi_dimension(dataset: Dataset, name: str, base: str = "gross",
                       thresholds: Optional[dict] = None) -> dict:
    """Dimension à valeurs multiples (un trade peut porter 2 émotions)."""
    extractor = _MULTI_EXTRACTORS.get(name)
    if extractor is None:
        raise KeyError(name)

    views = dataset.views
    rows = metrics_mod.group_by_multi(views, extractor, base, total_count=len(views))
    rows = [
        reliability.annotate(row, _r_values(views, extractor, row["key"]), thresholds)
        for row in rows
    ]
    return {
        "dimension": name,
        "base": base,
        "total_trades": len(views),
        # Un trade pouvant porter plusieurs valeurs, la somme des `n` peut
        # dépasser le total : l'interface doit le dire, sinon les pourcentages
        # paraissent faux.
        "overlapping": any(len(extractor(t)) > 1 for t in views),
        "rows": rows,
        **metrics_mod.best_and_worst(rows),
    }


def _r_values(views: Sequence[TradeView], extractor, key: str) -> List[float]:
    if key == metrics_mod.NOT_SET:
        return [t.r for t in views if t.r is not None and not extractor(t)]
    return [t.r for t in views if t.r is not None and key in extractor(t)]


def error_cost(dataset: Dataset, base: str = "gross",
               thresholds: Optional[dict] = None) -> dict:
    """Coût de chaque erreur, comparé au groupe de référence « sans erreur ».

    `delta_expectancy` est la vraie information : l'écart d'espérance par
    trade entre « avec cette erreur » et « sans erreur déclarée ». Négatif =
    cette erreur coûte, en moyenne, ce montant à chaque trade où elle apparaît.
    """
    views = dataset.views
    clean = [t for t in views if _is_clean(t)]
    reference = metrics_mod.summarize(clean, base) if clean else None

    buckets: dict[str, List[TradeView]] = {}
    unlabelled: List[TradeView] = []
    for trade in views:
        keys = [k for k in trade.errors if k.lower() not in NO_ERROR_KEYS]
        if not keys:
            if not trade.errors:
                unlabelled.append(trade)
            continue
        for key in keys:
            buckets.setdefault(key, []).append(trade)

    rows = []
    for key, trades in buckets.items():
        summary = metrics_mod.summarize(trades, base, total_count=len(views))
        summary = reliability.annotate(
            summary, [t.r for t in trades if t.r is not None], thresholds
        )
        summary["key"] = key
        # COÛT : la somme des résultats des trades portant cette erreur.
        # Un coût positif est possible et instructif — une erreur n'est pas
        # toujours punie par le marché, ce qui est précisément le piège.
        summary["cost"] = summary["pnl"]
        summary["cost_r"] = round(sum(t.r for t in trades if t.r is not None), 2) \
            if any(t.r is not None for t in trades) else None
        summary["delta_expectancy"] = (
            round(summary["expectancy"] - reference["expectancy"], 2) if reference else None
        )
        summary["delta_win_rate"] = (
            round(summary["win_rate"] - reference["win_rate"], 1) if reference else None
        )
        rows.append(summary)

    rows.sort(key=lambda r: r["cost"])  # le plus coûteux en premier

    return {
        "base": base,
        "total_trades": len(views),
        "reference": reference,
        "reference_trades": len(clean),
        "unlabelled_trades": len(unlabelled),
        "rows": rows,
        "most_costly": rows[0]["key"] if rows else None,
    }


def _is_clean(trade: TradeView) -> bool:
    """Trade explicitement déclaré SANS erreur. Un trade non renseigné n'est
    pas « propre » : il est inconnu, et il ne doit pas gonfler le groupe de
    référence."""
    if not trade.errors:
        return False
    return all(key.lower() in NO_ERROR_KEYS for key in trade.errors)


def emotion_matrix(dataset: Dataset, base: str = "gross") -> dict:
    """Croisement émotion × résultat — répond à « est-ce que je trade moins
    bien quand je suis stressé ? »."""
    rows = []
    for emotion in sorted({e for t in dataset.views for e in t.emotions}):
        trades = [t for t in dataset.views if emotion in t.emotions]
        summary = metrics_mod.summarize(trades, base, total_count=len(dataset.views))
        rows.append({"key": emotion, **summary})
    rows.sort(key=lambda r: r["expectancy"], reverse=True)
    return {"base": base, "rows": rows}
