"""Équité réelle et charge du dépôt — phase 6.

Deux indicateurs qu'un compte de trading rend visibles dans MetaTrader mais
qu'un simple journal de trades clôturés ne peut pas déduire seul :

ÉQUITÉ RÉELLE = solde + résultat flottant des positions ouvertes.
    Le SOLDE se reconstruit exactement : capital de départ + mouvements de
    capital (dépôts / retraits) + P&L NET (profit + commission + swap) de
    chaque trade clôturé, à sa date de clôture. C'est le solde du courtier,
    pas le P&L brut de l'application. L'ÉQUITÉ, elle, dépend d'un flottant
    qui n'existe dans aucune ligne de trade : on la relève (EquitySnapshot,
    voir `record_snapshot`) et on n'invente jamais de valeur intermédiaire.
    Avant le premier relevé, seule la courbe de solde est disponible.

CHARGE DU DÉPÔT = marge utilisée / équité (en %).
    - MESURÉE : chaque relevé porte la marge et l'équité du courtier.
    - ESTIMÉE : sur tout l'historique, à partir de la marge de chaque trade
      (`Trade.margin`, calculée par MT5 à la synchro ou saisie ; sinon
      estimée par `estimate_margin` quand la devise du compte et le symbole
      le permettent) rapportée au SOLDE à chaque ouverture / clôture. Le
      flottant étant inconnu entre deux relevés, cette estimation est un
      minorant de la charge réelle en cas de perte latente — d'où la
      distinction mesurée / estimée dans les résultats.

Toutes les fonctions travaillent sur UN compte (login) : aucune donnée d'un
autre compte n'est jamais lue. Ce module n'importe pas `state` (le service
MT5 y est injecté par l'appelant) pour rester testable seul.
"""
from __future__ import annotations

import bisect
import math
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Account, EquitySnapshot, Trade
from services import movements as movement_service

# Relevés : au plus un par minute ; un relevé identique au précédent n'est
# pas gardé sauf s'il date de plus d'une heure (trace de vie de la courbe).
SNAPSHOT_MIN_GAP_S = 60
SNAPSHOT_MAX_SILENCE_S = 3600

# Nombre maximal de points renvoyés par série (au-delà : réduction qui
# conserve les extrêmes de chaque paquet, donc les pics de charge et les creux).
MAX_POINTS = 1500

# Écart toléré entre le solde reconstruit et celui du courtier.
RECONCILE_MIN_TOLERANCE = 0.5
RECONCILE_RELATIVE_TOLERANCE = 0.002


# ── Utilitaires ──────────────────────────────────────────────────────────

def net(trade: Trade) -> float:
    """P&L net d'un trade : profit + commission + swap (NULL = 0)."""
    return (trade.profit or 0.0) + (trade.commission or 0.0) + (trade.swap or 0.0)


