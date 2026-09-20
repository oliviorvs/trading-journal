from pydantic import BaseModel, ConfigDict, Field, constr
from typing import Optional
from datetime import datetime

class MT5ConnectRequest(BaseModel):
    """Corps de requête pour la connexion MT5 — jamais en query string (le mot
    de passe se retrouverait sinon dans les logs serveur et l'historique du
    navigateur)."""
    # gt=0 : les logins négatifs sont réservés aux comptes manuels
    # (identifiant synthétique, voir state.next_manual_login).
    login: int = Field(..., gt=0)
    password: str = Field(..., max_length=128)
    server: str = Field(..., max_length=100)
    label: Optional[str] = Field(None, max_length=50)  # nom donné au compte dans le sélecteur ("Compte principal"...)

class InitializeJournalRequest(BaseModel):
    """Choix fait par l'utilisateur au premier lancement pour un compte.

    mode = "from_now"    → table rase, aucune date requise.
    mode = "historical"  → import complet, `start_date` obligatoire (date à
                            partir de laquelle importer l'historique)."""
    mode: str  # "from_now" | "historical"
    start_date: Optional[datetime] = None


class ManualAccountCreate(BaseModel):
    """Création d'un compte manuel (`mode = "manual"`) : aucune connexion MT5,
    ni mot de passe, ni serveur. Sert aux deux portes d'entrée « Compte manuel
    (import) » et « Full manuel » — c'est le même type de compte."""
    name: str = Field(..., min_length=1, max_length=50)          # nom affiché dans le sélecteur
    currency: str = Field("USD", pattern=r"^[A-Za-z]{3,10}$")
    initial_balance: float = Field(..., gt=0, le=1e12)            # capital de départ
    start_date: Optional[datetime] = None                          # date de début du journal (défaut : maintenant)
    # Levier du compte : sert uniquement à ESTIMER la marge (charge du dépôt)
    # des trades saisis sans marge. 100 par défaut.
    leverage: int = Field(100, ge=1, le=3000)


class TradeCreate(BaseModel):
    """Ajout manuel d'un trade sur un compte manuel (POST /api/trades).

    Le profit, la commission et le swap sont SAISIS (leur calcul depuis les
    prix demanderait la valeur du contrat, propre à chaque broker). `ticket`,
    `pips`, `is_open`, `initial_sl`, `source` sont fixés par le serveur.
    Pas de date + prix de clôture ⇒ trade ouvert.
    """
    symbol: str = Field(..., min_length=1, max_length=20)
    direction: str = Field(..., min_length=3, max_length=4)   # buy | sell (insensible à la casse)
    volume: float = Field(..., gt=0, le=1_000_000, allow_inf_nan=False)
    open_price: float = Field(..., gt=0, allow_inf_nan=False)
    open_time: datetime
    close_price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    close_time: Optional[datetime] = None
    sl: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    tp: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    profit: float = Field(0.0, ge=-1e12, le=1e12, allow_inf_nan=False)
    commission: float = Field(0.0, ge=-1e12, le=1e12, allow_inf_nan=False)
    swap: float = Field(0.0, ge=-1e12, le=1e12, allow_inf_nan=False)
    comment: Optional[str] = Field(None, max_length=255)
    # Marge immobilisée (devise du compte), facultative : sert à la charge du
    # dépôt. Absente ⇒ estimée quand c'est possible (services/equity.py).
    margin: Optional[float] = Field(None, ge=0, le=1e12, allow_inf_nan=False)
    # Champs "journal"
    notes: Optional[str] = Field(None, max_length=5000)
    setup_tag: Optional[str] = Field(None, max_length=30)
    error_tag: Optional[str] = Field(None, max_length=30)
    emotion: Optional[str] = Field(None, max_length=20)
    playbook: Optional[str] = Field(None, max_length=50)
    exit_reason: Optional[str] = Field(None, max_length=20)
    entry_timeframe: Optional[str] = Field(None, max_length=10)
    risk_percent: Optional[float] = Field(None, ge=0, le=100)


