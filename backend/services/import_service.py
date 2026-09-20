"""Orchestration de l'import (CSV / rapport MT5) — étape 4.

Réutilise `services/manual_trades.py` (validation, pips, ticket) : un seul
normalisateur produit les trades, qu'ils viennent d'une saisie manuelle ou
d'un import (voir Recap-structure-manuel-import.md, section 5).

Flux : POST /api/import/preview lit le fichier, construit une liste de
trades CANDIDATS (validés mais pas encore écrits en base) et les garde en
mémoire sous un jeton ; POST /api/import/commit relit ce jeton et écrit
réellement les trades retenus. Rien n'est stocké entre deux lancements de
l'application (compte-tenu de l'usage mono-utilisateur local) : un aperçu
abandonné disparaît simplement après expiration.
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Account, Attachment, CapitalMovement, Trade
from services import import_parsers, manual_trades
from services import movements as movement_service
from services.pips import price_to_pips
import state

PREVIEW_TTL_SECONDS = 30 * 60


@dataclass
class CandidateTrade:
    ticket: int
    data: dict                       # champs prêts pour models.Trade
    row_ref: str                     # repère lisible ("ligne 4", "position 6183602817"…)
    error: Optional[str] = None      # motif si invalide (affiché, jamais importé)
    duplicate: bool = False          # déjà présent en base (même compte+ticket)


@dataclass
class CandidateMovement:
    """Un dépôt / retrait détecté dans un rapport (phase 6), pas encore écrit."""
    ticket: int                      # n° d'opération du rapport (ou hash déterministe)
    time: Optional[datetime]
    amount: float                    # signé : > 0 dépôt, < 0 retrait
    comment: Optional[str]
    row_ref: str
    duplicate: bool = False


@dataclass
class ImportPreview:
    token: str
    created_at: float
    source_kind: str                 # "csv" | "html" | "xlsx"
    filename: str
    account_header: dict
    candidates: list[CandidateTrade]
    integrity: dict
    csv_needs_mapping: bool = False
    csv_headers: Optional[list[str]] = None
    csv_sample_rows: Optional[list[dict]] = None
    movements: list[CandidateMovement] = field(default_factory=list)
    # Compte manuel ACTIF au moment de l'aperçu (celui dont les doublons ont
    # été comparés), ou None. Le commit refuse d'écrire dans un autre compte
    # si l'utilisateur en a changé entre-temps (isolation des données).
    target_login: Optional[int] = None


_previews: dict[str, ImportPreview] = {}
_lock = threading.Lock()


def _cleanup() -> None:
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    for token in [t for t, p in _previews.items() if p.created_at < cutoff]:
        _previews.pop(token, None)


def _store(preview: ImportPreview) -> None:
    with _lock:
        _cleanup()
        _previews[preview.token] = preview


def get_preview(token: str) -> Optional[ImportPreview]:
    with _lock:
        _cleanup()
        return _previews.get(token)


def discard_preview(token: str) -> None:
    with _lock:
        _previews.pop(token, None)


# ── Ticket / anti-doublons ───────────────────────────────────────────────

def deterministic_ticket(symbol: str, direction: str, open_time, volume: float, open_price: float) -> int:
    """Ticket négatif STABLE quand la source n'en fournit pas (CSV générique
    sans colonne ticket) : hash de symbole + sens + heure d'ouverture +
    volume + prix d'ouverture (anti-doublons décrit section 6 — réimporter le
    même fichier ne doit rien recréer)."""
    key = f"{(symbol or '').upper()}|{(direction or '').lower()}|{open_time}|{volume}|{open_price}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return -(int(digest[:15], 16) % 900_000_000 + 100_000_000)


def _existing_tickets(db: Session, account_login: int) -> set[int]:
    rows = db.query(Trade.ticket).filter(Trade.account_id == account_login).all()
    return {t for (t,) in rows}


# ── Construction des trades candidats ───────────────────────────────────

def _finalize_candidates(raw_trades: list[dict], db: Optional[Session], account_login: Optional[int]) -> list[CandidateTrade]:
    existing = _existing_tickets(db, account_login) if (db is not None and account_login is not None) else set()
    seen_in_batch: set[int] = set()
    out: list[CandidateTrade] = []

    for raw in raw_trades:
        row_ref = f"ligne {raw.get('_row_number')}" if raw.get("_row_number") else f"ticket {raw.get('ticket')}"
        if raw.get("_row_error"):
            out.append(CandidateTrade(ticket=0, data=raw, row_ref=row_ref, error=raw["_row_error"]))
            continue

        ticket = raw.get("ticket")
        if not ticket:
            ticket = deterministic_ticket(
                raw.get("symbol"), raw.get("direction"), raw.get("open_time"),
                raw.get("volume"), raw.get("open_price"),
            )
        ticket = int(ticket)

        data = manual_trades.normalize_fields({k: v for k, v in raw.items() if not k.startswith("_")})
        data.pop("ticket", None)
        trade_stub = Trade(ticket=ticket, source="import", **{
            k: v for k, v in data.items() if k in (
                "symbol", "direction", "volume", "open_price", "close_price",
                "sl", "tp", "open_time", "close_time", "profit", "commission",
                "swap", "comment",
            )
        })
        trade_stub.initial_sl = trade_stub.sl
        error = None
        try:
            manual_trades.validate_trade(trade_stub)
            manual_trades.recompute_derived(trade_stub)
        except Exception as exc:  # HTTPException levée par validate_trade
            error = getattr(exc, "detail", str(exc))

        duplicate = ticket in existing or ticket in seen_in_batch
        seen_in_batch.add(ticket)
        candidate = CandidateTrade(
            ticket=ticket,
            data={
                "symbol": trade_stub.symbol, "direction": trade_stub.direction,
                "volume": trade_stub.volume, "open_price": trade_stub.open_price,
                "close_price": trade_stub.close_price, "sl": trade_stub.sl,
                "initial_sl": trade_stub.initial_sl, "tp": trade_stub.tp,
                "open_time": trade_stub.open_time, "close_time": trade_stub.close_time,
                "profit": trade_stub.profit or 0.0, "commission": trade_stub.commission or 0.0,
                "swap": trade_stub.swap or 0.0, "comment": trade_stub.comment,
                "pips": trade_stub.pips, "is_open": trade_stub.is_open,
                "exit_reason": raw.get("exit_reason"),
            },
            row_ref=row_ref,
            error=error,
            duplicate=duplicate,
        )
        out.append(candidate)
    return out


# ── CSV générique ────────────────────────────────────────────────────────

def preview_csv(raw: bytes, filename: str, mapping: Optional[dict], db: Session, account_login: Optional[int]) -> ImportPreview:
    text = import_parsers.decode_bytes(raw)
    headers, rows = import_parsers.read_csv_rows(text)
    token = uuid.uuid4().hex

    if not mapping:
        preview = ImportPreview(
            token=token, created_at=time.time(), source_kind="csv", filename=filename,
            account_header={}, candidates=[], integrity={},
            csv_needs_mapping=True, csv_headers=headers, csv_sample_rows=rows[:5],
        )
        _store(preview)
        return preview

    raw_trades, _errors = import_parsers.rows_to_trades_via_mapping(rows, mapping)
    candidates = _finalize_candidates(raw_trades, db, account_login)
    preview = ImportPreview(
        token=token, created_at=time.time(), source_kind="csv", filename=filename,
        account_header={}, candidates=candidates, integrity={},
    )
    _store(preview)
    return preview


# ── Rapport MT5 (HTML / xlsx) ────────────────────────────────────────────

def _match_exit_reason(symbol: str, close_time, deals: list[dict]) -> Optional[str]:
    """Motif de sortie déduit du commentaire de la ligne « out » correspondante
    (`[sl ...]` -> StopLoss, `[tp ...]` -> TakeProfit, sinon Manuel) — voir
    section 6.

    Vérifié sur le rapport réel : ni la colonne « Opération » (ticket de la
    transaction elle-même) ni « Ordre » (ticket de l'ORDRE qui a généré la
    transaction, distinct du ticket de POSITION pour une clôture) ne
    permettent de relier une ligne « out » à sa position par un simple
    ticket — le rapprochement le plus fiable est donc symbole + heure de
    clôture, qui coïncident exactement entre la table Positions et la ligne
    « out » correspondante des Transactions.
    """
    if not close_time:
        return None
    candidates = [d for d in deals if str(d.get("entry", "")).strip().lower() in ("out", "sortie")]
    candidates = [
        d for d in candidates
        if (d.get("symbol") or "").strip().upper() == (symbol or "").upper()
        and import_parsers.parse_datetime(d.get("time")) == close_time
    ]
    if not candidates:
        return None
    comment = str(candidates[0].get("comment") or "").lower()
    if "sl" in comment:
        return "StopLoss"
    if "tp" in comment:
        return "TakeProfit"
    return "Manuel"


def _safe_int(v) -> Optional[int]:
    n = import_parsers.parse_number(v)
    return int(n) if n is not None else None


def _positions_to_raw_trades(report: import_parsers.ParsedReport) -> list[dict]:
    raw_trades = []
    for pos in report.positions:
        ticket = _safe_int(pos.get("ticket"))
        direction = import_parsers._normalize_direction(pos.get("direction"))
        open_time = import_parsers.parse_datetime(pos.get("open_time"))
        close_time = import_parsers.parse_datetime(pos.get("close_time"))
        close_price = import_parsers.parse_number(pos.get("close_price"))
        data = {
            "ticket": ticket,
            "symbol": (pos.get("symbol") or "").strip().upper(),
            "direction": direction,
            "volume": import_parsers.parse_number(pos.get("volume")),
            "open_price": import_parsers.parse_number(pos.get("open_price")),
            "open_time": open_time,
            "close_price": close_price,
            # Un rapport d'historique ne contient que du clôturé (section 6) :
            # une ligne sans prix de clôture n'est pas exploitable en import.
            "close_time": close_time if close_price is not None else None,
            "sl": import_parsers.parse_number(pos.get("sl")) or None,
            "tp": import_parsers.parse_number(pos.get("tp")) or None,
            "profit": import_parsers.parse_number(pos.get("profit")) or 0.0,
            "commission": import_parsers.parse_number(pos.get("commission")) or 0.0,
            "swap": import_parsers.parse_number(pos.get("swap")) or 0.0,
        }
        data["exit_reason"] = _match_exit_reason(data["symbol"], close_time, report.deals)
        if not data["direction"]:
            data["_row_error"] = f"Position {pos.get('ticket')} : sens (achat/vente) illisible"
        if data["open_price"] is None or data["volume"] is None or open_time is None:
            data["_row_error"] = f"Position {pos.get('ticket')} : champs d'ouverture illisibles"
        raw_trades.append(data)
    return raw_trades


@dataclass
class BalanceAnalysis:
    """Lecture des dépôts / retraits d'un rapport (phase 6)."""
    pre_balance: Optional[float] = None       # solde AVANT la première ligne de Transactions
    initial_funding: Optional[float] = None   # dépôt(s) initial(aux) à solde nul → capital de départ
    movements: list[dict] = field(default_factory=list)   # hors dépôt initial