def _utcnow() -> datetime:
    """UTC courant SANS fuseau (convention des colonnes DateTime du projet)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


# ── Marge d'un trade ─────────────────────────────────────────────────────

_CURRENCIES = frozenset(
    "USD EUR GBP JPY CHF AUD NZD CAD SEK NOK DKK PLN CZK HUF MXN ZAR TRY SGD HKD CNH".split()
)
_SYMBOL_LETTERS = re.compile(r"^([A-Z]{3})([A-Z]{3})")
# Taille de contrat par famille (unités du sous-jacent pour 1 lot).
_METAL_CONTRACT = {"XAU": 100.0, "XAG": 5000.0}
_CRYPTO_BASES = frozenset({"BTC", "ETH", "LTC", "XRP", "SOL", "ADA", "DOGE"})
FOREX_CONTRACT = 100_000.0


def estimate_margin(symbol: str, volume: float, open_price: float,
                    currency: str, leverage: Optional[int]) -> Optional[float]:
    """Marge estimée d'une position, en devise du compte, ou None si elle
    n'est pas calculable SANS taux de change tiers ni fiche de contrat.

    Cas couverts : paires de devises dont l'une des deux monnaies est celle
    du compte (contrat 100 000), or / argent et grandes cryptos cotés dans la
    devise du compte (contrats 100 oz / 5 000 oz / 1 unité). Indices, énergie,
    actions et croisées sans la devise du compte : None (saisir la marge).
    Repli hors MT5 : quand MT5 tourne, `Trade.margin` vient du courtier.
    """
    if not symbol or not _finite(volume) or volume <= 0 or not _finite(open_price) or open_price <= 0:
        return None
    if not leverage or leverage <= 0:
        return None
    cur = (currency or "").upper()
    found = _SYMBOL_LETTERS.match(symbol.upper())
    if not found:
        return None
    base, quote = found.group(1), found.group(2)

    contract: Optional[float] = None
    if base in _METAL_CONTRACT:
        contract = _METAL_CONTRACT[base]
    elif base in _CRYPTO_BASES:
        contract = 1.0
    if contract is not None:
        # Cotés dans une devise (USD le plus souvent) : la marge est
        # exprimée dans cette devise, calculable si c'est celle du compte.
        if quote != cur:
            return None
        return round(volume * contract * open_price / leverage, 2)

    if base not in _CURRENCIES or quote not in _CURRENCIES or base == quote:
        return None
    in_base = volume * FOREX_CONTRACT / leverage
    if base == cur:
        return round(in_base, 2)
    if quote == cur:
        return round(in_base * open_price, 2)
    return None


def trade_margin(trade: Trade, account: Account) -> Tuple[Optional[float], Optional[str]]:
    """(marge, origine) d'un trade : origine « stored » (calculée par MT5 ou
    saisie), « estimated » (estimation locale) ou None (inconnue)."""
    if trade.margin is not None and _finite(trade.margin) and trade.margin >= 0:
        return float(trade.margin), "stored"
    value = estimate_margin(trade.symbol, trade.volume, trade.open_price,
                            account.currency, account.leverage)
    if value is not None:
        return value, "estimated"
    return None, None


# ── Relevés d'équité (comptes MT5) ───────────────────────────────────────

def record_snapshot(db: Session, account: Account, info: Optional[dict],
                    now: Optional[datetime] = None, commit: bool = False) -> bool:
    """Enregistre l'équité RÉELLE d'un compte MT5 (relevé daté). `info` est le
    dict de `MT5Service.get_account_info()` du compte connecté ; à charge de
    l'appelant de ne le fournir que pour le bon compte. Retourne True si un
    relevé a été ajouté.

    Rien n'est enregistré pour un compte manuel, une simulation, une valeur
    illisible, ni quand le relevé n'apporte rien (même valeurs que le
    précédent, à moins d'une heure). Ne commit que si `commit=True` (la synchro
    appelle cette fonction dans sa propre transaction).
    """
    if not account or account.mode == "manual" or not info or info.get("simulated"):
        return False
    balance, equity = info.get("balance"), info.get("equity")
    if not _finite(balance) or not _finite(equity):
        return False
    margin = info.get("margin")
    margin = float(margin) if _finite(margin) else 0.0
    free_margin = info.get("free_margin")
    free_margin = float(free_margin) if _finite(free_margin) else None
    now = (now or _utcnow()).replace(microsecond=0)

    last = (
        db.query(EquitySnapshot)
        .filter(EquitySnapshot.account_id == account.login)
        .order_by(EquitySnapshot.time.desc())
        .first()
    )
    if last is not None:
        gap = (now - last.time).total_seconds()
        if gap < SNAPSHOT_MIN_GAP_S:
            return False
        same = (
            round(last.balance or 0.0, 2) == round(balance, 2)
            and round(last.equity or 0.0, 2) == round(equity, 2)
            and round(last.margin or 0.0, 2) == round(margin, 2)
        )
        if same and gap < SNAPSHOT_MAX_SILENCE_S:
            return False

    open_count = db.query(func.count(Trade.id)).filter(
        Trade.account_id == account.login, Trade.is_open.is_(True)
    ).scalar() or 0
    db.add(EquitySnapshot(
        account_id=account.login, time=now, balance=round(balance, 2),
        equity=round(equity, 2), margin=round(margin, 2),
        free_margin=None if free_margin is None else round(free_margin, 2),
        open_count=int(open_count),
    ))
    if commit:
        db.commit()
    return True


def account_snapshots(db: Session, login: Optional[int]) -> List[EquitySnapshot]:
    if login is None:
        return []
    return (
        db.query(EquitySnapshot)
        .filter(EquitySnapshot.account_id == login)
        .order_by(EquitySnapshot.time)
        .all()
    )


# ── Compte manuel : positions ouvertes saisies ───────────────────────────

def open_trades(db: Session, login: int) -> List[Trade]:
    return db.query(Trade).filter(Trade.account_id == login, Trade.is_open.is_(True)).all()


def manual_open_floating(db: Session, account: Account) -> float:
    """Résultat flottant d'un compte MANUEL : Σ (profit + commission + swap)
    saisis sur ses trades ouverts. Aucun prix de marché n'est connu, donc le
    flottant ne vaut que ce que l'utilisateur a saisi (0 par défaut)."""
    return sum(net(t) for t in open_trades(db, account.login))


