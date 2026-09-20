"""Mouvements de capital (dépôts / retraits) — phase 6.

Décision de conception (récapitulatif, section 11) : dépôts et retraits sont
pris en compte sur TOUS les comptes, MT5 synchronisés compris.

Règle d'or : un mouvement de capital fait varier le SOLDE, jamais la
PERFORMANCE. Il entre donc :
- dans le capital courant (`state.manual_capital`) et dans le capital « avant
  trade » (`stats.build_capital_curve`), donc dans le risque en % et le
  R-multiple ;
- dans la courbe de capital (`/api/performance/equity-curve`) ;
- dans le drawdown, mais NEUTRALISÉ : un retrait ne crée pas de drawdown, un
  dépôt ne le masque pas (le sommet est décalé du même montant, voir
  `stats.max_drawdown`).
Il n'entre JAMAIS dans les P&L, win rate, calendrier ou répartitions.

Ce module ne contient que des fonctions utilitaires (aucune route).
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Account, CapitalMovement, Trade

KIND_DEPOSIT = "deposit"
KIND_WITHDRAWAL = "withdrawal"

SOURCE_MT5 = "mt5"
SOURCE_MANUAL = "manual"
SOURCE_IMPORT = "import"

MAX_AMOUNT = 1e12


def kind_of(amount: float) -> str:
    return KIND_DEPOSIT if amount > 0 else KIND_WITHDRAWAL


def naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Colonnes DateTime SQLite : UTC naïf partout (voir manual_trades.naive)."""
    if dt is not None and dt.tzinfo is not None:
        from datetime import timezone
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def validate_amount(amount) -> float:
    """Montant signé valide : fini, non nul, borné. Lève 422 sinon."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(422, "Montant illisible")
    if not math.isfinite(value):
        raise HTTPException(422, "Montant invalide")
    if value == 0:
        raise HTTPException(422, "Le montant doit être différent de zéro")
    if abs(value) > MAX_AMOUNT:
        raise HTTPException(422, "Montant trop grand")
    return round(value, 2)


def deterministic_ticket(time: Optional[datetime], amount: float, comment: Optional[str] = None) -> int:
    """Ticket négatif STABLE pour un mouvement importé sans numéro
    d'opération : hash de l'heure, du montant et du commentaire (réimporter
    le même fichier ne doit rien recréer). Deux mouvements strictement
    identiques à la seconde près (même heure, montant et commentaire) sont
    indiscernables — cas jugé négligeable."""
    key = f"{time}|{round(float(amount), 2)}|{(comment or '').strip().lower()}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return -(int(digest[:15], 16) % 900_000_000 + 100_000_000)


# ── Lecture ──────────────────────────────────────────────────────────────

def account_movements(db: Session, login: Optional[int]) -> list[CapitalMovement]:
    """Mouvements d'un compte, du plus ancien au plus récent."""
    if login is None:
        return []
    return (
        db.query(CapitalMovement)
        .filter(CapitalMovement.account_id == login)
        .order_by(CapitalMovement.time, CapitalMovement.id)
        .all()
    )


def movement_events(db: Session, login: Optional[int]) -> list[tuple[datetime, float]]:
    """[(heure, montant signé)] triés par heure — forme attendue par
    `stats.max_drawdown` et `build_capital_curve`."""
    return [(m.time, float(m.amount or 0.0)) for m in account_movements(db, login) if m.time is not None]


def movements_sum(db: Session, login: Optional[int]) -> float:
    if login is None:
        return 0.0
    total = db.query(func.coalesce(func.sum(CapitalMovement.amount), 0.0)).filter(
        CapitalMovement.account_id == login
    ).scalar()
    return float(total or 0.0)


def totals(db: Session, login: Optional[int]) -> dict:
    deposits = withdrawals = 0.0
    count = 0
    for m in account_movements(db, login):
        count += 1
        if (m.amount or 0.0) > 0:
            deposits += m.amount
        else:
            withdrawals += m.amount
    return {
        "count": count,
        "total_deposits": round(deposits, 2),
        "total_withdrawals": round(-withdrawals, 2),   # positif : montant retiré
        "net": round(deposits + withdrawals, 2),
    }


# ── Garde-fou : le capital d'un compte manuel ne devient jamais négatif ──

def check_no_negative_capital(db: Session, account: Account) -> None:
    """Rejoue la chronologie du compte manuel (capital de départ, P&L net des
    trades CLÔTURÉS à leur heure de clôture, mouvements à leur heure) et lève
    422 si le capital passe sous zéro à un moment. À appeler APRÈS l'écriture
    (flush, avant commit) d'un retrait ou d'une modification : un retrait
    supérieur au capital disponible à sa date est une saisie erronée.

    À heure identique, les montants positifs passent avant les négatifs.
    Un centime de tolérance absorbe les arrondis flottants.
    """
    events: list[tuple[datetime, int, float]] = []
    for t in db.query(Trade).filter(Trade.account_id == account.login, Trade.is_open.is_(False)).all():
        when = t.close_time or t.open_time
        if when is None:
            continue
        net = (t.profit or 0.0) + (t.commission or 0.0) + (t.swap or 0.0)
        events.append((when, 0 if net >= 0 else 1, net))
    for m in account_movements(db, account.login):
        if m.time is None:
            continue
        events.append((m.time, 0 if (m.amount or 0.0) >= 0 else 1, float(m.amount or 0.0)))
    events.sort(key=lambda e: (e[0], e[1]))

    running = float(account.initial_balance or 0.0)
    for when, _order, delta in events:
        running += delta
        if running < -0.01:
            raise HTTPException(
                422,
                f"Cette opération ferait passer le capital sous zéro "
                f"({running:.2f}) le {when.strftime('%d/%m/%Y %H:%M')}",
            )
