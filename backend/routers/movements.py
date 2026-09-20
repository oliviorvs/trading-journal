"""Routes du journal des mouvements de capital (dépôts / retraits) — phase 6.

- GET    /api/capital-movements        liste + totaux du compte actif (tous types de compte)
- POST   /api/capital-movements        saisie d'un dépôt / retrait (compte MANUEL uniquement)
- PATCH  /api/capital-movements/{id}   modification (saisis ou importés, compte manuel)
- DELETE /api/capital-movements/{id}   suppression (idem)

Sur un compte MT5, les mouvements viennent de la synchro (deals de type
« balance », voir mt5_service.sync_trades) et sont en LECTURE SEULE : les
modifier ici ferait diverger le journal du solde réel du broker.
Voir services/movements.py pour la règle « un mouvement fait varier le solde,
jamais la performance ».
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import CapitalMovement
from schemas import MovementCreate, MovementListOut, MovementOut, MovementUpdate
from services import movements as movement_service
import state

router = APIRouter(prefix="/api/capital-movements", tags=["capital"])


def _editable(movement: CapitalMovement, account) -> bool:
    return state.is_manual(account) and movement.source in (
        movement_service.SOURCE_MANUAL, movement_service.SOURCE_IMPORT
    )


def _out(movement: CapitalMovement, account) -> MovementOut:
    amount = float(movement.amount or 0.0)
    return MovementOut(
        id=movement.id, time=movement.time, type=movement_service.kind_of(amount),
        amount=abs(amount), signed_amount=amount, comment=movement.comment,
        source=movement.source, editable=_editable(movement, account),
    )


def _manual_account_or_400(db: Session):
    account = state.get_active_account(db)
    if not account:
        raise HTTPException(400, "Aucun compte actif")
    if not state.is_manual(account):
        raise HTTPException(
            400,
            "Les dépôts et retraits d'un compte MT5 viennent de la synchronisation "
            "avec MetaTrader 5 : ils ne se saisissent pas ici.",
        )
    return account


def _finish_write(db: Session, account) -> None:
    """Contrôle de cohérence (capital jamais négatif) puis validation. Sur
    échec : annule tout et remonte l'erreur 422 au client."""
    try:
        db.flush()
        movement_service.check_no_negative_capital(db, account)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    state.sync_manual_balance(db, account)


@router.get("", response_model=MovementListOut)
def list_movements(db: Session = Depends(get_db)):
    account = state.get_active_account(db)
    if not account:
        return MovementListOut(
            items=[], count=0, total_deposits=0.0, total_withdrawals=0.0, net=0.0, can_edit=False,
        )
    rows = movement_service.account_movements(db, account.login)
    rows.sort(key=lambda m: (m.time, m.id), reverse=True)   # plus récent d'abord
    totals = movement_service.totals(db, account.login)
    return MovementListOut(
        items=[_out(m, account) for m in rows],
        count=totals["count"], total_deposits=totals["total_deposits"],
        total_withdrawals=totals["total_withdrawals"], net=totals["net"],
        can_edit=state.is_manual(account),
        balance_after=round(state.manual_capital(db, account), 2) if state.is_manual(account) else None,
    )


@router.post("", response_model=MovementOut, status_code=201)
def create_movement(body: MovementCreate, db: Session = Depends(get_db)):
    account = _manual_account_or_400(db)
    signed = body.amount if body.type == movement_service.KIND_DEPOSIT else -body.amount
    movement = CapitalMovement(
        account_id=account.login,
        ticket=None,
        time=movement_service.naive(body.time) or state.utcnow(),
        amount=movement_service.validate_amount(signed),
        comment=(body.comment or "").strip() or None,
        source=movement_service.SOURCE_MANUAL,
    )
    db.add(movement)
    _finish_write(db, account)
    db.refresh(movement)
    return _out(movement, account)


def _get_editable(db: Session, movement_id: int, account) -> CapitalMovement:
    movement = db.query(CapitalMovement).filter(
        CapitalMovement.id == movement_id, CapitalMovement.account_id == account.login
    ).first()
    if not movement:
        raise HTTPException(404, "Mouvement introuvable")
    if not _editable(movement, account):
        raise HTTPException(
            400,
            "Ce mouvement vient de la synchronisation MetaTrader 5 : il est en lecture seule.",
        )
    return movement


@router.patch("/{movement_id}", response_model=MovementOut)
def update_movement(movement_id: int, body: MovementUpdate, db: Session = Depends(get_db)):
    account = _manual_account_or_400(db)
    movement = _get_editable(db, movement_id, account)

    fields = body.model_dump(exclude_unset=True)
    if "time" in fields:
        if body.time is None:
            raise HTTPException(422, "La date ne peut pas être vide")
        movement.time = movement_service.naive(body.time)
    if "comment" in fields:
        movement.comment = (body.comment or "").strip() or None
    if "type" in fields or "amount" in fields:
        current = float(movement.amount or 0.0)
        kind = body.type if body.type else movement_service.kind_of(current)
        magnitude = body.amount if body.amount is not None else abs(current)
        signed = magnitude if kind == movement_service.KIND_DEPOSIT else -magnitude
        movement.amount = movement_service.validate_amount(signed)
    _finish_write(db, account)
    db.refresh(movement)
    return _out(movement, account)


@router.delete("/{movement_id}", status_code=204)
def delete_movement(movement_id: int, db: Session = Depends(get_db)):
    account = _manual_account_or_400(db)
    movement = _get_editable(db, movement_id, account)
    db.delete(movement)
    _finish_write(db, account)
