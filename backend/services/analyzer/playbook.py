"""« Règles proposées » — ex-« playbook généré » du cahier v1.

À NE PAS CONFONDRE avec la colonne existante `Trade.playbook`, qui est le nom
de la stratégie en texte libre. Dans l'interface, le mot employé est
« Règles ». Ce module ne touche jamais à cette colonne.

Principe (règle R8) : une règle est PROPOSÉE, jamais appliquée. Chaque
proposition porte son libellé de prudence et sa PREUVE figée (n, intervalle,
période, filtres) — de sorte qu'une règle acceptée reste lisible et
contestable des mois plus tard, même si les trades ont changé depuis.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from services.analyzer import reliability
from services.analyzer.models import AnalyzerPlaybookRule

PRUDENCE = "Conclusion statistique à vérifier"

# On ne propose une règle que sur des statuts qui le méritent : sous SIGNAL
# INTÉRESSANT, l'échantillon ne permet pas d'en tirer quoi que ce soit.
ELIGIBLE_STATUSES = {reliability.INTERESTING, reliability.ROBUST}


def suggest(pattern_result: dict, error_result: dict, frequency_result: dict,
            filters: dict, limit: int = 12) -> List[dict]:
    """Construit des propositions à partir des analyses déjà calculées.

    Aucune écriture en base ici : la génération est gratuite et rejouable,
    seule la DÉCISION de l'utilisateur est persistée (règle R6).
    """
    suggestions: List[dict] = []
    baseline = pattern_result.get("baseline") or {}

    # 1. Patterns favorables et défavorables.
    for row in pattern_result.get("rows", []):
        if row.get("status") not in ELIGIBLE_STATUSES or row.get("inconclusive"):
            continue
        delta = row.get("delta_expectancy") or 0
        if abs(delta) < 0.01:
            continue
        favourable = delta > 0
        verb = "Privilégier" if favourable else "Éviter ou réduire"
        suggestions.append(_rule(
            rule_type="pattern",
            text=(
                f"{verb} : {row['condition']} — espérance {row['expectancy']:+.2f} "
                f"contre {baseline.get('expectancy', 0):+.2f} en moyenne "
                f"({row['trades']} trades)."
            ),
            evidence={
                "condition": row["condition"],
                "n": row["trades"],
                "win_rate": row["win_rate"],
                "win_rate_ci": row.get("win_rate_ci"),
                "expectancy": row["expectancy"],
                "expectancy_r": row.get("expectancy_r"),
                "avg_r_ci": row.get("avg_r_ci"),
                "status": row.get("status"),
                "baseline_expectancy": baseline.get("expectancy"),
                "filters": filters,
            },
            score=abs(delta) * (1 if favourable else 1.2),
        ))

    # 2. Erreurs les plus coûteuses.
    for row in (error_result.get("rows") or [])[:3]:
        if row.get("cost", 0) >= 0 or row["trades"] < 3:
            continue
        suggestions.append(_rule(
            rule_type="error_cost",
            text=(
                f"Travailler l'erreur « {row['key']} » : {row['trades']} trades, "
                f"coût cumulé {row['cost']:+.2f}"
                + (f", soit {row['delta_expectancy']:+.2f} par trade contre les trades sans erreur"
                   if row.get("delta_expectancy") is not None else "")
                + "."
            ),
            evidence={
                "error": row["key"],
                "n": row["trades"],
                "cost": row["cost"],
                "cost_r": row.get("cost_r"),
                "delta_expectancy": row.get("delta_expectancy"),
                "reference_trades": error_result.get("reference_trades"),
                "status": row.get("status"),
                "filters": filters,
            },
            score=abs(row["cost"]) / 100,
        ))

    # 3. Fréquence : une tranche nettement moins bonne que les autres.
    rows = [r for r in (frequency_result.get("rows") or []) if r["trades"] >= 10]
    if len(rows) >= 2:
        worst = min(rows, key=lambda r: r["expectancy"])
        best = max(rows, key=lambda r: r["expectancy"])
        if worst["key"] != best["key"] and worst["expectancy"] < best["expectancy"]:
            suggestions.append(_rule(
                rule_type="frequency",
                text=(
                    f"Limiter les journées à « {worst['key']} » : espérance "
                    f"{worst['expectancy']:+.2f} contre {best['expectancy']:+.2f} "
                    f"les jours à « {best['key']} »."
                ),
                evidence={
                    "worst_bucket": worst["key"], "worst_n": worst["trades"],
                    "worst_expectancy": worst["expectancy"],
                    "best_bucket": best["key"], "best_n": best["trades"],
                    "best_expectancy": best["expectancy"],
                    "filters": filters,
                },
                score=abs(best["expectancy"] - worst["expectancy"]) / 10,
            ))

    suggestions.sort(key=lambda s: s["score"], reverse=True)
    return suggestions[:limit]


def _rule(rule_type: str, text: str, evidence: dict, score: float) -> dict:
    return {
        "rule_type": rule_type,
        "text": text,
        "caveat": PRUDENCE,
        "evidence": evidence,
        "score": score,
    }


def persist(db: Session, account_id: int, suggestions: List[dict],
            run_id: Optional[int] = None) -> List[AnalyzerPlaybookRule]:
    """Enregistre les propositions non encore présentes (anti-doublon sur le
    texte). Les règles déjà décidées ne sont jamais ré-proposées."""
    existing = {
        rule.text for rule in
        db.query(AnalyzerPlaybookRule).filter(AnalyzerPlaybookRule.account_id == account_id).all()
    }
    created = []
    for suggestion in suggestions:
        if suggestion["text"] in existing:
            continue
        rule = AnalyzerPlaybookRule(
            account_id=account_id,
            run_id=run_id,
            rule_type=suggestion["rule_type"],
            text=suggestion["text"],
            evidence=json.dumps(suggestion["evidence"], ensure_ascii=False, default=str),
            status="proposed",
        )
        db.add(rule)
        created.append(rule)
    db.flush()
    return created


def decide(db: Session, rule: AnalyzerPlaybookRule, status: str) -> AnalyzerPlaybookRule:
    if status not in ("accepted", "rejected", "proposed"):
        raise ValueError("Statut inconnu.")
    rule.status = status
    rule.decided_at = datetime.now() if status != "proposed" else None
    db.flush()
    return rule


def serialize(rule: AnalyzerPlaybookRule) -> dict:
    try:
        evidence = json.loads(rule.evidence) if rule.evidence else None
    except ValueError:
        evidence = None
    return {
        "id": rule.id,
        "type": rule.rule_type,
        "text": rule.text,
        "caveat": PRUDENCE,
        "evidence": evidence,
        "status": rule.status,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "decided_at": rule.decided_at.isoformat() if rule.decided_at else None,
    }
