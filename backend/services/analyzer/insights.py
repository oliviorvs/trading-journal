"""Analyse 15 — Constats automatiques.
"""
from __future__ import annotations

from statistics import mean, pstdev
from typing import List, Optional

from services.analyzer.adapter import Dataset, TradeView

# Multiple de la perte moyenne au-delà duquel une perte est signalée comme
# exceptionnelle. Seuil délibérément élevé (le rapport de référence citait
# une perte à ~4×) : en dessous, la dispersion normale d'un système à R
# variable produirait trop de faux signaux.
OUTLIER_LOSS_MULTIPLE = 3.0
MIN_LOSSES_FOR_OUTLIERS = 5

# Nombre d'instruments considérés pour la concentration des gains.
CONCENTRATION_TOP_N = 2
# Le constat n'est affiché que si ces `top N` instruments pèsent au moins
# cette part du résultat net — en dessous, "quelques instruments dominent"
# ne dit rien de plus que "le résultat vient de partout".
CONCENTRATION_MIN_SHARE = 60.0


def compute(dataset: Dataset, bundle: dict, base: str = "gross",
            currency: str = "") -> List[dict]:
    """Assemble les constats disponibles, dans un ordre de lecture fixe.

    Chaque constat : {key, title, text, kind, trades}. `kind` ∈
    {"risque", "force", "info"} — sert uniquement à l'affichage (couleur),
    jamais à une décision automatique.
    """
    views = dataset.views
    items: List[dict] = []
    if not views:
        return items

    overview = (bundle.get("overview") or {}).get("analyzer") or {}
    series = bundle.get("series") or {}
    exits = (bundle.get("dimensions") or {}).get("exit_reason") or {}
    symbols = (bundle.get("dimensions") or {}).get("symbol") or {}

    ratio = _win_loss_ratio(overview)
    if ratio:
        items.append(ratio)

    outliers = _outlier_losses(views, base, currency)
    if outliers:
        items.append(outliers)

    streak = _losing_streak(series, len(views))
    if streak:
        items.append(streak)

    drawdown = _worst_drawdown(series, currency)
    if drawdown:
        items.append(drawdown)

    exit_insight = _exit_contribution(exits, currency)
    if exit_insight:
        items.append(exit_insight)

    concentration = _concentration(symbols, overview, currency)
    if concentration:
        items.append(concentration)

    revenge = _revenge_trading(bundle.get("overtrading"), currency)
    if revenge:
        items.append(revenge)

    return items


# ── 1. Ratio gain/perte plutôt que taux de réussite ─────────────────────────

def _win_loss_ratio(overview: dict) -> Optional[dict]:
    win_rate = overview.get("win_rate")
    avg_win = overview.get("avg_win")
    avg_loss = overview.get("avg_loss")
    trades = overview.get("trades", 0)
    if not trades or win_rate is None or not avg_loss:
        return None
    payoff = round(avg_win / avg_loss, 2) if avg_loss else None
    if payoff is None:
        return None
    # Le constat n'est intéressant que dans le cas asymétrique : un win rate
    # sous 50 % compensé par un ratio gain/perte élevé, ou l'inverse (win
    # rate élevé mais gains à peine supérieurs aux pertes).
    if win_rate < 50 and payoff >= 1.5:
        text = (
            f"Le win rate est de {win_rate:.1f} % — moins d'un trade sur deux "
            f"est gagnant — mais le gain moyen ({avg_win:,.2f}) vaut {payoff:.2f}× "
            f"la perte moyenne ({avg_loss:,.2f}). Le résultat repose donc sur ce "
            "ratio, pas sur la fréquence des trades gagnants."
        ).replace(",", " ")
        kind = "force" if overview.get("pnl", 0) >= 0 else "risque"
    elif win_rate >= 60 and payoff < 1:
        text = (
            f"Le win rate est élevé ({win_rate:.1f} %), mais le gain moyen "
            f"({avg_win:,.2f}) est INFÉRIEUR à la perte moyenne ({avg_loss:,.2f}, "
            f"ratio {payoff:.2f}×). Un système peut gagner souvent et rester "
            "fragile si chaque perte coûte plus qu'un gain."
        ).replace(",", " ")
        kind = "risque"
    else:
        return None
    return {
        "key": "win_loss_ratio", "kind": kind,
        "title": "Ratio gain/perte plutôt que taux de réussite",
        "text": text, "trades": trades,
    }


