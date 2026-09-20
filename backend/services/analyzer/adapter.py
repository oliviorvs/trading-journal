"""`JournalDataAdapter` — la SEULE porte d'entrée vers les données du journal.

Règle R7 du cahier : aucun analyseur ne requête `Trade` ni ne voit la session
SQLAlchemy. Ils reçoivent une liste de `TradeView` immuables.

Pourquoi (§14 du cahier, mesuré) : à 100 000 trades, chaque endpoint actuel
relit les trades ET reconstruit la courbe de capital — 2,9 s pour
`/performance/stats`, 7,3 s pour `/performance/repartitions`. Un écran
Analyzer bâti de la même façon coûterait 5 à 10 s. Ici, UNE lecture et UN
calcul de `capital_before` / R servent TOUTES les analyses d'un écran.

Mémorisation (décision D4) : en MÉMOIRE, indexée par (compte, révision,
filtres). La révision est un compteur incrémenté par des écouteurs SQLAlchemy
posés PAR CE MODULE sur `Trade` et les tables `trade_*` — aucun router
existant n'est modifié. Le cahier v1 proposait une clé de cache fondée sur la
« date de modification des trades » : elle n'est pas calculable ici, `Trade`
n'ayant que `created_at`.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import event
from sqlalchemy.orm import Session

from models import Account, CapitalMovement, Trade
from services import stats
from services.analyzer import config as analyzer_config
from services.analyzer import sessions as sessions_mod
from services.analyzer.models import (
    AnalyzerSetting, AnalyzerSopItem, AnalyzerSopVersion, AnalyzerSymbolMap,
    TradeEmotion, TradeError, TradeSopResult, TradeTag,
)
import state

# ── Compteur de révision ────────────────────────────────────────────────────

_revision = 0
_revision_lock = Lock()


def _bump(*_args) -> None:
    global _revision
    with _revision_lock:
        _revision += 1


def current_revision() -> int:
    return _revision


def _register_listeners() -> None:
    """Écoute les écritures sur les tables qui alimentent une analyse.

    Volontairement posé ici et pas dans les routers : ajouter un écouteur est
    additif et réversible (retirer l'import du module suffit), modifier huit
    routers ne l'est pas. Effet de bord accepté : toute écriture invalide le
    cache de TOUS les comptes — c'est un compteur global, pas par compte.
    Recharger coûte ~0,2 s à 100 000 trades, et les écritures sont rares.
    """
    # Les tables de CONFIGURATION comptent autant que les données : changer
    # une fenêtre de session ou une règle de normalisation change le contenu
    # de chaque `TradeView`. Sans elles, l'utilisateur modifiait un réglage et
    # voyait l'écran inchangé — le cache lui resservant la version d'avant.
    # `CapitalMovement` et `Account` ont été AJOUTÉS à cette liste. Chaque
    # `TradeView` porte le capital disponible à l'ouverture du trade, donc son
    # risque en % et son R-multiple — trois valeurs que `stats.build_capital_curve`
    # dérive du capital de départ du compte ET de ses dépôts / retraits. Sans
    # ces deux tables, enregistrer un dépôt ou recalibrer le capital de
    # référence laissait l'Analyzer resservir les anciens R-multiples jusqu'à
    # la prochaine écriture sur une autre table — c'est-à-dire potentiellement
    # jamais, sur un compte qu'on ne fait que consulter.
    for model in (Trade, CapitalMovement, Account,
                  TradeEmotion, TradeError, TradeSopResult, TradeTag,
                  AnalyzerSetting, AnalyzerSymbolMap, AnalyzerSopItem, AnalyzerSopVersion):
        for hook in ("after_insert", "after_update", "after_delete"):
            event.listen(model, hook, _bump)
    # Un import de lot ou une suppression de compte passent par des DELETE en
    # masse (`query(...).delete()`), qui NE déclenchent PAS les événements
    # ORM ci-dessus. `after_bulk_delete` couvre ce chemin.
    event.listen(Session, "after_bulk_delete", _bump)
    event.listen(Session, "after_bulk_update", _bump)


_register_listeners()


# ── Vue immuable d'un trade ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TradeView:
    """Tout ce dont un analyseur a besoin, déjà calculé. Immuable : deux
    analyses ne peuvent pas se marcher dessus."""
    account_id: int
    ticket: int
    source: str
    symbol_raw: str
    symbol: str                      # symbole canonique (table de normalisation)
    direction: Optional[str]
    open_time: datetime
    close_time: Optional[datetime]
    duration_minutes: Optional[float]
    profit: float                    # BRUT
    net: float                       # profit + commission + swap
    r: Optional[float]
    risk_percent: Optional[float]
    risk_source: Optional[str]       # "manuel" | "auto" | None
    capital_before: Optional[float]
    setup: Optional[str]
    playbook: Optional[str]
    timeframe: Optional[str]
    exit_reason: Optional[str]
    has_sl: bool
    session: str
    day: date
    weekday: int
    hour: int
    rank_in_day: int                 # 1, 2, 3… (ordre d'ouverture du jour)
    day_pnl_before: float            # cumul du jour AVANT ce trade (overtrading)
    after_loss: bool                 # le trade précédent du jour est-il perdant ?
    sop_score: Optional[float]       # 0..1 sur les éléments REQUIS ; None si non renseigné
    sop_version_id: Optional[int]
    emotions: Tuple[str, ...]
    errors: Tuple[str, ...]
    tags: Tuple[str, ...]

    def value(self, base: str = "gross") -> float:
        """Résultat $ selon la base demandée (décision D1)."""
        return self.net if base == "net" else self.profit

    @property
    def is_win(self) -> bool:
        # Classification TOUJOURS sur le brut — voir docs/definitions-metriques.md.
        return self.profit > 0

    @property
    def is_loss(self) -> bool:
        return self.profit < 0


@dataclass(frozen=True, slots=True)
class TradeSnapshot:
    """Copie figée des seuls champs que lit `stats.compute_stats`.

    Pourquoi ne PAS garder les objets `Trade` : le Dataset est mémorisé entre
    deux requêtes, or une instance SQLAlchemy dont la session est fermée est
    « détachée » — le premier accès à un attribut expiré lève
    `DetachedInstanceError`. Le symptôme n'apparaît qu'au SECOND appel, donc
    seulement en usage réel. Cet instantané supprime la classe de bug entière.

    On reste en canard typé avec `Trade` : `compute_stats` et `net_pnl`
    continuent de fonctionner sans modification, donc la parité du §6 est
    préservée — c'est bien la MÊME fonction du journal qui calcule.
    """
    ticket: int
    symbol: Optional[str]
    profit: float
    commission: float
    swap: float
    volume: float
    open_time: datetime
    close_time: Optional[datetime]

    @classmethod
    def of(cls, trade) -> "TradeSnapshot":
        return cls(
            ticket=trade.ticket,
            symbol=trade.symbol,
            profit=trade.profit or 0.0,
            commission=trade.commission or 0.0,
            swap=trade.swap or 0.0,
            volume=trade.volume or 0.0,
            open_time=trade.open_time,
            close_time=trade.close_time,
        )


@dataclass(frozen=True, slots=True)
class Dataset:
    """Résultat d'une lecture : les vues pour les analyseurs, et l'instantané
    des trades UNIQUEMENT pour `stats.compute_stats` (exigence de parité du
    §6 — on réutilise la fonction du journal telle quelle plutôt que d'en
    réécrire une version « équivalente »)."""
    views: Tuple[TradeView, ...]
    trades: Tuple[TradeSnapshot, ...]
    account_id: Optional[int]
    is_manual: bool
    filters: dict
    session_windows: Tuple[dict, ...]


# ── Filtres ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Filters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    symbol: Optional[str] = None
    source: Optional[str] = None

    def cache_key(self) -> tuple:
        return (self.date_from, self.date_to, self.symbol, self.source)

    def as_dict(self) -> dict:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "symbol": self.symbol,
            "source": self.source,
        }


# ── Cache mémoire ───────────────────────────────────────────────────────────

_CACHE_MAX = 8
_cache: "OrderedDict[tuple, Dataset]" = OrderedDict()
_cache_lock = Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


class JournalDataAdapter:
    """Lecture unique. `load()` est la seule méthode publique."""

    @staticmethod
    def load(db: Session, filters: Optional[Filters] = None) -> Dataset:
        filters = filters or Filters()
        account = state.get_active_account(db)
        account_id = account.login if account else None
        key = (account_id, current_revision(), filters.cache_key())
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                _cache.move_to_end(key)
                return cached
        dataset = _build(db, filters, account)
        with _cache_lock:
            _cache[key] = dataset
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
        return dataset


def _build(db: Session, filters: Filters, account) -> Dataset:
    account_id = account.login if account else None

    # Entités `Trade` complètes, et non une sélection de colonnes.
    # Mesuré à 100 000 trades : la sélection de colonnes construit les lignes
    # plus vite (0,6 s contre 2,1 s) mais l'accès aux attributs d'un `Row`
    # SQLAlchemy passe par une résolution de nom — sur ~2 millions d'accès,
    # l'adaptateur complet passait de 5,3 s à 7,4 s. L'optimisation a donc été
    # ANNULÉE : ne pas la retenter sans mesurer.
    query = db.query(Trade).filter(Trade.is_open.is_(False))
    query = state.filter_active(query, db)
    if filters.date_from:
        query = query.filter(Trade.open_time >= datetime.combine(filters.date_from, datetime.min.time()))
    if filters.date_to:
        query = query.filter(Trade.open_time <= datetime.combine(filters.date_to, datetime.max.time()))
    if filters.symbol:
        query = query.filter(Trade.symbol == filters.symbol)
    if filters.source:
        query = query.filter(Trade.source == filters.source)
    trades = query.order_by(Trade.open_time).all()

    # Un trade marqué clôturé mais sans `open_time` (ligne héritée d'un import
    # partiel) ferait tomber toutes les analyses temporelles : on l'écarte ici
    # plutôt que de semer des gardes dans chaque analyseur. Il reste compté
    # par « Qualité des données ».
    trades = [t for t in trades if t.open_time is not None]

    # UN SEUL calcul de la courbe de capital pour tous les trades (§7).
    curve = stats.build_capital_curve(db)
    windows = analyzer_config.get_setting(db, "sessions", account_id) or []
    symbol_map = _load_symbol_map(db, account_id)
    emotions_by_ticket = _load_links(db, TradeEmotion, TradeEmotion.emotion_key, account_id)
    errors_by_ticket = _load_links(db, TradeError, TradeError.error_key, account_id)
    tags_by_ticket = _load_links(db, TradeTag, TradeTag.tag, account_id)
    sop_by_ticket = _load_sop_scores(db, account_id)

    # Rang dans le jour + cumul du jour avant le trade : la liste est déjà
    # triée par `open_time`, un seul passage suffit.
    per_day_count: Dict[date, int] = {}
    per_day_pnl: Dict[date, float] = {}
    per_day_last_loss: Dict[date, bool] = {}

    views: List[TradeView] = []
    for trade in trades:
        day = trade.open_time.date()
        rank = per_day_count.get(day, 0) + 1
        per_day_count[day] = rank
        pnl_before = per_day_pnl.get(day, 0.0)
        after_loss = per_day_last_loss.get(day, False)

        capital = stats.capital_before(trade.open_time, curve)
        risk_pct, risk_src = stats.effective_risk(trade, capital)
        r_value = stats.r_multiple(trade, capital)
        profit = trade.profit or 0.0
        duration = None
        if trade.close_time is not None:
            duration = (trade.close_time - trade.open_time).total_seconds() / 60
        score, version_id = sop_by_ticket.get(trade.ticket, (None, None))
        raw_symbol = trade.symbol or ""

        views.append(TradeView(
            account_id=trade.account_id,
            ticket=trade.ticket,
            source=trade.source or "mt5",
            symbol_raw=raw_symbol,
            symbol=symbol_map.get(raw_symbol.upper(), raw_symbol),
            direction=trade.direction,
            open_time=trade.open_time,
            close_time=trade.close_time,
            duration_minutes=duration,
            profit=profit,
            net=stats.net_pnl(trade),
            r=round(r_value, 4) if r_value is not None else None,
            risk_percent=risk_pct,
            risk_source=risk_src,
            capital_before=capital,
            setup=trade.setup_tag,
            playbook=trade.playbook,
            timeframe=trade.entry_timeframe,
            exit_reason=trade.exit_reason,
            has_sl=bool(trade.initial_sl or trade.sl),
            session=sessions_mod.session_of(trade.open_time, windows),
            day=day,
            weekday=trade.open_time.weekday(),
            hour=trade.open_time.hour,
            rank_in_day=rank,
            day_pnl_before=round(pnl_before, 2),
            after_loss=after_loss,
            sop_score=score,
            sop_version_id=version_id,
            # COMPATIBILITÉ ASCENDANTE (§7 du cahier) : tant qu'aucune ligne
            # n'existe dans les tables de liaison, la colonne historique fait
            # foi. C'est ce qui rend les analyses Émotions et Erreurs
            # immédiatement utiles, sans aucune nouvelle saisie.
            emotions=emotions_by_ticket.get(trade.ticket) or _single(trade.emotion),
            errors=errors_by_ticket.get(trade.ticket) or _single(trade.error_tag),
            tags=tags_by_ticket.get(trade.ticket) or (),
        ))

        per_day_pnl[day] = pnl_before + profit
        per_day_last_loss[day] = profit < 0

    return Dataset(
        views=tuple(views),
        trades=tuple(TradeSnapshot.of(t) for t in trades),
        account_id=account_id,
        is_manual=state.is_manual(account),
        filters=filters.as_dict(),
        session_windows=tuple(windows),
    )


def _single(value: Optional[str]) -> Tuple[str, ...]:
    return (value,) if value else ()


def _load_symbol_map(db: Session, account_id: Optional[int]) -> Dict[str, str]:
    """Règles du compte, puis règles globales — la règle du compte gagne."""
    rows = (
        db.query(AnalyzerSymbolMap)
        .filter(
            (AnalyzerSymbolMap.account_id == account_id)
            | (AnalyzerSymbolMap.account_id.is_(None))
        )
        .all()
    )
    # Clés en MAJUSCULES : le journal normalise `Trade.symbol` ainsi à la
    # création. Comparer tel quel laisserait une règle saisie « xauusd.a »
    # sans effet, sans le moindre message — le pire mode d'échec pour un
    # réglage.
    mapping: Dict[str, str] = {}
    for row in sorted(rows, key=lambda r: (r.account_id is not None)):
        mapping[row.raw_symbol.upper()] = row.canonical_symbol
    return mapping


def _load_links(db: Session, model, column, account_id: Optional[int]) -> Dict[int, Tuple[str, ...]]:
    if account_id is None:
        return {}
    rows = db.query(model.ticket, column).filter(model.account_id == account_id).all()
    grouped: Dict[int, List[str]] = {}
    for ticket, value in rows:
        if value:
            grouped.setdefault(ticket, []).append(value)
    return {ticket: tuple(sorted(values)) for ticket, values in grouped.items()}


def _load_sop_scores(db: Session, account_id: Optional[int]) -> Dict[int, Tuple[Optional[float], Optional[int]]]:
    """Score SOP par ticket : éléments REQUIS cochés ÷ éléments requis de LA
    VERSION utilisée par ce trade. Un trade sans aucune ligne n'apparaît pas
    dans le dictionnaire → score None → exclu des comparaisons (§9.1)."""
    if account_id is None:
        return {}
    results = (
        db.query(TradeSopResult)
        .filter(TradeSopResult.account_id == account_id)
        .all()
    )
    if not results:
        return {}
    version_ids = {r.sop_version_id for r in results}
    items = (
        db.query(AnalyzerSopItem)
        .filter(AnalyzerSopItem.sop_version_id.in_(version_ids))
        .all()
    )
    required_by_version: Dict[int, set] = {}
    for item in items:
        if item.required:
            required_by_version.setdefault(item.sop_version_id, set()).add(item.id)

    per_ticket: Dict[int, Dict[int, set]] = {}
    version_of: Dict[int, int] = {}
    for row in results:
        version_of[row.ticket] = row.sop_version_id
        if row.checked:
            per_ticket.setdefault(row.ticket, {}).setdefault(row.sop_version_id, set()).add(row.item_id)

    scores: Dict[int, Tuple[Optional[float], Optional[int]]] = {}
    for ticket, version_id in version_of.items():
        required = required_by_version.get(version_id, set())
        if not required:
            # Version sans élément requis : le score n'a pas de sens, mais le
            # trade est bien « renseigné ». On le marque conforme (1.0).
            scores[ticket] = (1.0, version_id)
            continue
        checked = per_ticket.get(ticket, {}).get(version_id, set())
        scores[ticket] = (len(checked & required) / len(required), version_id)
    return scores


def active_sop_version(db: Session, account_id: Optional[int]) -> Optional[AnalyzerSopVersion]:
    if account_id is None:
        return None
    return (
        db.query(AnalyzerSopVersion)
        .filter(AnalyzerSopVersion.account_id == account_id, AnalyzerSopVersion.active.is_(True))
        .order_by(AnalyzerSopVersion.id.desc())
        .first()
    )
