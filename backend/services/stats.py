from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from datetime import datetime
import bisect

from models import Trade
from services import movements
import state


def net_pnl(trade: Trade) -> float:
    """P&L net d'un trade : profit + commission + swap (NULL traités comme 0)."""
    return (trade.profit or 0.0) + (trade.commission or 0.0) + (trade.swap or 0.0)


def max_drawdown(
    trades: List[Trade],
    starting_balance: float = 0.0,
    net: bool = False,
    movements: Optional[List[Tuple[datetime, float]]] = None,
) -> Tuple[float, float]:
    """`net=True` (comptes manuels) : l'équité intègre commission et swap,
    comme le capital du compte (voir state.manual_capital). `net=False` :
    comportement d'origine (comptes MT5).

    `movements` (phase 6) : dépôts / retraits `[(heure, montant signé)]`
    triés par heure. Un mouvement décale l'équité ET le sommet du même
    montant : il ne peut donc ni créer de drawdown (retrait) ni en masquer
    un (dépôt) — le drawdown ne mesure que la performance de trading. Chaque
    mouvement est appliqué juste avant le premier trade dont l'heure de
    clôture (à défaut d'ouverture) est postérieure ; l'ordre des trades
    reste celui fourni par l'appelant. Sans mouvement, calcul strictement
    inchangé.
    """

    equity = starting_balance
    peak = starting_balance
    has_peak = starting_balance != 0
    max_dd_pct = 0.0
    max_dd_abs = 0.0
    pending = list(movements or [])
    next_mv = 0
    for t in trades:
        if pending:
            when = t.close_time or t.open_time
            while next_mv < len(pending) and when is not None and pending[next_mv][0] <= when:
                flow = pending[next_mv][1]
                equity += flow
                if has_peak:
                    peak += flow
                next_mv += 1
        equity += net_pnl(t) if net else t.profit
        if not has_peak or equity > peak:
            peak = equity
            has_peak = True
        dd_abs = equity - peak
        dd_pct = (dd_abs / abs(peak) * 100) if peak != 0 else 0.0
        if dd_pct < max_dd_pct:
            max_dd_pct = dd_pct
        if dd_abs < max_dd_abs:
            max_dd_abs = dd_abs
    return round(max_dd_pct, 2), round(max_dd_abs, 2)


# ── Capital au moment du trade & risque effectif ────────────────────────────
#
# Avant : le risque en % était uniquement saisi manuellement (`risk_percent`)
# et le R-multiple appliquait ce % au capital ACTUEL du compte pour tous les
# trades, quelle que soit leur date — faussant le calcul dès que le capital
# avait varié depuis. Cette section calcule désormais :
#   1. le capital réellement disponible au moment où CHAQUE trade a été
#      ouvert (solde MT5 actuel moins les P&L réalisés après cet instant) ;
#   2. un risque en % estimé automatiquement à partir de la distance au
#      Stop Loss, quand il n'a pas été saisi manuellement.

