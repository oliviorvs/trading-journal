from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, Text, UniqueConstraint
from sqlalchemy.sql import func
from database import Base

class Account(Base):

    __tablename__ = "accounts"

    id          = Column(Integer, primary_key=True, index=True)
    login       = Column(Integer, unique=True, index=True)
    # Type de compte : "mt5" (synchronisé avec MetaTrader 5, comportement
    # d'origine) | "manual" (aucune connexion MT5 : trades importés depuis
    # un fichier et/ou saisis à la main — voir Recap-structure-manuel-import.md).
    # « Manuel avec import » et « full manuel » sont le MÊME type : seule la
    # porte d'entrée de l'assistant de création diffère.
    # Pour un compte "manual", `login` est un identifiant SYNTHÉTIQUE NÉGATIF
    # (-1, -2…) : les vrais logins MT5 sont positifs, donc aucune collision, et
    # filter_active / la suppression de compte / le calendrier restent
    # inchangés (voir state.next_manual_login).
    mode        = Column(String(10), nullable=False, default="mt5", server_default="mt5", index=True)
    # Chiffré (voir backend/crypto_utils.py, correction #5) — jamais stocké
    # en clair, jamais renvoyé par l'API (voir AccountOut dans schemas.py).
    # String(500) plutôt que 200 : un token Fernet est plus long que le mot
    # de passe en clair (SQLite n'impose de toute façon aucune limite réelle
    # sur un TEXT/VARCHAR, cette taille est purement documentaire).
    password    = Column(String(500), nullable=True)
    server      = Column(String(100))
    name        = Column(String(100))
    label       = Column(String(50), nullable=True)   # nom donné par l'utilisateur ("Compte principal"...)
    is_active   = Column(Boolean, default=False)      # compte actuellement affiché/utilisé
    currency    = Column(String(10), default="USD")
    balance     = Column(Float, default=0.0)
    equity      = Column(Float, default=0.0)
    margin      = Column(Float, default=0.0)
    free_margin = Column(Float, default=0.0)
    leverage    = Column(Integer, default=100)
    last_sync   = Column(DateTime, default=func.now())
    # Capital de référence FIGÉ (correction #3) — initialisé une seule fois
    # à la première synchronisation du compte, puis stable jusqu'à un
    # recalibrage manuel explicite (voir POST .../recalibrate dans main.py).
    # Remplace l'ancienne reconstruction dynamique (balance − Σ P&L), plus
    # déroutante puisqu'elle bougeait toute seule à chaque suppression de
    # trade.
    initial_balance = Column(Float, nullable=True)
    # Equity au moment de l'initialisation du journal (peut différer de
    # initial_balance si des positions étaient déjà ouvertes à ce moment).
    initial_equity  = Column(Float, nullable=True)
    # Choix fait par l'utilisateur au premier lancement (voir
    # POST /api/mt5/accounts/{login}/initialize dans main.py) :
    # "from_now"   → ne pas importer l'historique, ne suivre que les
    #                 trades à partir de `start_date`.
    # "historical" → import de l'historique complet (comportement
    #                 d'origine, seul mode existant avant cette feature).
    # NULL pour un compte connecté mais pas encore initialisé : sync_trades
    # refuse de tourner tant que ce choix n'a pas été fait (voir main.py).
    initialization_mode = Column(String(20), nullable=True)
    # Instant choisi par l'utilisateur comme point de départ du journal.
    start_date = Column(DateTime, nullable=True)
    # Phase 6 (équité réelle) — le capital de référence est-il ancré sur le
    # solde RÉEL du courtier ?
    #   True  → oui (comptes existants, comptes « from_now », comptes manuels :
    #           le capital de départ est exact par construction) ;
    #   False → compte MT5 initialisé en mode « historical » dont la
    #           première synchro n'a pas encore eu lieu : `initial_balance`
    #           vaut alors le solde ACTUEL, pas celui du début de période.
    #           La synchro le recale UNE fois (solde − Σ net clôturé −
    #           Σ mouvements) puis passe ce drapeau à True : la courbe
    #           d'équité finit alors exactement sur le solde du courtier.
    capital_calibrated = Column(Boolean, nullable=False, default=True, server_default="1")