class TradeOut(BaseModel):
    id: int
    ticket: int
    symbol: str
    direction: str
    volume: float
    open_price: float
    close_price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    initial_sl: Optional[float] = None
    open_time: datetime
    close_time: Optional[datetime]
    profit: float
    commission: float
    swap: float
    pips: float
    comment: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = Field(None, max_length=5000)
    is_open: bool
    setup_tag: Optional[str] = Field(None, max_length=30)
    error_tag: Optional[str] = Field(None, max_length=30)
    emotion: Optional[str] = Field(None, max_length=20)
    playbook: Optional[str] = Field(None, max_length=50)
    exit_reason: Optional[str] = Field(None, max_length=20)
    entry_timeframe: Optional[str] = Field(None, max_length=10)
    risk_percent: Optional[float] = None
    margin: Optional[float] = None             # marge immobilisée (None = inconnue)
    source: str = "mt5"                        # "mt5" | "manual" | "import"
    import_batch: Optional[str] = None
    # ── Champs calculés (non stockés en base, attachés à la volée) ──
    # Risque effectif : `risk_percent` s'il a été saisi manuellement, sinon
    # estimé automatiquement depuis le SL et le capital du compte au moment
    # de l'ouverture du trade (voir `_effective_risk` dans main.py).
    risk_percent_effective: Optional[float] = None
    risk_percent_source: Optional[str] = None   # "manuel" | "auto" | None
    r_multiple: Optional[float] = None          # profit / montant risqué

    model_config = ConfigDict(from_attributes=True)

class TradeUpdate(BaseModel):
    """Champs éditables d'un trade (PATCH /api/trades/{ticket}).

    - Champs "journal" (notes, tags, émotion, playbook, motif de sortie,
      timeframe, risque) : éditables sur TOUS les trades.
    - Champs de données (symbole, direction, volume, prix, dates, SL/TP,
      profit, commission, swap, commentaire) : éditables UNIQUEMENT sur un
      trade `manual` ou `import`. Sur un trade `mt5` ils sont refusés (400) :
      ils viennent du broker et restent fidèles à ses données.
    """
    # ── Journal ──
    notes: Optional[str] = Field(None, max_length=5000)
    setup_tag: Optional[str] = Field(None, max_length=30)
    error_tag: Optional[str] = Field(None, max_length=30)
    emotion: Optional[str] = Field(None, max_length=20)
    playbook: Optional[str] = Field(None, max_length=50)
    exit_reason: Optional[str] = Field(None, max_length=20)
    entry_timeframe: Optional[str] = Field(None, max_length=10)
    # Correction : aucune contrainte de plage n'existait auparavant (ni ici
    # ni côté frontend, où seul un `min="0"` HTML — non bloquant — était
    # posé). Un risque négatif ou dépassant 100 % du capital n'a pas de
    # sens pour ce champ ("% du capital risqué sur ce trade").
    risk_percent: Optional[float] = Field(None, ge=0, le=100)
    # ── Données (trades non-MT5 uniquement) ──
    symbol: Optional[str] = Field(None, max_length=20)
    direction: Optional[str] = Field(None, max_length=4)
    volume: Optional[float] = Field(None, gt=0, le=1_000_000, allow_inf_nan=False)
    open_price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    close_price: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    sl: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    initial_sl: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    tp: Optional[float] = Field(None, gt=0, allow_inf_nan=False)
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    profit: Optional[float] = Field(None, ge=-1e12, le=1e12, allow_inf_nan=False)
    commission: Optional[float] = Field(None, ge=-1e12, le=1e12, allow_inf_nan=False)
    swap: Optional[float] = Field(None, ge=-1e12, le=1e12, allow_inf_nan=False)
    comment: Optional[str] = Field(None, max_length=255)
    margin: Optional[float] = Field(None, ge=0, le=1e12, allow_inf_nan=False)