def manual_open_margin(db: Session, account: Account) -> float:
    """Marge immobilisée par les trades ouverts d'un compte manuel (saisie ou
    estimation ; les trades sans marge connue comptent pour 0)."""
    total = 0.0
    for t in open_trades(db, account.login):
        value, _kind = trade_margin(t, account)
        total += value or 0.0
    return round(total, 2)


# ── Solde reconstruit ────────────────────────────────────────────────────

class BalanceTimeline:
    """Solde du compte dans le temps : capital de départ, puis chaque
    trade clôturé (P&L net, à sa clôture) et chaque mouvement de capital."""

    def __init__(self, db: Session, account: Account):
        self.start_balance = float(account.initial_balance or 0.0)
        raw: List[Tuple[datetime, str, float]] = []
        for t in db.query(Trade).filter(Trade.account_id == account.login, Trade.is_open.is_(False)).all():
            when = t.close_time or t.open_time
            if when is not None:
                raw.append((when, "trade", net(t)))
        for when, amount in movement_service.movement_events(db, account.login):
            raw.append((when, "movement", amount))
        raw.sort(key=lambda e: e[0])
        self.events = raw
        self.times: List[datetime] = []
        self.balances: List[float] = []
        running = self.start_balance
        for when, _kind, delta in raw:
            running += delta
            self.times.append(when)
            self.balances.append(running)
        self.expected_balance = running
        self.start_time: Optional[datetime] = (
            account.start_date if account.start_date and (not raw or account.start_date <= raw[0][0])
            else (raw[0][0] if raw else account.start_date)
        )

    def at(self, when: datetime) -> float:
        idx = bisect.bisect_right(self.times, when) - 1
        return self.balances[idx] if idx >= 0 else self.start_balance

    def rows(self) -> List[dict]:
        """Points de la courbe de solde (heures en datetime)."""
        out: List[dict] = []
        if self.start_time is not None:
            out.append({"time": self.start_time, "balance": round(self.start_balance, 2),
                        "delta": 0.0, "kind": "start"})
        for (when, kind, delta), balance in zip(self.events, self.balances):
            out.append({"time": when, "balance": round(balance, 2),
                        "delta": round(delta, 2), "kind": kind})
        return out


# ── Drawdown neutre vis-à-vis des dépôts / retraits ──────────────────────

def neutral_drawdown(points: List[Tuple[datetime, float]],
                     flows: List[Tuple[datetime, float]],
                     start_value: Optional[float] = None) -> Tuple[float, float]:
    """(drawdown max en %, en montant) d'une série d'équité / de solde. Un
    mouvement de capital décale le sommet du même montant (voir
    services/stats.max_drawdown) : un retrait ne crée pas de drawdown, un
    dépôt n'en masque pas. `points` et `flows` triés par heure."""
    peak: Optional[float] = start_value
    max_pct = max_abs = 0.0
    j = 0
    for when, value in points:
        while j < len(flows) and flows[j][0] <= when:
            if peak is not None:
                peak += flows[j][1]
            j += 1
        if peak is None or value > peak:
            peak = value
        dd_abs = value - peak
        dd_pct = dd_abs / abs(peak) * 100 if peak else 0.0
        max_pct = min(max_pct, dd_pct)
        max_abs = min(max_abs, dd_abs)
    return round(max_pct, 2), round(max_abs, 2)


# ── Réduction de séries ──────────────────────────────────────────────────

