"""Routes de l'Analyzer — toutes sous `/api/analyzer`.

Protégées AUTOMATIQUEMENT par le middleware d'authentification existant
(`/api/*` hors `/api/auth/*`) : rien à ajouter ici, et rien à oublier.

Fonctions `def` synchrones, pas `async def` : FastAPI les exécute dans son
pool de threads, donc une analyse longue ne bloque pas la boucle d'événements
ni l'interface. Un `async def` contenant du calcul SQLAlchemy synchrone
ferait exactement l'inverse.
"""
from __future__ import annotations

import json
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from services.analyzer import config as analyzer_config
from services.analyzer import exports as exports_mod
from services.analyzer import playbook as playbook_mod
from services.analyzer import report as report_mod
from services.analyzer import sessions as sessions_mod
from services.analyzer import sop as sop_mod
from services.analyzer.adapter import Filters, JournalDataAdapter
from services.analyzer.analyzers import (
    behaviour, data_quality, dimensions, discipline, patterns, performance, psychology,
)
from services.analyzer.models import (
    AnalyzerPlaybookRule, AnalyzerRun, AnalyzerSymbolMap,
    TradeEmotion, TradeError, TradeSopResult, TradeTag,
)
from models import Trade
import state

router = APIRouter(prefix="/api/analyzer", tags=["analyzer"])


# ── Utilitaires communs ─────────────────────────────────────────────────────

def _filters(date_from: Optional[date], date_to: Optional[date],
             symbol: Optional[str], source: Optional[str]) -> Filters:
    return Filters(date_from=date_from, date_to=date_to, symbol=symbol, source=source)


def _base(value: Optional[str], db: Session, account_id: Optional[int]) -> str:
    """Base brut / net (décision D1). Défaut : le réglage, sinon « brut » —
    ce qui garantit la parité avec le Dashboard tant que l'utilisateur ne
    demande pas explicitement autre chose."""
    if value in ("gross", "net"):
        return value
    setting = analyzer_config.get_setting(db, "base", account_id)
    return setting if setting in ("gross", "net") else "gross"


def _thresholds(db: Session, account_id: Optional[int]) -> dict:
    return analyzer_config.get_setting(db, "reliability", account_id) or {}


def _require_account(db: Session) -> int:
    account = state.get_active_account(db)
    if account is None:
        raise HTTPException(404, "Aucun compte actif.")
    return account.login


# Dépendance commune : les quatre filtres partagés par toutes les lectures.
def common_filters(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    symbol: Optional[str] = None,
    source: Optional[str] = None,
    base: Optional[str] = Query(None, pattern="^(gross|net)$"),
) -> dict:
    return {"filters": _filters(date_from, date_to, symbol, source), "base": base}


# ── Lectures ────────────────────────────────────────────────────────────────