class MovementCreate(BaseModel):
    """Saisie manuelle d'un dépôt ou d'un retrait (compte manuel seulement).
    `amount` est TOUJOURS positif : c'est `type` qui donne le sens."""
    type: str = Field(..., pattern=r"^(deposit|withdrawal)$")
    amount: float = Field(..., gt=0, le=1e12, allow_inf_nan=False)
    time: Optional[datetime] = None          # défaut : maintenant
    comment: Optional[str] = Field(None, max_length=255)


class MovementUpdate(BaseModel):
    """Modification d'un mouvement saisi ou importé. Tous les champs sont
    optionnels ; `type` + `amount` peuvent être changés ensemble."""
    type: Optional[str] = Field(None, pattern=r"^(deposit|withdrawal)$")
    amount: Optional[float] = Field(None, gt=0, le=1e12, allow_inf_nan=False)
    time: Optional[datetime] = None
    comment: Optional[str] = Field(None, max_length=255)


class MovementOut(BaseModel):
    id: int
    time: datetime
    type: str                                # "deposit" | "withdrawal"
    amount: float                            # toujours positif (le sens est dans `type`)
    signed_amount: float                     # + dépôt, − retrait
    comment: Optional[str] = None
    source: str                              # "mt5" | "manual" | "import"
    editable: bool                           # faux pour un mouvement synchronisé depuis MT5


class MovementListOut(BaseModel):
    items: list[MovementOut]
    count: int
    total_deposits: float
    total_withdrawals: float                 # montant retiré, positif
    net: float                               # dépôts − retraits
    can_edit: bool                           # vrai pour un compte manuel
    balance_after: Optional[float] = None    # capital courant du compte manuel


class ImportMovementOut(BaseModel):
    """Un dépôt / retrait détecté dans un rapport importé, pas encore écrit."""
    row_ref: str
    ticket: int
    time: Optional[datetime] = None
    type: str                                # "deposit" | "withdrawal"
    amount: float                            # positif
    comment: Optional[str] = None
    duplicate: bool = False


class ImportCandidateOut(BaseModel):
    """Un trade détecté dans le fichier importé, pas encore écrit en base."""
    ticket: int
    row_ref: str
    error: Optional[str] = None
    duplicate: bool = False
    symbol: Optional[str] = None
    direction: Optional[str] = None
    volume: Optional[float] = None
    open_price: Optional[float] = None
    open_time: Optional[datetime] = None
    close_price: Optional[float] = None
    close_time: Optional[datetime] = None
    profit: Optional[float] = None
    commission: Optional[float] = None
    swap: Optional[float] = None
    exit_reason: Optional[str] = None


class ImportIntegrityCheck(BaseModel):
    computed: float
    expected: float
    matches: bool


class ImportPreviewOut(BaseModel):
    """Réponse de POST /api/import/preview.

    Un fichier CSV sans correspondance de colonnes fournie renvoie
    `needs_mapping = true` avec les en-têtes détectés (`csv_headers`) et un
    aperçu des premières lignes, pour l'écran de mapping ; l'appelant
    renvoie alors une seconde requête avec `mapping` rempli.
    """
    token: str
    source_kind: str  # "csv" | "html" | "xlsx"
    filename: str
    needs_mapping: bool = False
    csv_headers: Optional[list[str]] = None
    csv_sample_rows: Optional[list[dict]] = None
    csv_target_fields: Optional[list[str]] = None
    account_header: dict = {}
    candidates: list[ImportCandidateOut] = []
    valid_count: int = 0
    duplicate_count: int = 0
    error_count: int = 0
    integrity: dict[str, ImportIntegrityCheck] = {}
    # Dépôts / retraits lus dans le rapport (phase 6). Le dépôt initial (celui
    # qui crée le compte à solde nul) n'en fait pas partie : il est proposé
    # comme capital de départ (`account_header.suggested_initial_balance`).
    movements: list[ImportMovementOut] = []
    movement_count: int = 0                  # à importer (hors doublons)
    movement_duplicate_count: int = 0
    movements_net: float = 0.0               # dépôts − retraits à importer


