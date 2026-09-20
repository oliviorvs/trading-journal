from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone

from database import get_db
from models import Account, CapitalMovement, Trade
from schemas import AccountOut, MT5ConnectRequest, InitializeJournalRequest
from services import movements as movement_service
import crypto_utils
import state

router = APIRouter(prefix="/api/mt5", tags=["mt5"])
logger = state.logger

MANUAL_ACCOUNT_MSG = (
    "Cette action n'est pas disponible pour un compte manuel "
    "(aucune connexion MetaTrader 5)."
)


def _reject_manual(acc) -> None:
    """Refuse proprement une opération MT5 sur un compte manuel."""
    if state.is_manual(acc):
        raise HTTPException(400, MANUAL_ACCOUNT_MSG)


def _connect_failure_message() -> str:
    """Traduit la cause réelle du dernier échec de connexion (voir
    MT5Service.last_connect_error) en message actionnable pour
    l'utilisateur.

    Correction : ce message était auparavant toujours « vérifiez vos
    identifiants », quelle que soit la cause — y compris quand MT5 n'était
    pas lancé/installé ou que le serveur du broker était injoignable faute
    d'internet. Changer l'identifiant n'avait alors logiquement aucun
    effet. On distingue maintenant les trois cas.
    """
    detail = state.mt5.last_connect_error()
    if not detail:
        return "Connexion MT5 échouée — vérifiez vos identifiants"
    suffix = f" ({detail['detail']})" if detail.get("detail") else ""
    if detail["kind"] == "unreachable":
        return (
            "MetaTrader 5 est introuvable ou n'est pas lancé sur ce poste — "
            f"ouvrez le terminal MT5 puis réessayez{suffix}"
        )
    if detail["kind"] == "network":
        return (
            "Le serveur du broker est injoignable — vérifiez votre connexion "
            f"internet, puis réessayez{suffix}"
        )
    return f"Connexion MT5 échouée — identifiant, mot de passe ou serveur incorrect{suffix}"


@router.post("/connect", response_model=None)
def connect_mt5(body: MT5ConnectRequest, db: Session = Depends(get_db)):

    existing_logins = {a.login for a in db.query(Account.login).all()}
    if body.login not in existing_logins and len(existing_logins) >= state.MAX_SAVED_ACCOUNTS:
        raise HTTPException(
            400,
            f"Maximum {state.MAX_SAVED_ACCOUNTS} comptes enregistrés. "
            "Supprimez-en un avant d'en ajouter un nouveau.",
        )

    ok = state.mt5.connect(body.login, body.password, body.server)
    if not ok:
        raise HTTPException(400, _connect_failure_message())

    state.mt5.register_account(db)

    acc = db.query(Account).filter(Account.login == body.login).first()
    if acc:
        # Chiffré avant écriture (correction #5) — voir crypto_utils.py.
        acc.password = crypto_utils.encrypt(body.password)
        if body.label:
            acc.label = body.label
        elif not acc.label:
            acc.label = acc.name
        db.commit()
    state.set_active_account(db, body.login)

    needs_initialization = bool(acc and not acc.initialization_mode)
    return {
        "status": "connected",
        "account": state.mt5.get_account_info(),
        "needs_initialization": needs_initialization,
    }