def _analyze_balance(report: import_parsers.ParsedReport) -> BalanceAnalysis:
    """Sépare, dans les Transactions du rapport :

    - le DÉPÔT INITIAL : la ou les toutes premières lignes « balance » lues à
      solde nul (le compte est alimenté puis tradé). Ce n'est pas un
      mouvement de capital mais le capital de départ du compte : il est
      proposé comme `suggested_initial_balance`, jamais importé en double ;
    - les MOUVEMENTS : tous les autres dépôts / retraits, importés dans le
      journal des mouvements de capital.

    Le solde avant la première ligne se déduit de sa colonne « Solde » moins
    son propre montant (profit + commission + échange + frais). Sans colonne
    « Solde » lisible, aucun dépôt n'est considéré comme initial.
    """
    analysis = BalanceAnalysis()
    deals = report.deals
    if deals:
        first_balance = import_parsers.parse_number(deals[0].get("balance"))
        if first_balance is not None:
            analysis.pre_balance = round(first_balance - import_parsers.deal_net(deals[0]), 2)

    running = analysis.pre_balance
    initial_rows: set[int] = set()
    funding = 0.0
    for idx, deal in enumerate(deals):
        amount = import_parsers.parse_number(deal.get("profit"))
        if (import_parsers.is_balance_deal(deal) and running is not None
                and abs(running) < 0.005 and amount is not None and amount > 0):
            funding += amount
            running += amount
            initial_rows.add(idx)
        else:
            break
    if initial_rows:
        analysis.initial_funding = round(funding, 2)

    for idx, deal in enumerate(deals):
        if idx in initial_rows or not import_parsers.is_balance_deal(deal):
            continue
        analysis.movements.append({
            "deal_id": _safe_int(deal.get("deal_id")),
            "time": import_parsers.parse_datetime(deal.get("time")),
            "amount": import_parsers.parse_number(deal.get("profit")),
            "comment": (str(deal.get("comment")).strip() or None) if deal.get("comment") is not None else None,
            "row_ref": f"opération {deal.get('deal_id')}" if deal.get("deal_id") else f"ligne {idx + 1}",
        })
    return analysis


