from datetime import datetime, timezone
from typing import Iterable, Optional

from services.pips import price_to_pips


def _utc_naive(timestamp) -> datetime:
    """Horodatage MT5 (epoch UTC) -> datetime UTC naïf.

    `datetime.utcfromtimestamp()` est déprécié depuis Python 3.12 et sera
    supprimé ; `fromtimestamp(..., timezone.utc)` est l'équivalent exact. Le
    tzinfo est retiré ensuite car toutes les colonnes DateTime du projet
    stockent des instants UTC naïfs.
    """
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)


def weighted_average_open(ins: Iterable) -> tuple:

    ins = list(ins)
    in_volume = sum(d.volume for d in ins)
    open_price = (
        sum(d.price * d.volume for d in ins) / in_volume
        if in_volume else ins[0].price
    )
    open_time = _utc_naive(min(d.time for d in ins))
    return open_price, open_time, in_volume


def weighted_average_close(outs: Iterable) -> tuple:
    outs = list(outs)
    out_volume = sum(d.volume for d in outs)
    close_price = (
        sum(d.price * d.volume for d in outs) / out_volume
        if out_volume else outs[-1].price
    )
    close_time = _utc_naive(max(d.time for d in outs))
    profit = sum(d.profit for d in outs)
    return close_price, close_time, out_volume, profit


def exit_reason_from_deals(outs: Iterable, reason_sl, reason_tp) -> Optional[str]:

    outs = list(outs)
    if reason_sl is not None and any(getattr(d, "reason", None) == reason_sl for d in outs):
        return "StopLoss"
    if reason_tp is not None and any(getattr(d, "reason", None) == reason_tp for d in outs):
        return "TakeProfit"
    # Correction : tout ce qui n'était ni StopLoss ni TakeProfit renvoyait
    # None, et le trade disparaissait purement et simplement du tableau
    # "raison de sortie" — c'est-à-dire TOUTES les sorties manuelles. Un
    # utilisateur qui clôture à la main ne voyait donc qu'un tableau vide, et
    # les pourcentages affichés étaient calculés sur les seules sorties
    # étiquetées ("50 % StopLoss" alors que c'était 25 % du total réel).
    # Une position clôturée qui n'a touché ni SL ni TP l'a été par une action
    # manuelle (ou un EA) : c'est bien "Manuel", pas une information absente.
    # `reason_sl is None` signifie que les constantes MT5 ne sont pas
    # disponibles (mode simulation) : dans ce cas on ne tranche pas.
    if reason_sl is None and reason_tp is None:
        return None
    return "Manuel"


def aggregate_closed_position(
    ins: Iterable,
    outs: Iterable,
    deal_type_buy,
    reason_sl=None,
    reason_tp=None,
    mt5_api=None,
) -> dict:

    ins = list(ins)
    outs = list(outs)
    if not outs:
        raise ValueError("aggregate_closed_position nécessite au moins un deal de sortie")

    if ins:
        open_price, open_time, volume = weighted_average_open(ins)
        direction = "buy" if ins[0].type == deal_type_buy else "sell"
    else:
        open_price = open_time = volume = direction = None

    close_price, close_time, _out_volume, profit = weighted_average_close(outs)
    commission = sum(d.commission for d in ins) + sum(d.commission for d in outs)
    swap = sum(d.swap for d in ins) + sum(d.swap for d in outs)
    pips = (
        price_to_pips(close_price - open_price, outs[0].symbol, mt5_api)
        if open_price is not None
        else None
    )
    exit_reason = exit_reason_from_deals(outs, reason_sl, reason_tp)

    return {
        "open_price": open_price,
        "open_time": open_time,
        "volume": volume,
        "direction": direction,
        "close_price": close_price,
        "close_time": close_time,
        "profit": profit,
        "commission": commission,
        "swap": swap,
        "pips": pips,
        "exit_reason": exit_reason,
    }