@router.get("/overview")
def get_overview(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    """KPIs globaux. Le bloc `journal` est le retour BRUT de
    `stats.compute_stats` : c'est lui qui porte la garantie de parité."""
    dataset = JournalDataAdapter.load(db, params["filters"])
    return performance.overview(dataset, db, _base(params["base"], db, dataset.account_id))


@router.get("/dimension/{name}")
def get_dimension(name: str, params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    base = _base(params["base"], db, dataset.account_id)
    try:
        return dimensions.by_dimension(dataset, name, base, _thresholds(db, dataset.account_id))
    except KeyError:
        raise HTTPException(
            404,
            f"Dimension inconnue : « {name} ». Disponibles : "
            + ", ".join(dimensions.available_dimensions()),
        )


@router.get("/dimensions")
def list_dimensions():
    return {"dimensions": dimensions.available_dimensions()}


@router.get("/heatmap")
def get_heatmap(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return dimensions.heatmap(dataset, _base(params["base"], db, dataset.account_id))


@router.get("/errors")
def get_errors(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return psychology.error_cost(
        dataset, _base(params["base"], db, dataset.account_id), _thresholds(db, dataset.account_id)
    )


@router.get("/series")
def get_series(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return behaviour.series(dataset, _base(params["base"], db, dataset.account_id))


@router.get("/frequency")
def get_frequency(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return behaviour.frequency(
        dataset, _base(params["base"], db, dataset.account_id), _thresholds(db, dataset.account_id)
    )


@router.get("/overtrading")
def get_overtrading(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return behaviour.overtrading(
        dataset, _base(params["base"], db, dataset.account_id), _thresholds(db, dataset.account_id)
    )


@router.get("/discipline")
def get_discipline(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    base = _base(params["base"], db, dataset.account_id)
    return {
        "compliance": discipline.compliance(dataset, db, base, _thresholds(db, dataset.account_id)),
        "plan_vs_reality": discipline.plan_vs_reality(dataset, db, base),
    }


@router.get("/patterns")
def get_patterns(min_n: int = Query(10, ge=2, le=500),
                 params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return patterns.patterns(
        dataset, _base(params["base"], db, dataset.account_id), min_n,
        _thresholds(db, dataset.account_id),
    )


@router.get("/data-quality")
def get_data_quality(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    dataset = JournalDataAdapter.load(db, params["filters"])
    return data_quality.data_quality(dataset)


# ── Paquet complet (rapport, exports, règles) ───────────────────────────────

REPORT_DIMENSIONS = ["setup", "symbol", "session", "weekday", "hour", "emotion", "error"]


def _bundle(db: Session, filters: Filters, base: Optional[str]) -> dict:
    """UNE lecture, toutes les analyses (§7). C'est ce qui rend un écran
    complet ou un rapport aussi rapide qu'un seul endpoint."""
    dataset = JournalDataAdapter.load(db, filters)
    resolved = _base(base, db, dataset.account_id)
    thresholds = _thresholds(db, dataset.account_id)

    bundle = {
        "overview": performance.overview(dataset, db, resolved),
        "dimensions": {
            name: dimensions.by_dimension(dataset, name, resolved, thresholds)
            for name in REPORT_DIMENSIONS
        },
        "errors": psychology.error_cost(dataset, resolved, thresholds),
        "series": behaviour.series(dataset, resolved),
        "frequency": behaviour.frequency(dataset, resolved, thresholds),
        "overtrading": behaviour.overtrading(dataset, resolved, thresholds),
        "discipline": discipline.compliance(dataset, db, resolved, thresholds),
        "plan_vs_reality": discipline.plan_vs_reality(dataset, db, resolved),
        "patterns": patterns.patterns(dataset, resolved, 10, thresholds),
        "data_quality": data_quality.data_quality(dataset),
        "filters": dataset.filters,
        "base": resolved,
        "account_id": dataset.account_id,
    }
    bundle["rules"] = playbook_mod.suggest(
        bundle["patterns"], bundle["errors"], bundle["frequency"], dataset.filters
    )
    return bundle


@router.get("/full")
def get_full(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    return _bundle(db, params["filters"], params["base"])


@router.get("/report.html", response_class=HTMLResponse)
def get_report(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    bundle = _bundle(db, params["filters"], params["base"])
    account = state.get_active_account(db)
    label = (account.label or account.name or str(account.login)) if account else ""
    settings = state.get_settings(db)
    html = report_mod.build_report(bundle, label, settings.currency or "")
    return HTMLResponse(
        content=html,
        headers={"Content-Disposition": 'attachment; filename="rapport-analyzer.html"'},
    )


@router.get("/export/{fmt}")
def get_export(fmt: str, params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    if fmt not in ("json", "csv", "xlsx"):
        raise HTTPException(404, "Format inconnu. Attendu : json, csv ou xlsx.")
    bundle = _bundle(db, params["filters"], params["base"])
    if fmt == "json":
        content, media = exports_mod.to_json(bundle), "application/json"
    elif fmt == "csv":
        content, media = exports_mod.to_csv(bundle), "text/csv; charset=utf-8"
    else:
        content = exports_mod.to_xlsx(bundle)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="analyzer.{fmt}"'},
    )


# ── Runs : figer une analyse ────────────────────────────────────────────────

@router.post("/runs", status_code=201)
def create_run(params: dict = Depends(common_filters), db: Session = Depends(get_db)):
    """Fige une analyse ET génère les règles proposées associées.

    Les écrans courants ne lisent JAMAIS ces tables : elles ne servent qu'à
    conserver une preuve datée (§5.3). Recalculer est gratuit, conserver une
    décision ne l'est pas.
    """
    account_id = _require_account(db)
    bundle = _bundle(db, params["filters"], params["base"])
    run = AnalyzerRun(
        account_id=account_id,
        filters=json.dumps(bundle["filters"], ensure_ascii=False),
        run_type="report",
        status="ok",
    )
    db.add(run)
    db.flush()
    created = playbook_mod.persist(db, account_id, bundle["rules"], run.id)
    db.commit()
    return {
        "run_id": run.id,
        "rules_created": len(created),
        "rules": [playbook_mod.serialize(rule) for rule in created],
    }


# ── Règles proposées ────────────────────────────────────────────────────────

@router.get("/playbook/rules")
def list_rules(status: Optional[str] = None, db: Session = Depends(get_db)):
    account_id = _require_account(db)
    query = db.query(AnalyzerPlaybookRule).filter(AnalyzerPlaybookRule.account_id == account_id)
    if status:
        query = query.filter(AnalyzerPlaybookRule.status == status)
    rules = query.order_by(AnalyzerPlaybookRule.id.desc()).all()
    return {"rules": [playbook_mod.serialize(rule) for rule in rules]}


@router.patch("/playbook/rules/{rule_id}")
def update_rule(rule_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    account_id = _require_account(db)
    rule = (
        db.query(AnalyzerPlaybookRule)
        .filter(AnalyzerPlaybookRule.id == rule_id, AnalyzerPlaybookRule.account_id == account_id)
        .first()
    )
    if rule is None:
        raise HTTPException(404, "Règle introuvable.")
    try:
        playbook_mod.decide(db, rule, body.get("status", ""))
    except ValueError as error:
        raise HTTPException(422, str(error))
    db.commit()
    return playbook_mod.serialize(rule)


# ── Réglages ────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    account_id = state.active_login(db)
    return {
        "settings": analyzer_config.all_settings(db, account_id),
        "defaults": analyzer_config.DEFAULTS,
        "account_id": account_id,
    }


@router.put("/settings")
def put_settings(body: dict = Body(...), db: Session = Depends(get_db)):
    """Enregistre un réglage. `scope` = "account" (défaut) ou "global".

    Le réglage par compte existe pour les comptes manuels, dont l'heure saisie
    peut être locale alors que les comptes MT5 portent l'heure serveur (§9.4).
    """
    account_id = state.active_login(db)
    scope_account = body.get("scope", "account") == "account" and account_id is not None
    target = account_id if scope_account else None

    if "sessions" in body:
        try:
            windows = sessions_mod.validate_windows(body["sessions"])
        except ValueError as error:
            raise HTTPException(422, str(error))
        analyzer_config.set_setting(db, "sessions", windows, target)
    if "base" in body:
        if body["base"] not in ("gross", "net"):
            raise HTTPException(422, "Base attendue : « gross » ou « net ».")
        analyzer_config.set_setting(db, "base", body["base"], target)
    if "reliability" in body:
        thresholds = body["reliability"]
        if not isinstance(thresholds, dict):
            raise HTTPException(422, "Seuils de fiabilité invalides.")
        merged = {**analyzer_config.DEFAULT_RELIABILITY}
        for key in merged:
            if key in thresholds:
                try:
                    merged[key] = max(1, int(thresholds[key]))
                except (TypeError, ValueError):
                    raise HTTPException(422, f"Seuil « {key} » invalide.")
        if not merged["insufficient"] <= merged["watch"] <= merged["interesting"]:
            raise HTTPException(422, "Les seuils doivent être croissants.")
        analyzer_config.set_setting(db, "reliability", merged, target)
    if "time_reference" in body:
        if body["time_reference"] not in ("server", "local"):
            raise HTTPException(422, "Référence horaire attendue : « server » ou « local ».")
        analyzer_config.set_setting(db, "time_reference", body["time_reference"], target)

    db.commit()
    return {"settings": analyzer_config.all_settings(db, account_id)}


# ── Normalisation des symboles ──────────────────────────────────────────────

@router.get("/symbol-map")
def get_symbol_map(db: Session = Depends(get_db)):
    account_id = state.active_login(db)
    rows = (
        db.query(AnalyzerSymbolMap)
        .filter((AnalyzerSymbolMap.account_id == account_id) | (AnalyzerSymbolMap.account_id.is_(None)))
        .order_by(AnalyzerSymbolMap.raw_symbol)
        .all()
    )
    raw_symbols = [
        row[0] for row in
        state.filter_active(db.query(Trade.symbol).filter(Trade.is_open.is_(False)), db)
        .distinct().all() if row[0]
    ]
    from services.analyzer.analyzers.data_quality import suggest_symbol_merges
    return {
        "mappings": [
            {"id": row.id, "raw_symbol": row.raw_symbol,
             "canonical_symbol": row.canonical_symbol, "global": row.account_id is None}
            for row in rows
        ],
        "raw_symbols": sorted(raw_symbols),
        "suggestions": suggest_symbol_merges(sorted(raw_symbols)),
    }


@router.put("/symbol-map")
def put_symbol_map(body: dict = Body(...), db: Session = Depends(get_db)):
    """Remplace la table de normalisation DU COMPTE ACTIF (les règles
    globales, si elles existent, ne sont pas touchées)."""
    account_id = _require_account(db)
    mappings = body.get("mappings")
    if not isinstance(mappings, list):
        raise HTTPException(422, "« mappings » doit être une liste.")

    db.query(AnalyzerSymbolMap).filter(AnalyzerSymbolMap.account_id == account_id).delete()
    seen = set()
    for entry in mappings:
        # Même normalisation qu'à la création d'un trade : le journal stocke
        # `Trade.symbol` en majuscules, une règle saisie en minuscules ne
        # correspondrait à rien.
        raw = (entry.get("raw_symbol") or "").strip().upper()
        canonical = (entry.get("canonical_symbol") or "").strip().upper()
        if not raw or not canonical:
            continue
        if raw in seen:
            raise HTTPException(422, f"Le symbole « {raw} » apparaît deux fois.")
        seen.add(raw)
        db.add(AnalyzerSymbolMap(account_id=account_id, raw_symbol=raw, canonical_symbol=canonical))
    db.commit()
    return get_symbol_map(db)


# ── SOP ─────────────────────────────────────────────────────────────────────

@router.get("/sop")
def get_sop(db: Session = Depends(get_db)):
    account_id = _require_account(db)
    active = sop_mod.get_active(db, account_id)
    return {
        "active": sop_mod.serialize(db, active),
        "versions": sop_mod.all_versions(db, account_id),
        "default_items": sop_mod.DEFAULT_ITEMS,
    }


@router.post("/sop", status_code=201)
def post_sop(body: dict = Body(...), db: Session = Depends(get_db)):
    """Crée une NOUVELLE version. Ne modifie jamais une version existante :
    les analyses déjà produites ne changent donc pas rétroactivement."""
    account_id = _require_account(db)
    items = body.get("items")
    if items is None:
        items = [{"label": label, "required": True} for label in sop_mod.DEFAULT_ITEMS]
    try:
        version = sop_mod.create_version(
            db, account_id, body.get("name") or "SOP",
            items, float(body.get("threshold", 1.0)),
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error))
    db.commit()
    return sop_mod.serialize(db, version)


# ── Saisie par trade (SOP, émotions, erreurs, tags) ─────────────────────────

@router.get("/trades/{ticket}/journal")
def get_trade_journal(ticket: int, db: Session = Depends(get_db)):
    account_id = _require_account(db)
    sop_rows = (
        db.query(TradeSopResult)
        .filter(TradeSopResult.account_id == account_id, TradeSopResult.ticket == ticket)
        .all()
    )
    return {
        "ticket": ticket,
        "sop": {row.item_id: row.checked for row in sop_rows},
        "sop_version_id": sop_rows[0].sop_version_id if sop_rows else None,
        "emotions": sorted(
            row.emotion_key for row in
            db.query(TradeEmotion).filter(
                TradeEmotion.account_id == account_id, TradeEmotion.ticket == ticket).all()
        ),
        "errors": sorted(
            row.error_key for row in
            db.query(TradeError).filter(
                TradeError.account_id == account_id, TradeError.ticket == ticket).all()
        ),
        "tags": sorted(
            row.tag for row in
            db.query(TradeTag).filter(
                TradeTag.account_id == account_id, TradeTag.ticket == ticket).all()
        ),
    }


@router.put("/trades/{ticket}/journal")
def put_trade_journal(ticket: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Saisie complémentaire d'un trade.

    DOUBLE ÉCRITURE (§9.2) : la première émotion / erreur cochée est recopiée
    dans les colonnes historiques `Trade.emotion` / `Trade.error_tag`. Sans
    cela, l'export PDF, les filtres du tableau des trades et les compteurs
    d'usage des Réglages — qui lisent ces colonnes — cesseraient de voir ces
    valeurs dès que l'utilisateur passerait par le nouvel écran.
    """
    account_id = _require_account(db)
    trade = (
        db.query(Trade)
        .filter(Trade.account_id == account_id, Trade.ticket == ticket)
        .first()
    )
    if trade is None:
        raise HTTPException(404, "Trade introuvable sur le compte actif.")

    if "emotions" in body:
        values = _clean_list(body["emotions"])
        _replace_links(db, TradeEmotion, account_id, ticket,
                       [TradeEmotion(account_id=account_id, ticket=ticket, emotion_key=v) for v in values])
        trade.emotion = values[0] if values else None

    if "errors" in body:
        values = _clean_list(body["errors"])
        _replace_links(db, TradeError, account_id, ticket,
                       [TradeError(account_id=account_id, ticket=ticket, error_key=v) for v in values])
        trade.error_tag = values[0] if values else None

    if "tags" in body:
        values = _clean_list(body["tags"])
        _replace_links(db, TradeTag, account_id, ticket,
                       [TradeTag(account_id=account_id, ticket=ticket, tag=v) for v in values])

    if "sop" in body:
        checked = body["sop"]
        if not isinstance(checked, dict):
            raise HTTPException(422, "« sop » doit être un objet {item_id: bool}.")
        version = sop_mod.get_active(db, account_id) or sop_mod.ensure_default(db, account_id)
        valid_ids = {item.id for item in sop_mod.items_of(db, version.id)}
        db.query(TradeSopResult).filter(
            TradeSopResult.account_id == account_id, TradeSopResult.ticket == ticket).delete()
        for raw_id, value in checked.items():
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if item_id not in valid_ids:
                continue
            db.add(TradeSopResult(
                account_id=account_id, ticket=ticket, sop_version_id=version.id,
                item_id=item_id, checked=bool(value),
            ))

    db.commit()
    return get_trade_journal(ticket, db)


def _clean_list(values) -> List[str]:
    if not isinstance(values, list):
        raise HTTPException(422, "Une liste de valeurs est attendue.")
    cleaned: List[str] = []
    for value in values:
        # Normalisation en minuscules : c'est déjà ce que fait Réglages sur
        # les listes de catégories, il faut rester cohérent sinon « FOMO » et
        # « fomo » deviendraient deux groupes distincts dans les analyses.
        text = str(value or "").strip().lower()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _replace_links(db: Session, model, account_id: int, ticket: int, rows: list) -> None:
    db.query(model).filter(model.account_id == account_id, model.ticket == ticket).delete()
    for row in rows:
        db.add(row)