def _integrity_checks(report: import_parsers.ParsedReport, raw_trades: list[dict],
                      balance: Optional[BalanceAnalysis] = None) -> dict:
    """Contrôles décrits section 6 : la somme profit+commission+swap+frais
    des Transactions doit correspondre au « Profit Total Net » du rapport,
    et le nombre de positions au « Nb trades ». Le calcul part des
    Transactions (pas des Positions) car seules elles portent la colonne
    « Frais », absente de la table Positions sur le rapport réel. Un écart
    n'empêche pas l'aperçu — il est affiché pour que l'utilisateur décide
    (l'import reste bloqué côté commit tant qu'il n'a pas confirmé malgré
    l'écart, voir le routeur).

    Phase 6 : les lignes de dépôt / retrait sont EXCLUES du profit net (un
    dépôt n'est pas un gain — sans cela chaque rapport contenant un dépôt
    serait bloqué) et un contrôle du SOLDE FINAL est ajouté : solde avant la
    première ligne + somme de toutes les lignes (dépôts compris) doit égaler
    le « Solde » du rapport.
    """
    checks: dict = {}
    trade_deals = [d for d in report.deals if not import_parsers.is_balance_deal(d)]
    net = sum(import_parsers.deal_net(d) for d in trade_deals)
    if report.summary.get("total_net_profit") is not None:
        expected = report.summary["total_net_profit"]
        checks["net_profit"] = {
            "computed": round(net, 2), "expected": expected,
            "matches": abs(net - expected) < 0.01,
        }
    if report.summary.get("trades_count") is not None:
        expected_count = report.summary["trades_count"]
        checks["trades_count"] = {
            "computed": len(raw_trades), "expected": expected_count,
            "matches": len(raw_trades) == expected_count,
        }
    if balance is not None and balance.pre_balance is not None and report.summary.get("balance") is not None:
        computed_balance = balance.pre_balance + sum(import_parsers.deal_net(d) for d in report.deals)
        expected_balance = report.summary["balance"]
        checks["final_balance"] = {
            "computed": round(computed_balance, 2), "expected": expected_balance,
            "matches": abs(computed_balance - expected_balance) < 0.01,
        }
    return checks


