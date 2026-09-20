"""Analyses 12 et 13 — Conformité au plan, Plan vs Réalité.

« Conformité » ≠ « bulletin de discipline ». Le bulletin existant reste tel
quel dans Répartitions : c'est une heuristique. Ici, on compare le trade à un
plan ENREGISTRÉ et VERSIONNÉ (voir sop.py). Les deux coexistent, chacun avec
son libellé — les confondre reviendrait à laisser croire qu'une note « A » de
discipline signifie « conforme au plan ».

Plan vs Réalité mesure quatre écarts précis (le cahier v1 restait vague) :
risque prévu vs risque déduit du SL, R:R prévu vs R réalisé, sortie prévue
vs sortie réelle, conformité vs résultat.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from sqlalchemy.orm import Session

from services.analyzer.adapter import Dataset, TradeView
from services.analyzer import metrics as metrics_mod
from services.analyzer import reliability
from services.analyzer import sop as sop_mod


def compliance(dataset: Dataset, db: Session, base: str = "gross",
               thresholds: Optional[dict] = None) -> dict:
    """Conformes vs hors plan, avec la version du SOP utilisée."""
    version = sop_mod.get_active(db, dataset.account_id) if dataset.account_id else None
    threshold = version.threshold if version else 1.0

    scored = [t for t in dataset.views if t.sop_score is not None]
    unscored = [t for t in dataset.views if t.sop_score is None]
    compliant = [t for t in scored if t.sop_score >= threshold]
    deviant = [t for t in scored if t.sop_score < threshold]

    rows = []
    for label, group in (("Conforme", compliant), ("Hors plan", deviant)):
        if not group:
            continue
        summary = metrics_mod.summarize(group, base, total_count=len(dataset.views))
        summary = reliability.annotate(summary, [t.r for t in group if t.r is not None], thresholds)
        summary["key"] = label
        rows.append(summary)

    # Le groupe non renseigné est compté À PART et jamais mélangé aux deux
    # autres (§9.1) : l'absence de saisie n'est pas un manquement au plan.
    if unscored:
        summary = metrics_mod.summarize(unscored, base, total_count=len(dataset.views))
        summary["key"] = "Non renseigné"
        summary["excluded_from_comparison"] = True
        rows.append(summary)

    compliant_summary = metrics_mod.summarize(compliant, base) if compliant else None
    deviant_summary = metrics_mod.summarize(deviant, base) if deviant else None

    return {
        "base": base,
        "sop_version": sop_mod.serialize(db, version) if version else None,
        "threshold": threshold,
        "scored_trades": len(scored),
        "unscored_trades": len(unscored),
        "compliance_rate": round(len(compliant) / len(scored) * 100, 1) if scored else None,
        "rows": rows,
        "delta_expectancy": (
            round(compliant_summary["expectancy"] - deviant_summary["expectancy"], 2)
            if compliant_summary and deviant_summary else None
        ),
        "by_item": _by_item(dataset, db, version, base),
    }


def _by_item(dataset: Dataset, db: Session, version, base: str) -> List[dict]:
    """Performance associée à CHAQUE élément du SOP, coché ou non.

    Réponse à « quel critère de mon plan fait réellement la différence ? » —
    et, parfois, « ce critère n'en fait aucune ».
    """
    if version is None or dataset.account_id is None:
        return []
    from services.analyzer.models import TradeSopResult

    items = sop_mod.items_of(db, version.id)
    if not items:
        return []
    rows_db = (
        db.query(TradeSopResult.ticket, TradeSopResult.item_id, TradeSopResult.checked)
        .filter(
            TradeSopResult.account_id == dataset.account_id,
            TradeSopResult.sop_version_id == version.id,
        )
        .all()
    )
    checked_by_item: dict[int, set] = {}
    known_tickets: set = set()
    for ticket, item_id, checked in rows_db:
        known_tickets.add(ticket)
        if checked:
            checked_by_item.setdefault(item_id, set()).add(ticket)

    by_ticket = {t.ticket: t for t in dataset.views}
    result = []
    for item in items:
        tickets_ok = checked_by_item.get(item.id, set())
        respected = [by_ticket[t] for t in tickets_ok if t in by_ticket]
        skipped = [by_ticket[t] for t in known_tickets - tickets_ok if t in by_ticket]
        if not respected and not skipped:
            continue
        respected_summary = metrics_mod.summarize(respected, base) if respected else None
        skipped_summary = metrics_mod.summarize(skipped, base) if skipped else None
        result.append({
            "item": item.label,
            "required": item.required,
            "respected": respected_summary,
            "skipped": skipped_summary,
            "delta_expectancy": (
                round(respected_summary["expectancy"] - skipped_summary["expectancy"], 2)
                if respected_summary and skipped_summary else None
            ),
        })
    return result


def plan_vs_reality(dataset: Dataset, db: Session, base: str = "gross") -> dict:
    """Quatre écarts mesurables entre ce qui était prévu et ce qui s'est passé."""
    views = dataset.views

    # 1. Risque prévu (saisi) vs risque déduit du SL.
    manual = [t for t in views if t.risk_source == "manuel" and t.risk_percent is not None]
    auto = [t for t in views if t.risk_source == "auto" and t.risk_percent is not None]
    risk_block = {
        "declared_trades": len(manual),
        "deduced_trades": len(auto),
        "avg_declared": round(sum(t.risk_percent for t in manual) / len(manual), 2) if manual else None,
        "avg_deduced": round(sum(t.risk_percent for t in auto) / len(auto), 2) if auto else None,
        "max_risk": round(max((t.risk_percent for t in views if t.risk_percent is not None), default=0), 2),
        # Un risque saisi systématiquement inférieur au risque déduit du SL
        # signale soit un sizing plus agressif que prévu, soit un stop mental.
        "over_risk_trades": sum(
            1 for t in views if t.risk_percent is not None and t.risk_percent > 2
        ),
    }

    # 2. R:R prévu (TP / SL initial) vs R réalisé — disponible seulement
    # quand le trade porte les deux niveaux.
    rr_rows = _planned_vs_realised_r(views)

    # 3. Sortie prévue vs sortie réelle.
    exit_rows = metrics_mod.group_by(views, lambda t: t.exit_reason, base, total_count=len(views))

    # 4. Conformité vs résultat : déjà couverte par compliance(), résumée ici.
    version = sop_mod.get_active(db, dataset.account_id) if dataset.account_id else None
    threshold = version.threshold if version else 1.0
    scored = [t for t in views if t.sop_score is not None]

    return {
        "base": base,
        "risk": risk_block,
        "rr": rr_rows,
        "exits": exit_rows,
        "sop": {
            "scored_trades": len(scored),
            "compliance_rate": (
                round(sum(1 for t in scored if t.sop_score >= threshold) / len(scored) * 100, 1)
                if scored else None
            ),
        },
    }


def _planned_vs_realised_r(views: Sequence[TradeView]) -> dict:
    """Le R:R « prévu » n'est calculable que depuis les prix, que l'adaptateur
    n'expose pas (TradeView est volontairement dépouillé). On se limite donc
    à ce qui est disponible : la distribution du R RÉALISÉ par raison de
    sortie, qui répond à la même question de façon plus honnête."""
    realised = [t.r for t in views if t.r is not None]
    if not realised:
        return {"available": False}
    positive = [r for r in realised if r > 0]
    negative = [r for r in realised if r < 0]
    return {
        "available": True,
        "avg_r": round(sum(realised) / len(realised), 2),
        "avg_win_r": round(sum(positive) / len(positive), 2) if positive else None,
        "avg_loss_r": round(sum(negative) / len(negative), 2) if negative else None,
        # Un R moyen perdant nettement inférieur à −1 signale des stops
        # dépassés (slippage, stop déplacé, sortie tardive).
        "worse_than_1r": sum(1 for r in negative if r < -1.05),
        "coverage": round(len(realised) / len(views) * 100, 1),
    }
