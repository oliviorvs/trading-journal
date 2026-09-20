"""Logique partagée des trades NON-MT5 (source = "manual" | "import").

Utilisée par l'ajout / l'édition manuels (routers/trades.py) et, à l'étape 4,
par l'import de fichiers : une seule validation, un seul calcul de pips, un
seul générateur de ticket. Voir Recap-structure-manuel-import.md (sections 4-5).

Les heures sont stockées TELLES QUELLES (heure serveur), comme la synchro MT5 :
aucune conversion de fuseau. Un datetime « aware » reçu de l'API perd
simplement son fuseau (l'heure affichée est conservée).
"""
import math
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Attachment, Trade
from services.pips import price_to_pips

# Sources dont TOUS les champs sont éditables. Un trade "mt5" reste en
# lecture seule (hors champs journal) pour rester fidèle aux données broker.
EDITABLE_SOURCES = ("manual", "import")

# Champs "journal" : éditables sur TOUS les trades, quelle que soit la source.
JOURNAL_FIELDS = frozenset({
    "notes", "setup_tag", "error_tag", "emotion", "playbook",
    "exit_reason", "entry_timeframe", "risk_percent",
})

# Champs de données du trade : éditables uniquement hors "mt5".
DATA_FIELDS = frozenset({
    "symbol", "direction", "volume", "open_price", "close_price", "sl",
    "initial_sl", "tp", "open_time", "close_time", "profit", "commission",
    "swap", "comment", "margin",
})

# Champs qui n'acceptent jamais null.
REQUIRED_FIELDS = frozenset({
    "symbol", "direction", "volume", "open_price", "open_time",
    "profit", "commission", "swap",
})


def next_manual_ticket(db: Session) -> int:
    """Prochain ticket NÉGATIF, unique GLOBALEMENT (tous comptes confondus).

    Les pièces jointes ne référencent qu'un ticket (pas un couple compte +
    ticket) : un ticket négatif unique évite toute collision, avec les
    tickets MT5 (positifs) comme entre comptes manuels. On regarde aussi les
    pièces jointes : un ticket ne doit pas être réutilisé tant qu'une pièce
    jointe le référence encore.
    """
    lowest_trade = db.query(func.min(Trade.ticket)).scalar()
    lowest_attach = db.query(func.min(Attachment.trade_ticket)).scalar()
    lowest = min(v for v in (lowest_trade, lowest_attach, 0) if v is not None)
    return lowest - 1


def naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Supprime le fuseau SANS convertir (heure serveur conservée telle quelle)."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _bad(message: str) -> HTTPException:
    return HTTPException(422, message)


def _finite(value) -> bool:
    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))


def validate_trade(trade: Trade) -> None:
    """Validation serveur d'un trade non-MT5 (état final, après fusion).

    - direction buy | sell ; symbole non vide ; volume > 0 ;
    - prix (ouverture, clôture, SL, TP) > 0 quand renseignés ;
    - clôture postérieure ou égale à l'ouverture ;
    - date ET prix de clôture renseignés ensemble, ou absents ensemble
      (trade ouvert) ;
    - aucun nombre infini ou NaN.
    """
    if not (trade.symbol or "").strip():
        raise _bad("Le symbole est requis")
    if trade.direction not in ("buy", "sell"):
        raise _bad("La direction doit être « buy » ou « sell »")
    if trade.open_time is None:
        raise _bad("La date d'ouverture est requise")

    numeric = {
        "volume": trade.volume, "open_price": trade.open_price,
        "close_price": trade.close_price, "sl": trade.sl, "initial_sl": trade.initial_sl,
        "tp": trade.tp, "profit": trade.profit, "commission": trade.commission,
        "swap": trade.swap, "margin": trade.margin,
    }
    for name, value in numeric.items():
        if not _finite(value):
            raise _bad(f"Valeur numérique invalide pour « {name} »")

    if trade.volume is None or trade.volume <= 0:
        raise _bad("Le volume doit être strictement positif")
    if trade.open_price is None or trade.open_price <= 0:
        raise _bad("Le prix d'ouverture doit être strictement positif")
    for name in ("close_price", "sl", "initial_sl", "tp"):
        value = getattr(trade, name)
        if value is not None and value <= 0:
            raise _bad(f"Le champ « {name} » doit être strictement positif")
    if trade.margin is not None and trade.margin < 0:
        raise _bad("La marge ne peut pas être négative")

    has_close_time = trade.close_time is not None
    has_close_price = trade.close_price is not None
    if has_close_time != has_close_price:
        raise _bad(
            "Date et prix de clôture vont ensemble : renseignez les deux "
            "(trade clôturé) ou aucun des deux (trade ouvert)"
        )
    if has_close_time and trade.close_time < trade.open_time:
        raise _bad("La clôture ne peut pas être antérieure à l'ouverture")


def recompute_derived(trade: Trade) -> None:
    """Champs dérivés : `is_open` (pas de clôture ⇒ ouvert) et `pips`.

    Pips non signés, comme la synchro MT5 (voir mt5_service.sync_trades).
    Aucune connexion MT5 ici : repli par famille d'instruments (voir
    services/pips.py). Un trade ouvert n'a pas de pips (0).
    """
    trade.is_open = trade.close_time is None
    if trade.is_open or trade.close_price is None:
        trade.pips = 0.0
    else:
        trade.pips = price_to_pips(trade.close_price - trade.open_price, trade.symbol)


def normalize_fields(data: dict) -> dict:
    """Normalise symbole (majuscules), direction (minuscules) et datetimes."""
    out = dict(data)
    if isinstance(out.get("symbol"), str):
        out["symbol"] = out["symbol"].strip().upper()
    if isinstance(out.get("direction"), str):
        out["direction"] = out["direction"].strip().lower()
    for key in ("open_time", "close_time"):
        if key in out:
            out[key] = naive(out[key])
    return out