def build_capital_curve(db: Session) -> Tuple[float, List[datetime], List[float]]:
    """Courbe du capital du compte actif : (capital de départ, heures,
    capital après chaque événement). Un événement est la clôture d'un trade
    OU un mouvement de capital (dépôt / retrait, phase 6) : `capital_before`
    voit donc un capital qui intègre les dépôts et retraits déjà passés."""

    account = state.get_active_account(db)
    closed = state.filter_active(db.query(Trade).filter(Trade.is_open.is_(False)), db).all()
    closed = sorted((t for t in closed if t.close_time), key=lambda t: t.close_time)
    flows = movements.movement_events(db, account.login if account else None)
    is_manual = state.is_manual(account)

    if is_manual:
        # Compte manuel : capital = capital de départ + mouvements + Σ P&L
        # NET clôturés (voir state.manual_capital). Jamais le solde MT5 en
        # direct : MT5 peut être connecté sur un AUTRE compte pendant qu'un
        # compte manuel est actif.
        starting_balance = account.initial_balance or 0.0
        value = net_pnl
    else:
        # Comptes MT5 : même base NETTE que ci-dessus, voir le bloc suivant.
        # Solde en direct UNIQUEMENT s'il vient de CE compte : le terminal MT5
        # n'a qu'une session, ouverte peut-être sur un autre compte (ou en
        # simulation, où le solde affiché est fictif) — ses chiffres ne
        # doivent jamais servir de capital à un autre compte du journal.
        live_info = state.live_account_info(account) or {}
        current_balance = live_info.get("balance") if live_info else None
        if current_balance is None:
            current_balance = account.balance if account and account.balance is not None else 0.0
        # Capital de départ reconstruit à rebours depuis le solde broker :
        # on retire les P&L clôturés ET les mouvements enregistrés (sans quoi
        # un dépôt serait pris pour un gain de trading).
        #
        # CORRECTION — le P&L retiré ici (et cumulé plus bas) est le P&L NET,
        # plus le profit brut. Le solde du courtier intègre les commissions et
        # les swaps ; les retrancher en brut laissait le capital de départ
        # décalé d'exactement Σ(commission + swap) sur tout l'historique — un
        # décalage qui grandit avec le nombre de trades et qui se propageait à
        # `capital_before`, donc au risque en % estimé et au R-multiple de
        # CHAQUE trade. Les deux autres endroits qui refont ce calcul
        # (mt5_service._calibrate_reference_capital et la route
        # /api/mt5/accounts/{login}/recalibrate) utilisaient déjà le net :
        # cette fonction était la seule à en diverger, et c'est elle qui
        # alimente les colonnes « Risque » et « R » du tableau des trades.
        starting_balance = current_balance - sum(net_pnl(t) for t in closed) - sum(a for _t, a in flows)
        value = net_pnl

    events: List[Tuple[datetime, float]] = [(t.close_time, value(t)) for t in closed] + list(flows)
    # Tri stable par heure : à heure égale, les trades (ajoutés d'abord)
    # précèdent les mouvements.
    events.sort(key=lambda e: e[0])
    close_times: List[datetime] = []
    cumulative: List[float] = []
    running = starting_balance
    for when, delta in events:
        running += delta
        close_times.append(when)
        cumulative.append(running)
    return starting_balance, close_times, cumulative


def capital_before(open_time: datetime, curve: Tuple[float, List[datetime], List[float]]) -> float:
    """Capital du compte juste avant l'ouverture d'un trade à `open_time` :
    solde reconstruit à partir du solde MT5 actuel et des P&L déjà clôturés
    à cet instant. Recherche par dichotomie dans la courbe pré-triée (voir
    `build_capital_curve`)."""
    initial, close_times, cumulative = curve
    idx = bisect.bisect_right(close_times, open_time) - 1
    return cumulative[idx] if idx >= 0 else initial


def auto_risk_amount(trade: Trade) -> Optional[float]:

    risk_sl = getattr(trade, "initial_sl", None) or trade.sl
    if not risk_sl:
        if trade.exit_reason == "StopLoss" and trade.profit < 0:
            return abs(trade.profit)
        return None
    if not trade.open_price or not trade.close_price:
        return None
    # Note : cette estimation n'a PAS besoin de la taille du pip.
    # L'ancien code multipliait les deux distances par un `pip_factor`
    # (faux pour l'or, les indices et la crypto — voir services/pips.py),
    # mais ce facteur apparaissait au numérateur ET au dénominateur :
    #     (dist_SL x f) x (|profit| / (mouvement x f)) = dist_SL / mouvement x |profit|
    # Il se simplifiait donc intégralement. Le retirer supprime une source
    # d'erreur sans changer d'un centime le résultat, et évite de propager
    # ici une dépendance au terminal MT5 (cette fonction tourne aussi sur des
    # trades historiques, hors connexion).
    sl_distance = abs(trade.open_price - risk_sl)
    if not sl_distance:
        return None
    price_move = abs(trade.close_price - trade.open_price)
    if not price_move:
        return None
    return sl_distance / price_move * abs(trade.profit)