class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("account_id", "ticket", name="uq_trade_account_ticket"),)

    id          = Column(Integer, primary_key=True, index=True)
    ticket      = Column(Integer, index=True)   # ID MT5, unique per account
    account_id  = Column(Integer, index=True)
    symbol      = Column(String(20), index=True)
    direction   = Column(String(4), index=True)          # "buy" | "sell"
    volume      = Column(Float)              # Lots
    open_price  = Column(Float)
    close_price = Column(Float)
    sl          = Column(Float, nullable=True)
    initial_sl  = Column(Float, nullable=True)  # SL défini à l'ouverture, jamais écrasé
    tp          = Column(Float, nullable=True)
    open_time   = Column(DateTime, index=True)
    close_time  = Column(DateTime, nullable=True)
    profit      = Column(Float, default=0.0)
    commission  = Column(Float, default=0.0)
    swap        = Column(Float, default=0.0)
    pips        = Column(Float, default=0.0)
    comment     = Column(Text, nullable=True)      # Commentaire MT5 (broker/EA) — jamais écrasé par l'utilisateur
    notes       = Column(Text, nullable=True)      # Note personnelle de l'utilisateur (setup, contexte, ressenti...)
    is_open     = Column(Boolean, default=False, index=True)  # Trade en cours ?
    created_at  = Column(DateTime, default=func.now())

    # ── Champs "journal" additionnels (édités manuellement, jamais par MT5) ──
    setup_tag       = Column(String(30), nullable=True)   # breakout, pullback, news, range, autre
    error_tag       = Column(String(30), nullable=True)   # entree_trop_tot, sur_sizing, pas_de_stop, fomo, aucune, autre
    emotion         = Column(String(20), nullable=True)   # calme, stresse, confiant
    playbook        = Column(String(50), nullable=True)   # nom de la stratégie / playbook (texte libre)
    exit_reason     = Column(String(20), nullable=True)   # StopLoss, TakeProfit, Manuel, Breakeven
    entry_timeframe = Column(String(10), nullable=True)   # 1m, 5m, 15m, 30m, 1h, 4h, 1D
    risk_percent    = Column(Float, nullable=True)        # % du capital risqué sur ce trade
    # Marge immobilisée par la position, en devise du compte (phase 6, charge
    # du dépôt). Trades MT5 : calculée par `order_calc_margin` à la synchro
    # (règles ACTUELLES du courtier, donc une estimation pour un vieux trade).
    # Trades saisis / importés : saisie facultative ; à défaut, estimée à la
    # lecture (services/equity.estimate_margin) sans être stockée. NULL = inconnue.
    margin          = Column(Float, nullable=True)

    # ── Origine du trade ──
    # "mt5"    → synchronisé depuis MetaTrader 5 (seul cas avant cette feature)
    # "manual" → saisi à la main
    # "import" → issu d'un fichier importé (CSV / rapport HTML / xlsx)
    # Seuls les trades "mt5" restent en lecture seule pour prix/volume/dates.
    source          = Column(String(10), nullable=False, default="mt5", server_default="mt5", index=True)
    # Identifiant du lot d'import : permet d'annuler un import entier.
    # NULL pour les trades "mt5" et "manual".
    import_batch    = Column(String(40), nullable=True, index=True)

class CapitalMovement(Base):
    """Mouvement de capital d'un compte : dépôt (montant > 0) ou retrait
    (montant < 0). Journal séparé des trades — un dépôt n'est PAS un gain,
    un retrait n'est PAS une perte : ces mouvements font varier le capital
    sans jamais entrer dans les indicateurs de performance (P&L, win rate…)
    ni dans le drawdown (voir services/stats.py, phase 6 du récapitulatif).

    - Comptes MT5 : alimenté par la synchro (deals de type « balance »),
      lecture seule (`source = "mt5"`).
    - Comptes manuels : saisi à la main (`source = "manual"`) ou lu dans un
      rapport importé (`source = "import"`, annulable avec son lot).
    `account_id` porte le `login` du compte (négatif pour un compte manuel),
    comme `Trade.account_id`.
    """
    __tablename__ = "capital_movements"
    __table_args__ = (UniqueConstraint("account_id", "ticket", name="uq_movement_account_ticket"),)

    id           = Column(Integer, primary_key=True, index=True)
    account_id   = Column(Integer, index=True)
    # Numéro du deal MT5 (synchro) ou de l'opération du rapport (import) :
    # sert d'anti-doublon. NULL pour une saisie manuelle (SQLite considère
    # deux NULL comme distincts, donc la contrainte ne les gêne pas).
    ticket       = Column(Integer, nullable=True)
    time         = Column(DateTime, index=True)     # heure serveur, stockée telle quelle (comme les trades)
    amount       = Column(Float)                    # signé : > 0 dépôt, < 0 retrait
    comment      = Column(String(255), nullable=True)
    source       = Column(String(10), nullable=False, default="manual", server_default="manual", index=True)
    import_batch = Column(String(40), nullable=True, index=True)
    created_at   = Column(DateTime, default=func.now())

