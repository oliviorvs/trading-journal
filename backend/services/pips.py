"""Taille d'un pip par symbole.

Correction du calcul précédent, dupliqué en quatre endroits :

    pip_factor = 10000 if "JPY" not in symbol else 100

Cette règle ne vaut que pour les paires de devises. Elle donnait des valeurs
absurdes partout ailleurs :

- XAUUSD (or) : un mouvement de 1,00 $ vaut 10 pips, pas 10 000 ;
- US30 / NAS100 (indices) : un mouvement de 100 points affichait 1 000 000 ;
- BTCUSD : idem, des ordres de grandeur d'écart.

La colonne « pips » du journal était donc inexploitable dès qu'on tradait
autre chose que du forex.

Principe retenu
---------------
Ne PAS deviner à partir du nom du symbole quand on peut demander la vraie
valeur à MT5. `symbol_info()` expose `point` (plus petite variation de prix
cotée) et `digits` (nombre de décimales). La convention du marché :

- cotation à 5 décimales (EURUSD 1.08451) ou 3 (USDJPY 149.123) : le dernier
  chiffre est un « point », et 1 pip = 10 points ;
- cotation à 4 ou 2 décimales : 1 pip = 1 point.

Cette règle couvre correctement forex, métaux, indices et crypto, parce que
c'est le broker lui-même qui fournit `digits` pour chaque instrument.

Repli hors MT5
--------------
En mode simulation, ou si le symbole est inconnu du terminal (symbole
supprimé du Market Watch, trade historique sur un instrument retiré), on
retombe sur une table par famille d'instruments. Elle reste approximative
par nature : c'est un repli, pas la source de vérité.
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Cache par symbole : `symbol_info()` fait un aller-retour vers le terminal,
# et la synchronisation appelle ce code une fois par trade.
_pip_size_cache: dict = {}

# Repli par famille, utilisé UNIQUEMENT quand MT5 ne peut pas répondre.
# Les motifs sont testés dans l'ordre : le premier qui correspond gagne.
_FALLBACK_PATTERNS = [
    (re.compile(r"^(XAU|GOLD)", re.I), 0.1),      # or : 1 pip = 0,10 $
    (re.compile(r"^(XAG|SILVER)", re.I), 0.01),   # argent
    (re.compile(r"^(XPT|XPD)", re.I), 0.1),       # platine / palladium
    (re.compile(r"^(BTC|ETH|LTC|XRP|SOL|ADA|DOGE)", re.I), 1.0),   # crypto
    (re.compile(r"^(US30|US100|US500|NAS|SPX|DJI|GER|DAX|UK100|FRA|JP225|HK50|AUS200)", re.I), 1.0),  # indices
    (re.compile(r"^(WTI|BRENT|USOIL|UKOIL|OIL)", re.I), 0.01),     # énergie
    (re.compile(r"JPY", re.I), 0.01),             # paires en yen
]

_DEFAULT_PIP_SIZE = 0.0001  # paires de devises standard


def _match_family(symbol: str) -> Optional[float]:
    """Taille de pip conventionnelle pour une famille reconnue, ou None si
    le symbole n'en relève pas (cas général : une paire de devises)."""
    for pattern, size in _FALLBACK_PATTERNS:
        if pattern.search(symbol or ""):
            return size
    return None


def pip_size(symbol: str, mt5_api=None) -> float:
    """Valeur d'un pip, en unités de prix, pour `symbol`.

    `mt5_api` est le module MetaTrader5 déjà chargé (voir mt5_service.py) ou
    None. On ne déclenche jamais son import depuis ici : cette fonction est
    aussi appelée sur des trades historiques, hors de toute connexion.

    Arbitrage retenu — important, car les deux sources peuvent diverger :

    - Pour les familles hors forex (métaux, indices, crypto, énergie), c'est
      la CONVENTION DE MARCHÉ qui prime, pas `digits`. Exemple : l'or est
      coté à 2 décimales, donc la règle `digits` donnerait 1 pip = 0,01 $,
      alors qu'un trader compte l'or en dixièmes de dollar (1 pip = 0,10 $).
      Suivre `digits` afficherait des nombres corrects mais dix fois éloignés
      de ce que l'utilisateur lit sur sa plateforme.
    - Pour le forex, `digits` est au contraire la bonne source : c'est lui
      qui distingue une cotation 4 décimales d'une cotation 5 décimales,
      information que le nom du symbole ne porte pas.

    Conséquence : le résultat est le même que MT5 soit connecté ou non, ce
    qui évite qu'un même trade change de valeur entre deux synchronisations.
    """
    if not symbol:
        return _DEFAULT_PIP_SIZE

    cached = _pip_size_cache.get(symbol)
    if cached is not None:
        return cached

    # 1. Famille non-forex reconnue : la convention prime (voir docstring).
    size = _match_family(symbol)

    # 2. Sinon, on demande à MT5 le nombre de décimales réellement coté.
    if size is None and mt5_api is not None:
        try:
            info = mt5_api.symbol_info(symbol)
            if info is not None and info.point:
                # 5 ou 3 décimales : le dernier chiffre est un dixième de pip.
                size = info.point * 10 if info.digits in (3, 5) else info.point
        except Exception:
            # Un symbole inconnu ou un terminal qui ne répond plus ne doit
            # jamais faire échouer une synchronisation : on passe au repli.
            logger.debug("symbol_info(%s) indisponible — repli sur la table", symbol, exc_info=True)

    # 3. Repli final : paire de devises standard.
    used_default = not size
    if used_default:
        size = _DEFAULT_PIP_SIZE

    # Hors MT5 (ex. trade saisi à la main, aucun terminal interrogé), ce
    # repli « par défaut » n'est qu'une supposition : on ne le met PAS en
    # cache, sinon il masquerait la vraie valeur (`symbol_info`) quand un
    # compte MT5 trade plus tard le même symbole.
    if not (used_default and mt5_api is None):
        _pip_size_cache[symbol] = size
    return size


def price_to_pips(price_delta: float, symbol: str, mt5_api=None) -> float:
    """Convertit un écart de prix en pips pour `symbol`."""
    size = pip_size(symbol, mt5_api)
    if not size:
        return 0.0
    return round(abs(price_delta) / size, 1)


def reset_cache() -> None:
    """Vide le cache — utile après un changement de compte/broker, les
    spécifications de symboles pouvant différer d'un broker à l'autre."""
    _pip_size_cache.clear()