def _suggest_initial_balance(report: import_parsers.ParsedReport,
                             balance: Optional[BalanceAnalysis] = None) -> Optional[float]:
    """Capital de départ PROPOSÉ à la création du compte (section 6) :

    1. dépôt initial du compte (première ligne « balance » à solde nul) ;
    2. sinon solde avant la première ligne des Transactions (176,96 sur le
       rapport d'exemple — identique à l'ancien « solde de la première ligne
       in » tant que cette ligne ne porte ni commission ni échange) ;
    3. à défaut, solde final − profit net du rapport − dépôts/retraits.

    Simple proposition : l'utilisateur la confirme ou la corrige dans l'aperçu.
    """
    if balance is not None:
        if balance.initial_funding is not None:
            return balance.initial_funding
        if balance.pre_balance is not None:
            return balance.pre_balance
    for deal in report.deals:
        if str(deal.get("entry", "")).strip().lower() in ("in", "entree", "entrée"):
            balance_value = import_parsers.parse_number(deal.get("balance"))
            if balance_value is not None:
                return round(balance_value, 2)
    final_balance = report.summary.get("balance")
    net_profit = report.summary.get("total_net_profit")
    if final_balance is not None and net_profit is not None:
        flows = sum((m["amount"] or 0.0) for m in balance.movements) if balance is not None else 0.0
        return round(final_balance - net_profit - flows, 2)
    return None