# ── 2. Pertes exceptionnelles ────────────────────────────────────────────────

def _outlier_losses(views, base: str, currency: str) -> Optional[dict]:
    losses = [(-t.value(base), t) for t in views if t.value(base) < 0]
    if len(losses) < MIN_LOSSES_FOR_OUTLIERS:
        return None
    magnitudes = [m for m, _ in losses]
    avg_loss = mean(magnitudes)
    if avg_loss <= 0:
        return None
    outliers = sorted(
        ((m, t) for m, t in losses if m >= avg_loss * OUTLIER_LOSS_MULTIPLE),
        key=lambda pair: pair[0], reverse=True,
    )
    if not outliers:
        return None
    lines = [
        f"{t.symbol} du {t.open_time.date().isoformat()} : "
        f"-{m:,.2f} {currency} ({m / avg_loss:.1f}× la perte moyenne)".replace(",", " ")
        for m, t in outliers[:5]
    ]
    plural = "s" if len(outliers) > 1 else ""
    text = (
        f"{len(outliers)} perte{plural} dépasse{'nt' if len(outliers) > 1 else ''} "
        f"{OUTLIER_LOSS_MULTIPLE:.0f}× la perte moyenne ({avg_loss:,.2f} {currency}) : "
        .replace(",", " ") + "; ".join(lines) + ". "
        "À vérifier au cas par cas : stop déplacé, absence de stop, ou risque "
        "initial simplement plus élevé sur ces trades."
    )
    return {
        "key": "outlier_losses", "kind": "risque",
        "title": "Pertes nettement supérieures à la moyenne",
        "text": text, "trades": len(outliers),
        "detail": [
            {"symbol": t.symbol, "date": t.open_time.date().isoformat(),
             "amount": round(-m, 2), "multiple": round(m / avg_loss, 2)}
            for m, t in outliers
        ],
    }


# ── 3. Série de pertes consécutives ─────────────────────────────────────────

def _losing_streak(series: dict, total_trades: int) -> Optional[dict]:
    longest = series.get("longest_loss") or 0
    if longest < 5 or not total_trades:
        return None
    share = round(longest / total_trades * 100, 1)
    text = (
        f"La plus longue série de pertes consécutives compte {longest} trades, "
        f"soit {share:.1f} % de l'historique. Une série de cette longueur peut "
        "survenir même dans un système globalement rentable (voir le ratio "
        "gain/perte ci-dessus) — mais elle teste directement la capacité à "
        "continuer d'exécuter le plan sans réduire ou couper la taille."
    )
    return {
        "key": "losing_streak", "kind": "info",
        "title": "Série de pertes consécutives",
        "text": text, "trades": longest,
    }


# ── 4. Pire épisode de baisse (du groupe, voir behaviour._drawdown_episodes)

def _worst_drawdown(series: dict, currency: str) -> Optional[dict]:
    episodes = series.get("drawdowns") or []
    if not episodes:
        return None
    worst = min(episodes, key=lambda e: e["depth"])
    if worst["depth"] >= 0:
        return None
    if worst["recovered"]:
        recovery = (
            f"récupéré en {worst['trades_to_recover']} trades"
            + (f" ({worst['days_to_recover']} j)" if worst.get("days_to_recover") is not None else "")
        )
    else:
        recovery = "toujours EN COURS à la fin de la période analysée"
    text = (
        f"Le creux le plus marqué de la séquence atteint {worst['depth']:,.2f} {currency} "
        .replace(",", " ") +
        f"(depuis le sommet du {worst['from']}, creux le {worst['trough']}), {recovery}."
    )
    return {
        "key": "worst_drawdown",
        "kind": "risque" if not worst["recovered"] else "info",
        "title": "Creux le plus marqué",
        "text": text, "trades": worst.get("trades_in_drawdown"),
    }


