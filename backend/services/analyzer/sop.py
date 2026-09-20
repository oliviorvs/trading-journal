"""SOP versionné — checklist de plan de trading (décision D5).

Distinction importante avec l'existant : le journal a déjà un « bulletin de
discipline » (`stats.discipline_bulletin`), qui est une HEURISTIQUE — SL
renseigné, risque maximal, concentration horaire. Il ne mesure pas si le
trade respectait le plan, pour la bonne raison qu'aucun plan n'est enregistré.

Ce module enregistre ce plan, et le VERSIONNE : modifier la checklist crée
une nouvelle version et n'altère jamais les résultats déjà saisis. Sans cela,
ajouter un 7ᵉ critère rendrait rétroactivement « non conformes » tous les
trades passés — et l'historique de conformité deviendrait ininterprétable.

Aucune règle n'est codée en dur : les 6 éléments ci-dessous ne sont qu'un
contenu de départ, modifiable et supprimable.
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy.orm import Session

from services.analyzer.models import AnalyzerSopItem, AnalyzerSopVersion

DEFAULT_ITEMS = [
    "Structure claire",
    "Zone valide",
    "Liquidité / sweep",
    "Retest",
    "Confirmation",
    "Setup autorisé",
]


def get_active(db: Session, account_id: int) -> Optional[AnalyzerSopVersion]:
    return (
        db.query(AnalyzerSopVersion)
        .filter(AnalyzerSopVersion.account_id == account_id, AnalyzerSopVersion.active.is_(True))
        .order_by(AnalyzerSopVersion.id.desc())
        .first()
    )


def items_of(db: Session, version_id: int) -> List[AnalyzerSopItem]:
    return (
        db.query(AnalyzerSopItem)
        .filter(AnalyzerSopItem.sop_version_id == version_id)
        .order_by(AnalyzerSopItem.position, AnalyzerSopItem.id)
        .all()
    )


def ensure_default(db: Session, account_id: int) -> AnalyzerSopVersion:
    """Crée la version de départ si le compte n'en a aucune. Ne commit pas."""
    existing = get_active(db, account_id)
    if existing:
        return existing
    version = AnalyzerSopVersion(account_id=account_id, name="SOP v1", threshold=1.0, active=True)
    db.add(version)
    db.flush()
    for position, label in enumerate(DEFAULT_ITEMS):
        db.add(AnalyzerSopItem(
            sop_version_id=version.id, label=label, position=position, required=True,
        ))
    db.flush()
    return version


def create_version(db: Session, account_id: int, name: str, labels: List[dict],
                   threshold: float = 1.0) -> AnalyzerSopVersion:
    """Nouvelle version : désactive la précédente, n'en modifie aucune.

    `labels` : [{"label": str, "required": bool}].
    """
    if not labels:
        raise ValueError("Un SOP doit contenir au moins un élément.")
    if not 0 < threshold <= 1:
        raise ValueError("Le seuil de validation doit être compris entre 0 et 1.")
    if not any(item.get("required", True) for item in labels):
        raise ValueError("Au moins un élément du SOP doit être requis.")

    (db.query(AnalyzerSopVersion)
       .filter(AnalyzerSopVersion.account_id == account_id)
       .update({AnalyzerSopVersion.active: False}))

    version = AnalyzerSopVersion(
        account_id=account_id, name=name or "SOP", threshold=threshold, active=True,
    )
    db.add(version)
    db.flush()
    for position, item in enumerate(labels):
        label = (item.get("label") or "").strip()
        if not label:
            raise ValueError("Un élément de SOP ne peut pas être vide.")
        db.add(AnalyzerSopItem(
            sop_version_id=version.id,
            label=label,
            position=position,
            required=bool(item.get("required", True)),
        ))
    db.flush()
    return version


def serialize(db: Session, version: Optional[AnalyzerSopVersion]) -> Optional[dict]:
    if version is None:
        return None
    return {
        "id": version.id,
        "name": version.name,
        "threshold": version.threshold,
        "active": version.active,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "items": [
            {"id": item.id, "label": item.label, "position": item.position, "required": item.required}
            for item in items_of(db, version.id)
        ],
    }


def all_versions(db: Session, account_id: int) -> List[dict]:
    versions = (
        db.query(AnalyzerSopVersion)
        .filter(AnalyzerSopVersion.account_id == account_id)
        .order_by(AnalyzerSopVersion.id.desc())
        .all()
    )
    return [serialize(db, version) for version in versions]
