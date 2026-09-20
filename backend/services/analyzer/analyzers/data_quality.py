"""Analyse 11 — Qualité des données.

Section volontairement placée en évidence dans l'interface et dans le rapport.
Raison (§8 des limites du cahier) : toute la valeur des analyses Émotions,
Erreurs, Setups et Conformité dépend de ce que l'utilisateur a renseigné.
Afficher « FOMO : win rate 31 % » sans dire que 82 % des trades n'ont aucune
émotion renseignée, c'est donner une fausse impression de mesure.

Cet écran ne juge pas : il chiffre ce qui manque, et signale les anomalies
franches (dates incohérentes, doublons probables).
"""
from __future__ import annotations

from collections import Counter
from typing import List

from services.analyzer.adapter import Dataset
from services.analyzer.sessions import HORS_SESSION


def data_quality(dataset: Dataset) -> dict:
    views = dataset.views
    total = len(views)
    if not total:
        return {"total_trades": 0, "coverage": [], "anomalies": [], "sources": []}

    def missing(predicate) -> dict:
        count = sum(1 for t in views if predicate(t))
        return {
            "missing": count,
            "filled": total - count,
            "coverage": round((total - count) / total * 100, 1),
        }

    coverage = [
        {"field": "Stop Loss", "key": "sl", **missing(lambda t: not t.has_sl)},
        {"field": "R-multiple", "key": "r", **missing(lambda t: t.r is None)},
        {"field": "Risque (%)", "key": "risk", **missing(lambda t: t.risk_percent is None)},
        {"field": "Setup", "key": "setup", **missing(lambda t: not t.setup)},
        {"field": "Playbook", "key": "playbook", **missing(lambda t: not t.playbook)},
        {"field": "Timeframe", "key": "timeframe", **missing(lambda t: not t.timeframe)},
        {"field": "Raison de sortie", "key": "exit", **missing(lambda t: not t.exit_reason)},
        {"field": "Émotion", "key": "emotion", **missing(lambda t: not t.emotions)},
        {"field": "Erreur", "key": "error", **missing(lambda t: not t.errors)},
        {"field": "Conformité au plan (SOP)", "key": "sop", **missing(lambda t: t.sop_score is None)},
    ]

    anomalies: List[dict] = []

    out_of_session = sum(1 for t in views if t.session == HORS_SESSION)
    if out_of_session:
        anomalies.append({
            "key": "out_of_session",
            "count": out_of_session,
            "label": "Trades hors de toute fenêtre de session",
            # Point d'attention du §9.4 : les heures sont en heure SERVEUR.
            # Un compte manuel saisi en heure locale produit exactement ce
            # symptôme — beaucoup de trades « hors session » sans raison.
            "hint": "Vérifiez les fenêtres de session (onglet Paramètres) : "
                    "les heures sont celles du serveur du courtier, pas votre heure locale.",
        })

    bad_dates = sum(
        1 for t in views if t.close_time is not None and t.close_time < t.open_time
    )
    if bad_dates:
        anomalies.append({
            "key": "bad_dates",
            "count": bad_dates,
            "label": "Clôture antérieure à l'ouverture",
            "hint": "Données d'import probablement mal mappées (colonnes inversées).",
        })

    no_close = sum(1 for t in views if t.close_time is None)
    if no_close:
        anomalies.append({
            "key": "no_close_time",
            "count": no_close,
            "label": "Trades clôturés sans heure de clôture",
            "hint": "Les durées de détention de ces trades ne sont pas calculables.",
        })

    # Doublons probables : même symbole, même direction, même heure
    # d'ouverture, même volume. Deux tickets distincts peuvent légitimement
    # correspondre à une position découpée — d'où « probables », jamais une
    # suppression automatique.
    signatures = Counter(
        (t.symbol_raw, t.direction, t.open_time, round(t.profit, 2)) for t in views
    )
    duplicates = sum(count - 1 for count in signatures.values() if count > 1)
    if duplicates:
        anomalies.append({
            "key": "duplicates",
            "count": duplicates,
            "label": "Doublons probables (même symbole, heure et résultat)",
            "hint": "Peut être légitime si une position a été ouverte en plusieurs ordres.",
        })

    zero_volume = sum(1 for t in views if not t.duration_minutes and t.close_time)
    if zero_volume:
        anomalies.append({
            "key": "instant_trades",
            "count": zero_volume,
            "label": "Trades d'une durée nulle",
            "hint": "Ouverture et clôture à la même seconde — fréquent sur certains rapports importés.",
        })

    sources = [
        {"source": source, "trades": count}
        for source, count in Counter(t.source for t in views).most_common()
    ]

    raw_symbols = sorted({t.symbol_raw for t in views})
    return {
        "total_trades": total,
        "coverage": coverage,
        "anomalies": anomalies,
        "sources": sources,
        "distinct_raw_symbols": len(raw_symbols),
        "distinct_symbols": len({t.symbol for t in views}),
        "symbol_suggestions": suggest_symbol_merges(raw_symbols),
    }


def suggest_symbol_merges(raw_symbols: List[str]) -> List[dict]:
    """Suggère de fusionner `XAUUSD.a`, `XAUUSD.m`, `XAUUSDc`… sous `XAUUSD`.

    Heuristique volontairement prudente : on ne regroupe que des symboles
    partageant un même PRÉFIXE déjà présent tel quel, ou un préfixe obtenu en
    retirant un suffixe de courtier reconnaissable (`.a`, `.m`, `-ecn`, `c`…).
    Rien n'est appliqué automatiquement : c'est une proposition à valider.
    """
    groups: dict[str, list] = {}
    for symbol in raw_symbols:
        groups.setdefault(_canonical_guess(symbol), []).append(symbol)
    return [
        {"canonical": canonical, "raw_symbols": sorted(members)}
        for canonical, members in sorted(groups.items())
        if len(members) > 1
    ]


def _canonical_guess(symbol: str) -> str:
    base = symbol.split(".")[0].split("-")[0].split("_")[0]
    # Suffixe d'une seule lettre minuscule collé à un code en majuscules
    # (XAUUSDc, EURUSDm) : convention de compte « cent » ou « micro ».
    if len(base) > 4 and base[-1].islower() and base[:-1].isupper():
        base = base[:-1]
    return base.upper()
