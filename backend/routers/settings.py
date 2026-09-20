"""Routes Réglages et Compte. Extrait de main.py."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json

from database import get_db
from models import Trade
from schemas import SettingsOut, SettingsUpdate, AccountOut
import state

router = APIRouter(tags=["settings"])


def _reject_values_in_use(db: Session, column, removed: set, kind_label: str) -> None:
    """Empêche de retirer une valeur (setup, émotion, tag erreur) encore
    posée sur au moins un trade — dans TOUS les comptes, ces listes sont un
    réglage global de l'application, pas par compte.

    Avant cette vérification, supprimer une valeur depuis « Gérer les
    listes » se contentait de la retirer du menu déroulant : les trades qui
    la portaient déjà gardaient une étiquette devenue invisible partout
    ailleurs (plus de libellé dans le tableau, dans les répartitions, dans
    l'export PDF) — une perte d'information silencieuse, sans confirmation
    ni moyen de revenir en arrière.
    """
    if not removed:
        return
    rows = (
        db.query(column, Trade.ticket)
        .filter(column.in_(removed))
        .all()
    )
    counts: dict[str, int] = {}
    for value, _ticket in rows:
        counts[value] = counts.get(value, 0) + 1
    # Lot 3 de l'Analyzer : une émotion ou une erreur peut désormais vivre
    # AUSSI dans une table de liaison (plusieurs valeurs par trade). Sans
    # cette extension, la garde laisserait supprimer une valeur encore
    # utilisée — la perte d'information silencieuse qu'elle existe justement
    # pour empêcher. La double écriture recopie la PREMIÈRE valeur dans la
    # colonne historique : les suivantes n'y figurent pas.
    for value, count in _analyzer_link_counts(db, column, removed).items():
        counts[value] = max(counts.get(value, 0), count)
    if not counts:
        return
    detail = " · ".join(
        f"« {value} » utilisé par {count} trade(s)" for value, count in sorted(counts.items())
    )
    raise HTTPException(
        409,
        f"Impossible de supprimer {kind_label} encore référencé sur des trades : {detail}. "
        "Retaguez ou supprimez ces trades avant de retirer cette valeur.",
    )


def _analyzer_link_model(column):
    """Table de liaison correspondant à une colonne historique, ou None.

    Import local et tolérant : si le module Analyzer est retiré, Réglages
    continue de fonctionner exactement comme avant.
    """
    try:
        from services.analyzer.models import TradeEmotion, TradeError
    except ImportError:  # pragma: no cover — module Analyzer absent
        return None, None
    if column is Trade.emotion:
        return TradeEmotion, TradeEmotion.emotion_key
    if column is Trade.error_tag:
        return TradeError, TradeError.error_key
    # `setup_tag` n'a pas de table de liaison : un trade n'a qu'un seul setup.
    return None, None


def _analyzer_link_counts(db: Session, column, values=None) -> dict:
    """Nombre de trades portant chaque valeur dans la table de liaison.

    `values = None` → toutes les valeurs (compteurs d'usage) ; un ensemble →
    seulement celles-là (vérification de suppression). Un ensemble VIDE n'a
    rien à compter, contrairement à None : les deux cas sont distingués.
    """
    model, key_column = _analyzer_link_model(column)
    if model is None:
        return {}
    query = db.query(key_column).filter(key_column.isnot(None))
    if values is not None:
        if not values:
            return {}
        query = query.filter(key_column.in_(values))
    rows = query.all()
    counts: dict[str, int] = {}
    for (value,) in rows:
        counts[value] = counts.get(value, 0) + 1
    return counts


@router.get("/api/settings/tag-usage")
def tag_usage(db: Session = Depends(get_db)):
    """Nombre de trades référençant chaque valeur de setup / émotion / tag
    erreur, TOUS COMPTES confondus (même périmètre que la vérification de
    suppression ci-dessus). Sert à désactiver le bouton « Suppr. » avant même
    le clic — plutôt que de laisser l'utilisateur cliquer, attendre la
    requête, et découvrir le refus après coup.
    """
    def counts_for(column):
        rows = db.query(column, Trade.ticket).filter(column.isnot(None)).all()
        result: dict[str, int] = {}
        for value, _ticket in rows:
            if value:
                result[value] = result.get(value, 0) + 1
        # Même raison que dans `_reject_values_in_use` : le bouton « Suppr. »
        # doit être désactivé AVANT le clic, y compris pour une valeur qui
        # n'existe plus que dans une table de liaison. `max` plutôt qu'une
        # somme : la double écriture fait qu'un même trade compte des deux
        # côtés — les additionner doublerait l'usage affiché.
        for value, count in _analyzer_link_counts(db, column, None).items():
            result[value] = max(result.get(value, 0), count)
        return result

    return {
        "setup_types": counts_for(Trade.setup_tag),
        "emotion_types": counts_for(Trade.emotion),
        "error_types": counts_for(Trade.error_tag),
    }


@router.get("/api/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    return state.settings_payload(state.get_settings(db), db)


@router.put("/api/settings", response_model=SettingsOut)
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)):

    settings = state.get_settings(db)
    if body.setup_types is not None:
        current = set(json.loads(settings.setup_types or "[]"))
        cleaned = []
        for setup_type in body.setup_types:
            value = setup_type.strip().lower()
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise HTTPException(422, "Au moins un type de setup est requis")
        _reject_values_in_use(db, Trade.setup_tag, current - set(cleaned), "un type de setup")
        settings.setup_types = json.dumps(cleaned)
    if body.emotion_types is not None:
        current = set(json.loads(settings.emotion_types or "[]"))
        cleaned = []
        for emotion_type in body.emotion_types:
            value = emotion_type.strip().lower()
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise HTTPException(422, "Au moins une émotion est requise")
        _reject_values_in_use(db, Trade.emotion, current - set(cleaned), "une émotion")
        settings.emotion_types = json.dumps(cleaned)
    if body.error_types is not None:
        current = set(json.loads(settings.error_types or "[]"))
        cleaned = []
        for error_type in body.error_types:
            value = error_type.strip().lower()
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise HTTPException(422, "Au moins un tag erreur est requis")
        _reject_values_in_use(db, Trade.error_tag, current - set(cleaned), "un tag erreur")
        settings.error_types = json.dumps(cleaned)
    db.commit()
    db.refresh(settings)
    return state.settings_payload(settings, db)


@router.get("/api/account", response_model=AccountOut)
def get_account(db: Session = Depends(get_db)):
    acc = state.get_active_account(db)
    if not acc:
        raise HTTPException(404, "Aucun compte actif")
    state.sync_manual_balance(db, acc)  # no-op pour un compte MT5
    return acc
