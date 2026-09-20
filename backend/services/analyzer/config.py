"""Réglages de l'Analyzer : lecture / écriture de `analyzer_settings`.

Deux niveaux, dans cet ordre de priorité : réglage du COMPTE ACTIF, puis
réglage GLOBAL (`account_id IS NULL`), puis valeur par défaut codée ici.
Ce repli à trois étages est ce qui permet au §9.4 du cahier de traiter le cas
des comptes manuels (heure possiblement locale) sans imposer un réglage à
tous les comptes.
"""
import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.analyzer.models import AnalyzerSetting

# ── Valeurs par défaut ──────────────────────────────────────────────────────
#
# Fenêtres de session en HEURE SERVEUR du courtier (décision D3 : pas de
# conversion de fuseau au MVP — l'utilisateur voit exactement les heures de
# ses graphiques MT5). L'ORDRE compte : un trade appartient à la PREMIÈRE
# fenêtre qui le contient. Les fenêtres peuvent donc se chevaucher sans créer
# de double comptage.
DEFAULT_SESSIONS = [
    {"key": "asia", "label": "Asie", "start": "00:00", "end": "08:00"},
    {"key": "london", "label": "Londres", "start": "08:00", "end": "13:00"},
    {"key": "newyork", "label": "New York", "start": "13:00", "end": "21:00"},
]

# Seuils de fiabilité (§10 du cahier, valeurs du cahier v1 conservées).
DEFAULT_RELIABILITY = {
    "insufficient": 10,   # n < 10           → INSUFFISANT
    "watch": 30,          # 10 ≤ n < 30      → À SURVEILLER
    "interesting": 100,   # 30 ≤ n < 100     → SIGNAL INTÉRESSANT ; ≥ 100 → ROBUSTE
    "min_display": 3,     # en dessous, le groupe est GRISÉ (jamais masqué)
}

DEFAULTS: dict[str, Any] = {
    "sessions": DEFAULT_SESSIONS,
    "reliability": DEFAULT_RELIABILITY,
    "base": "gross",          # décision D1 : brut par défaut (parité Dashboard)
    "time_reference": "server",  # "server" | "local" — informatif (§9.4)
}


def get_setting(db: Session, key: str, account_id: Optional[int] = None) -> Any:
    """Valeur d'un réglage : compte actif → global → défaut."""
    if account_id is not None:
        row = (
            db.query(AnalyzerSetting)
            .filter(AnalyzerSetting.account_id == account_id, AnalyzerSetting.key == key)
            .first()
        )
        if row:
            return _decode(row, key)
    row = (
        db.query(AnalyzerSetting)
        .filter(AnalyzerSetting.account_id.is_(None), AnalyzerSetting.key == key)
        .first()
    )
    if row:
        return _decode(row, key)
    return _copy_default(key)


def _decode(row: AnalyzerSetting, key: str) -> Any:
    try:
        return json.loads(row.value_json)
    except (ValueError, TypeError):
        # Une ligne corrompue ne doit jamais faire tomber l'écran : on
        # retombe silencieusement sur le défaut.
        return _copy_default(key)


def _copy_default(key: str) -> Any:
    value = DEFAULTS.get(key)
    return json.loads(json.dumps(value)) if value is not None else None


def set_setting(db: Session, key: str, value: Any, account_id: Optional[int] = None) -> None:
    """Écrit un réglage (global si `account_id` est None). Ne commit pas."""
    q = db.query(AnalyzerSetting).filter(AnalyzerSetting.key == key)
    q = q.filter(AnalyzerSetting.account_id == account_id) if account_id is not None \
        else q.filter(AnalyzerSetting.account_id.is_(None))
    row = q.first()
    payload = json.dumps(value, ensure_ascii=False)
    if row:
        row.value_json = payload
    else:
        db.add(AnalyzerSetting(account_id=account_id, key=key, value_json=payload))


def all_settings(db: Session, account_id: Optional[int] = None) -> dict:
    return {key: get_setting(db, key, account_id) for key in DEFAULTS}