class ImportCommitRequest(BaseModel):
    """Corps de POST /api/import/commit.

    `token` : jeton renvoyé par l'aperçu. Si `new_account` est fourni, un
    nouveau compte manuel est créé et activé (mêmes règles que
    POST /api/accounts/manual — possible même si un compte MT5 est actif) ;
    sinon l'import se fait dans le compte manuel actif (400 s'il n'y en a
    pas). `force` permet de confirmer malgré un écart d'intégrité
    signalé par l'aperçu (section 6 : « en cas d'écart, import bloqué avec
    un message clair » — tant que l'utilisateur n'a pas explicitement forcé).
    """
    token: str
    new_account: Optional[ManualAccountCreate] = None
    force: bool = False


class ImportCommitResult(BaseModel):
    batch_id: str
    inserted: int
    skipped_duplicate: int
    skipped_error: int
    movements_inserted: int = 0
    movements_skipped_duplicate: int = 0


class ImportBatchOut(BaseModel):
    batch_id: str
    trades_count: int
    movements_count: int = 0
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


class TradePage(BaseModel):
    items: list[TradeOut]
    total: int
    limit: int
    offset: int

class AttachmentOut(BaseModel):
    id: int
    trade_ticket: int
    filename: str
    original_name: Optional[str] = None
    label: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SettingsOut(BaseModel):
    currency: str  # Toujours dérivée du compte MT5 actif (voir state.settings_payload), jamais choisie librement
    setup_types: list[str]
    emotion_types: list[str]
    error_types: list[str]

    model_config = ConfigDict(from_attributes=True)

class SettingsUpdate(BaseModel):
    # Pas de champ `currency` ici : la devise n'est plus éditable par
    # l'utilisateur (correction bug confusion devise d'affichage / devise
    # réelle du compte), elle est toujours dérivée du compte MT5 actif.
    setup_types: Optional[list[constr(strip_whitespace=True, min_length=1, max_length=30)]] = Field(None, min_length=1, max_length=30)
    emotion_types: Optional[list[constr(strip_whitespace=True, min_length=1, max_length=30)]] = Field(None, min_length=1, max_length=30)
    error_types: Optional[list[constr(strip_whitespace=True, min_length=1, max_length=30)]] = Field(None, min_length=1, max_length=30)

class AccountOut(BaseModel):
    login: int
    mode: str = "mt5"                # "mt5" | "manual"
    server: Optional[str] = None     # None pour un compte manuel
    name: Optional[str] = None
    label: Optional[str] = None
    is_active: bool = False
    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    leverage: int
    last_sync: datetime
    initial_balance: Optional[float] = None
    initial_equity: Optional[float] = None
    initialization_mode: Optional[str] = None
    start_date: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class PerformanceStats(BaseModel):
    total_pnl: float
    win_rate: float
    max_drawdown: float          # en %, peut être imprécis si peak == 0 (voir max_drawdown_abs)
    max_drawdown_abs: float      # en $, toujours fiable quel que soit le signe de l'équité
    rr_ratio: Optional[float]    # None si aucun trade perdant (ratio non défini)
    total_trades: int
    wins: int
    losses: int
    breakeven: int
    profit_factor: Optional[float] = None      # None si aucune perte (ratio non défini)
    expectancy: Optional[float] = None         # espérance de gain par trade
    best_trade: Optional[dict] = None          # {amount, date, symbol, ticket}
    worst_trade: Optional[dict] = None
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    avg_duration_minutes: Optional[float] = None
    total_volume: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0
    best_day: Optional[dict] = None            # {date, pnl}
    worst_day: Optional[dict] = None
    best_symbol: Optional[dict] = None         # {symbol, pnl}
    worst_symbol: Optional[dict] = None
