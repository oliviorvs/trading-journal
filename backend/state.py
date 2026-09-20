from sqlalchemy.orm import Session
from typing import Optional
import json
import logging
import os
import threading

from database import SessionLocal
from models import Account, Attachment, EquitySnapshot, Settings, Trade
import mt5_service
from mt5_service import MT5Service
from paths import get_app_root
import crypto_utils

LOGS_DIR = os.path.join(get_app_root(), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "backend.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def utcnow():
    """Instant UTC courant, SANS fuseau attaché.

    `datetime.utcnow()` est déprécié depuis Python 3.12 (suppression prévue)
    et provoquait un avertissement à chaque appel. `datetime.now(timezone.utc)`
    est l'équivalent recommandé ; on retire ensuite le tzinfo pour rester
    strictement compatible avec les colonnes DateTime SQLite du projet, qui
    stockent toutes des instants UTC naïfs.
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Pièces jointes ───────────────────────────────────────────────────────────
UPLOADS_DIR = os.path.join(get_app_root(), "backend", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
ALLOWED_ATTACHMENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_ATTACHMENT_SIZE = 8 * 1024 * 1024  # 8 Mo
ATTACHMENT_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ATTACHMENT_CHUNK_SIZE = 1024 * 1024  # 1 Mo


def detect_attachment_type(header: bytes) -> Optional[str]:
    """Détecte le format réel d'une image depuis sa signature binaire."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None

# ── Service MT5 (singleton partagé par tous les routers) ────────────────────
mt5 = MT5Service()

# ── Multi-comptes MT5 ────────────────────────────────────────────────────────
MAX_SAVED_ACCOUNTS = 4

DEFAULT_SETUP_TYPES = ["breakout", "pullback", "news", "range", "autre"]
DEFAULT_EMOTION_TYPES = ["calme", "stresse", "confiant"]
DEFAULT_ERROR_TYPES = ["aucune", "entree_trop_tot", "sur_sizing", "pas_de_stop", "fomo", "autre"]


def get_active_account(db: Session) -> Optional[Account]:
    return db.query(Account).filter(Account.is_active.is_(True)).first()


def active_login(db: Session) -> Optional[int]:
    acc = get_active_account(db)
    return acc.login if acc else None


MODE_MT5 = "mt5"
MODE_MANUAL = "manual"


def is_manual(account: Optional[Account]) -> bool:
    """Vrai pour un compte sans connexion MT5 (mode "manual")."""
    return bool(account and account.mode == MODE_MANUAL)


def next_manual_login(db: Session) -> int:
    """Prochain identifiant synthétique NÉGATIF (-1, -2…) pour un compte manuel.

    On prend le minimum entre les logins de comptes ET les `account_id` déjà
    portés par des trades : si un compte manuel a été supprimé SANS ses
    trades (`delete_trades=false`), ses trades restent en base sous l'ancien
    identifiant — le réutiliser les rattacherait par erreur au nouveau compte.
    """
    from sqlalchemy import func
    from models import CapitalMovement
    lowest_account = db.query(func.min(Account.login)).scalar()
    lowest_trade = db.query(func.min(Trade.account_id)).scalar()
    # Les mouvements de capital (phase 6) restent eux aussi sous l'ancien
    # identifiant quand un compte est supprimé sans ses données.
    lowest_movement = db.query(func.min(CapitalMovement.account_id)).scalar()
    # Idem pour les relevés d'équité et les pièces jointes rattachées à un
    # compte : réutiliser un identifiant encore porté par ces lignes les
    # donnerait à un autre compte (isolation des données).
    lowest_snapshot = db.query(func.min(EquitySnapshot.account_id)).scalar()
    lowest_attachment = db.query(func.min(Attachment.account_id)).scalar()
    lowest = min(v for v in (
        lowest_account, lowest_trade, lowest_movement, lowest_snapshot, lowest_attachment, 0
    ) if v is not None)
    return lowest - 1


def manual_capital(db: Session, account: Account) -> float:
    """Capital courant d'un compte MANUEL — source de vérité UNIQUE :

        capital de départ + Σ mouvements de capital (dépôts − retraits)
                          + Σ (profit + commission + swap) des trades clôturés

    Calculé en NET (le profit seul ignorerait commission et swap, alors que
    le solde réel du broker les intègre). Utilisé par la courbe d'équité, le
    drawdown, `capital_before` (risque en %, R-multiple) et le solde affiché
    du compte. Les mouvements de capital (phase 6, voir
    services/movements.py) font varier ce capital sans jamais compter comme
    un gain ou une perte.
    """
    from sqlalchemy import func
    from services import movements
    net = db.query(
        func.coalesce(func.sum(
            func.coalesce(Trade.profit, 0.0)
            + func.coalesce(Trade.commission, 0.0)
            + func.coalesce(Trade.swap, 0.0)
        ), 0.0)
    ).filter(Trade.account_id == account.login, Trade.is_open.is_(False)).scalar()
    return (
        (account.initial_balance or 0.0)
        + movements.movements_sum(db, account.login)
        + float(net or 0.0)
    )


def sync_manual_balance(db: Session, account: Optional[Account]) -> None:
    """Aligne `balance` / `equity` / `margin` stockés d'un compte manuel sur
    ses valeurs calculées. Idempotent, ne commit que si une valeur change.
    Sans effet sur un compte MT5 (son solde vient du broker).

    - solde  = capital calculé (voir `manual_capital`) : trades CLÔTURÉS seuls ;
    - équité = solde + résultat flottant SAISI sur les trades ouverts
      (profit + commission + swap ; 0 tant que rien n'est saisi — aucun prix de
      marché n'est connu pour les valoriser autrement) ;
    - marge  = marge des trades ouverts (saisie ou estimée), marge libre =
      équité − marge.
    """
    if not is_manual(account):
        return
    from services import equity as equity_service
    capital = round(manual_capital(db, account), 6)
    floating = round(equity_service.manual_open_floating(db, account), 6)
    margin = round(equity_service.manual_open_margin(db, account), 6)
    equity = round(capital + floating, 6)
    free_margin = round(equity - margin, 6)

    def differs(current, target):
        return current is None or abs(current - target) > 1e-6

    if (differs(account.balance, capital) or differs(account.equity, equity)
            or differs(account.margin, margin) or differs(account.free_margin, free_margin)):
        account.balance = capital
        account.equity = equity
        account.margin = margin
        account.free_margin = free_margin
        db.commit()
        db.refresh(account)


def live_account_info(account: Optional[Account]) -> Optional[dict]:
    """Infos temps réel du courtier POUR CE COMPTE, ou None. Le terminal MT5
    n'a qu'une session : si elle est ouverte sur un AUTRE compte (ou en
    simulation), ses chiffres ne doivent jamais servir à ce compte-ci."""
    if not account or is_manual(account):
        return None
    if mt5.is_simulated() or mt5.current_login != account.login:
        return None
    info = mt5.get_account_info()
    if not info or info.get("simulated") or info.get("login") not in (None, account.login):
        return None
    return info


def filter_active(q, db: Session):

    login = active_login(db)
    if login is not None:
        return q.filter(Trade.account_id == login)
    return q.filter(Trade.account_id.is_(None))


def set_active_account(db: Session, login: int) -> None:
    """Active un seul compte à la fois (désactive tous les autres)."""
    db.query(Account).filter(Account.login != login).update({Account.is_active: False})
    acc = db.query(Account).filter(Account.login == login).first()
    if acc:
        acc.is_active = True
    db.commit()


def get_settings(db: Session) -> Settings:
    """Retourne la ligne unique de réglages, en la créant si besoin (ne
    devrait normalement plus arriver après le seed au démarrage)."""
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings(
            currency="USD",
            setup_types=json.dumps(DEFAULT_SETUP_TYPES),
            emotion_types=json.dumps(DEFAULT_EMOTION_TYPES),
            error_types=json.dumps(DEFAULT_ERROR_TYPES),
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def settings_payload(settings: Settings, db: Optional[Session] = None) -> dict:

    try:
        setup_types = json.loads(settings.setup_types or "[]")
    except (TypeError, json.JSONDecodeError):
        setup_types = []
    if not isinstance(setup_types, list) or not setup_types:
        setup_types = DEFAULT_SETUP_TYPES
    try:
        emotion_types = json.loads(settings.emotion_types or "[]")
    except (TypeError, json.JSONDecodeError):
        emotion_types = []
    if not isinstance(emotion_types, list) or not emotion_types:
        emotion_types = DEFAULT_EMOTION_TYPES
    try:
        error_types = json.loads(settings.error_types or "[]")
    except (TypeError, json.JSONDecodeError):
        error_types = []
    if not isinstance(error_types, list) or not error_types:
        error_types = DEFAULT_ERROR_TYPES
    currency = settings.currency
    if db is not None:
        account = get_active_account(db)
        if account and account.currency:
            currency = account.currency
    return {"currency": currency, "setup_types": setup_types, "emotion_types": emotion_types, "error_types": error_types}


def mt5_reference_capital(db: Session) -> Optional[float]:

    account = get_active_account(db)
    if not account:
        return None
    return account.initial_balance


def _remove_attachment_file(attachment: Attachment) -> None:
    file_path = os.path.join(UPLOADS_DIR, attachment.filename or "")
    try:
        if attachment.filename and os.path.isfile(file_path):
            os.remove(file_path)
    except OSError:
        # Un fichier verrouillé ou déjà absent ne doit pas empêcher le
        # nettoyage en base : on log et on continue, sinon une seule
        # capture bloquerait la suppression du compte entier.
        logger.warning("Suppression du fichier %s impossible", file_path, exc_info=True)


def purge_orphan_attachments(db: Session, account_id: Optional[int], candidate_tickets) -> int:
    """Supprime lignes ET fichiers des pièces jointes du compte `account_id`
    devenues orphelines : plus AUCUN trade DE CE COMPTE ne porte leur ticket.

    Une pièce jointe appartient à un couple (compte, ticket) : deux comptes
    peuvent porter le même numéro de ticket sans partager leurs captures. Le
    nettoyage ne regarde donc que ce compte — jamais les autres.

    Retourne le nombre de pièces jointes supprimées. À appeler APRÈS la
    suppression des trades concernés et avant le commit.
    """
    candidate_tickets = {t for t in candidate_tickets if t is not None}
    if account_id is None or not candidate_tickets:
        return 0

    still_used = {
        ticket
        for (ticket,) in db.query(Trade.ticket).filter(
            Trade.account_id == account_id, Trade.ticket.in_(candidate_tickets)
        ).all()
    }
    orphan_tickets = candidate_tickets - still_used
    if not orphan_tickets:
        return 0

    orphans = db.query(Attachment).filter(
        Attachment.account_id == account_id, Attachment.trade_ticket.in_(orphan_tickets)
    ).all()
    for attachment in orphans:
        _remove_attachment_file(attachment)
        db.delete(attachment)
    if orphans:
        logger.info("%s pièce(s) jointe(s) orpheline(s) supprimée(s)", len(orphans))
    return len(orphans)


def delete_account_data(db: Session, login: int) -> None:
    """Supprime TOUTES les données rattachées à un compte (trades, pièces
    jointes — lignes et fichiers —, mouvements de capital, relevés d'équité).
    Ne touche à aucun autre compte. Ne supprime pas la ligne `Account` et ne
    commit pas."""
    from models import CapitalMovement
    for attachment in db.query(Attachment).filter(Attachment.account_id == login).all():
        _remove_attachment_file(attachment)
        db.delete(attachment)
    db.query(Trade).filter(Trade.account_id == login).delete()
    db.query(CapitalMovement).filter(CapitalMovement.account_id == login).delete()
    db.query(EquitySnapshot).filter(EquitySnapshot.account_id == login).delete()
    _delete_analyzer_data(db, login)
    db.flush()


def _delete_analyzer_data(db: Session, login: int) -> None:
    """Purge les tables de l'Analyzer rattachées à ce compte.

    Isolé dans sa propre fonction et enveloppé d'un import local : si le
    module Analyzer est retiré (désactivation du chantier), la suppression
    d'un compte continue de fonctionner sans modification de ce fichier.

    `analyzer_results` et `analyzer_patterns` ne portent pas d'`account_id` :
    elles sont rattachées par `run_id`, donc purgées via leurs runs — sans
    quoi elles resteraient orphelines en base.
    """
    try:
        from services.analyzer.models import (
            ACCOUNT_SCOPED_MODELS, AnalyzerPattern, AnalyzerResult, AnalyzerRun,
            AnalyzerSopItem, AnalyzerSopVersion,
        )
    except ImportError:  # pragma: no cover — module Analyzer absent
        return
    # Même raisonnement que les runs : les éléments de checklist pendent aux
    # VERSIONS de SOP, pas au compte.
    version_ids = [
        row[0] for row in
        db.query(AnalyzerSopVersion.id).filter(AnalyzerSopVersion.account_id == login).all()
    ]
    if version_ids:
        db.query(AnalyzerSopItem).filter(AnalyzerSopItem.sop_version_id.in_(version_ids)).delete(
            synchronize_session=False)
    run_ids = [
        row[0] for row in
        db.query(AnalyzerRun.id).filter(AnalyzerRun.account_id == login).all()
    ]
    if run_ids:
        db.query(AnalyzerResult).filter(AnalyzerResult.run_id.in_(run_ids)).delete(
            synchronize_session=False)
        db.query(AnalyzerPattern).filter(AnalyzerPattern.run_id.in_(run_ids)).delete(
            synchronize_session=False)
    for model in ACCOUNT_SCOPED_MODELS:
        db.query(model).filter(model.account_id == login).delete(synchronize_session=False)


def seed_default_settings() -> None:
    """Garantit qu'une ligne de réglages existe toujours (devise), même
    avant toute connexion MT5. Appelé une fois au démarrage de l'app."""
    with SessionLocal() as db:
        if not db.query(Settings).first():
            db.add(Settings(
                currency="USD",
                setup_types=json.dumps(DEFAULT_SETUP_TYPES),
                emotion_types=json.dumps(DEFAULT_EMOTION_TYPES),
                error_types=json.dumps(DEFAULT_ERROR_TYPES),
            ))
            db.commit()


def auto_connect_mt5() -> None:
    """Reconnecte le compte actif enregistré lors du démarrage de l'API.

    GARDÉE par la même vérification que la boucle de fond
    (mt5_service.is_terminal_running()) — et c'était le trou qui restait.

    Cette fonction appelait `mt5.connect()` sans aucune condition à chaque
    lancement de l'application : pour quiconque a un compte actif enregistré
    avec mot de passe (le cas normal), ça revenait à FORCER l'ouverture de
    MT5 à chaque démarrage du journal, que l'utilisateur l'ait fermé la
    veille ou non — exactement le problème signalé. La boucle de fond
    (state.passive_reconnect_worker) avait déjà été corrigée pour vérifier
    l'environnement avant de tenter quoi que ce soit ; ce chemin-ci, lui,
    ne l'avait pas été.

    Le principe reste le même partout : `initialize()`, la fonction native
    qu'appelle `connect()`, relance le terminal s'il n'est pas déjà ouvert.
    Donc AUCUNE tentative automatique — ici ou ailleurs — ne doit
    l'appeler sans avoir d'abord confirmé, par un moyen qui ne passe
    jamais par l'API MetaTrader5, que le terminal tourne déjà.

    Terminal fermé au lancement de l'app ⇒ cette fonction ne fait
    STRICTEMENT rien. La restauration de la session se fera d'elle-même,
    sans action supplémentaire, dès que l'utilisateur ouvrira MT5 : la
    boucle de fond (déjà active, démarrée juste après ce thread — voir le
    lifespan de main.py) la détectera au cycle suivant et tentera la
    connexion à ce moment-là. Rien à cliquer, rien à relancer : c'est la
    « tentative automatique » demandée, simplement réordonnée pour ne
    jamais précéder la disponibilité réelle du terminal.

    Nuance importante : la garde ne s'applique QUE si MetaTrader5 est
    réellement disponible sur ce poste. En mode simulation (le paquet
    MetaTrader5 n'est pas installé — voir requirements.txt), il n'existe
    par définition aucun terminal réel à protéger d'une relance
    intempestive : `mt5_module()` ci-dessous résout d'abord cette
    disponibilité (elle bloque CETTE fonction le temps de l'import, mais
    tourne dans son propre thread démon — voir main.py — donc jamais le
    serveur ni la fenêtre) avant de décider si la garde s'applique.
    """
    # Résout la disponibilité de MetaTrader5 AVANT de décider si la garde
    # ci-dessous s'applique : en mode simulation (api is None), il n'y a
    # aucun terminal réel à protéger, donc aucune raison de bloquer la
    # restauration automatique de la session simulée.
    api = mt5_service.mt5_module()
    if api is not None and not mt5_service.is_terminal_running():
        logger.info(
            "MT5 fermé au démarrage de l'application — connexion automatique "
            "ignorée (la boucle de fond la tentera dès que le terminal sera ouvert)"
        )
        return

    import crypto_utils

    with SessionLocal() as db:
        account = get_active_account(db)
        if not account:
            logger.info("Aucun compte MT5 actif enregistré au démarrage")
            return
        if is_manual(account):
            logger.info("Compte actif manuel (%s) — aucune connexion MT5 à restaurer", account.login)
            return
        if not account.password:
            logger.warning("Compte MT5 actif %s sans mot de passe enregistré", account.login)
            return

        try:
            password = crypto_utils.decrypt(account.password)
        except Exception:
            logger.exception(
                "Mot de passe illisible pour le compte MT5 actif %s "
                "(clé de chiffrement changée ?)",
                account.login,
            )
            return

        if mt5.connect(account.login, password, account.server):
            logger.info("Connexion automatique MT5 réussie pour le compte %s", account.login)
            if not account.initialization_mode:
                logger.info(
                    "Synchronisation automatique ignorée pour le compte %s : "
                    "journal non initialisé",
                    account.login,
                )
                return
            try:
                synced = mt5.sync_trades(db)
                logger.info(
                    "Synchronisation automatique MT5 terminée pour le compte %s : %s trade(s)",
                    account.login,
                    synced,
                )
            except Exception:
                logger.exception(
                    "Synchronisation automatique MT5 échouée pour le compte %s",
                    account.login,
                )
        else:
            logger.warning("Connexion automatique MT5 échouée pour le compte %s", account.login)


def reconnect_active_account(db: Session) -> bool:
    """Reconnecte le compte actif.

    Deux appelants, tous deux sûrs vis-à-vis du risque de relance
    intempestive du terminal :
    1. Une action explicite de l'utilisateur (clic sur le compte actif
       dans le sélecteur, bouton « Relancer MT5 » — voir routers/mt5.py,
       POST /reconnect).
    2. `passive_reconnect_worker` ci-dessous — UNE BOUCLE DE FOND, mais qui
       n'appelle cette fonction qu'après avoir vérifié, par des moyens qui
       ne touchent jamais l'API MetaTrader5, que le terminal tourne déjà.

    ── Pourquoi une boucle de fond « bête » a été retirée, puis réintroduite
       sous cette forme ────────────────────────────────────────────────────
    Une version précédente rappelait cette fonction toutes les ~60 secondes
    sans condition. `mt5.connect()` appelle `initialize()`, qui RELANCE LE
    TERMINAL s'il n'est pas déjà ouvert : un utilisateur qui fermait
    volontairement MT5 se retrouvait avec le terminal qui se rouvrait tout
    seul quelques dizaines de secondes plus tard. Cette boucle a donc été
    supprimée.
    Elle revient ici, mais gardée : `passive_reconnect_worker` ne l'appelle
    QUE si `mt5_service.is_terminal_running()` (liste des process système,
    jamais l'API MT5) confirme que le terminal tourne déjà — auquel cas
    `initialize()` s'attache à ce terminal au lieu d'en lancer un nouveau,
    et `mt5_service.has_internet()` confirme qu'une tentative a une chance
    d'aboutir. Terminal fermé ⇒ la boucle ne fait STRICTEMENT rien.
    """
    account = get_active_account(db)
    if not account or is_manual(account) or not account.password:
        return False
    try:
        password = crypto_utils.decrypt(account.password)
    except Exception:
        logger.exception("Mot de passe illisible pour le compte MT5 actif %s", account.login)
        return False
    if mt5.connect(account.login, password, account.server):
        logger.info("Reconnexion MT5 réussie pour le compte %s", account.login)
        if account.initialization_mode:
            try:
                mt5.sync_trades(db)
            except Exception:
                logger.exception("Synchronisation après reconnexion échouée pour le compte %s", account.login)
        return True
    return False


# ── Boucle de reconnexion passive ────────────────────────────────────────────
#
# « Idéale » au sens de la demande : elle vérifie que MT5 est ouvert ET que
# la machine a une connexion réseau avant de tenter quoi que ce soit ; si le
# terminal n'est pas ouvert, elle ne fait RIEN — ni appel MT5, ni tentative
# de relance. C'est la différence avec l'ancienne boucle inconditionnelle :
# celle-ci ne peut jamais provoquer l'ouverture de MT5, elle ne fait que
# rattacher une session à un terminal déjà là.
PASSIVE_RECONNECT_POLL_S = 20.0

_passive_reconnect_stop = threading.Event()
_passive_reconnect_started = False


def _passive_reconnect_cycle() -> None:
    if mt5.is_connected():
        return  # rien à faire : déjà connecté
    if not mt5_service.is_terminal_running():
        return  # MT5 fermé : on ne le rallume pas — c'est le cœur de la demande
    if not mt5_service.has_internet():
        return  # pas de réseau : une tentative échouerait de toute façon
    try:
        with SessionLocal() as db:
            if is_manual(get_active_account(db)):
                return  # compte manuel actif : rien à reconnecter
            reconnect_active_account(db)
    except Exception:
        # Une boucle de fond ne doit jamais s'arrêter sur une erreur
        # ponctuelle (base momentanément verrouillée, terminal qui répond
        # de travers) : on journalise et on retentera au tour suivant.
        logger.exception("Cycle de reconnexion passive MT5 échoué")


# ── Relevés d'équité en arrière-plan (phase 6) ──────────────────────────────
# Tant que le compte MT5 ACTIF est connecté, on relève son équité réelle
# (solde, équité, marge) toutes les SNAPSHOT_INTERVAL_S secondes : c'est la
# seule façon d'obtenir une courbe d'équité qui contienne le flottant et une
# charge du dépôt mesurée. Comme la reconnexion passive, ce cycle ne touche
# jamais à MT5 quand le terminal n'est pas déjà connecté au bon compte.
SNAPSHOT_INTERVAL_S = 300.0
_last_snapshot_monotonic = 0.0


def _snapshot_cycle(force: bool = False) -> bool:
    """Relève l'équité du compte MT5 actif si (et seulement si) le terminal
    est connecté à CE compte, hors simulation. Retourne True si un relevé a
    été enregistré. `force` ignore la cadence (tests)."""
    global _last_snapshot_monotonic
    import time
    from services import equity as equity_service
    now = time.monotonic()
    if not force and now - _last_snapshot_monotonic < SNAPSHOT_INTERVAL_S:
        return False
    try:
        with SessionLocal() as db:
            account = get_active_account(db)
            if not account or is_manual(account) or not account.initialization_mode:
                return False
            info = live_account_info(account)
            if not info:
                return False
            _last_snapshot_monotonic = now
            return equity_service.record_snapshot(db, account, info, commit=True)
    except Exception:
        # Comme la reconnexion : une boucle de fond ne s'arrête jamais sur
        # une erreur ponctuelle (base verrouillée, terminal qui répond mal).
        logger.exception("Relevé d'équité échoué")
        return False


def passive_reconnect_worker() -> None:
    while not _passive_reconnect_stop.wait(PASSIVE_RECONNECT_POLL_S):
        _passive_reconnect_cycle()
        _snapshot_cycle()


def start_passive_reconnect_worker() -> None:
    """Idempotent : appelé une fois au démarrage de l'API (lifespan)."""
    global _passive_reconnect_started
    if _passive_reconnect_started:
        return
    _passive_reconnect_started = True
    threading.Thread(target=passive_reconnect_worker, daemon=True, name="mt5-passive-reconnect").start()
