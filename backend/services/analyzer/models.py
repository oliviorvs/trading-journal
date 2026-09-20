"""Tables de l'Analyzer — STRICTEMENT ADDITIVES.

Règles R1/R2/R3 du cahier v2 :
- aucune colonne n'est ajoutée aux tables existantes (`trades`, `accounts`,
  `capital_movements`…) : ce module ne fait que DÉCLARER de nouvelles tables,
  créées par `Base.metadata.create_all()` comme `settings` ou
  `equity_snapshots`. `run_light_migrations()` n'est pas touché ;
- chaque table porte `account_id` (= `Account.login`, NÉGATIF pour un compte
  manuel) pour que deux comptes ne se mélangent jamais, y compris quand ils
  portent les mêmes numéros de tickets ;
- un trade est référencé par le COUPLE `(account_id, ticket)` **sans clé
  étrangère stricte**, exactement comme `attachments` : une resynchronisation
  MT5 recrée les lignes `trades` avec de nouveaux `id`, les annotations
  saisies par l'utilisateur doivent y survivre.

PIÈGE D'INTÉGRATION : ce module doit être importé dans `main.py` AVANT la
ligne `Base.metadata.create_all(bind=engine)`, sinon ces tables ne sont
jamais créées (SQLAlchemy ne connaît que les modèles déjà importés).

Trois familles, avec une règle de conservation différente (R6) :
  1. SAISIE (`trade_*`)           → précieuse, jamais purgée sans confirmation ;
  2. CONFIGURATION (`analyzer_*`) → précieuse (décisions de l'utilisateur) ;
  3. RÉSULTATS (`analyzer_runs/results/patterns`) → jetables, recalculables.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base

# ═══════════════════════════════════════════════════════════════════════════
# 1. SAISIE — ce que l'utilisateur renseigne (précieux)
# ═══════════════════════════════════════════════════════════════════════════


class TradeSopResult(Base):
    """Résultat d'un élément de checklist SOP pour un trade.

    Porte la VERSION du SOP active au moment de la saisie : modifier le SOP
    crée une nouvelle version, les analyses déjà produites ne changent donc
    jamais rétroactivement (§9.1 du cahier).
    """
    __tablename__ = "trade_sop_results"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", "item_id", name="uq_sop_result_trade_item"),
        Index("ix_sop_result_account_ticket", "account_id", "ticket"),
    )

    id             = Column(Integer, primary_key=True, index=True)
    account_id     = Column(Integer, index=True, nullable=False)
    ticket         = Column(Integer, index=True, nullable=False)
    sop_version_id = Column(Integer, index=True, nullable=False)
    item_id        = Column(Integer, index=True, nullable=False)
    checked        = Column(Boolean, nullable=False, default=False)
    created_at     = Column(DateTime, default=func.now())


class TradeEmotion(Base):
    """Émotion associée à un trade. Plusieurs lignes possibles par trade
    (le journal n'avait qu'une seule valeur dans `Trade.emotion`).

    COMPATIBILITÉ ASCENDANTE : tant qu'un trade n'a AUCUNE ligne ici, c'est
    la colonne historique `Trade.emotion` qui fait foi (voir adapter.py).
    Les analyses Émotions fonctionnent donc dès le Lot 2, sans nouvelle saisie.
    """
    __tablename__ = "trade_emotions"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", "emotion_key", name="uq_trade_emotion"),
        Index("ix_trade_emotion_account_ticket", "account_id", "ticket"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, index=True, nullable=False)
    ticket      = Column(Integer, index=True, nullable=False)
    emotion_key = Column(String(40), nullable=False)
    created_at  = Column(DateTime, default=func.now())


class TradeError(Base):
    """Erreur associée à un trade (plusieurs possibles). Même mécanisme de
    compatibilité ascendante que TradeEmotion avec `Trade.error_tag`."""
    __tablename__ = "trade_errors"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", "error_key", name="uq_trade_error"),
        Index("ix_trade_error_account_ticket", "account_id", "ticket"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, index=True, nullable=False)
    ticket     = Column(Integer, index=True, nullable=False)
    error_key  = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=func.now())


class TradeTag(Base):
    """Étiquette libre sur un trade. Remplace le `custom_fields_json` du
    cahier v1 : une liste de mots-clés est analysable, un JSON libre non."""
    __tablename__ = "trade_tags"
    __table_args__ = (
        UniqueConstraint("account_id", "ticket", "tag", name="uq_trade_tag"),
        Index("ix_trade_tag_account_ticket", "account_id", "ticket"),
    )

    id         = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, index=True, nullable=False)
    ticket     = Column(Integer, index=True, nullable=False)
    tag        = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=func.now())


# ═══════════════════════════════════════════════════════════════════════════
# 2. CONFIGURATION ET DÉCISIONS (précieux)
# ═══════════════════════════════════════════════════════════════════════════


class AnalyzerSetting(Base):
    """Réglage de l'Analyzer, en JSON.

    `account_id` NULL = réglage GLOBAL (toutes les fenêtres de session par
    défaut, seuils de fiabilité…). `account_id` renseigné = surcharge pour ce
    compte précis — nécessaire pour les comptes manuels, dont l'heure saisie
    peut être locale alors que les comptes MT5 sont en heure serveur (§9.4).
    """
    __tablename__ = "analyzer_settings"
    __table_args__ = (UniqueConstraint("account_id", "key", name="uq_analyzer_setting"),)

    id         = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, index=True, nullable=True)
    key        = Column(String(60), nullable=False, index=True)
    value_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AnalyzerSopVersion(Base):
    """Version d'un SOP (checklist de plan de trading).

    Une modification du SOP ne modifie JAMAIS une version existante : elle en
    crée une nouvelle et bascule le drapeau `active`. Les `trade_sop_results`
    déjà saisis restent rattachés à leur version d'origine.
    """
    __tablename__ = "analyzer_sop_versions"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, index=True, nullable=False)
    name        = Column(String(80), nullable=False, default="SOP")
    # Seuil de validation : part des éléments REQUIS à cocher pour qu'un trade
    # soit « conforme ». 1.0 = tous cochés (défaut, décision D5).
    threshold   = Column(Float, nullable=False, default=1.0)
    active      = Column(Boolean, nullable=False, default=True, index=True)
    created_at  = Column(DateTime, default=func.now())


class AnalyzerSopItem(Base):
    """Élément de checklist d'une version de SOP. Aucune règle n'est codée
    en dur : les 6 éléments proposés par défaut ne sont que des lignes."""
    __tablename__ = "analyzer_sop_items"

    id             = Column(Integer, primary_key=True, index=True)
    sop_version_id = Column(Integer, index=True, nullable=False)
    label          = Column(String(120), nullable=False)
    position       = Column(Integer, nullable=False, default=0)
    required       = Column(Boolean, nullable=False, default=True)


class AnalyzerSymbolMap(Base):
    """Normalisation des symboles : `XAUUSD.a` et `GOLD` → `XAUUSD`.

    Le journal stocke les symboles BRUTS tels que fournis par le courtier :
    deux suffixes différents apparaissent aujourd'hui comme deux instruments
    distincts dans « Par symbole ».

    Écart assumé avec le §5.2 du cahier (qui proposait `raw_symbol UNIQUE`) :
    la contrainte porte sur `(account_id, raw_symbol)`. Raison — deux
    courtiers peuvent utiliser le même suffixe pour des instruments
    différents, et la règle R3 (isolation par compte) prime. `account_id`
    NULL reste possible pour une règle globale.
    """
    __tablename__ = "analyzer_symbol_map"
    __table_args__ = (UniqueConstraint("account_id", "raw_symbol", name="uq_symbol_map"),)

    id               = Column(Integer, primary_key=True, index=True)
    account_id       = Column(Integer, index=True, nullable=True)
    raw_symbol       = Column(String(30), nullable=False, index=True)
    canonical_symbol = Column(String(30), nullable=False)
    created_at       = Column(DateTime, default=func.now())


class AnalyzerPlaybookRule(Base):
    """« Règle proposée » issue d'une analyse.

    À NE PAS CONFONDRE avec la colonne existante `Trade.playbook` (nom de
    stratégie en texte libre). Dans l'interface, le mot employé est
    « Règles ». Chaque règle porte sa PREUVE figée (n, intervalle, période,
    filtres) : elle reste lisible même si les trades changent ensuite.
    """
    __tablename__ = "analyzer_playbook_rules"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, index=True, nullable=False)
    run_id      = Column(Integer, index=True, nullable=True)
    rule_type   = Column(String(40), nullable=False)      # pattern | underperformer | error_cost | frequency
    text        = Column(Text, nullable=False)
    evidence    = Column(Text, nullable=True)             # JSON figé
    status      = Column(String(12), nullable=False, default="proposed", index=True)  # proposed | accepted | rejected
    decided_at  = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=func.now())


class AnalyzerMeta(Base):
    """Version de schéma de l'ANALYZER UNIQUEMENT.

    Le journal n'a pas de numéro de version (ses migrations sont idempotentes,
    colonne par colonne). On n'en introduit donc pas un globalement : cette
    ligne ne concerne que les tables déclarées dans ce fichier.
    """
    __tablename__ = "analyzer_meta"

    id             = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    updated_at     = Column(DateTime, default=func.now(), onupdate=func.now())


# ═══════════════════════════════════════════════════════════════════════════
# 3. RÉSULTATS — jetables (R6)
# ═══════════════════════════════════════════════════════════════════════════
#
# Les écrans courants NE LISENT PAS ces tables : ils recalculent à la volée
# (décision D4, voir adapter.py). Elles servent uniquement à FIGER une analyse
# pour un rapport ou pour la preuve attachée à une règle.


class AnalyzerRun(Base):
    """Analyse figée à la demande."""
    __tablename__ = "analyzer_runs"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, index=True, nullable=False)
    created_at  = Column(DateTime, default=func.now(), index=True)
    filters     = Column(Text, nullable=True)     # JSON des filtres appliqués
    config_hash = Column(String(64), nullable=True)
    run_type    = Column(String(20), nullable=False, default="report")
    status      = Column(String(12), nullable=False, default="ok")


class AnalyzerResult(Base):
    """Bloc de résultat rattaché à un run."""
    __tablename__ = "analyzer_results"

    id           = Column(Integer, primary_key=True, index=True)
    run_id       = Column(Integer, index=True, nullable=False)
    result_type  = Column(String(40), nullable=False)
    dimension    = Column(String(40), nullable=True)
    key          = Column(String(80), nullable=True)
    metrics_json = Column(Text, nullable=False)


class AnalyzerPattern(Base):
    """Pattern détecté, figé avec son intervalle et son statut."""
    __tablename__ = "analyzer_patterns"

    id            = Column(Integer, primary_key=True, index=True)
    run_id        = Column(Integer, index=True, nullable=False)
    condition     = Column(String(160), nullable=False)
    n             = Column(Integer, nullable=False, default=0)
    win_rate      = Column(Float, nullable=True)
    avg_r         = Column(Float, nullable=True)
    expectancy    = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    drawdown      = Column(Float, nullable=True)
    ci_json       = Column(Text, nullable=True)
    status        = Column(String(24), nullable=True)


# Tables purgées quand un compte est supprimé (voir state.delete_account_data).
# Toutes portent `account_id`. `analyzer_results` / `analyzer_patterns` sont
# rattachées par `run_id` : elles sont purgées via leurs runs.
ACCOUNT_SCOPED_MODELS = (
    TradeSopResult, TradeEmotion, TradeError, TradeTag,
    AnalyzerSopVersion, AnalyzerSymbolMap, AnalyzerPlaybookRule, AnalyzerRun,
    AnalyzerSetting,
)

# Tous les modèles Analyzer — sert au nettoyage entre les tests (conftest).
ALL_MODELS = ACCOUNT_SCOPED_MODELS + (
    AnalyzerSopItem, AnalyzerResult, AnalyzerPattern, AnalyzerMeta,
)