@router.post("/accounts/{login}/initialize", response_model=AccountOut)
def initialize_journal(login: int, body: InitializeJournalRequest, db: Session = Depends(get_db)):
    """Choix du mode de démarrage du journal, à faire une fois par compte
    avant la première synchronisation des trades (voir connect_mt5).

    - "from_now"   : table rase — le journal démarre à l'état actuel du
      compte, sans importer les trades déjà clôturés avant ce moment.
    - "historical" : import complet des trades clôturés depuis `start_date`
      (obligatoire pour ce mode), en plus des positions déjà ouvertes.
    """
    acc = db.query(Account).filter(Account.login == login).first()
    if not acc:
        raise HTTPException(404, "Compte introuvable — connectez-vous d'abord (POST /api/mt5/connect)")
    _reject_manual(acc)
    if state.mt5.current_login != login:
        raise HTTPException(400, "Compte non connecté à MT5 — reconnectez-vous avant d'initialiser le journal")

    if body.mode == "from_now":
        return state.mt5.initialize_journal_from_now(db)

    if body.mode == "historical":
        if not body.start_date:
            raise HTTPException(400, "Date de départ requise pour le mode 'historical'")
        # `start_date` arrive de JSON : Pydantic le rend "aware" si le client
        # a envoyé un décalage (ex. "2026-01-01T00:00:00+01:00"), naïf sinon.
        # Comparer directement les deux formes lève un TypeError ("can't
        # compare offset-naive and offset-aware datetimes") — soit une 500 au
        # lieu d'un message clair. On normalise en UTC naïf avant de comparer,
        # comme le fait déjà initialize_journal_historical().
        start_date = body.start_date
        if start_date.tzinfo is not None:
            start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
        if start_date > datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(400, "La date de départ ne peut pas être dans le futur")
        # Correction : la valeur normalisée (UTC naïve) calculée juste
        # au-dessus n'était utilisée que pour la vérification "pas dans le
        # futur" — l'appel réel repartait de `body.start_date`, la valeur
        # BRUTE potentiellement encore « aware ». `initialize_journal_historical`
        # refait la même conversion en interne, donc ça restait correct,
        # mais les deux copies de cette logique pouvaient diverger au
        # prochain changement de l'une des deux : on réutilise maintenant la
        # valeur déjà normalisée.
        return state.mt5.initialize_journal_historical(db, start_date)

    raise HTTPException(400, "Mode d'initialisation inconnu (attendu : 'from_now' ou 'historical')")


@router.get("/accounts", response_model=List[AccountOut])
def list_mt5_accounts(db: Session = Depends(get_db)):
    """Liste les comptes MT5 enregistrés (jusqu'à MAX_SAVED_ACCOUNTS), pour
    le sélecteur de compte. Le mot de passe n'est jamais renvoyé (absent
    du schéma AccountOut)."""
    accounts = db.query(Account).order_by(Account.id).all()
    for acc in accounts:
        state.sync_manual_balance(db, acc)  # no-op pour un compte MT5
    return accounts


@router.post("/accounts/{login}/switch")
def switch_mt5_account(login: int, db: Session = Depends(get_db)):
    """Bascule le compte actif sur un compte déjà enregistré.

    Reconnecte réellement à MT5 avec les identifiants sauvegardés (MT5 ne
    gère qu'une session à la fois par terminal), puis resynchronise pour
    rafraîchir le solde et les derniers trades de ce compte avant de
    l'afficher.
    """
    acc = db.query(Account).filter(Account.login == login).first()
    if not acc:
        raise HTTPException(404, "Compte introuvable")
    if state.is_manual(acc):
        # Compte manuel : la bascule ne touche pas à MT5 (ni connexion, ni
        # synchronisation) — on change seulement le compte actif en base.
        state.set_active_account(db, login)
        state.sync_manual_balance(db, acc)
        db.refresh(acc)
        return {"status": "switched", "account": AccountOut.model_validate(acc).model_dump(mode="json")}
    if not acc.password:
        raise HTTPException(400, "Identifiants manquants pour ce compte — reconnectez-vous avec le mot de passe.")

    try:
        plain_password = crypto_utils.decrypt(acc.password)
    except Exception:
        logger.exception("Mot de passe illisible pour le compte %s (clé de chiffrement changée ?)", login)
        raise HTTPException(
            400,
            "Mot de passe illisible pour ce compte — reconnectez-vous avec le mot de passe.",
        )

    ok = state.mt5.connect(acc.login, plain_password, acc.server)
    if not ok:
        raise HTTPException(400, f"Bascule impossible — {_connect_failure_message()}")
    # Même garde-fou que POST /api/mt5/sync : un compte jamais initialisé
    # (rare ici, puisqu'il faut déjà avoir un mot de passe enregistré, mais
    # possible si l'initialisation a été interrompue) ne doit pas importer
    # l'historique complet par accident.
    state.set_active_account(db, login)
    if not acc.initialization_mode:
        return {
            "status": "switched",
            "account": state.mt5.get_account_info(),
            "needs_initialization": True,
        }
    state.mt5.sync_trades(db)
    return {"status": "switched", "account": state.mt5.get_account_info()}