def downsample(points: List[dict], key: str, limit: int = MAX_POINTS) -> List[dict]:
    """Réduit une série à ~`limit` points en gardant, par paquet, le minimum
    et le maximum de `key` (donc les creux et les pics) dans l'ordre du temps."""
    if len(points) <= limit or limit < 4:
        return points
    size = math.ceil(len(points) / (limit // 2))
    kept: List[dict] = []
    for start in range(0, len(points), size):
        bucket = points[start:start + size]
        vals = [(p.get(key) if p.get(key) is not None else float("-inf"), i) for i, p in enumerate(bucket)]
        lo = min(vals, key=lambda v: v[0])[1]
        hi = max(vals, key=lambda v: v[0])[1]
        for i in sorted({0, lo, hi, len(bucket) - 1}):
            kept.append(bucket[i])
    return kept


# ── Charge du dépôt ──────────────────────────────────────────────────────

def _load_pct(margin: Optional[float], equity: Optional[float]) -> Optional[float]:
    if margin is None or equity is None or equity <= 0:
        return None
    return round(margin / equity * 100, 2)


def estimated_load_series(db: Session, account: Account, timeline: BalanceTimeline,
                          now: datetime) -> Tuple[List[dict], int, int]:
    """Série ESTIMÉE de la charge du dépôt : à chaque ouverture / clôture de
    trade, marge totale immobilisée / solde du moment. Retourne (points,
    nombre de trades avec marge, nombre de trades sans marge connue)."""
    deltas: dict = {}
    with_margin = without_margin = 0
    for t in db.query(Trade).filter(Trade.account_id == account.login).all():
        value, _kind = trade_margin(t, account)
        if value is None:
            without_margin += 1
            continue
        if t.open_time is None:
            without_margin += 1
            continue
        with_margin += 1
        deltas[t.open_time] = deltas.get(t.open_time, 0.0) + value
        if not t.is_open:
            end = t.close_time or t.open_time
            deltas[end] = deltas.get(end, 0.0) - value

    points: List[dict] = []
    active = 0.0
    for when in sorted(deltas):
        active = max(round(active + deltas[when], 6), 0.0)
        equity = timeline.at(when)
        points.append({"time": when, "margin": round(active, 2), "equity": round(equity, 2),
                       "load_pct": _load_pct(active, equity), "kind": "estimated"})
    if points and points[-1]["margin"] > 0 and points[-1]["time"] < now:
        # Position(s) encore ouverte(s) : la charge se prolonge jusqu'à maintenant.
        last = points[-1]
        equity = timeline.at(now)
        points.append({"time": now, "margin": last["margin"], "equity": round(equity, 2),
                       "load_pct": _load_pct(last["margin"], equity), "kind": "estimated"})
    return points, with_margin, without_margin


def _time_weighted_average(points: List[dict]) -> Optional[float]:
    """Charge moyenne pondérée par la durée, sur les seuls intervalles où de
    la marge est immobilisée (les périodes sans position ne diluent pas)."""
    total = weighted = 0.0
    for cur, nxt in zip(points, points[1:]):
        if cur["margin"] <= 0 or cur["load_pct"] is None:
            continue
        seconds = (nxt["time"] - cur["time"]).total_seconds()
        if seconds <= 0:
            continue
        total += seconds
        weighted += cur["load_pct"] * seconds
    return round(weighted / total, 2) if total else None


def _clip(points: List[dict], date_from: Optional[datetime]) -> List[dict]:
    """Points à partir de `date_from`, précédés d'un point d'ancrage (état en
    vigueur à cette date) pour que la fenêtre ne démarre pas dans le vide."""
    if not date_from:
        return points
    before = [p for p in points if p["time"] < date_from]
    after = [p for p in points if p["time"] >= date_from]
    if before and (not after or after[0]["time"] > date_from):
        anchor = dict(before[-1])
        anchor["time"] = date_from
        if "delta" in anchor:            # courbe de solde : l'ancrage n'est pas un événement
            anchor["delta"] = 0.0
            anchor["kind"] = "anchor"
        after.insert(0, anchor)
    return after


def _serialise(points: Iterable[dict]) -> List[dict]:
    out = []
    for p in points:
        row = dict(p)
        row["time"] = _iso(p["time"])
        out.append(row)
    return out


# ── Rapport complet ──────────────────────────────────────────────────────

def real_equity_report(db: Session, account: Account, live: Optional[dict] = None,
                       date_from: Optional[datetime] = None,
                       now: Optional[datetime] = None) -> dict:
    """Équité réelle + charge du dépôt du compte `account`.

    `live` : `MT5Service.get_account_info()` UNIQUEMENT si le terminal est
    connecté à CE compte (et pas en simulation) ; sinon None et l'état du
    compte est celui du dernier relevé / de la dernière synchro.
    `date_from` : borne d'affichage ET des indicateurs (fenêtre du dashboard).
    """
    now = (now or _utcnow()).replace(microsecond=0)
    manual = account.mode == "manual"
    timeline = BalanceTimeline(db, account)
    flows = movement_service.movement_events(db, account.login)

    # ── État courant ──
    if manual:
        balance = timeline.expected_balance
        floating = manual_open_floating(db, account)
        equity = balance + floating
        margin = manual_open_margin(db, account)
        free_margin = equity - margin
        open_count = len(open_trades(db, account.login))
        origin = "computed"
    elif live:
        balance, equity = float(live["balance"]), float(live["equity"])
        margin = float(live.get("margin") or 0.0)
        free_margin = live.get("free_margin")
        open_count = len(open_trades(db, account.login))
        floating = equity - balance
        origin = "live"
    else:
        balance = float(account.balance or 0.0)
        equity = float(account.equity or 0.0)
        margin = float(account.margin or 0.0)
        free_margin = account.free_margin
        open_count = len(open_trades(db, account.login))
        floating = equity - balance
        origin = "last_sync"
    current = {
        "origin": origin,
        "balance": round(balance, 2), "equity": round(equity, 2),
        "floating": round(floating, 2), "margin": round(margin, 2),
        "free_margin": None if free_margin is None else round(float(free_margin), 2),
        "load_pct": _load_pct(margin, equity),
        "open_positions": open_count,
    }

    # ── Rapprochement avec le solde du courtier (comptes MT5) ──
    gap = None if manual else round(balance - timeline.expected_balance, 2)
    tolerance = max(RECONCILE_MIN_TOLERANCE, RECONCILE_RELATIVE_TOLERANCE * abs(balance))
    reconciliation = {
        "initial_balance": round(timeline.start_balance, 2),
        "expected_balance": round(timeline.expected_balance, 2),
        "broker_balance": None if manual else round(balance, 2),
        "gap": gap,
        "reconciled": True if manual else abs(gap) <= tolerance,
        "calibrated": bool(account.capital_calibrated) if account.capital_calibrated is not None else True,
    }

    # ── Séries de solde et d'équité ──
    balance_rows = _clip(timeline.rows(), date_from)
    balance_out = _serialise(balance_rows)

    snapshots = [] if manual else account_snapshots(db, account.login)
    snap_rows = [
        {"time": s.time, "equity": s.equity, "balance": s.balance, "margin": s.margin or 0.0,
         "load_pct": _load_pct(s.margin, s.equity), "open_positions": s.open_count, "kind": "snapshot"}
        for s in snapshots
    ]
    equity_rows = list(snap_rows)
    if live and not manual:
        equity_rows.append({
            "time": now, "equity": current["equity"], "balance": current["balance"],
            "margin": current["margin"], "load_pct": current["load_pct"],
            "open_positions": open_count, "kind": "live",
        })
    elif manual and floating:
        equity_rows.append({
            "time": now, "equity": current["equity"], "balance": current["balance"],
            "margin": current["margin"], "load_pct": current["load_pct"],
            "open_positions": open_count, "kind": "computed",
        })
    equity_rows = _clip(equity_rows, date_from)

    # ── Drawdown d'équité ──
    if len(equity_rows) >= 2:
        basis = "equity"
        dd_pct, dd_abs = neutral_drawdown([(r["time"], r["equity"]) for r in equity_rows], flows)
    else:
        basis = "balance"
        window = [(r["time"], r["balance"]) for r in balance_rows]
        dd_pct, dd_abs = neutral_drawdown(window, [f for f in flows if not date_from or f[0] >= date_from])

    # ── Charge du dépôt ──
    est_all, with_margin, without_margin = estimated_load_series(db, account, timeline, now)
    est = _clip(est_all, date_from)
    est_loads = [p for p in est if p["load_pct"] is not None]
    meas_loads = [r for r in equity_rows if r["kind"] != "computed" and r["load_pct"] is not None]
    max_est = max(est_loads, key=lambda p: p["load_pct"]) if est_loads else None
    max_meas = max(meas_loads, key=lambda p: p["load_pct"]) if meas_loads else None
    overall = None
    if max_est and max_meas:
        overall, source = (max_meas, "measured") if max_meas["load_pct"] >= max_est["load_pct"] else (max_est, "estimated")
    elif max_meas:
        overall, source = max_meas, "measured"
    elif max_est:
        overall, source = max_est, "estimated"
    else:
        source = None

    est_avg = _time_weighted_average(est)
    deposit_load = {
        "max_pct": overall["load_pct"] if overall else None,
        "max_time": _iso(overall["time"]) if overall else None,
        "max_source": source,
        "max_estimated_pct": max_est["load_pct"] if max_est else None,
        "max_measured_pct": max_meas["load_pct"] if max_meas else None,
        "average_pct": est_avg,
        "current_pct": current["load_pct"],
        "trades_with_margin": with_margin,
        "trades_without_margin": without_margin,
        "estimated_points": _serialise(downsample(est, "load_pct")),
        "measured_points": _serialise(downsample(meas_loads, "load_pct")),
    }

    return {
        "account": {"login": account.login, "mode": account.mode or "mt5",
                    "currency": account.currency, "leverage": account.leverage},
        "current": current,
        "reconciliation": reconciliation,
        "drawdown": {"basis": basis, "max_pct": dd_pct, "max_abs": dd_abs},
        "balance_points": downsample(balance_out, "balance"),
        "equity_points": _serialise(downsample(equity_rows, "equity")),
        "snapshot_count": len(snap_rows),
        "deposit_load": deposit_load,
    }
