"""
Service de connexion MetaTrader 5.
Utilise la librairie officielle MetaTrader5 (Windows uniquement).
"""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Import DIFFÉRÉ de MetaTrader5 ────────────────────────────────────────────
#
# AVANT : `import MetaTrader5 as mt5` s'exécutait à l'import du module, donc
# dans la chaîne `launcher.py -> import main -> state.py -> mt5_service.py`,
# c'est-à-dire AVANT `uvicorn.run()` et avant l'ouverture de la fenêtre.
# Ce paquet charge une DLL native qui cherche (et peut lancer) le terminal
# MetaTrader : sur un poste où MT5 est installé mais lent à répondre, cet
# import bloquait plusieurs secondes pendant lesquelles le port n'écoutait
# pas encore. Le launcher atteignait alors son délai d'attente et l'app ne
# s'ouvrait pas du tout — alors que la seule chose vraiment lente était une
# fonctionnalité optionnelle.
#
# MAINTENANT : l'import se fait dans un thread démon lancé au démarrage de
# l'API. Le serveur HTTP écoute immédiatement. Les méthodes qui ont
# réellement besoin de MT5 (connexion, synchronisation) attendent la fin de
# cet import, avec un timeout ; les routes de statut, elles, ne bloquent
# jamais et renvoient l'état "en cours de chargement".

MT5_IMPORT_TIMEOUT = 30.0

# ── Timeout des appels natifs initialize()/login() ───────────────────────────
#
# La valeur par défaut de l'API MetaTrader5 est de 60 000 ms. Cette extension
# C ne libère pas toujours le GIL pendant l'attente : un terminal injoignable
# pouvait donc geler TOUT le process Python (serveur uvicorn ET fenêtre
# pywebview) jusqu'à une minute — et pas seulement le thread appelant.
#
# 3 s : au-delà, on considère MT5 injoignable et on rend la main. Ce délai
# reste utile même si connect() n'est plus JAMAIS rappelé automatiquement en
# tâche de fond (voir state.reconnect_active_account) : une action explicite
# de l'utilisateur — bouton « Relancer MT5 », clic sur le compte actif — peut
# elle aussi tomber sur un terminal injoignable, et ne doit pas non plus
# geler l'interface en l'attendant.
MT5_INITIALIZE_TIMEOUT_MS = 3000

# ── Fenêtre de lecture de l'historique (voir MT5Service._sync_trades_locked) ──
# Recouvrement vers le passé à chaque synchro, et marge vers le futur : les
# horodatages des deals sont en heure du serveur du courtier, pas en UTC.
SYNC_OVERLAP = timedelta(days=3)
SYNC_FUTURE_MARGIN = timedelta(days=1)

# ── Détection du terminal MT5 SANS passer par l'API MetaTrader5 ─────────────
#
# La reconnexion automatique en tâche de fond a été retirée dans une version
# précédente (voir state.reconnect_active_account) parce que `initialize()`
# relance le terminal s'il n'est pas déjà ouvert : une boucle qui rappelait
# connect() toutes les ~60 s rouvrait donc silencieusement MT5 même après
# une fermeture volontaire.
#
# Ces deux fonctions permettent de réintroduire une boucle de fond SANS ce
# risque : on vérifie l'environnement par des moyens qui ne peuvent PAS
# lancer le terminal (liste des process du système, simple test réseau) et
# on n'appelle `connect()` — donc `initialize()` — QUE si le terminal
# tourne déjà. Dans ce cas, `initialize()` s'attache au terminal existant
# au lieu d'en démarrer un nouveau : aucun risque de relance intempestive.
TERMINAL_PROCESS_NAMES = ("terminal64.exe", "terminal.exe")


def is_terminal_running() -> bool:
    """Vrai si un processus terminal MetaTrader 5 tourne déjà sur la
    machine — détecté via la liste des process du système (psutil), jamais
    via l'API MetaTrader5.

    En cas de doute (psutil absent, erreur de lecture des process), on
    renvoie False plutôt que de risquer un `connect()` qui relancerait le
    terminal : la prudence va toujours vers « ne rien faire ».
    """
    try:
        import psutil
    except Exception:
        logger.warning("psutil indisponible — détection du terminal MT5 désactivée")
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in TERMINAL_PROCESS_NAMES:
                return True
    except Exception:
        logger.warning("Lecture des process système en échec — détection MT5 ignorée", exc_info=True)
        return False
    return False


def has_internet(timeout: float = 1.5) -> bool:
    """Signal léger de connectivité réseau, indépendant de MetaTrader5 :
    une tentative de connexion TCP vers un résolveur DNS public, sans
    aucune requête HTTP. Ce n'est pas une garantie que le serveur du
    broker précis est joignable (seul `login()` peut vraiment le
    confirmer) — juste de quoi éviter un appel MT5 en pure perte quand la
    machine n'a manifestement aucun réseau.
    """
    import socket
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False

_mt5 = None                       # module MetaTrader5 une fois importé
_import_done = threading.Event()  # posé dès que l'import a abouti OU échoué
_import_lock = threading.Lock()
_import_launched = False


