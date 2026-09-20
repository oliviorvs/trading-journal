import re
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

from paths import get_app_root

# Base de données SQLite locale — fichier dans le dossier db/, à côté de
# l'exécutable une fois l'app compilée (voir paths.py : ne pas recalculer
# ce chemin depuis __file__, qui serait temporaire une fois "frozen").
DB_PATH = os.path.join(get_app_root(), "db", "trading_journal.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.abspath(DB_PATH)}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # Requis pour SQLite
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def run_light_migrations():
    """Migrations légères pour SQLite (pas d'Alembic dans ce projet).

    N'ajoute que les colonnes manquantes sur des tables déjà existantes,
    sans jamais recréer ni vider la base. Idempotent : sans effet si la
    colonne existe déjà. Les nouvelles tables (ex. Settings) sont créées
    séparément par `Base.metadata.create_all()`.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if "trades" in existing_tables:
        trade_columns = {c["name"] for c in inspector.get_columns("trades")}
        if "notes" not in trade_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE trades ADD COLUMN notes TEXT"))

        # Champs "journal" additionnels (tags, playbook, R, émotion...) —
        # ajoutés colonne par colonne pour ne jamais toucher aux données
        # existantes (voir docstring de la fonction).
        new_columns = {
            "initial_sl": "REAL",
            "setup_tag": "TEXT",
            "error_tag": "TEXT",
            "emotion": "TEXT",
            "playbook": "TEXT",
            "exit_reason": "TEXT",
            "entry_timeframe": "TEXT",
            "risk_percent": "REAL",
            # Comptes manuels / import (voir Recap-structure-manuel-import.md).
            # DEFAULT 'mt5' : tous les trades existants deviennent "mt5".
            "source": "TEXT NOT NULL DEFAULT 'mt5'",
            "import_batch": "TEXT",
            # Phase 6 — marge immobilisée (charge du dépôt), NULL = inconnue.
            "margin": "REAL",
        }
        for col, col_type in new_columns.items():
            if col not in trade_columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE trades ADD COLUMN {col} {col_type}"))

        # Index rétroactifs (correction #7) : `Base.metadata.create_all()`
        # ne crée les index déclarés sur `Column(index=True)` que pour les
        # NOUVELLES tables — SQLite ne les ajoute jamais tout seul sur une
        # table déjà existante. On les crée donc explicitement ici,
        # `IF NOT EXISTS` rendant l'opération idempotente.
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_is_open ON trades (is_open)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_direction ON trades (direction)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_source ON trades (source)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_import_batch ON trades (import_batch)"))

        # SQLite ne permet pas de retirer l'ancienne contrainte UNIQUE(ticket)
        # avec ALTER TABLE. La table est reconstruite en conservant les
        # données afin que deux comptes puissent partager un numéro de ticket.
        with engine.begin() as conn:
            table_sql = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
            )).scalar() or ""
            # Correction : la comparaison était `"UNIQUE (ticket)" in table_sql.upper()`
            # — minuscules contre majuscules, donc JAMAIS vraie : la table n'était
            # jamais reconstruite et une base ancienne gardait UNIQUE(ticket)
            # (deux comptes ne pouvaient pas partager un ticket ; un import d'un
            # rapport dont les tickets existent déjà en base levait une erreur
            # d'intégrité). On détecte la contrainte, écrite en table ou en
            # colonne, sur le texte mis en majuscules des deux côtés.
            if re.search(r"UNIQUE\s*\(\s*TICKET\s*\)|\bTICKET\s+INTEGER\b[^,]*\bUNIQUE\b", table_sql.upper()):
                conn.execute(text("ALTER TABLE trades RENAME TO trades_legacy"))
                conn.execute(text("""
                    CREATE TABLE trades (
                        id INTEGER PRIMARY KEY, ticket INTEGER, account_id INTEGER,
                        symbol VARCHAR(20), direction VARCHAR(4), volume FLOAT,
                        open_price FLOAT, close_price FLOAT, sl FLOAT,
                        initial_sl FLOAT, tp FLOAT, open_time DATETIME,
                        close_time DATETIME, profit FLOAT, commission FLOAT,
                        swap FLOAT, pips FLOAT, comment TEXT, notes TEXT,
                        is_open BOOLEAN, created_at DATETIME, setup_tag VARCHAR(30),
                        error_tag VARCHAR(30), emotion VARCHAR(20),
                        playbook VARCHAR(50), exit_reason VARCHAR(20),
                        entry_timeframe VARCHAR(10), risk_percent FLOAT,
                        source VARCHAR(10) NOT NULL DEFAULT 'mt5', import_batch VARCHAR(40),
                        margin FLOAT,
                        CONSTRAINT uq_trade_account_ticket UNIQUE (account_id, ticket)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO trades (
                        id, ticket, account_id, symbol, direction, volume,
                        open_price, close_price, sl, initial_sl, tp, open_time,
                        close_time, profit, commission, swap, pips, comment, notes,
                        is_open, created_at, setup_tag, error_tag, emotion, playbook,
                        exit_reason, entry_timeframe, risk_percent, source, import_batch,
                        margin
                    ) SELECT id, ticket, account_id, symbol, direction, volume,
                        open_price, close_price, sl, initial_sl, tp, open_time,
                        close_time, profit, commission, swap, pips, comment, notes,
                        is_open, created_at, setup_tag, error_tag, emotion, playbook,
                        exit_reason, entry_timeframe, risk_percent,
                        COALESCE(source, 'mt5'), import_batch, margin
                    FROM trades_legacy
                """))
                conn.execute(text("DROP TABLE trades_legacy"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_ticket ON trades (ticket)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_account_id ON trades (account_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_open_time ON trades (open_time)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_close_time ON trades (close_time)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trades_account_ticket ON trades (account_id, ticket)"))

    account_columns: set = set()
    if "accounts" in existing_tables:
        account_columns = {c["name"] for c in inspector.get_columns("accounts")}
        new_account_columns = {
            "password": "TEXT",
            "label": "TEXT",
            "is_active": "BOOLEAN DEFAULT 0",
            # Capital de référence figé (correction #3) — NULL pour les
            # comptes déjà existants, initialisé automatiquement à la
            # prochaine synchronisation (voir MT5Service._sync_account).
            "initial_balance": "REAL",
            "initial_equity": "REAL",
            "initialization_mode": "TEXT",
            "start_date": "DATETIME",
            # Type de compte (voir models.Account.mode) — tous les comptes
            # existants deviennent "mt5", aucune perte de données.
            "mode": "TEXT NOT NULL DEFAULT 'mt5'",
            # Phase 6 — DEFAULT 1 : les comptes existants ne sont JAMAIS
            # recalés automatiquement (leur capital de référence est figé,
            # voir models.Account.capital_calibrated).
            "capital_calibrated": "BOOLEAN NOT NULL DEFAULT 1",
        }
        for col, col_type in new_account_columns.items():
            if col not in account_columns:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE accounts ADD COLUMN {col} {col_type}"))

    if "accounts" in existing_tables:
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_mode ON accounts (mode)"))

    if "settings" in existing_tables:
        settings_columns = {c["name"] for c in inspector.get_columns("settings")}
        if "setup_types" not in settings_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN setup_types TEXT"))
        if "emotion_types" not in settings_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN emotion_types TEXT"))
        if "error_types" not in settings_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE settings ADD COLUMN error_types TEXT"))

    # Correction : les deux rattrapages ci-dessous portent sur la table
    # `accounts`, mais étaient imbriqués dans le bloc `if "settings" in
    # existing_tables`. Deux conséquences :
    #   1. `account_columns` n'existait pas si la table `accounts` était
    #      absente alors que `settings` existait → NameError au démarrage,
    #      donc API qui refuse de se lancer, migrations interrompues à
    #      mi-parcours ;
    #   2. sur une base sans table `settings` (installation ancienne), le
    #      rattrapage du compte actif n'était jamais exécuté → dashboard vide
    #      après mise à jour.
    # Ils sont désormais rattachés à la bonne table.
    if "accounts" in existing_tables:
        # Rétrocompatibilité : un compte qui a déjà des trades importés
        # (donc déjà synchronisé au moins une fois avant l'ajout de cette
        # colonne) est nécessairement passé par l'ancien comportement
        # "import complet" — on le marque "historical" pour ne pas le
        # bloquer par le nouveau garde-fou de sync_trades() (voir main.py),
        # qui refuse de tourner tant que initialization_mode est vide.
        if "initialization_mode" not in account_columns and "trades" in existing_tables:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE accounts SET initialization_mode = 'historical' "
                    "WHERE initialization_mode IS NULL AND last_sync IS NOT NULL "
                    "AND EXISTS (SELECT 1 FROM trades WHERE trades.account_id = accounts.login)"
                ))
        # Un compte pré-existant (créé avant la gestion multi-comptes) n'a
        # jamais eu `is_active` défini : sans ce rattrapage, un utilisateur
        # qui avait déjà un compte connecté verrait un dashboard vide après
        # la mise à jour (plus aucun compte marqué actif => filtre sur un
        # login inexistant). On active le compte le plus récemment
        # synchronisé s'il n'y en a aucun d'actif.
        with engine.begin() as conn:
            has_active = conn.execute(text("SELECT COUNT(*) FROM accounts WHERE is_active = 1")).scalar()
            if not has_active:
                conn.execute(text(
                    "UPDATE accounts SET is_active = 1 WHERE id = "
                    "(SELECT id FROM accounts ORDER BY last_sync DESC LIMIT 1)"
                ))

    # ── Phase 6 : pièces jointes rattachées à un COMPTE ──────────────────
    # Avant, une pièce jointe ne référençait qu'un ticket : deux comptes
    # portant le même numéro de ticket partageaient (et pouvaient s'effacer)
    # leurs captures. On ajoute le compte propriétaire et on rattache
    # l'existant, sans rien perdre.
    if "attachments" in existing_tables:
        attachment_columns = {c["name"] for c in inspector.get_columns("attachments")}
        if "account_id" not in attachment_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE attachments ADD COLUMN account_id INTEGER"))
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_attachments_account_id ON attachments (account_id)"))
        if "trades" in existing_tables:
            _backfill_attachment_accounts()


def _backfill_attachment_accounts() -> None:
    """Rattache chaque pièce jointe encore sans compte au(x) compte(s) qui
    portent réellement son ticket. Idempotent (ne traite que `account_id IS
    NULL`).

    - un seul compte porte le ticket : la pièce lui est attribuée ;
    - plusieurs comptes le portent (ancien partage silencieux) : la pièce
      reste au premier (plus petit login) et CHAQUE autre compte reçoit sa
      propre COPIE du fichier — isolation complète, aucune capture perdue ;
    - aucun trade ne porte plus ce ticket (pièce orpheline) : laissée sans
      compte, donc inaccessible depuis l'interface.
    """
    import shutil
    import uuid
    from sqlalchemy import text

    uploads = os.path.join(get_app_root(), "backend", "uploads")
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, trade_ticket, filename, original_name, label, created_at "
            "FROM attachments WHERE account_id IS NULL"
        )).fetchall()
        for row in rows:
            owners = [
                r[0] for r in conn.execute(
                    text("SELECT DISTINCT account_id FROM trades "
                         "WHERE ticket = :t AND account_id IS NOT NULL ORDER BY account_id"),
                    {"t": row.trade_ticket},
                ).fetchall()
            ]
            if not owners:
                continue
            conn.execute(text("UPDATE attachments SET account_id = :a WHERE id = :i"),
                         {"a": owners[0], "i": row.id})
            for other in owners[1:]:
                new_name = row.filename
                source_path = os.path.join(uploads, row.filename or "")
                if row.filename and os.path.isfile(source_path):
                    ext = os.path.splitext(row.filename)[1]
                    new_name = f"{uuid.uuid4().hex}{ext}"
                    try:
                        shutil.copyfile(source_path, os.path.join(uploads, new_name))
                    except OSError:
                        continue      # copie impossible : on n'invente pas de ligne sans fichier
                else:
                    continue          # fichier absent : rien à copier
                conn.execute(text(
                    "INSERT INTO attachments (trade_ticket, account_id, filename, original_name, label, created_at) "
                    "VALUES (:t, :a, :f, :o, :l, :c)"),
                    {"t": row.trade_ticket, "a": other, "f": new_name,
                     "o": row.original_name, "l": row.label, "c": row.created_at})
