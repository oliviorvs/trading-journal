"""Routes des comptes MANUELS (mode = "manual") : aucune connexion MT5.

Voir Recap-structure-manuel-import.md (sections 2, 4 et 5).
- « Manuel avec import » et « full manuel » créent le MÊME type de compte ;
  seule la porte d'entrée de l'assistant côté interface diffère.
- Le compte est identifié par un login synthétique négatif (voir
  state.next_manual_login) : filter_active, la suppression de compte et le
  calendrier fonctionnent donc sans modification.
- La limite de comptes (MAX_SAVED_ACCOUNTS) est partagée avec les comptes MT5.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import timezone

from database import get_db
from models import Account
from schemas import AccountOut, ManualAccountCreate
import state

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.post("/manual", response_model=AccountOut, status_code=201)
def create_manual_account(body: ManualAccountCreate, db: Session = Depends(get_db)):
    """Crée un compte manuel et l'active.

    Le capital de départ est saisi ici (il sera proposé pré-rempli par
    l'assistant d'import à partir d'un rapport, voir étape 4). Le compte est
    marqué `initialization_mode = "manual"` : le garde-fou « journal non
    initialisé » et la modale d'initialisation MT5 ne le concernent pas.
    """
    total = db.query(Account).count()
    if total >= state.MAX_SAVED_ACCOUNTS:
        raise HTTPException(
            400,
            f"Maximum {state.MAX_SAVED_ACCOUNTS} comptes enregistrés. "
            "Supprimez-en un avant d'en ajouter un nouveau.",
        )

    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Le nom du compte est requis")

    start_date = body.start_date
    if start_date is not None and start_date.tzinfo is not None:
        # Colonnes DateTime SQLite : UTC naïf partout dans le projet.
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if start_date is None:
        start_date = state.utcnow()

    account = Account(
        login=state.next_manual_login(db),
        mode=state.MODE_MANUAL,
        password=None,
        server=None,
        name=name,
        label=name,
        currency=body.currency.upper(),
        balance=body.initial_balance,
        equity=body.initial_balance,
        margin=0.0,
        free_margin=body.initial_balance,
        leverage=body.leverage,
        initial_balance=body.initial_balance,
        initial_equity=body.initial_balance,
        initialization_mode="manual",
        start_date=start_date,
        last_sync=state.utcnow(),
    )
    db.add(account)
    db.commit()

    state.set_active_account(db, account.login)
    db.refresh(account)
    return account