def effective_risk(trade: Trade, capital_before_value: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    """Risque en % effectif d'un trade, avec sa source :
    - ("manuel", valeur) si l'utilisateur l'a saisi lui-même sur le trade
      (prioritaire — il peut connaître un risque réel différent du SL
      littéral : plusieurs ordres, stop mental, sizing ajusté...) ;
    - ("auto", valeur) sinon, estimé à partir du SL et du capital du compte
      AU MOMENT de l'ouverture du trade (pas le capital actuel) ;
    - (None, None) si ni l'un ni l'autre n'est calculable (pas de SL, pas
      de capital de référence connu...).
    """
    if trade.risk_percent is not None:
        return round(trade.risk_percent, 2), "manuel"
    amount = auto_risk_amount(trade)
    if amount is None or not capital_before_value:
        return None, None
    return round(amount / abs(capital_before_value) * 100, 2), "auto"


def r_multiple(trade: Trade, capital_before_value: Optional[float]) -> Optional[float]:
    """Calcule le R-multiple avec le capital disponible à l'ouverture.

    Le montant risqué est `capital_before * risque_percent`. Les prix MT5
    restent un repli uniquement si le capital ou le risque en pourcentage
    n'est pas disponible.
    """
    risk_pct, _source = effective_risk(trade, capital_before_value)
    if risk_pct is not None and capital_before_value:
        risk_amount = abs(capital_before_value) * (risk_pct / 100)
        if risk_amount:
            return trade.profit / risk_amount

    initial_sl = getattr(trade, "initial_sl", None)
    risk_sl = initial_sl or trade.sl
    if risk_sl and trade.open_price and trade.close_price:
        risk_distance = abs(trade.open_price - risk_sl)
        if risk_distance:
            if trade.exit_reason == "StopLoss" and not initial_sl:
                return -1.0
            if trade.exit_reason == "TakeProfit" and trade.tp:
                reward_distance = abs(trade.tp - trade.open_price)
                return reward_distance / risk_distance if reward_distance else None
            price_move = (
                trade.close_price - trade.open_price
                if trade.direction == "buy"
                else trade.open_price - trade.close_price
            )
            return price_move / risk_distance

    # MT5 identifie explicitement les sorties par Stop Loss. Même si le
    # broker applique un léger slippage ou des frais, ce résultat représente
    # une perte du risque initial, donc exactement -1R.
    if trade.exit_reason == "StopLoss":
        return -1.0
    if trade.profit < 0 and risk_sl and trade.close_price:
        price_tolerance = max(abs(trade.open_price) * 1e-5, 1e-8)
        if abs(trade.close_price - risk_sl) <= price_tolerance:
            return -1.0

    return None


def attach_computed_fields(trades: List[Trade], db: Session) -> None:
    """Attache à chaque trade (attributs non persistés, lus par TradeOut)
    son risque effectif et son R-multiple, calculés avec le capital du
    compte au moment où CE trade a été ouvert plutôt qu'avec le capital
    actuel. Sert à afficher ces valeurs dans le tableau des trades sans
    obliger l'utilisateur à saisir le risque manuellement."""
    if not trades:
        return
    curve = build_capital_curve(db)
    for t in trades:
        cb = capital_before(t.open_time, curve)
        risk_pct, source = effective_risk(t, cb)
        t.risk_percent_effective = risk_pct
        t.risk_percent_source = source
        t.r_multiple = r_multiple(t, cb)
        if t.r_multiple is not None:
            t.r_multiple = round(t.r_multiple, 2)


def duration_bucket(trade: Trade) -> Optional[str]:
    if trade.close_time is None:
        return None
    minutes = (trade.close_time - trade.open_time).total_seconds() / 60
    if minutes < 5:
        return "<5min"
    if minutes < 15:
        return "5-15min"
    if minutes < 60:
        return "15-60min"
    if minutes < 240:
        return "1-4h"
    return ">4h"


def group_stats(trades: List[Trade], curve: Tuple[float, List[datetime], List[float]], total_count: Optional[int] = None) -> dict:
    """Agrège trades/win rate/R moyen/risque moyen/P&L pour un groupe de
    trades. `total_count`, si fourni, ajoute `pct_of_total` : la part (%)
    que ce groupe représente sur l'ensemble des trades clôturés — permet
    d'afficher, par ex., que 42% des sorties se font en Stop Loss."""
    count = len(trades)
    wins = [t for t in trades if t.profit > 0]
    win_rate = round(len(wins) / count * 100) if count else 0
    pnl = round(sum(t.profit for t in trades), 2)
    r_values: List[float] = []
    risk_values: List[float] = []
    for t in trades:
        cb = capital_before(t.open_time, curve)
        r = r_multiple(t, cb)
        if r is not None:
            r_values.append(r)
        risk_pct, _source = effective_risk(t, cb)
        if risk_pct is not None:
            risk_values.append(risk_pct)
    avg_r = round(sum(r_values) / len(r_values), 2) if r_values else None
    avg_risk = round(sum(risk_values) / len(risk_values), 2) if risk_values else None
    result = {"trades": count, "win_rate": win_rate, "avg_r": avg_r, "avg_risk": avg_risk, "pnl": pnl}
    if total_count:
        result["pct_of_total"] = round(count / total_count * 100, 1)
    return result


def grade_from_ratio(ratio: float, thresholds=(0.9, 0.7, 0.5)) -> str:
    """Convertit un ratio (0-1, plus haut = mieux) en note A/B/C/D."""
    if ratio >= thresholds[0]:
        return "A"
    if ratio >= thresholds[1]:
        return "B"
    if ratio >= thresholds[2]:
        return "C"
    return "D"


def discipline_bulletin(trades: List[Trade], curve: Tuple[float, List[datetime], List[float]]) -> dict:
    """Bulletin de discipline heuristique basé sur l'usage du stop loss, la
    taille de position et la régularité horaire.

    Ce sont des heuristiques simples (pas une vérité absolue) destinées à
    donner un signal rapide, pas un jugement définitif sur le trading.
    """
    grades: List[str] = []

    # Usage du Stop Loss : proportion de trades avec un SL défini.
    sl_grade = "N/A"
    if trades:
        with_sl = sum(1 for t in trades if t.sl)
        sl_grade = grade_from_ratio(with_sl / len(trades))
        grades.append(sl_grade)

    # Taille de position : risque max pris sur un trade (manuel si saisi,
    # sinon estimé automatiquement depuis le SL et le capital du moment —
    # voir `effective_risk`).
    sizing_grade = "N/A"
    risk_values = []
    for t in trades:
        risk_pct, _source = effective_risk(t, capital_before(t.open_time, curve))
        if risk_pct is not None:
            risk_values.append(risk_pct)
    if risk_values:
        max_risk = max(risk_values)
        if max_risk <= 1:
            sizing_grade = "A"
        elif max_risk <= 2:
            sizing_grade = "B"
        elif max_risk <= 3:
            sizing_grade = "C"
        else:
            sizing_grade = "D"
        grades.append(sizing_grade)

    # Discipline horaire : concentration des trades sur les heures habituelles.
    # Nécessite un minimum de trades pour être significatif.
    hourly_grade = "N/A"
    if len(trades) >= 15:
        hour_counts: dict = {}
        for t in trades:
            h = t.open_time.hour
            hour_counts[h] = hour_counts.get(h, 0) + 1
        top2 = sorted(hour_counts.values(), reverse=True)[:2]
        concentration = sum(top2) / len(trades)
        hourly_grade = grade_from_ratio(concentration, thresholds=(0.7, 0.5, 0.3))
        grades.append(hourly_grade)

    grade_score = {"A": 4, "B": 3, "C": 2, "D": 1}
    overall = "N/A"
    if grades:
        avg_score = sum(grade_score[g] for g in grades) / len(grades)
        overall = min(grade_score, key=lambda g: abs(grade_score[g] - avg_score))

    # Séries de gains/pertes consécutifs + jours profitables (%).
    longest_win_streak = longest_loss_streak = 0
    cur_win = cur_loss = 0
    for t in trades:
        if t.profit > 0:
            cur_win += 1
            cur_loss = 0
        elif t.profit < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win_streak = max(longest_win_streak, cur_win)
        longest_loss_streak = max(longest_loss_streak, cur_loss)

    daily_pnl: dict = {}
    for t in trades:
        day = t.open_time.date().isoformat()
        daily_pnl[day] = daily_pnl.get(day, 0) + t.profit
    profitable_days_pct = round(
        sum(1 for v in daily_pnl.values() if v > 0) / len(daily_pnl) * 100
    ) if daily_pnl else 0

    return {
        "overall_grade": overall,
        "sl_grade": sl_grade,
        "sizing_grade": sizing_grade,
        "hourly_grade": hourly_grade,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "profitable_days_pct": profitable_days_pct,
    }


def compute_stats(trades: List[Trade], db: Session, include_movements: bool = True) -> dict:
    """Statistiques globales : P&L, win rate, drawdown, R:R — logique de
    l'ancien `get_stats()` de main.py, désormais autonome (ne porte que sur
    les trades déjà filtrés/passés par l'appelant).

    `include_movements` : les dépôts / retraits du compte actif neutralisent
    le drawdown (voir max_drawdown). À désactiver quand l'appelant a filtré
    les trades (ex. par symbole) : un dépôt n'appartient à aucun symbole.
    """
    if not trades:
        return {
            "total_pnl": 0, "win_rate": 0, "max_drawdown": 0, "max_drawdown_abs": 0,
            "rr_ratio": None, "total_trades": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "profit_factor": None, "expectancy": None,
            "best_trade": None, "worst_trade": None,
            "longest_win_streak": 0, "longest_loss_streak": 0,
            "avg_duration_minutes": None,
            "total_volume": 0, "total_commission": 0, "total_swap": 0,
            "best_day": None, "worst_day": None,
            "best_symbol": None, "worst_symbol": None,
        }

    total_pnl = sum(t.profit for t in trades)
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit < 0]
    breakeven = [t for t in trades if t.profit == 0]
    win_rate = len(wins) / len(trades) * 100

    avg_win = sum(t.profit for t in wins) / len(wins) if wins else 0
    # Avant : avg_loss valait 1 par défaut s'il n'y avait aucune perte, ce
    # qui produisait un ratio R:R arbitraire (avg_win / 1). On renvoie
    # désormais `None` (affiché "N/A" côté frontend) quand le ratio n'est
    # pas défini.
    sum_losses_abs = abs(sum(t.profit for t in losses)) if losses else 0
    if losses:
        avg_loss = sum_losses_abs / len(losses)
        rr = round(avg_win / avg_loss, 2) if avg_loss else None
    else:
        avg_loss = 0
        rr = None

    ref_capital = state.mt5_reference_capital(db) or 0.0
    active = state.get_active_account(db)
    flows = movements.movement_events(db, active.login if active else None) if include_movements else None
    max_dd_pct, max_dd_abs = max_drawdown(
        trades, ref_capital, net=state.is_manual(active), movements=flows
    )

    # Profit factor = somme des gains / somme des pertes (valeur absolue).
    # None (affiché "N/A") si aucune perte : le ratio n'est pas défini,
    # plutôt que d'afficher une valeur infinie ou arbitraire.
    sum_wins = sum(t.profit for t in wins)
    profit_factor = round(sum_wins / sum_losses_abs, 2) if sum_losses_abs else None

    # Espérance mathématique par trade, sur la base des trades gagnants /
    # perdants / breakeven rapportés au nombre total de trades.
    win_rate_frac = len(wins) / len(trades)
    loss_rate_frac = len(losses) / len(trades)
    expectancy = round((win_rate_frac * avg_win) - (loss_rate_frac * avg_loss), 2)

    def _trade_summary(t: Trade) -> dict:
        return {
            "amount": round(t.profit, 2),
            "date": t.open_time.isoformat(),
            "symbol": t.symbol,
            "ticket": t.ticket,
        }

    # Chaque indicateur reste dans sa catégorie : un filtre contenant
    # uniquement des pertes ne doit pas afficher une perte comme "meilleur"
    # trade, et inversement pour les indicateurs "pires".
    best_trade_obj = max(wins, key=lambda t: t.profit) if wins else None
    worst_trade_obj = min(losses, key=lambda t: t.profit) if losses else None
    best_trade = _trade_summary(best_trade_obj) if best_trade_obj else None
    worst_trade = _trade_summary(worst_trade_obj) if worst_trade_obj else None

    # Plus longues séries de gains / pertes consécutifs. Un trade breakeven
    # (profit == 0) casse les deux séries en cours (ni gain ni perte).
    longest_win_streak = longest_loss_streak = 0
    cur_win_streak = cur_loss_streak = 0
    for t in trades:
        if t.profit > 0:
            cur_win_streak += 1
            cur_loss_streak = 0
        elif t.profit < 0:
            cur_loss_streak += 1
            cur_win_streak = 0
        else:
            cur_win_streak = 0
            cur_loss_streak = 0
        longest_win_streak = max(longest_win_streak, cur_win_streak)
        longest_loss_streak = max(longest_loss_streak, cur_loss_streak)

    # Durée moyenne de détention (close_time - open_time), en minutes.
    # Utile pour distinguer scalping / swing / position.
    durations = [
        (t.close_time - t.open_time).total_seconds() / 60
        for t in trades if t.close_time is not None
    ]
    avg_duration_minutes = round(sum(durations) / len(durations), 1) if durations else None

    total_volume = round(sum(t.volume for t in trades), 2)
    total_commission = round(sum(t.commission for t in trades), 2)
    total_swap = round(sum(t.swap for t in trades), 2)

    # Meilleur / pire jour.
    daily_pnl: dict = {}
    for t in trades:
        day = t.open_time.date().isoformat()
        daily_pnl[day] = daily_pnl.get(day, 0) + t.profit
    best_day = worst_day = None
    positive_days = {key: value for key, value in daily_pnl.items() if value > 0}
    negative_days = {key: value for key, value in daily_pnl.items() if value < 0}
    if positive_days:
        key = max(positive_days, key=positive_days.get)
        best_day = {"date": key, "pnl": round(positive_days[key], 2)}
    if negative_days:
        key = min(negative_days, key=negative_days.get)
        worst_day = {"date": key, "pnl": round(negative_days[key], 2)}

    # Meilleur / pire symbole (le classement complet existe déjà via
    # /api/performance/by-symbol ; on ne fait ici que mettre en avant les
    # extrêmes pour l'affichage dashboard).
    symbol_pnl: dict = {}
    for t in trades:
        symbol_pnl[t.symbol] = symbol_pnl.get(t.symbol, 0) + t.profit
    best_symbol = worst_symbol = None
    positive_symbols = {key: value for key, value in symbol_pnl.items() if value > 0}
    negative_symbols = {key: value for key, value in symbol_pnl.items() if value < 0}
    if positive_symbols:
        key = max(positive_symbols, key=positive_symbols.get)
        best_symbol = {"symbol": key, "pnl": round(positive_symbols[key], 2)}
    if negative_symbols:
        key = min(negative_symbols, key=negative_symbols.get)
        worst_symbol = {"symbol": key, "pnl": round(negative_symbols[key], 2)}

    return {
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "max_drawdown": max_dd_pct,
        "max_drawdown_abs": max_dd_abs,
        "rr_ratio": rr,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "longest_win_streak": longest_win_streak,
        "longest_loss_streak": longest_loss_streak,
        "avg_duration_minutes": avg_duration_minutes,
        "total_volume": total_volume,
        "total_commission": total_commission,
        "total_swap": total_swap,
        "best_day": best_day,
        "worst_day": worst_day,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
    }