def _existing_movement_tickets(db: Session, account_login: int) -> set[int]:
    rows = db.query(CapitalMovement.ticket).filter(
        CapitalMovement.account_id == account_login, CapitalMovement.ticket.isnot(None)
    ).all()
    return {t for (t,) in rows}


def _movement_candidates(analysis: BalanceAnalysis, db: Optional[Session],
                         account_login: Optional[int]) -> list[CandidateMovement]:
    existing = (_existing_movement_tickets(db, account_login)
                if (db is not None and account_login is not None) else set())
    seen: set[int] = set()
    out: list[CandidateMovement] = []
    for raw in analysis.movements:
        amount = raw["amount"]
        if amount is None or amount == 0 or raw["time"] is None:
            continue   # ligne « balance » sans montant ou sans date lisible : ignorée
        amount = round(float(amount), 2)
        ticket = raw["deal_id"] or movement_service.deterministic_ticket(raw["time"], amount, raw["comment"])
        out.append(CandidateMovement(
            ticket=int(ticket), time=raw["time"], amount=amount, comment=raw["comment"],
            row_ref=raw["row_ref"], duplicate=ticket in existing or ticket in seen,
        ))
        seen.add(ticket)
    return out


def preview_report(raw: bytes, filename: str, kind: str, db: Session, account_login: Optional[int]) -> ImportPreview:
    if kind == "html":
        text = import_parsers.decode_bytes(raw)
        rows = import_parsers.rows_from_html(text)
    else:
        rows = import_parsers.rows_from_xlsx(raw)

    report = import_parsers.parse_report_rows(rows)
    raw_trades = _positions_to_raw_trades(report)
    candidates = _finalize_candidates(raw_trades, db, account_login)
    balance = _analyze_balance(report)
    integrity = _integrity_checks(report, raw_trades, balance)
    movements = _movement_candidates(balance, db, account_login)

    header = dict(report.account_header)
    suggested = _suggest_initial_balance(report, balance)
    if suggested is not None and suggested > 0:
        header["suggested_initial_balance"] = suggested
    if balance.initial_funding is not None:
        header["initial_funding"] = balance.initial_funding

    preview = ImportPreview(
        token=uuid.uuid4().hex, created_at=time.time(), source_kind=kind, filename=filename,
        account_header=header, candidates=candidates, integrity=integrity, movements=movements,
    )
    _store(preview)
    return preview


# ── Commit ───────────────────────────────────────────────────────────────

