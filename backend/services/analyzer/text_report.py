"""Rapport en texte brut — synthèse courte et rapport complet.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

_KIND_LABEL = {"risque": "Point d'attention", "force": "Point fort", "info": "Constat"}


def _money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f} {currency}".replace(",", " ").strip()


def _num(value: Optional[float], suffix: str = "", digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _period_label(filters: dict) -> str:
    if filters.get("date_from") or filters.get("date_to"):
        start = filters.get("date_from") or "…"
        end = filters.get("date_to") or "…"
        return f"{start} → {end}"
    return "historique complet"


# ── Synthèse courte (2e exemple fourni) ─────────────────────────────────────

def build_short(bundle: dict, insights: List[dict], account_label: str,
                 currency: str) -> str:
    overview = (bundle.get("overview") or {}).get("analyzer") or {}
    journal = (bundle.get("overview") or {}).get("journal") or {}
    filters = bundle.get("filters") or {}
    quality = bundle.get("data_quality") or {}

    trades = overview.get("trades", 0)
    lines: List[str] = []
    lines.append("RÉSUMÉ DE PERFORMANCE")
    if account_label:
        lines.append(account_label)
    lines.append(f"Période : {_period_label(filters)}")
    lines.append(f"Trades : {trades}")
    lines.append(f"Résultat : {_money(overview.get('pnl'), currency)}")
    lines.append(f"Win rate : {_num(overview.get('win_rate'), ' %', 1)}")
    lines.append(f"Profit factor : {_num(overview.get('profit_factor'))}")
    lines.append(f"Expectancy : {_money(overview.get('expectancy'), currency)}/trade")
    if journal.get("max_drawdown") is not None:
        lines.append(f"Drawdown max (compte) : {_num(journal.get('max_drawdown'), ' %', 2)}")
    lines.append("")

    if not trades:
        lines.append("Aucun trade sur cette période.")
        return "\n".join(lines)

    lines.append("CE QUI SE PASSE")
    payoff = None
    if overview.get("avg_loss"):
        payoff = overview["avg_win"] / overview["avg_loss"]
    if payoff:
        lines.append(
            f"Win rate de {_num(overview.get('win_rate'), ' %', 1)}, gain moyen "
            f"{_money(overview.get('avg_win'), currency)} contre perte moyenne "
            f"{_money(-abs(overview.get('avg_loss') or 0), currency)} "
            f"(ratio {payoff:.2f}×)."
        )
    lines.append("")

    risques = [i for i in insights if i["kind"] == "risque"]
    if risques:
        lines.append("POINTS D'ATTENTION")
        for item in risques:
            lines.append(f"- {item['text']}")
        lines.append("")

    forces = [i for i in insights if i["kind"] == "force"]
    if forces:
        lines.append("POINTS FORTS")
        for item in forces:
            lines.append(f"- {item['text']}")
        lines.append("")

    autres = [i for i in insights if i["kind"] == "info"]
    if autres:
        lines.append("AUTRES CONSTATS")
        for item in autres:
            lines.append(f"- {item['title']} : {item['text']}")
        lines.append("")

    low_coverage = [c for c in quality.get("coverage", [])
                    if c["coverage"] < 50 and c["key"] in ("setup", "sop", "exit")]
    if low_coverage:
        champs = ", ".join(c["field"] for c in low_coverage)
        lines.append("DONNÉES À AMÉLIORER EN PRIORITÉ")
        lines.append(
            f"{champs} — renseignés sur moins de la moitié des trades. "
            "Compléter ces champs permettrait des analyses plus précises "
            "(setups, conformité au plan) sur les prochaines périodes."
        )
        lines.append("")

    lines.append(
        f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — "
        "observations chiffrées, pas des recommandations d'investissement."
    )
    return "\n".join(lines)


# ── Rapport complet (1er exemple fourni) ────────────────────────────────────

def build_full(bundle: dict, insights: List[dict], account_label: str,
                currency: str, views: Optional[list] = None) -> str:
    overview = (bundle.get("overview") or {}).get("analyzer") or {}
    journal = (bundle.get("overview") or {}).get("journal") or {}
    filters = bundle.get("filters") or {}
    dims = bundle.get("dimensions") or {}
    series = bundle.get("series") or {}
    errors = bundle.get("errors") or {}
    quality = bundle.get("data_quality") or {}
    patterns = bundle.get("patterns") or {}

    trades = overview.get("trades", 0)
    out: List[str] = []

    def h(title: str) -> None:
        out.append("")
        out.append(title.upper())
        out.append("-" * len(title))

    out.append("RAPPORT D'ANALYSE DU JOURNAL DE TRADING")
    if account_label:
        out.append(account_label)
    out.append(f"Période analysée : {_period_label(filters)}")
    out.append(f"Nombre de trades analysés : {trades}")
    out.append(f"Résultat net : {_money(overview.get('pnl'), currency)}")
    out.append(f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")

    if not trades:
        out.append("")
        out.append("Aucun trade sur la période sélectionnée.")
        return "\n".join(out)

    # 1. Synthèse de la performance
    h("1. Synthèse de la performance")
    out.append(f"{trades} opérations enregistrées.")
    out.append(
        f"Trades gagnants : {overview.get('wins', 0)} · "
        f"Trades perdants : {overview.get('losses', 0)}"
    )
    out.append(f"Win rate : {_num(overview.get('win_rate'), ' %', 2)}")
    out.append(f"Loss rate : {_num(overview.get('loss_rate'), ' %', 2)}")
    out.append(f"Gain moyen : {_money(overview.get('avg_win'), currency)}")
    out.append(f"Perte moyenne : {_money(-abs(overview.get('avg_loss') or 0), currency)}")
    if overview.get("avg_loss"):
        out.append(f"Ratio gain moyen / perte moyenne : {overview['avg_win'] / overview['avg_loss']:.2f}")
    out.append(f"Profit factor : {_num(overview.get('profit_factor'))}")
    out.append(f"Expectancy moyenne : {_money(overview.get('expectancy'), currency)} par trade")
    if overview.get("expectancy_r") is not None:
        out.append(
            f"Expectancy (R) : {_num(overview.get('expectancy_r'), 'R')} "
            f"— couverture {_num(overview.get('r_coverage'), ' %', 1)}"
        )

    # 2. Évolution du capital / séries
    h("2. Évolution du capital et séries")
    if journal.get("max_drawdown") is not None:
        out.append(f"Drawdown maximal (compte) : {_num(journal.get('max_drawdown'), ' %', 2)}")
    if series.get("longest_loss"):
        out.append(f"Plus longue série de pertes consécutives : {series['longest_loss']} trades")
    if series.get("longest_win"):
        out.append(f"Plus longue série de gains consécutifs : {series['longest_win']} trades")
    for episode in (series.get("drawdowns") or [])[:3]:
        statut = "récupéré" if episode["recovered"] else "en cours"
        out.append(
            f"Creux du {episode['from']} au {episode['trough']} : "
            f"{episode['depth']:+,.2f} {currency} ({statut})".replace(",", " ")
        )

    # 3. Gains et pertes extrêmes
    if views:
        base_key = overview.get("base", "gross")
        best_trade = max(views, key=lambda t: t.value(base_key))
        worst_trade = min(views, key=lambda t: t.value(base_key))
        if best_trade.value(base_key) > 0 or worst_trade.value(base_key) < 0:
            h("3. Gains et pertes extrêmes")
            if best_trade.value(base_key) > 0:
                out.append(
                    f"Plus gros gain : {_money(best_trade.value(base_key), currency)} "
                    f"sur {best_trade.symbol} le {best_trade.open_time.date().isoformat()}"
                )
            if worst_trade.value(base_key) < 0:
                out.append(
                    f"Plus grosse perte : {_money(worst_trade.value(base_key), currency)} "
                    f"sur {worst_trade.symbol} le {worst_trade.open_time.date().isoformat()}"
                )

    # 4. Sorties
    exits = dims.get("exit_reason")
    if exits and exits.get("rows"):
        h("4. Analyse des sorties")
        for row in exits["rows"]:
            out.append(
                f"{row['key']} : {row['trades']} trade(s) — {_money(row.get('pnl'), currency)}"
            )
        exit_insight = next((i for i in insights if i["key"] == "exit_contribution"), None)
        if exit_insight:
            out.append(exit_insight["text"])

    # 5. Instruments
    symbols = dims.get("symbol")
    if symbols and symbols.get("rows"):
        h("5. Analyse par instrument")
        positive = sorted((r for r in symbols["rows"] if r.get("pnl", 0) > 0),
                           key=lambda r: r["pnl"], reverse=True)
        negative = sorted((r for r in symbols["rows"] if r.get("pnl", 0) < 0),
                           key=lambda r: r["pnl"])
        if positive:
            out.append("Résultats positifs :")
            for row in positive:
                out.append(f"  {row['key']} : {_money(row['pnl'], currency)} ({row['trades']} trade(s))")
        if negative:
            out.append("Résultats négatifs :")
            for row in negative:
                out.append(f"  {row['key']} : {_money(row['pnl'], currency)} ({row['trades']} trade(s))")

    # 6. Setups
    setups = dims.get("setup")
    if setups and setups.get("rows"):
        h("6. Analyse par setup")
        for row in setups["rows"]:
            if row["key"] == "Non renseigné":
                continue
            out.append(
                f"{row['key']} : {row['trades']} trade(s) — {_money(row.get('pnl'), currency)} "
                f"— win rate {_num(row.get('win_rate'), ' %', 1)}"
            )
        not_set = next((r for r in setups["rows"] if r["key"] == "Non renseigné"), None)
        if not_set and not_set["trades"]:
            out.append(
                f"{not_set['trades']} trade(s) sans setup renseigné "
                f"({_num(not_set.get('pct_of_total'), ' %', 1)} du total)."
            )

    # 7. Coût des erreurs déclarées
    if errors.get("rows"):
        h("7. Coût des erreurs déclarées")
        for row in errors["rows"]:
            out.append(
                f"{row['key']} : {row['trades']} trade(s) — coût cumulé "
                f"{_money(row.get('cost'), currency)}"
            )
        out.append(f"Groupe de référence (sans erreur déclarée) : {errors.get('reference_trades', 0)} trades.")

    # 8. Constats automatiques (insights.py)
    if insights:
        h("8. Constats automatiques")
        for item in insights:
            out.append(f"[{_KIND_LABEL.get(item['kind'], 'Constat')}] {item['title']}")
            out.append(item["text"])
            out.append("")

    # 9. Patterns statistiques (patterns.py — croisements à 1 et 2 variables)
    strong_rows = [
        r for r in (patterns.get("rows") or [])
        if not r.get("inconclusive") and r.get("status") in ("SIGNAL INTÉRESSANT", "PATTERN ROBUSTE")
    ]
    if strong_rows:
        h("9. Patterns statistiques significatifs")
        out.append((patterns.get("multiplicity") or {}).get("text", ""))
        for row in strong_rows[:10]:
            out.append(
                f"{row['condition']} — n={row['trades']}, win rate "
                f"{_num(row.get('win_rate'), ' %', 1)}, expectancy "
                f"{_money(row.get('expectancy'), currency)} [{row.get('status')}]"
            )

    # 10. Qualité des données
    h("10. Qualité des données")
    for row in quality.get("coverage", []):
        out.append(f"{row['field']} : {row['coverage']:.1f} % renseigné ({row['filled']}/{row['filled'] + row['missing']})")
    for anomaly in quality.get("anomalies", []):
        out.append(f"Anomalie — {anomaly['label']} : {anomaly['count']} ({anomaly.get('hint', '')})")

    # 11. Conclusion
    h("11. Conclusion")
    out.append(
        f"Sur {trades} trades étudiés, le compte affiche un résultat net de "
        f"{_money(overview.get('pnl'), currency)}."
    )
    if risques := [i for i in insights if i["kind"] == "risque"]:
        out.append("Éléments à surveiller en priorité :")
        for i, item in enumerate(risques, start=1):
            out.append(f"{i}. {item['title']}")
    out.append(
        "Ce rapport ne constitue pas une recommandation d'investissement : "
        "ce sont des observations statistiques sur l'historique analysé, à "
        "vérifier sur un échantillon plus large avant d'en tirer une règle."
    )

    return "\n".join(out)