def _import_mt5() -> None:
    global _mt5
    try:
        import MetaTrader5 as module

        _mt5 = module
        logger.info("MetaTrader5 chargé — connexion à un compte réel possible")
    except Exception:
        _mt5 = None
        # `except Exception` et pas seulement `ImportError` : sur une machine
        # sans terminal MT5 (ou avec une installation cassée), l'import lui-même
        # peut lever autre chose qu'une ImportError. Dans tous les cas, l'app
        # doit rester utilisable en mode simulation plutôt que de planter.
        logger.warning("MetaTrader5 indisponible — mode simulation activé", exc_info=True)
    finally:
        _import_done.set()


def start_mt5_import() -> None:
    """Démarre l'import de MetaTrader5 en tâche de fond. Idempotent : appelé
    au démarrage de l'API (voir le lifespan de main.py) et, par sécurité, à
    la première utilisation réelle de MT5."""
    global _import_launched
    with _import_lock:
        if _import_launched:
            return
        _import_launched = True
    threading.Thread(target=_import_mt5, daemon=True, name="mt5-import").start()


def mt5_import_status() -> str:
    """"pending" (import en cours), "available" (module chargé) ou
    "unavailable" (MetaTrader5 non installable sur ce poste)."""
    if not _import_done.is_set():
        return "pending"
    return "available" if _mt5 is not None else "unavailable"


def mt5_module(timeout: float = MT5_IMPORT_TIMEOUT):
    """Renvoie le module MetaTrader5, en attendant au plus `timeout`
    secondes la fin de l'import de fond. None si MT5 est indisponible ou
    n'est pas encore prêt. `timeout=0` = ne jamais bloquer."""
    start_mt5_import()
    if timeout:
        _import_done.wait(timeout)
    return _mt5


from sqlalchemy.orm import Session
from models import Trade, Account, CapitalMovement
from services.mt5_aggregation import aggregate_closed_position
from services.pips import price_to_pips