def commit_preview(db: Session, preview: ImportPreview, account: Account) -> dict:
    """Écrit les trades valides et non-doublons du jeton d'aperçu. Un même
    lot (`import_batch`) permet d'annuler l'import entier (DELETE
    /api/import/batch/{id})."""
    batch_id = preview.token
    inserted = 0
    skipped_duplicate = 0
    skipped_error = 0
    # Les doublons sont recalculés ICI, sur le compte de destination réel :
    # l'aperçu a pu être calculé avant que ce compte n'existe (import dans un
    # nouveau compte) ou pour un autre compte actif — son drapeau `duplicate`
    # n'est donc qu'indicatif.
    existing = _existing_tickets(db, account.login)
    seen_in_batch: set[int] = set()
    for c in preview.candidates:
        if c.error:
            skipped_error += 1
            continue
        if c.ticket in existing or c.ticket in seen_in_batch:
            skipped_duplicate += 1
            continue
        seen_in_batch.add(c.ticket)
        trade = Trade(
            ticket=c.ticket, account_id=account.login, source="import", import_batch=batch_id,
            **{k: v for k, v in c.data.items() if k not in ("exit_reason",)},
        )
        if c.data.get("exit_reason"):
            trade.exit_reason = c.data["exit_reason"]
        db.add(trade)
        inserted += 1

    # Dépôts / retraits (phase 6) : même anti-doublons (compte + n° d'opération),
    # même lot (`import_batch`) que les trades — annulés ensemble.
    existing_movements = _existing_movement_tickets(db, account.login)
    seen_movements: set[int] = set()
    movements_inserted = 0
    movements_skipped = 0
    for m in preview.movements:
        if m.ticket in existing_movements or m.ticket in seen_movements:
            movements_skipped += 1
            continue
        seen_movements.add(m.ticket)
        db.add(CapitalMovement(
            account_id=account.login, ticket=m.ticket, time=m.time, amount=m.amount,
            comment=m.comment, source=movement_service.SOURCE_IMPORT, import_batch=batch_id,
        ))
        movements_inserted += 1

    db.commit()
    state.sync_manual_balance(db, account)
    discard_preview(preview.token)
    return {
        "batch_id": batch_id, "inserted": inserted,
        "skipped_duplicate": skipped_duplicate, "skipped_error": skipped_error,
        "movements_inserted": movements_inserted,
        "movements_skipped_duplicate": movements_skipped,
    }


def list_batches(db: Session, account_login: int) -> list[dict]:
    trade_rows = (
        db.query(Trade.import_batch, func.count(Trade.id), func.min(Trade.open_time), func.max(Trade.open_time))
        .filter(Trade.account_id == account_login, Trade.import_batch.isnot(None))
        .group_by(Trade.import_batch)
        .all()
    )
    batches: dict[str, dict] = {
        batch_id: {"batch_id": batch_id, "trades_count": count, "movements_count": 0,
                   "date_from": dfrom, "date_to": dto}
        for batch_id, count, dfrom, dto in trade_rows
    }
    movement_rows = (
        db.query(CapitalMovement.import_batch, func.count(CapitalMovement.id),
                 func.min(CapitalMovement.time), func.max(CapitalMovement.time))
        .filter(CapitalMovement.account_id == account_login, CapitalMovement.import_batch.isnot(None))
        .group_by(CapitalMovement.import_batch)
        .all()
    )
    for batch_id, count, dfrom, dto in movement_rows:
        entry = batches.setdefault(batch_id, {
            "batch_id": batch_id, "trades_count": 0, "movements_count": 0,
            "date_from": dfrom, "date_to": dto,
        })
        entry["movements_count"] = count
        entry["date_from"] = min(d for d in (entry["date_from"], dfrom) if d is not None)
        entry["date_to"] = max(d for d in (entry["date_to"], dto) if d is not None)
    return list(batches.values())


def cancel_batch(db: Session, account: Account, batch_id: str) -> dict:
    """Annule un lot d'import : supprime ses trades (pièces jointes
    orphelines comprises) ET ses dépôts / retraits. Renvoie les deux
    compteurs ; (0, 0) = lot introuvable."""
    trades = db.query(Trade).filter(Trade.account_id == account.login, Trade.import_batch == batch_id).all()
    tickets = [t.ticket for t in trades]
    for t in trades:
        db.delete(t)
    movements_deleted = db.query(CapitalMovement).filter(
        CapitalMovement.account_id == account.login, CapitalMovement.import_batch == batch_id
    ).delete()
    db.flush()
    if tickets:
        state.purge_orphan_attachments(db, account.login, tickets)
    db.commit()
    state.sync_manual_balance(db, account)
    return {"trades": len(tickets), "movements": movements_deleted}