class Attachment(Base):
    """Pièce jointe liée à un trade (capture d'écran du graphique, setup
    avant/après...). Le fichier est stocké sur disque dans backend/uploads/,
    seul le nom de fichier généré est conservé en base."""
    __tablename__ = "attachments"

    id           = Column(Integer, primary_key=True, index=True)
    trade_ticket = Column(Integer, index=True)             # référence Trade.ticket (pas de FK stricte : un trade peut être resynchronisé)
    # Compte propriétaire (login, négatif pour un compte manuel). Une pièce
    # jointe est identifiée par le COUPLE (compte, ticket) : deux comptes
    # peuvent porter le même numéro de ticket (autre courtier, ou rapport MT5
    # importé dans un compte manuel) sans partager leurs captures.
    account_id   = Column(Integer, index=True, nullable=True)
    filename     = Column(String(255))                     # nom de fichier stocké sur disque (unique, généré)
    original_name = Column(String(255), nullable=True)     # nom de fichier original, pour affichage
    label        = Column(String(20), default="autre")     # "avant" | "apres" | "autre"
    created_at   = Column(DateTime, default=func.now())

class Settings(Base):

    __tablename__ = "settings"

    id       = Column(Integer, primary_key=True)
    currency = Column(String(10), default="USD")
    setup_types = Column(Text, nullable=True)
    emotion_types = Column(Text, nullable=True)
    error_types = Column(Text, nullable=True)

class AppAuth(Base):

    __tablename__ = "app_auth"

    id            = Column(Integer, primary_key=True)
    code_hash     = Column(String(200))
    code_salt     = Column(String(200))
    recovery_hash = Column(String(200))
    recovery_salt = Column(String(200))
    created_at    = Column(DateTime, default=func.now())
    updated_at    = Column(DateTime, default=func.now(), onupdate=func.now())


class EquitySnapshot(Base):
    """Relevé de l'ÉQUITÉ RÉELLE d'un compte MT5 à un instant donné (phase 6).

    L'équité = solde + résultat flottant des positions ouvertes : elle ne se
    déduit pas des trades clôturés, il faut la relever quand elle existe. Un
    relevé est pris à chaque synchro et, en arrière-plan, toutes les
    quelques minutes tant que le compte actif est connecté (voir
    state._snapshot_cycle). Il porte aussi la marge utilisée : c'est la
    mesure directe de la CHARGE DU DÉPÔT (marge / équité).

    Toujours rattaché à un compte (`account_id` = login) : aucun relevé n'est
    jamais partagé entre deux comptes.
    """
    __tablename__ = "equity_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "time", name="uq_snapshot_account_time"),)

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, index=True, nullable=False)
    time        = Column(DateTime, index=True, nullable=False)   # UTC naïf
    balance     = Column(Float)
    equity      = Column(Float)
    margin      = Column(Float, default=0.0)
    free_margin = Column(Float, nullable=True)
    open_count  = Column(Integer, default=0)                     # positions ouvertes au relevé


class DailySnapshot(Base):
    """OBSOLÈTE — jamais alimentée, et unique par date sans notion de compte
    (elle mélangerait les comptes). Remplacée par `EquitySnapshot`. Conservée
    uniquement pour ne pas toucher au schéma des bases existantes."""
    __tablename__ = "daily_snapshots"

    id        = Column(Integer, primary_key=True)
    date      = Column(String(10), unique=True, index=True)  # "2026-05-06"
    balance   = Column(Float)
    equity    = Column(Float)
    daily_pnl = Column(Float)
    trades    = Column(Integer, default=0)