# ── 5. Contribution des sorties (SL / TP / manuel…) ─────────────────────────

def _exit_contribution(exits: dict, currency: str) -> Optional[dict]:
    rows = exits.get("rows") or []
    if len(rows) < 2:
        return None
    total_pnl = sum(r.get("pnl", 0) for r in rows)
    parts = [
        f"{r['key']} : {r['trades']} trade(s), {r['pnl']:+,.2f} {currency}".replace(",", " ")
        for r in rows
    ]
    # La part la plus instructive : celle dont le résultat cumulé, en valeur
    # absolue, pèse le plus — qu'elle soit positive (le rapport de référence
    # citait les sorties manuelles) ou négative (le SL, le plus souvent).
    dominant = max(rows, key=lambda r: abs(r.get("pnl", 0)))
    share = round(abs(dominant["pnl"]) / abs(total_pnl) * 100, 1) if total_pnl else None
    text = "Répartition par type de sortie — " + "; ".join(parts) + "."
    if share is not None and dominant["trades"] > 0:
        text += (
            f" « {dominant['key']} » représente à lui seul "
            f"{share:.1f} % du résultat total en valeur absolue, sur "
            f"{dominant['trades']} trade(s)."
        )
    return {
        "key": "exit_contribution", "kind": "info",
        "title": "Contribution par type de sortie",
        "text": text, "trades": sum(r["trades"] for r in rows),
    }


# ── 6. Concentration du résultat sur quelques instruments ──────────────────

def _concentration(symbols: dict, overview: dict, currency: str) -> Optional[dict]:
    rows = symbols.get("rows") or []
    net_pnl = overview.get("pnl")
    if not rows or not net_pnl:
        return None
    positive = sorted((r for r in rows if r.get("pnl", 0) > 0),
                       key=lambda r: r["pnl"], reverse=True)
    if len(positive) < CONCENTRATION_TOP_N:
        return None
    top = positive[:CONCENTRATION_TOP_N]
    top_sum = sum(r["pnl"] for r in top)
    share = round(top_sum / net_pnl * 100, 1) if net_pnl else None
    if share is None or share < CONCENTRATION_MIN_SHARE:
        return None
    names = " et ".join(r["key"] for r in top)
    text = (
        f"{names} génèrent ensemble {top_sum:+,.2f} {currency}, soit {share:.0f} % "
        .replace(",", " ") + f"du résultat net ({net_pnl:+,.2f} {currency}). "
        .replace(",", " ") +
        ("Les autres opérations ont, dans l'ensemble, réduit une partie de ce "
         "gain plutôt qu'ajouté au résultat. " if share > 100 else "") +
        "À surveiller sur un échantillon plus large : ceci peut refléter un "
        "avantage réel sur ces instruments ou l'effet de quelques trades isolés."
    )
    return {
        "key": "concentration", "kind": "info",
        "title": "Concentration du résultat",
        "text": text, "trades": sum(r["trades"] for r in top),
    }


# ── 7. Revenge trading (réutilise behaviour.overtrading, déjà calculé) ─────

def _revenge_trading(overtrading: Optional[dict], currency: str) -> Optional[dict]:
    if not overtrading:
        return None
    delta = overtrading.get("after_loss_delta")
    after = overtrading.get("after_loss")
    if delta is None or not after or after.get("trades", 0) < 10:
        return None
    if delta >= 0:
        return None
    text = (
        f"Sur {after['trades']} trades pris juste après une perte, l'espérance "
        f"moyenne est {delta:+,.2f} {currency} inférieure à celle des autres trades. "
        .replace(",", " ") +
        "Un échantillon de cette taille commence à être lisible — sans y voir une "
        "règle, c'est un point à observer sur les prochaines périodes."
    )
    return {
        "key": "revenge_trading", "kind": "risque",
        "title": "Trade suivant une perte",
        "text": text, "trades": after["trades"],
    }