@router.delete("/accounts/{login}", status_code=204)
def delete_mt5_account(login: int, delete_trades: bool = False, db: Session = Depends(get_db)):

    acc = db.query(Account).filter(Account.login == login).first()
    if not acc:
        raise HTTPException(404, "Compte introuvable")
    was_active = acc.is_active
    if delete_trades:
        # Toutes les données DE CE COMPTE, et de lui seul : trades, pièces
        # jointes (lignes ET fichiers — elles s'accumulaient autrefois sans
        # qu'aucune interface ne permette de les retrouver), mouvements de
        # capital et relevés d'équité. Une pièce jointe appartient à un couple
        # (compte, ticket) : un autre compte portant le même ticket garde les
        # siennes (voir state.delete_account_data).
        state.delete_account_data(db, login)
    db.delete(acc)
    db.commit()
    if was_active:
        remaining = db.query(Account).order_by(Account.last_sync.desc()).first()
        if remaining:
            state.set_active_account(db, remaining.login)


@router.post("/sync")
def sync_trades(db: Session = Depends(get_db)):

    acc = state.get_active_account(db)
    _reject_manual(acc)
    # Isolation : le terminal MT5 n'a qu'une session. Si elle n'est pas ouverte
    # sur le compte ACTIF du journal, synchroniser importerait les données d'un
    # autre compte (ou n'aurait aucun sens) — on refuse plutôt que de mélanger.
    if acc and state.mt5.current_login not in (None, acc.login):
        raise HTTPException(
            400,
            f"MetaTrader 5 est connecté au compte {state.mt5.current_login}, pas au compte "
            f"actif du journal ({acc.login}) — utilisez « Relancer MT5 » pour reconnecter le bon compte.",
        )
    if acc and not acc.initialization_mode:
        raise HTTPException(
            400,
            "Journal non initialisé pour ce compte — choisissez un mode de "
            "démarrage avant la première synchronisation "
            "(POST /api/mt5/accounts/{login}/initialize)",
        )
    try:
        count = state.mt5.sync_trades(db)
    except RuntimeError as e:
        # Échec PONCTUEL et attendu de l'API MetaTrader5 (voir le
        # commentaire de MT5Service.sync_trades, Phase B) : la fenêtre
        # incrémentale n'a pas avancé, la prochaine synchronisation
        # reprendra exactement là où celle-ci s'est arrêtée — aucune donnée
        # perdue. Sans ce bloc, l'exception remontait telle quelle : un 500
        # brut, avec la trace Python complète dans les logs mais AUCUN
        # message dans l'interface (le bouton « Synchroniser » se
        # réactivait en silence, sans le moindre indice que ça avait
        # échoué). Elle devient un message actionnable, sur lequel le
        # frontend peut afficher un toast (voir js/account.js, syncTrades).
        logger.warning("Synchronisation MT5 interrompue : %s", e)
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        # Filet de sécurité générique : toute autre panne inattendue en
        # cours de synchronisation (réponse MT5 malformée, session coupée
        # au milieu de l'import...) ne doit plus jamais, elle non plus,
        # atterrir comme un 500 muet.
        logger.exception("Synchronisation MT5 : erreur inattendue")
        raise HTTPException(503, f"Synchronisation MT5 échouée : {e}") from e
    return {"synced": count}


