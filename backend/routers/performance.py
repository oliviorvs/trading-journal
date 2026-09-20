from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import Optional, cast
from datetime import date, datetime, timedelta

from database import get_db
from models import Trade
from services import equity as equity_service
from services import movements as movement_service
from services import stats
from services.pdf_report import build_journal_pdf
import state

router = APIRouter(prefix="/api/performance", tags=["performance"])


def _profit(trade: Trade) -> float:
    """Valeur d'instance SQLAlchemy, pas l'attribut Column de la classe."""
    return cast(float, trade.profit)


@router.get("/stats")
def get_stats(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    symbol: Optional[str] = None,
    db: Session = Depends(get_db)
):

    q = db.query(Trade).filter(Trade.is_open.is_(False))
    q = state.filter_active(q, db)
    if date_from:
        q = q.filter(Trade.open_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(Trade.open_time <= datetime.combine(date_to, datetime.max.time()))
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    trades = q.order_by(Trade.open_time).all()
    # Filtré par symbole : les dépôts / retraits (qui n'ont pas de symbole)
    # ne sont pas pris en compte dans le drawdown.
    return stats.compute_stats(trades, db, include_movements=not symbol)


@router.get("/equity-curve")
def equity_curve(symbol: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None, db: Session = Depends(get_db)):
    """Retourne la courbe de capital jour par jour (trades clôturés uniquement).

    date_from/date_to ne restreignent que la fenêtre AFFICHÉE : le capital
    cumulé (equity) à chaque date reste calculé sur tout l'historique des
    trades, pour ne pas fausser le niveau de capital réel au début de la
    période choisie (ex. filtre "3 mois" sur le dashboard).

    Phase 6 — les dépôts / retraits du compte sont des marches de la courbe
    (champ `movement` de chaque point, 0 si aucun ce jour-là), mais ils sont
    NEUTRALISÉS dans le drawdown : le sommet est décalé du même montant, un
    retrait ne crée donc pas de drawdown et un dépôt n'en masque pas. Filtrée
    par symbole, la courbe ne porte que les trades de ce symbole (un dépôt
    n'appartient à aucun symbole : ignoré).
    """
    q = db.query(Trade).filter(Trade.is_open.is_(False))
    q = state.filter_active(q, db)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    trades = q.order_by(Trade.open_time).all()
    # Compte manuel : la courbe suit le capital NET (profit + commission +
    # swap), cohérent avec state.manual_capital. Comptes MT5 : inchangé.
    account = state.get_active_account(db)
    is_manual = state.is_manual(account)
    daily = {}
    for t in trades:
        # Correction : un trade marqué clôturé mais sans `close_time` (import
        # MT5 partiel, ligne héritée d'une ancienne version) faisait planter
        # toute la route en AttributeError sur None — donc un dashboard vide
        # avec une erreur 500, alors qu'une seule ligne était en cause. On
        # retombe sur `open_time`, toujours renseigné.
        reference = t.close_time or t.open_time
        if reference is None:
            continue
        day = reference.date().isoformat()
        daily[day] = daily.get(day, 0) + (stats.net_pnl(t) if is_manual else _profit(t))

    daily_flow: dict = {}
    if not symbol:
        for when, amount in movement_service.movement_events(db, account.login if account else None):
            day = when.date().isoformat()
            daily_flow[day] = daily_flow.get(day, 0.0) + amount

    all_days = set(daily) | set(daily_flow)
    starting_balance = state.mt5_reference_capital(db) or 0.0
    cumul = starting_balance
    peak = starting_balance
    has_peak = starting_balance != 0
    result = []
    if starting_balance and all_days:
        first_day = datetime.strptime(min(all_days), "%Y-%m-%d").date()
        result.append({
            "date": (first_day - timedelta(days=1)).isoformat(),
            "pnl": 0.0,
            "movement": 0.0,
            "equity": round(starting_balance, 2),
            "drawdown": 0.0,
        })
    for day in sorted(all_days):
        pnl = daily.get(day, 0)
        flow = daily_flow.get(day, 0.0)
        # Mouvement de capital : décale l'équité ET le sommet (drawdown neutre).
        cumul += flow
        if has_peak:
            peak += flow
        cumul += pnl
        if not has_peak or cumul > peak:
            peak = cumul
            has_peak = True
        # Même correction de drawdown que dans services/stats.max_drawdown() :
        # peak part du capital réel du compte MT5 (ou de la première valeur
        # d'équité si aucun compte n'est connecté) et on divise par
        # abs(peak) pour rester correct quand peak est négatif.
        dd_pct = round((cumul - peak) / abs(peak) * 100, 2) if peak != 0 else 0.0
        result.append({
            "date": day, "pnl": round(pnl, 2), "movement": round(flow, 2),
            "equity": round(cumul, 2), "drawdown": dd_pct,
        })

    if date_from:
        # Conserver le niveau d'équité juste avant la fenêtre évite une
        # courbe qui démarre artificiellement sur le premier trade affiché.
        # Le point d'ancrage est calculé sur la même série historique, puis
        # les jours réellement compris dans la fenêtre sont ajoutés.
        visible = [r for r in result if r["date"] >= date_from.isoformat()]
        before = [r for r in result if r["date"] < date_from.isoformat()]
        if before and visible:
            anchor = before[-1].copy()
            anchor["date"] = date_from.isoformat()
            anchor["pnl"] = 0.0
            anchor["movement"] = 0.0
            if visible[0]["date"] != anchor["date"]:
                visible.insert(0, anchor)
        result = visible
    if date_to:
        result = [r for r in result if r["date"] <= date_to.isoformat()]
    if not result and starting_balance:
        # Même sans trade dans la fenêtre, afficher le niveau de départ afin
        # que la courbe reste lisible et ne disparaisse pas complètement.
        anchor_date = date_from.isoformat() if date_from else date.today().isoformat()
        anchor = datetime.strptime(anchor_date, "%Y-%m-%d").date()
        initial_point = {"pnl": 0.0, "movement": 0.0, "equity": round(starting_balance, 2), "drawdown": 0.0}
        result = [
            {"date": (anchor - timedelta(days=1)).isoformat(), **initial_point},
            {"date": anchor_date, **initial_point},
        ]
    return result


@router.get("/real-equity")
def real_equity(date_from: Optional[date] = None, db: Session = Depends(get_db)):
    """Équité réelle et charge du dépôt du compte actif (phase 6).

    - `balance_points` : solde reconstruit (capital de départ + mouvements +
      P&L NET des trades clôturés), au fil des clôtures et des mouvements ;
    - `equity_points` : équité relevée (solde + flottant) — vide tant qu'aucun
      relevé n'existe (compte MT5 jamais synchronisé depuis la phase 6) ;
    - `reconciliation` : écart entre le solde reconstruit et celui du
      courtier (comptes MT5) — un écart signale un capital de référence à
      recaler ;
    - `deposit_load` : marge / équité, mesurée (relevés) et estimée (trades).
    Voir services/equity.py pour les définitions et leurs limites. Toutes les
    données sont celles du compte ACTIF, jamais d'un autre.
    """
    account = state.get_active_account(db)
    if not account:
        raise HTTPException(404, "Aucun compte actif")
    state.sync_manual_balance(db, account)   # no-op pour un compte MT5
    since = datetime.combine(date_from, datetime.min.time()) if date_from else None
    return equity_service.real_equity_report(
        db, account, live=state.live_account_info(account), date_from=since
    )


@router.get("/monthly")
def by_month(symbol: Optional[str] = None, db: Session = Depends(get_db)):

    q = db.query(Trade).filter(Trade.is_open.is_(False))
    q = state.filter_active(q, db)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    trades = q.order_by(Trade.open_time).all()
    months: dict = {}
    for t in trades:
        key = t.open_time.strftime("%Y-%m")
        profit = cast(float, t.profit)
        bucket = months.setdefault(key, {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0})
        bucket["pnl"] += profit
        if profit > 0:
            bucket["wins"] += 1
        elif profit < 0:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1
    return [
        {
            "month": key,
            "pnl": round(bucket["pnl"], 2),
            "trades": bucket["wins"] + bucket["losses"] + bucket["breakeven"],
            "wins": bucket["wins"],
            "losses": bucket["losses"],
            "breakeven": bucket["breakeven"],
            "win_rate": round(bucket["wins"] / (bucket["wins"] + bucket["losses"] + bucket["breakeven"]) * 100, 1),
        }
        for key, bucket in sorted(months.items())
    ]


@router.get("/by-week")
def by_week(symbol: Optional[str] = None, db: Session = Depends(get_db)):
    """P&L agrégé par semaine ISO (trades clôturés uniquement)."""
    q = db.query(Trade).filter(Trade.is_open.is_(False))
    q = state.filter_active(q, db)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    trades = q.order_by(Trade.open_time).all()
    weeks = {}
    for t in trades:
        iso = t.open_time.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        profit = cast(float, t.profit)
        bucket = weeks.setdefault(key, {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0})
        bucket["pnl"] += profit
        if profit > 0:
            bucket["wins"] += 1
        elif profit < 0:
            bucket["losses"] += 1
        else:
            bucket["breakeven"] += 1
    return [
        {
            "week": key,
            "pnl": round(bucket["pnl"], 2),
            "trades": bucket["wins"] + bucket["losses"] + bucket["breakeven"],
            "wins": bucket["wins"],
            "losses": bucket["losses"],
            "breakeven": bucket["breakeven"],
            "win_rate": round(bucket["wins"] / (bucket["wins"] + bucket["losses"] + bucket["breakeven"]) * 100, 1),
        }
        for key, bucket in sorted(weeks.items())
    ]


@router.get("/by-symbol")
def by_symbol(db: Session = Depends(get_db)):
    """P&L et win rate par symbole (trades clôturés uniquement)."""
    trades = state.filter_active(db.query(Trade).filter(Trade.is_open.is_(False)), db).all()
    syms = {}
    for t in trades:
        s = t.symbol
        profit = cast(float, t.profit)
        if s not in syms:
            syms[s] = {"symbol": s, "total": 0, "wins": 0, "count": 0}
        syms[s]["total"] += profit
        syms[s]["count"] += 1
        if profit > 0:
            syms[s]["wins"] += 1
    result = []
    for s, d in syms.items():
        result.append({
            "symbol": s,
            "pnl": round(d["total"], 2),
            "win_rate": round(d["wins"] / d["count"] * 100, 1),
            "trades": d["count"]
        })
    return sorted(result, key=lambda x: x["pnl"], reverse=True)


@router.get("/calendar")
def calendar(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    """Calendrier de performance : P&L, R-multiple cumulé, nombre de trades et
    win rate par jour (trades clôturés uniquement), plus un résumé du mois."""
    now = state.utcnow()
    year = year or now.year
    month = month or now.month
    if not 1 <= month <= 12 or not 1970 <= year <= 9999:
        raise HTTPException(422, "Année ou mois invalide")
    next_month = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    trades = state.filter_active(db.query(Trade).filter(
        Trade.is_open.is_(False),
        Trade.open_time >= datetime(year, month, 1),
        Trade.open_time < next_month,
    ), db).all()

    # Le R-multiple de chaque trade dépend du capital disponible au moment
    # où il a été ouvert (voir services/stats.r_multiple) : on reconstruit
    # la courbe de capital une seule fois plutôt que par trade.
    curve = stats.build_capital_curve(db)

    daily: dict = {}
    for t in trades:
        day = t.open_time.day
        bucket = daily.setdefault(day, {"pnl": 0.0, "r_values": [], "trades": 0, "wins": 0})
        profit = _profit(t)
        bucket["pnl"] += profit
        bucket["trades"] += 1
        if profit > 0:
            bucket["wins"] += 1
        r = stats.r_multiple(t, stats.capital_before(t.open_time, curve))
        if r is not None:
            bucket["r_values"].append(r)

    days = {}
    total_pnl = 0.0
    total_r = 0.0
    total_trades = 0
    for day, bucket in daily.items():
        r_sum = round(sum(bucket["r_values"]), 2) if bucket["r_values"] else None
        win_rate = round(bucket["wins"] / bucket["trades"] * 100) if bucket["trades"] else None
        days[str(day)] = {
            "pnl": round(bucket["pnl"], 2),
            "r_sum": r_sum,
            "trades": bucket["trades"],
            "win_rate": win_rate,
        }
        total_pnl += bucket["pnl"]
        total_r += r_sum or 0.0
        total_trades += bucket["trades"]

    return {
        "year": year,
        "month": month,
        "summary": {
            "pnl": round(total_pnl, 2),
            "r_sum": round(total_r, 2),
            "trades": total_trades,
        },
        "days": days,
    }


@router.get("/repartitions")
def repartitions(db: Session = Depends(get_db)):
    """Performance ventilée par symbole, sens, tag, playbook, sortie, durée,
    timeframe — plus un bulletin de discipline synthétique.

    Ne porte que sur les trades clôturés, comme le reste de /api/performance/*.
    """
    trades = state.filter_active(db.query(Trade).filter(Trade.is_open.is_(False)), db).order_by(Trade.open_time).all()
    curve = stats.build_capital_curve(db)
    total = len(trades)

    # Par symbole
    by_symbol_map: dict = {}
    for t in trades:
        by_symbol_map.setdefault(t.symbol, []).append(t)
    by_symbol = [
        {"symbol": s, **stats.group_stats(ts, curve, total)}
        for s, ts in sorted(by_symbol_map.items(), key=lambda kv: sum(_profit(x) for x in kv[1]), reverse=True)
    ]
    # group_stats renvoie avg_r/avg_risk, non utilisés par cette table
    for row in by_symbol:
        row.pop("avg_r", None)
        row.pop("avg_risk", None)

    # Long / Short
    long_short = {}
    for direction, label in (("buy", "long"), ("sell", "short")):
        dtrades = [t for t in trades if t.direction == direction]
        wins = [t for t in dtrades if _profit(t) > 0]
        losses = [t for t in dtrades if _profit(t) < 0]
        count = len(dtrades)
        pnl = sum(_profit(t) for t in dtrades)
        long_short[label] = {
            "trades": count,
            "win_rate": round(len(wins) / count * 100) if count else 0,
            "pnl": round(pnl, 2),
            "avg_pnl": round(pnl / count, 2) if count else 0.0,
            "avg_win": round(sum(_profit(t) for t in wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(_profit(t) for t in losses) / len(losses), 2) if losses else 0.0,
        }

    # Par tag / setup
    tag_map: dict = {}
    for t in trades:
        if t.setup_tag:
            tag_map.setdefault(t.setup_tag, []).append(t)
    by_tag = [
        {"tag": tag, **stats.group_stats(ts, curve, total)}
        for tag, ts in sorted(tag_map.items(), key=lambda kv: kv[0])
    ]

    # Par playbook / stratégie
    playbook_map: dict = {}
    for t in trades:
        if t.playbook:
            playbook_map.setdefault(t.playbook, []).append(t)
    by_playbook = [
        {"playbook": pb, **stats.group_stats(ts, curve, total)}
        for pb, ts in sorted(playbook_map.items(), key=lambda kv: kv[0])
    ]

    # Par raison de sortie (StopLoss / TakeProfit / Manuel / Breakeven), avec
    # la part (%) que chaque raison représente sur l'ensemble des sorties.
    #
    # Correction : les trades sans `exit_reason` étaient ignorés, et le
    # dénominateur du pourcentage ne comptait que les trades étiquetés. Sur
    # un journal importé avant la correction de `exit_reason_from_deals` (ou
    # rempli à la main), cela donnait un tableau vide ou des pourcentages
    # faux. Ces trades sont désormais regroupés sous "Non renseigné" : le
    # total fait 100 % des trades clôturés, et l'utilisateur voit
    # explicitement ce qui lui reste à qualifier.
    UNLABELLED = "Non renseigné"
    exit_map: dict = {}
    for t in trades:
        exit_map.setdefault(t.exit_reason or UNLABELLED, []).append(t)
    exit_total = sum(len(ts) for ts in exit_map.values())
    by_exit_reason = [
        {"reason": reason, **stats.group_stats(ts, curve, exit_total)}
        # "Non renseigné" en dernier : c'est un reliquat, pas une catégorie.
        for reason, ts in sorted(exit_map.items(), key=lambda kv: (kv[0] == UNLABELLED, kv[0]))
    ]

    # Par durée de détention (ordre fixe, y compris les buckets vides)
    duration_order = ["<5min", "5-15min", "15-60min", "1-4h", ">4h"]
    duration_map: dict = {b: [] for b in duration_order}
    for t in trades:
        b = stats.duration_bucket(t)
        if b:
            duration_map[b].append(t)
    duration_total = sum(len(ts) for ts in duration_map.values())
    by_duration = [{"duration": b, **stats.group_stats(duration_map[b], curve, duration_total)} for b in duration_order]

    # Par timeframe d'entrée
    tf_map: dict = {}
    for t in trades:
        if t.entry_timeframe:
            tf_map.setdefault(t.entry_timeframe, []).append(t)
    by_timeframe = [
        {"timeframe": tf, **stats.group_stats(ts, curve, total)}
        for tf, ts in sorted(tf_map.items(), key=lambda kv: kv[0])
    ]

    # Discipline · bulletin
    discipline = stats.discipline_bulletin(trades, curve)

    return {
        "by_symbol": by_symbol,
        "long_short": long_short,
        "by_tag": by_tag,
        "by_playbook": by_playbook,
        "by_exit_reason": by_exit_reason,
        "by_duration": by_duration,
        "by_timeframe": by_timeframe,
        "discipline": discipline,
    }


@router.get("/extended-stats")
def extended_stats(db: Session = Depends(get_db)):
    """Distribution des R-multiples, distribution du risque, carte de chaleur
    horaire et performance glissante — affichées sous le calendrier.
    """
    trades = state.filter_active(db.query(Trade).filter(Trade.is_open.is_(False)), db).order_by(Trade.open_time).all()
    curve = stats.build_capital_curve(db)

    # Distribution des R-multiples (calculés avec le capital du moment de
    # chaque trade — voir services/stats.py), avec la part (%) que chaque
    # tranche représente sur les trades où un R est calculable (SL renseigné
    # ou risque saisi manuellement).
    r_buckets = [("<-2R", None, -2), ("-2..-1R", -2, -1), ("-1..0R", -1, 0),
                 ("0..1R", 0, 1), ("1..2R", 1, 2), ("2..3R", 2, 3), (">3R", 3, None)]
    r_counts = {label: 0 for label, _, _ in r_buckets}
    r_total = 0
    for t in trades:
        r = stats.r_multiple(t, stats.capital_before(t.open_time, curve))
        if r is None:
            continue
        r_total += 1
        for label, lo, hi in r_buckets:
            if (lo is None or r >= lo) and (hi is None or r < hi):
                r_counts[label] += 1
                break
    r_histogram = [
        {"bucket": label, "count": r_counts[label],
         "pct": round(r_counts[label] / r_total * 100, 1) if r_total else 0.0}
        for label, _, _ in r_buckets
    ]

    # Distribution du risque par trade (%) — risque effectif (manuel si
    # saisi, sinon estimé automatiquement depuis le SL et le capital du
    # compte au moment de l'ouverture de chaque trade).
    risk_buckets = [("<0.25%", None, 0.25), ("0.25-0.5%", 0.25, 0.5), ("0.5-1%", 0.5, 1),
                    ("1-2%", 1, 2), ("2-3%", 2, 3), (">3%", 3, None)]
    risk_counts = {label: 0 for label, _, _ in risk_buckets}
    risk_total = 0
    for t in trades:
        risk_pct, _source = stats.effective_risk(t, stats.capital_before(t.open_time, curve))
        if risk_pct is None:
            continue
        risk_total += 1
        for label, lo, hi in risk_buckets:
            if (lo is None or risk_pct >= lo) and (hi is None or risk_pct < hi):
                risk_counts[label] += 1
                break
    risk_distribution = [
        {"bucket": label, "count": risk_counts[label],
         "pct": round(risk_counts[label] / risk_total * 100, 1) if risk_total else 0.0}
        for label, _, _ in risk_buckets
    ]

    # Carte de chaleur horaire (jour x heure), P&L cumulé
    days_order = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
    hours = list(range(24))
    heat: dict = {(d, h): 0.0 for d in days_order for h in hours}
    for t in trades:
        weekday = days_order[t.open_time.weekday()]
        heat[(weekday, t.open_time.hour)] += _profit(t)
    heatmap = [
        {"day": d, "hour": f"{h:02d}", "pnl": round(heat[(d, h)], 2)}
        for d in days_order for h in hours
    ]

    # Performance glissante : taux de réussite glissant (fenêtre 10) & R cumulé
    rolling = []
    r_cum = 0.0
    window: list = []
    for i, t in enumerate(trades, start=1):
        window.append(1.0 if _profit(t) > 0 else 0.0)
        if len(window) > 10:
            window.pop(0)
        wr = round(sum(window) / len(window) * 100, 1)
        r = stats.r_multiple(t, stats.capital_before(t.open_time, curve))
        if r is not None:
            r_cum += r
        rolling.append({"index": i, "win_rate": wr, "r_cumulative": round(r_cum, 2)})

    return {
        "r_histogram": r_histogram,
        "risk_distribution": risk_distribution,
        "heatmap": heatmap,
        "rolling": rolling,
    }


export_router = APIRouter(prefix="/api/export", tags=["export"])


@export_router.get("/pdf")
def export_pdf(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    result: Optional[str] = Query(None, pattern="^(win|loss|breakeven)$"),
    source: Optional[str] = Query(None, pattern="^(mt5|manual|import)$"),
    db: Session = Depends(get_db),
):

    q = db.query(Trade).filter(Trade.is_open.is_(False))
    q = state.filter_active(q, db)
    if date_from:
        q = q.filter(Trade.open_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(Trade.open_time <= datetime.combine(date_to, datetime.max.time()))
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    if direction:
        q = q.filter(Trade.direction == direction.lower())
    if source:
        q = q.filter(Trade.source == source)
    if result == "win":
        q = q.filter(Trade.profit > 0)
    elif result == "loss":
        q = q.filter(Trade.profit < 0)
    elif result == "breakeven":
        q = q.filter(Trade.profit == 0)
    trades = q.order_by(Trade.open_time).all()
    stats.attach_computed_fields(trades, db)

    account = state.get_active_account(db)
    currency = state.get_settings(db).currency
    ref_capital = state.mt5_reference_capital(db) or 0.0

    # Dépôts / retraits : neutralisés dans le drawdown du rapport, seulement
    # quand aucun filtre ne restreint les trades (un dépôt n'a ni symbole,
    # ni sens, ni source de trade), et dans la fenêtre de dates demandée.
    flows = None
    if account and not (symbol or direction or result or source):
        lo = datetime.combine(date_from, datetime.min.time()) if date_from else None
        hi = datetime.combine(date_to, datetime.max.time()) if date_to else None
        flows = [
            (when, amount)
            for when, amount in movement_service.movement_events(db, account.login)
            if (lo is None or when >= lo) and (hi is None or when <= hi)
        ]

    pdf_bytes = build_journal_pdf(
        trades, account, date_from, date_to, currency, ref_capital,
        net_capital=state.is_manual(account), movements=flows,
    )

    if date_from and date_to:
        period_label = f"{date_from.isoformat()}_au_{date_to.isoformat()}"
    elif date_from:
        period_label = f"depuis_{date_from.isoformat()}"
    elif date_to:
        period_label = f"jusqu_au_{date_to.isoformat()}"
    else:
        period_label = "complet"
    filename = f"journal_trading_{period_label}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
