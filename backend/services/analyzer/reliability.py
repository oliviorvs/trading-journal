"""Fiabilité statistique : intervalles de confiance et statuts.

Raison d'être (règle R8) : le journal affiche aujourd'hui « 67 % de réussite »
sans dire si c'est sur 3 trades ou sur 300. Ce module rend cette différence
visible, parce que c'est elle qui décide si un chiffre mérite qu'on change
son plan de trading.

Aucune dépendance ajoutée (décision D2) : `math` suffit.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

Z_95 = 1.96

INSUFFICIENT = "INSUFFISANT"
WATCH = "À SURVEILLER"
INTERESTING = "SIGNAL INTÉRESSANT"
ROBUST = "PATTERN ROBUSTE"

# Ordre croissant de solidité — sert au plafonnement (D9).
STATUS_ORDER = [INSUFFICIENT, WATCH, INTERESTING, ROBUST]

# Minimum d'observations avant de calculer un intervalle sur une moyenne :
# en dessous, l'écart-type d'échantillon n'est pas exploitable (§10).
MIN_N_FOR_MEAN_CI = 10


def wilson_interval(successes: int, n: int, z: float = Z_95) -> Optional[Dict[str, float]]:
    """Intervalle de Wilson pour une proportion.

    Préféré à l'intervalle normal (p ± z√(p(1−p)/n)) parce qu'il reste dans
    [0, 1] et ne s'effondre pas quand p vaut 0 ou 1 — exactement les cas
    fréquents ici (« 5 trades, 5 gagnants »).
    """
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return {
        "point": round(p * 100, 1),
        "low": round(max(0.0, centre - half) * 100, 1),
        "high": round(min(1.0, centre + half) * 100, 1),
    }


def mean_interval(values: Sequence[float], z: float = Z_95,
                  min_n: int = MIN_N_FOR_MEAN_CI) -> Optional[Dict[str, float]]:
    """Intervalle de confiance d'une moyenne : moyenne ± z·s/√n."""
    n = len(values)
    if n < min_n:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    half = z * math.sqrt(variance) / math.sqrt(n)
    return {
        "point": round(mean, 3),
        "low": round(mean - half, 3),
        "high": round(mean + half, 3),
    }


def status_for(n: int, thresholds: Optional[dict] = None,
               r_interval: Optional[dict] = None) -> str:
    """Statut d'un groupe, avec le plafonnement de la décision D9.

    Les seuils seuls ne suffisent pas : 150 trades à +0,02R avec un
    intervalle [−0,31 ; +0,35] ne sont PAS un pattern robuste, juste un gros
    échantillon de bruit. Quand l'intervalle à 95 % de l'expectancy R contient
    0, le statut est donc plafonné à SIGNAL INTÉRESSANT.
    """
    thresholds = thresholds or {}
    insufficient = thresholds.get("insufficient", 10)
    watch = thresholds.get("watch", 30)
    interesting = thresholds.get("interesting", 100)

    if n < insufficient:
        status = INSUFFICIENT
    elif n < watch:
        status = WATCH
    elif n < interesting:
        status = INTERESTING
    else:
        status = ROBUST

    if r_interval and r_interval["low"] <= 0 <= r_interval["high"]:
        status = _cap(status, INTERESTING)
    return status


def _cap(status: str, ceiling: str) -> str:
    return status if STATUS_ORDER.index(status) <= STATUS_ORDER.index(ceiling) else ceiling


def annotate(summary: dict, r_values: Sequence[float], thresholds: Optional[dict] = None) -> dict:
    """Ajoute à un résumé de groupe son intervalle de win rate, son intervalle
    de R moyen, son statut, et le drapeau `low_sample` (affichage grisé)."""
    n = summary.get("trades", 0)
    wins = summary.get("wins", 0)
    win_ci = wilson_interval(wins, n)
    r_ci = mean_interval(list(r_values))
    thresholds = thresholds or {}
    summary = dict(summary)
    summary["win_rate_ci"] = win_ci
    summary["avg_r_ci"] = r_ci
    summary["status"] = status_for(n, thresholds, r_ci)
    summary["low_sample"] = n < thresholds.get("min_display", 3)
    # Une expectancy R dont l'intervalle contient 0 est « indiscernable du
    # hasard » — explicité pour que l'interface puisse le dire en toutes lettres
    # plutôt que de laisser l'utilisateur interpréter un badge.
    summary["inconclusive"] = bool(r_ci and r_ci["low"] <= 0 <= r_ci["high"])
    return summary


def multiplicity_note(tested: int) -> dict:
    """Avertissement de multiplicité (§10).

    À 95 %, environ 1 combinaison sur 20 paraît « significative » par pur
    hasard. Tester 60 croisements, c'est s'attendre à ~3 faux positifs : le
    dire explicitement évite de bâtir une règle dessus.
    """
    return {
        "tested": tested,
        "expected_false_positives": round(tested * 0.05, 1),
        "text": (
            f"{tested} combinaisons testées. À 95 %, environ "
            f"{round(tested * 0.05, 1)} d'entre elles paraîtront significatives "
            "par pur hasard : un résultat isolé n'est pas une règle."
        ),
    }