class MT5Service:
    def __init__(self):
        self._connected = False
        self._login: Optional[int] = None
        # L'API MetaTrader5 n'est pas conçue pour être appelée en parallèle.
        # connect() peut être invoquée depuis plusieurs threads : celui de
        # connexion automatique lancé au démarrage (restauration de la
        # session précédente, une seule fois), celui d'une requête
        # utilisateur (bouton « Relancer MT5 », clic sur un compte,
        # formulaire de connexion), et celui de la boucle de reconnexion
        # passive (state.passive_reconnect_worker — gardée : elle ne
        # tente une connexion que si le terminal tourne déjà). Sans ce
        # verrou, deux appels concomitants pourraient geler chacun de leur
        # côté sur initialize().
        self._connect_lock = threading.Lock()
        # Correction : un échec de connexion était TOUJOURS annoncé au
        # frontend comme « identifiants incorrects », y compris quand la
        # vraie cause était que MT5 n'était pas lancé/installé ou que le
        # serveur du broker était injoignable (pas d'internet). Changer le
        # login n'avait alors aucun effet — le message ne correspondait pas
        # à l'état réel. Ces deux champs mémorisent la nature exacte du
        # dernier échec pour que le routeur (voir routers/mt5.py) puisse
        # renvoyer un message qui reflète ce qui s'est réellement passé.
        #   kind: "unreachable" (initialize() a échoué : terminal MT5 non
        #         lancé ou introuvable) | "network" (login() a échoué pour
        #         une raison de connexion : pas d'internet, serveur
        #         injoignable) | "credentials" (login() a échoué, cause la
        #         plus probable : identifiants/serveur invalides) | None si
        #         la dernière tentative a réussi ou qu'aucune n'a encore eu
        #         lieu.
        self._last_error_kind: Optional[str] = None
        self._last_error_detail: Optional[str] = None
        # Une seule synchronisation de trades à la fois (bouton manuel +
        # boucle de fond), sinon doublons de tickets possibles.
        self._sync_lock = threading.Lock()
        # Incrémenté quand une synchro change des données visibles (nouveau
        # trade, clôture, dépôt/retrait). Exposé par /api/mt5/status pour que
        # l'interface se recharge toute seule.
        self._data_version = 0

    @property
    def data_version(self) -> int:
        return self._data_version

    def connect(self, login: int, password: str, server: str) -> bool:
        # Action explicite de l'utilisateur (ou reconnexion automatique en
        # tâche de fond) : c'est le seul endroit où il est légitime
        # d'ATTENDRE la fin de l'import de MetaTrader5.
        api = mt5_module()
        if api is None:
            logger.info("[SIMULATION] Connexion MT5 — login=%s, server=%s", login, server)
            self._connected = True
            self._login = login
            self._last_error_kind = None
            self._last_error_detail = None
            return True

        # Non bloquant : si une tentative est déjà en cours, on rend la main
        # immédiatement plutôt que d'ajouter une attente de 3 s à la requête
        # HTTP courante. L'appelant retentera au prochain cycle.
        if not self._connect_lock.acquire(blocking=False):
            logger.debug("Tentative de connexion MT5 ignorée : une autre est déjà en cours")
            return False
        try:
            return self._connect_locked(api, login, password, server)
        finally:
            self._connect_lock.release()

    def _connect_locked(self, api, login: int, password: str, server: str) -> bool:
        if not api.initialize(timeout=MT5_INITIALIZE_TIMEOUT_MS):
            # `warning` et non `error` : MT5 simplement fermé est un cas
            # nominal pour cette app, pas une anomalie du programme.
            _, desc = self._safe_last_error(api)
            logger.warning(
                "initialize() timeout/échec (%s ms) : %s",
                MT5_INITIALIZE_TIMEOUT_MS, desc,
            )
            self._connected = False
            # Le terminal n'a même pas pu démarrer : ce n'est jamais un
            # problème d'identifiants, changer le login ne changera rien.
            self._last_error_kind = "unreachable"
            self._last_error_detail = desc
            return False

        ok = api.login(login, password=password, server=server, timeout=MT5_INITIALIZE_TIMEOUT_MS)
        if ok:
            self._connected = True
            self._login = login
            self._last_error_kind = None
            self._last_error_detail = None
        else:
            _, desc = self._safe_last_error(api)
            logger.error("login() failed: %s", desc)
            self._connected = False
            # Le terminal a démarré mais l'authentification a échoué : ça
            # peut être un mauvais login/mot de passe/serveur, mais aussi une
            # absence de connexion internet (le terminal ne peut alors pas
            # joindre le serveur du broker pour vérifier quoi que ce soit) —
            # les deux cas remontent la même API MT5, on distingue donc sur
            # le texte de l'erreur pour ne pas accuser les identifiants à
            # tort.
            lowered = (desc or "").lower()
            network_markers = (
                "no connection", "connection", "network", "timeout",
                "internet", "unreachable", "connexion",
            )
            if any(marker in lowered for marker in network_markers):
                self._last_error_kind = "network"
            else:
                self._last_error_kind = "credentials"
            self._last_error_detail = desc
            # Correction : initialize() avait réussi mais login() a échoué
            # (mauvais mot de passe/serveur) — sans shutdown(), le terminal
            # MT5 restait initialisé côté process pour rien, jusqu'à la
            # prochaine tentative. On libère la ressource immédiatement.
            api.shutdown()
        return ok

    @staticmethod
    def _safe_last_error(api):
        """api.last_error() renvoie un tuple (code, description) mais peut
        lever si le terminal vient de planter — on ne veut jamais que la
        collecte du diagnostic fasse elle-même échouer la connexion."""
        try:
            return api.last_error()
        except Exception:
            return (None, None)

    def last_connect_error(self) -> Optional[dict]:
        """Nature de la dernière tentative de connexion échouée : voir les
        docstrings de `_last_error_kind`/`_last_error_detail` ci-dessus.
        None si la dernière tentative a réussi ou qu'aucune n'a encore eu
        lieu (ex. mode simulation, voir connect())."""
        if self._last_error_kind is None:
            return None
        return {"kind": self._last_error_kind, "detail": self._last_error_detail}

    def shutdown(self) -> None:
        """Libère la connexion au terminal MT5. À appeler à l'arrêt de
        l'API (voir atexit dans main.py) pour ne pas laisser le terminal
        initialisé après la fermeture du process.

        `timeout=0` : à l'arrêt du process, on ne déclenche/attend jamais un
        import de MetaTrader5 qui n'aurait pas abouti — il n'y a alors rien
        à libérer, et attendre ici retarderait la fermeture de l'app.
        """
        api = mt5_module(0)
        if api is not None and self._connected:
            try:
                api.shutdown()
            except Exception:
                logger.warning("shutdown() MT5 en erreur — ignoré", exc_info=True)
        self._connected = False

    def is_connected(self) -> bool:
        """État RÉEL de la connexion, interrogé à chaque appel plutôt que
        renvoyé depuis un simple drapeau mis en cache.

        Avant : seul `mt5.terminal_info()` était vérifié, qui confirme que
        le terminal MetaTrader est lancé mais PAS que la session de trading
        est toujours valide — un compte déconnecté côté serveur (broker
        injoignable, session expirée, identifiants révoqués...) alors que
        le terminal reste ouvert affichait donc "Connecté" à tort. On
        vérifie désormais `mt5.account_info()`, qui renvoie None dès que le
        serveur ne répond plus, quelle qu'en soit la raison.
        """
        # timeout=0 : cette méthode est appelée par /api/mt5/status, sollicitée
        # en boucle par le frontend — elle ne doit jamais attendre l'import.
        status = mt5_import_status()
        if status == "pending":
            # Tant que MT5 charge, rien n'est connecté : on le dit franchement
            # plutôt que de faire patienter la requête.
            return False
        if status == "unavailable":
            return self._connected  # mode simulation
        if not self._connected:
            return False
        info = _mt5.account_info()
        if info is None:
            # Le serveur ne répond plus (ou plus la même session) : on
            # reflète immédiatement l'état réel plutôt que de laisser le
            # drapeau `_connected` mentir jusqu'à la prochaine action.
            self._connected = False
            return False
        return True

    def is_simulated(self) -> bool:
        """Vrai uniquement quand MetaTrader5 est confirmé indisponible.
        Pendant le chargement, on ne prétend pas encore être en simulation :
        `is_loading()` sert à distinguer les deux côté interface."""
        return mt5_import_status() == "unavailable"

    def is_loading(self) -> bool:
        """L'import de MetaTrader5 est encore en cours (voir en-tête du
        module) — exposé par /api/mt5/status pour que l'interface affiche
        "initialisation" plutôt que "non connecté"."""
        return mt5_import_status() == "pending"

    @property
    def current_login(self) -> Optional[int]:
        """Login actuellement connecté (None si aucune connexion active).
        Vérifie l'état réel via `is_connected()`, pas juste le drapeau
        interne — voir sa docstring."""
        return self._login if self.is_connected() else None

    def last_error(self) -> Optional[str]:
        """Dernière erreur MT5 connue (ex. cause d'une déconnexion), pour
        affichage éventuel côté frontend."""
        api = mt5_module(0)
        if api is None:
            return None
        try:
            code, desc = api.last_error()
            return f"{desc} ({code})"
        except Exception:
            logger.exception("Impossible de récupérer la dernière erreur MT5")
            return None

    def get_account_info(self) -> dict:
        api = mt5_module(0)
        if api is None:
            # IMPORTANT : la librairie MetaTrader5 n'est pas installée
            # (voir requirements.txt) — on ne s'est PAS connecté à un vrai
            # compte, on renvoie des données simulées. Le champ "simulated"
            # doit être utilisé côté frontend pour prévenir l'utilisateur,
            # sinon il pourrait croire qu'il voit ses vraies performances.
            return {
                "login": self._login or 0,
                "server": "Demo-Server (simulation)",
                "name": "Compte Démo",
                "currency": "USD",
                "balance": 10000.0,
                "equity": 10000.0,
                "margin": 0.0,
                "free_margin": 10000.0,
                "leverage": 100,
                "simulated": True,
            }
        info = api.account_info()
        if info is None:
            return {}
        return {
            "login": info.login,
            "server": info.server,
            "name": info.name,
            "currency": info.currency,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "leverage": info.leverage,
            "simulated": False,
        }

    def sync_trades(self, db: Session, blocking: bool = True) -> int:
        """Synchronise positions ouvertes + historique clôturé du compte connecté.

        `blocking=True` (bouton « Synchroniser », reconnexion) attend la fin
        d'une synchro éventuellement déjà en cours ; `blocking=False` (boucle
        de fond, voir state._trade_sync_cycle) abandonne simplement le tour si
        une synchro tourne déjà — sans verrou, deux synchros concomitantes
        pouvaient insérer le même ticket deux fois (contrainte d'unicité).
        Retourne le nombre de trades nouveaux ou nouvellement clôturés.
        """
        api = mt5_module()
        if api is None:
            return self._seed_demo_data(db)
        if not self._sync_lock.acquire(blocking=blocking):
            logger.debug("Synchronisation MT5 ignorée : une autre est déjà en cours")
            return 0
        try:
            return self._sync_trades_locked(api, db)
        finally:
            self._sync_lock.release()

    @staticmethod
    def _add_deal(api, group: dict, deal) -> None:
        """Range un deal dans les entrées / sorties d'une position."""
        entry = deal.entry
        if entry == api.DEAL_ENTRY_IN:
            group["ins"].append(deal)
        elif entry == api.DEAL_ENTRY_OUT or entry == getattr(api, "DEAL_ENTRY_OUT_BY", -1):
            group["outs"].append(deal)

    @staticmethod
    def _position_deals(api, position_id: int) -> dict:
        """TOUS les deals d'une position, sans dépendre d'une fenêtre de dates
        (`history_deals_get(position=...)`). Source de vérité pour agréger une
        position : la fenêtre incrémentale peut n'en contenir qu'une partie."""
        group = {"ins": [], "outs": []}
        try:
            deals = api.history_deals_get(position=position_id)
        except Exception:
            logger.debug("history_deals_get(position=%s) indisponible", position_id, exc_info=True)
            return group
        for deal in deals or ():
            MT5Service._add_deal(api, group, deal)
        return group

    @staticmethod
    def _initial_stop(api, position_id: int) -> Optional[float]:
        """Premier SL non nul posé sur un ordre de cette position (= SL initial)."""
        try:
            orders = api.history_orders_get(position=position_id) or ()
        except Exception:
            logger.debug("history_orders_get(position=%s) indisponible", position_id, exc_info=True)
            return None
        for order in sorted(orders, key=lambda item: getattr(item, "time_setup", 0)):
            stop_loss = getattr(order, "sl", 0)
            if stop_loss:
                return stop_loss
        return None

    def _sync_trades_locked(self, api, db: Session) -> int:
        account = db.query(Account).filter(Account.login == self._login).first()
        last_sync = account.last_sync if (account and account.last_sync) else datetime(2000, 1, 1)
        if last_sync.tzinfo is None:
            # `last_sync` est stocké naïf (colonne DateTime SQLite) mais
            # représente bien un instant UTC : on lui rattache le fuseau.
            last_sync = last_sync.replace(tzinfo=timezone.utc)

        # ── Fenêtre de lecture de l'historique ─────────────────────────
        # Correction (trades clôturés récemment absents du journal) :
        #  - AVANT : fenêtre [last_sync, maintenant UTC]. Or les horodatages
        #    des deals MT5 sont en heure du SERVEUR du courtier (typiquement
        #    UTC+2/+3) : un trade clôturé il y a moins de ~3 h portait une
        #    date « dans le futur » par rapport à `maintenant UTC` et sortait
        #    de la fenêtre. Pire, le tour suivant repartait de `last_sync`,
        #    donc un trade dont l'entrée était dans un lot et la sortie dans
        #    le suivant était perdu pour de bon (« sortie sans entrée »).
        #  - MAINTENANT : borne haute = maintenant + 1 jour (couvre tous les
        #    fuseaux de courtier) et borne basse reculée de SYNC_OVERLAP. Le
        #    recouvrement est sans risque : tickets et deals « balance » sont
        #    dédoublonnés, et une position déjà à jour est ignorée.
        from_date = last_sync - SYNC_OVERLAP
        start = account.start_date if account else None
        if start is not None:
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if from_date < start:
                from_date = start  # jamais avant la date de départ choisie
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        to_date = datetime.now(timezone.utc) + SYNC_FUTURE_MARGIN

        # Garde d'ISOLATION : le terminal MT5 n'a qu'UNE session, et
        # l'utilisateur peut l'avoir basculée sur un autre compte depuis le
        # terminal lui-même.
        terminal_info = self.get_account_info()
        terminal_login = terminal_info.get("login") if terminal_info else None
        if self._login and terminal_login and terminal_login != self._login:
            raise RuntimeError(
                f"Le terminal MetaTrader 5 est connecté au compte {terminal_login}, pas au "
                f"compte {self._login} du journal — reconnectez le bon compte avant de synchroniser"
            )

        self._sync_account(db)

        count = 0
        changed = False  # changement visible par l'interface (voir data_version)

        # ── Phase A — positions actuellement ouvertes ───────────────────
        open_tickets: set = set()
        open_positions = api.positions_get()
        if open_positions:
            for pos in open_positions:
                open_tickets.add(pos.ticket)
                pips = price_to_pips(pos.price_current - pos.price_open, pos.symbol, api)

                existing = db.query(Trade).filter(
                    Trade.account_id == self._login,
                    Trade.ticket == pos.ticket,
                ).first()
                if existing is not None and existing.source != "mt5":
                    # Trade saisi / importé : la synchro ne le modifie jamais.
                    continue
                if existing:
                    if not existing.is_open:
                        changed = True  # trade « rouvert » côté courtier
                    existing.profit = pos.profit
                    existing.close_price = pos.price_current
                    existing.sl = pos.sl or None
                    # `initial_sl` renseigné rétroactivement au premier stop
                    # observé, jamais ensuite (breakeven/trailing ne doivent
                    # pas réécrire le risque initial).
                    if existing.initial_sl is None and pos.sl:
                        existing.initial_sl = pos.sl
                    existing.tp = pos.tp or None
                    existing.pips = pips
                    existing.commission = pos.commission
                    existing.swap = pos.swap
                    existing.is_open = True
                    if existing.margin is None:
                        existing.margin = self._calc_margin(
                            api, pos.symbol, "buy" if pos.type == 0 else "sell",
                            pos.volume, pos.price_open)
                else:
                    db.add(Trade(
                        ticket=pos.ticket,
                        account_id=self._login,
                        symbol=pos.symbol,
                        direction="buy" if pos.type == 0 else "sell",
                        volume=pos.volume,
                        open_price=pos.price_open,
                        close_price=pos.price_current,
                        sl=pos.sl or None,
                        initial_sl=pos.sl or None,
                        tp=pos.tp or None,
                        open_time=datetime.fromtimestamp(pos.time, timezone.utc).replace(tzinfo=None),
                        profit=pos.profit,
                        commission=pos.commission,
                        swap=pos.swap,
                        pips=pips,
                        margin=self._calc_margin(
                            api, pos.symbol, "buy" if pos.type == 0 else "sell",
                            pos.volume, pos.price_open),
                        is_open=True,
                    ))
                    count += 1
                    changed = True

        # ── Phase B — historique des deals (fenêtre glissante) ──────────
        deals = api.history_deals_get(from_date, to_date)
        if deals is None:
            logger.error("history_deals_get failed for account %s: %s", self._login, api.last_error())
            raise RuntimeError(
                "Lecture de l'historique MT5 impossible; la prochaine synchronisation réessaiera la même fenêtre"
            )

        positions: dict = {}
        # Les deals de type « balance » ne sont pas des trades : ils
        # alimentent le journal des mouvements de capital (dépôts/retraits).
        balance_type = getattr(api, "DEAL_TYPE_BALANCE", 2)
        seen_movements: set = set()
        for deal in deals:
            if getattr(deal, "type", None) == balance_type:
                if self._record_balance_deal(db, deal, seen_movements):
                    changed = True
                continue
            self._add_deal(api, positions.setdefault(deal.position_id, {"ins": [], "outs": []}), deal)

        # Rattrapage : un trade resté « ouvert » en base alors que le courtier
        # ne le liste plus parmi les positions ouvertes a été clôturé hors de
        # la fenêtre ci-dessus (app fermée, clôture pendant une coupure…).
        # Sans ce filet il restait « ouvert » indéfiniment. On ne touche à
        # rien tant que le courtier ne fournit pas de deal de sortie.
        stale_open = [
            t.ticket for t in db.query(Trade).filter(
                Trade.account_id == self._login,
                Trade.source == "mt5",
                Trade.is_open.is_(True),
            ).all() if t.ticket not in open_tickets
        ]
        for pid in stale_open:
            if positions.get(pid, {}).get("outs"):
                continue
            full = self._position_deals(api, pid)
            if full["outs"]:
                positions[pid] = full

        for pid, group in positions.items():
            ins, outs = group["ins"], group["outs"]

            if pid in open_tickets:
                # Position toujours ouverte (clôture partielle) : la Phase A
                # a déjà mis à jour ses champs dynamiques.
                continue
            if not outs:
                # Entrée seule : position ouverte non listée (ou deal non
                # trade). On ignore plutôt que de créer un trade incomplet.
                continue

            existing = db.query(Trade).filter(
                Trade.account_id == self._login,
                Trade.ticket == pid,
            ).first()
            if existing is not None and existing.source != "mt5":
                continue  # Trade saisi / importé : jamais modifié par la synchro.

            last_out = datetime.fromtimestamp(max(d.time for d in outs), timezone.utc).replace(tzinfo=None)
            if existing is not None and existing.is_open is False and existing.close_time == last_out:
                continue  # déjà à jour : le recouvrement de fenêtre ne réécrit rien

            # Agrégation sur les deals COMPLETS de la position, pas sur ce que
            # la fenêtre en contient : une position renforcée à cheval sur la
            # borne basse aurait sinon un volume / prix d'entrée partiels.
            full = self._position_deals(api, pid)
            if full["outs"] and (full["ins"] or existing):
                ins, outs = full["ins"], full["outs"]
            if not ins and not existing:
                logger.warning(
                    "Deal(s) de sortie sans entrée connue pour la position %s — ignoré", pid
                )
                continue

            agg = aggregate_closed_position(
                ins, outs,
                deal_type_buy=api.DEAL_TYPE_BUY,
                reason_sl=getattr(api, "DEAL_REASON_SL", None),
                reason_tp=getattr(api, "DEAL_REASON_TP", None),
                mt5_api=api,
            )

            if ins:
                open_price, open_time, volume, direction = (
                    agg["open_price"], agg["open_time"], agg["volume"], agg["direction"]
                )
            else:
                # Entrée introuvable chez le courtier : on garde ce que la base connaît.
                open_price = existing.open_price
                open_time = existing.open_time
                volume = existing.volume
                direction = existing.direction

            close_price, close_time = agg["close_price"], agg["close_time"]
            profit, commission, swap = agg["profit"], agg["commission"], agg["swap"]
            pips = price_to_pips(close_price - open_price, outs[0].symbol, api)
            exit_reason = agg["exit_reason"]
            initial_stop = self._initial_stop(api, pid)

            if existing:
                if existing.is_open:
                    count += 1  # trade nouvellement clôturé
                existing.close_price = close_price
                existing.close_time = close_time
                existing.profit = profit
                existing.commission = commission
                existing.swap = swap
                existing.pips = pips
                existing.volume = volume
                existing.is_open = False
                if existing.margin is None:
                    existing.margin = self._calc_margin(api, outs[0].symbol, direction, volume, open_price)
                if initial_stop and not existing.initial_sl:
                    existing.initial_sl = initial_stop
                    existing.sl = initial_stop
                if exit_reason:
                    existing.exit_reason = exit_reason
                # Champs « journal » (notes, tags…) : jamais touchés ici.
            else:
                db.add(Trade(
                    ticket=pid,
                    account_id=self._login,
                    symbol=outs[0].symbol,
                    direction=direction,
                    volume=volume,
                    open_price=open_price,
                    close_price=close_price,
                    open_time=open_time,
                    close_time=close_time,
                    sl=initial_stop,
                    initial_sl=initial_stop,
                    profit=profit,
                    commission=commission,
                    swap=swap,
                    pips=pips,
                    comment=outs[-1].comment,
                    exit_reason=exit_reason,
                    margin=self._calc_margin(api, outs[0].symbol, direction, volume, open_price),
                    is_open=False,
                ))
                count += 1
            changed = True

        # `last_sync` : repère informatif. La fenêtre recule de SYNC_OVERLAP à
        # chaque tour, donc ce curseur n'a plus à être exact à la seconde.
        account = db.query(Account).filter(Account.login == self._login).first()
        if account:
            account.last_sync = now_naive

        # Les trades ajoutés ci-dessus doivent être visibles des requêtes qui
        # suivent (autoflush désactivé sur ces sessions).
        db.flush()
        self._backfill_margins(db, api)
        if account:
            self._calibrate_reference_capital(db, account)
            # Relevé d'équité RÉELLE (solde, équité, marge) pris à chaque synchro.
            from services import equity as equity_service
            equity_service.record_snapshot(db, account, self.get_account_info())

        db.commit()
        if changed:
            self._data_version += 1
        return count

    # ── Phase 6 : marge, calibrage du capital ────────────────────────────

    @staticmethod
    def _calc_margin(api, symbol: str, direction: str, volume: float, price: float) -> Optional[float]:
        """Marge (devise du compte) qu'immobilise une position, calculée par
        MetaTrader 5 (`order_calc_margin`). None si le terminal ne sait pas la
        calculer (symbole absent du Market Watch, API indisponible...) : la
        charge du dépôt retombera alors sur une estimation locale.

        Le terminal applique les règles ACTUELLES du courtier (levier,
        marge initiale) : pour une position clôturée depuis longtemps c'est
        une estimation, pas la valeur qu'avait le compte ce jour-là."""
        calc = getattr(api, "order_calc_margin", None)
        if calc is None or not symbol or not volume or not price:
            return None
        try:
            order_type = getattr(api, "ORDER_TYPE_BUY", 0) if direction == "buy" else getattr(api, "ORDER_TYPE_SELL", 1)
            value = calc(order_type, symbol, float(volume), float(price))
        except Exception:
            logger.debug("order_calc_margin(%s) indisponible", symbol, exc_info=True)
            return None
        if value is None or value != value or value < 0:
            return None
        return round(float(value), 2)

    def _backfill_margins(self, db: Session, api, limit: int = 200) -> None:
        """Renseigne la marge des trades MT5 de ce compte qui n'en ont pas
        encore (trades antérieurs à la phase 6, ou symbole alors indisponible),
        `limit` par synchro pour ne pas ralentir la synchronisation."""
        if getattr(api, "order_calc_margin", None) is None:
            return
        rows = (
            db.query(Trade)
            .filter(Trade.account_id == self._login, Trade.source == "mt5", Trade.margin.is_(None))
            .order_by(Trade.open_time.desc())
            .limit(limit)
            .all()
        )
        for trade in rows:
            trade.margin = self._calc_margin(api, trade.symbol, trade.direction, trade.volume, trade.open_price)

    def _calibrate_reference_capital(self, db: Session, account: Account) -> None:
        """Ancre UNE fois le capital de référence d'un compte « historical » sur
        le solde réel du courtier.

        À l'initialisation historique, `initial_balance` reçoit le solde
        ACTUEL alors que l'historique importé se rejoue ensuite par-dessus :
        la courbe finissait au-dessus du solde réel de tout le résultat de
        la période. Après la première synchro on pose donc
            capital de départ = solde − Σ net clôturé − Σ mouvements
        et on lève le drapeau : le capital reste ensuite figé (décision de
        la correction #3), jamais recalculé à chaque synchro.
        """
        if account.capital_calibrated is not False or account.initialization_mode != "historical":
            return
        from services import movements as movement_service
        closed = db.query(Trade).filter(
            Trade.account_id == account.login, Trade.is_open.is_(False)).all()
        net_closed = sum((t.profit or 0.0) + (t.commission or 0.0) + (t.swap or 0.0) for t in closed)
        account.initial_balance = round(
            (account.balance or 0.0) - net_closed - movement_service.movements_sum(db, account.login), 2)
        account.capital_calibrated = True

    def _record_balance_deal(self, db: Session, deal, seen: set) -> bool:
        """Enregistre un deal « balance » (dépôt si profit > 0, retrait si
        < 0) dans le journal des mouvements de capital, sans doublon
        (compte + numéro de deal). La fenêtre de synchro étant incrémentale
        mais pouvant se recouvrir, l'unicité est vérifiée en base ET dans le
        lot courant (l'autoflush est désactivé)."""
        try:
            ticket = int(deal.ticket)
            amount = round(float(deal.profit), 2)
        except (TypeError, ValueError, AttributeError):
            logger.warning("Deal « balance » illisible ignoré : %r", deal)
            return False
        if amount == 0 or ticket in seen:
            return False
        seen.add(ticket)
        already = db.query(CapitalMovement.id).filter(
            CapitalMovement.account_id == self._login, CapitalMovement.ticket == ticket
        ).first()
        if already:
            return False
        db.add(CapitalMovement(
            account_id=self._login,
            ticket=ticket,
            time=datetime.fromtimestamp(deal.time, timezone.utc).replace(tzinfo=None),
            amount=amount,
            comment=(getattr(deal, "comment", None) or None),
            source="mt5",
        ))
        return True

    def _sync_account(self, db: Session) -> None:

        info = self.get_account_info()
        if not info:
            return

        existing = db.query(Account).filter(Account.login == info["login"]).first()
        if existing:
            existing.server = info["server"]
            existing.name = info["name"]
            existing.currency = info["currency"]
            existing.balance = info["balance"]
            existing.equity = info["equity"]
            existing.margin = info["margin"]
            existing.free_margin = info["free_margin"]
            existing.leverage = info["leverage"]
            if existing.initial_balance is None:
                existing.initial_balance = info["balance"]
            if existing.initial_equity is None:
                existing.initial_equity = info["equity"]
        else:
            db.add(Account(
                login=info["login"],
                server=info["server"],
                name=info["name"],
                currency=info["currency"],
                balance=info["balance"],
                equity=info["equity"],
                margin=info["margin"],
                free_margin=info["free_margin"],
                leverage=info["leverage"],
                initial_balance=info["balance"],
                initial_equity=info["equity"],
            ))
        db.commit()

    def register_account(self, db: Session) -> None:
        """Enregistre/actualise le compte (solde, equity, devise...) SANS
        importer aucun trade. Utilisé par POST /api/mt5/connect (voir
        main.py) : la connexion à MT5 ne doit plus, à elle seule, déclencher
        un import complet de l'historique — l'utilisateur doit d'abord
        choisir un mode via /api/mt5/accounts/{login}/initialize."""
        self._sync_account(db)

    def initialize_journal_from_now(self, db: Session) -> Account:

        self._sync_account(db)  # crée/actualise la ligne Account si besoin
        info = self.get_account_info()
        account = db.query(Account).filter(Account.login == info["login"]).first()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        account.initial_balance = info["balance"]
        account.initial_equity = info["equity"]
        account.start_date = now
        account.initialization_mode = "from_now"
        account.last_sync = now
        account.capital_calibrated = True   # solde actuel = capital de départ, exact par construction
        db.commit()
        db.refresh(account)
        return account

    def initialize_journal_historical(self, db: Session, start_date: datetime) -> Account:

        if start_date.tzinfo is not None:
            start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)

        self._sync_account(db)  # crée/actualise la ligne Account si besoin
        info = self.get_account_info()
        account = db.query(Account).filter(Account.login == info["login"]).first()

        account.initial_balance = info["balance"]
        account.initial_equity = info["equity"]
        account.start_date = start_date
        account.initialization_mode = "historical"
        account.last_sync = start_date
        # `initial_balance` = solde actuel : sera recalé par la première synchro.
        account.capital_calibrated = False
        db.commit()
        db.refresh(account)
        return account

    def _seed_demo_data(self, db: Session) -> int:

        from datetime import timedelta
        login = self._login or 0

        account = db.query(Account).filter(Account.login == login).first()
        if not account:
            account = Account(
                login=login, server="Demo-Server (simulation)", name="Compte Démo",
                currency="USD", balance=10000, equity=10000,
                margin=0, free_margin=10000, leverage=100,
                initial_balance=10000,
            )
            db.add(account)
            db.commit()
        elif account.initial_balance is None:
            account.initial_balance = account.balance
            db.commit()

        # Ce login a déjà ses trades de démo : ne pas les dupliquer.
        if db.query(Trade).filter(Trade.account_id == login).count() > 0:
            return 0

        demo_trades = [
            ("EURUSD","buy",0.5,1.0850,1.0882,320,32),
            ("GBPUSD","sell",0.3,1.2710,1.2724,-140,-14),
            ("XAUUSD","buy",0.2,2310.0,2339.0,580,29),
            ("USDJPY","sell",1.0,149.50,149.72,-220,-22),
            ("EURUSD","sell",0.5,1.0900,1.0859,410,41),
            ("GBPUSD","buy",0.4,1.2650,1.2669,190,19),
            ("XAUUSD","sell",0.3,2350.0,2365.0,-310,-15),
            ("EURUSD","buy",0.5,1.0820,1.0846,260,26),
            ("USDJPY","buy",0.8,148.80,149.23,430,43),
            ("GBPUSD","sell",0.2,1.2700,1.2709,-90,-9),
            ("XAUUSD","buy",0.5,2280.0,2317.0,740,37),
            ("EURUSD","sell",0.3,1.0950,1.0932,180,18),
        ]

        base = datetime(2026, 4, 20)
        count = 0
        for i, (sym, dir_, vol, op, cp, pnl, pips) in enumerate(demo_trades):
            db.add(Trade(
                ticket=login * 1000 + i,
                account_id=login,
                symbol=sym, direction=dir_,
                volume=vol, open_price=op, close_price=cp,
                open_time=base + timedelta(days=i, hours=9),
                close_time=base + timedelta(days=i, hours=14),
                profit=pnl, commission=-2.5, swap=0,
                pips=float(pips), is_open=False,
            ))
            count += 1
        db.commit()
        return count