@router.post("/accounts/{login}/recalibrate")
def recalibrate_reference_capital(login: int, db: Session = Depends(get_db)):

    acc = state.get_active_account(db)
    if not acc or acc.login != login:
        raise HTTPException(403, "Seul le compte MT5 actif peut être recalibré")
    _reject_manual(acc)

    realized = (
        db.query(Trade)
        .filter(Trade.is_open.is_(False), Trade.account_id == login)
        .all()
    )
    # Phase 6 : P&L NET (profit + commission + swap), comme le solde du courtier
    # qui les intègre — avec le profit seul, le capital recalé restait décalé
    # du total des commissions et swaps.
    total_realized = sum((t.profit or 0.0) + (t.commission or 0.0) + (t.swap or 0.0) for t in realized)
    # Les dépôts / retraits enregistrés ne sont pas du P&L — on les retire
    # aussi, sinon un dépôt serait repris dans le capital de référence comme
    # s'il était déjà là au départ (et compté deux fois par la courbe).
    acc.initial_balance = acc.balance - total_realized - movement_service.movements_sum(db, login)
    acc.capital_calibrated = True
    db.commit()
    db.refresh(acc)
    return {"initial_balance": acc.initial_balance}


@router.get("/status")
def mt5_status():
    """État de la connexion MT5. Route volontairement NON BLOQUANTE et SANS
    AUCUN EFFET DE BORD : elle ne fait que lire l'état déjà connu, sans
    jamais appeler l'API MetaTrader5.

    Deux raisons à ça, cumulées :
    1. Le frontend interroge cette route en boucle. Une tentative de
       connexion native (`initialize()`) peut bloquer plusieurs secondes
       sans libérer le GIL — l'appeler ici gèlerait l'application entière
       à intervalle régulier dès que MT5 ou le réseau est absent.
    2. `initialize()` relance le terminal MT5 s'il n'est pas déjà ouvert.
       Une route sondée aussi souvent ne doit donc jamais la déclencher.
    La reconnexion peut avoir lieu sur action explicite (POST /reconnect,
    POST /accounts/{login}/switch, POST /connect) ou, en tâche de fond, via
    state.passive_reconnect_worker() — mais celui-ci ne tente une connexion
    QUE s'il a d'abord confirmé, sans toucher à l'API MetaTrader5, que le
    terminal tourne déjà : fermer MT5 volontairement ne le rouvre jamais.
    """
    return {
        "connected": state.mt5.is_connected(),
        "loading": state.mt5.is_loading(),
        "simulated": state.mt5.is_simulated(),
        "last_error": state.mt5.last_error(),
    }


@router.post("/reconnect")
def reconnect_mt5(db: Session = Depends(get_db)):
    """Retente la connexion au compte actif — sur action explicite de
    l'utilisateur (bouton « Relancer MT5 » de la carte Compte MT5, ou clic
    sur le compte déjà actif dans le sélecteur quand il apparaît
    déconnecté). La même logique de reconnexion tourne aussi en tâche de
    fond (state.passive_reconnect_worker()), mais gardée par une détection
    du terminal qui ne passe jamais par l'API MetaTrader5 — voir
    state.reconnect_active_account() pour le détail des deux appelants.

    Cette route PEUT bloquer jusqu'à quelques secondes (appel natif
    MetaTrader5) : c'est attendu et sans risque ici, puisqu'elle ne part
    que d'un clic.
    """
    account = state.get_active_account(db)
    if not account:
        raise HTTPException(400, "Aucun compte actif à reconnecter — connectez-vous d'abord.")
    _reject_manual(account)
    if not account.password:
        raise HTTPException(400, "Identifiants manquants pour ce compte — reconnectez-vous avec le mot de passe.")

    ok = state.reconnect_active_account(db)
    if not ok:
        raise HTTPException(400, f"Reconnexion impossible — {_connect_failure_message()}")
    return {"status": "connected", "account": state.mt5.get_account_info()}
