"""Routes d'import de fichiers (étape 4) — voir Recap-structure-manuel-import.md.

Assistant en deux temps, cohérent avec la section 7 (« Assistant d'import ») :

1. POST /api/import/preview — lit le fichier, renvoie un aperçu (trades
   détectés, erreurs ligne par ligne, contrôles d'intégrité). Pour un CSV
   générique sans `mapping`, renvoie d'abord les en-têtes détectés pour
   l'écran de correspondance de colonnes ; rien n'est écrit en base.
2. POST /api/import/commit — écrit les trades valides et non-doublons du
   jeton d'aperçu (et les dépôts / retraits du rapport), dans le compte manuel actif (ou un nouveau compte créé au
   passage).

GET /api/import/batches et DELETE /api/import/batch/{id} permettent de lister
puis d'annuler un lot d'import entier sur le compte actif (section 5).
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from schemas import (
    AccountOut, ImportBatchOut, ImportCandidateOut, ImportCommitRequest,
    ImportCommitResult, ImportMovementOut, ImportPreviewOut,
)
from services import movements as movement_service
from services import import_parsers, import_service
import state

router = APIRouter(prefix="/api/import", tags=["import"])


def _detect_kind(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".html") or lower.endswith(".htm"):
        return "html"
    if lower.endswith(".xlsx"):
        return "xlsx"
    raise HTTPException(400, "Format non reconnu — fichiers acceptés : .csv, .html, .xlsx")


def _preview_to_schema(preview: import_service.ImportPreview) -> ImportPreviewOut:
    candidates = [
        ImportCandidateOut(
            ticket=c.ticket, row_ref=c.row_ref, error=c.error, duplicate=c.duplicate,
            symbol=c.data.get("symbol"), direction=c.data.get("direction"),
            volume=c.data.get("volume"), open_price=c.data.get("open_price"),
            open_time=c.data.get("open_time"), close_price=c.data.get("close_price"),
            close_time=c.data.get("close_time"), profit=c.data.get("profit"),
            commission=c.data.get("commission"), swap=c.data.get("swap"),
            exit_reason=c.data.get("exit_reason"),
        )
        for c in preview.candidates
    ]
    valid = [c for c in candidates if not c.error and not c.duplicate]
    movements = [
        ImportMovementOut(
            row_ref=m.row_ref, ticket=m.ticket, time=m.time,
            type=movement_service.kind_of(m.amount), amount=abs(m.amount),
            comment=m.comment, duplicate=m.duplicate,
        )
        for m in preview.movements
    ]
    new_movements = [m for m in preview.movements if not m.duplicate]
    return ImportPreviewOut(
        token=preview.token, source_kind=preview.source_kind, filename=preview.filename,
        needs_mapping=preview.csv_needs_mapping, csv_headers=preview.csv_headers,
        csv_sample_rows=preview.csv_sample_rows,
        csv_target_fields=list(import_parsers.CSV_TARGET_FIELDS) if preview.csv_needs_mapping else None,
        account_header=preview.account_header, candidates=candidates,
        valid_count=len(valid),
        duplicate_count=sum(1 for c in candidates if c.duplicate),
        error_count=sum(1 for c in candidates if c.error),
        integrity=preview.integrity,
        movements=movements,
        movement_count=len(new_movements),
        movement_duplicate_count=len(movements) - len(new_movements),
        movements_net=round(sum(m.amount for m in new_movements), 2),
    )


@router.post("/preview", response_model=ImportPreviewOut)
async def preview_import(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None),  # JSON — voir ImportPreviewOut.csv_target_fields
    # « new » : l'import créera un NOUVEAU compte manuel (accueil, ou aucun
    # compte manuel actif) — aucun trade existant à comparer. Absent /
    # « active » : import dans le compte manuel actif (anti-doublons sur lui).
    target: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    kind = _detect_kind(file.filename)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Fichier vide")

    # Doublons comparés au seul compte manuel actif. Un compte MT5 actif n'est
    # JAMAIS la référence : ses tickets peuvent coïncider avec ceux du rapport
    # importé, et tout serait alors signalé « déjà importé » à tort.
    account = state.get_active_account(db)
    account_login = None
    if target != "new" and state.is_manual(account):
        account_login = account.login

    parsed_mapping = None
    if mapping:
        try:
            parsed_mapping = json.loads(mapping)
        except json.JSONDecodeError:
            raise HTTPException(422, "Correspondance de colonnes invalide (JSON attendu)")

    # Fichier corrompu ou qui n'est pas ce qu'il prétend être (un « .xlsx » qui
    # n'est pas un classeur, un CSV binaire…) : erreur claire plutôt qu'un 500.
    try:
        if kind == "csv":
            preview = import_service.preview_csv(raw, file.filename, parsed_mapping, db, account_login)
        else:
            preview = import_service.preview_report(raw, file.filename, kind, db, account_login)
    except HTTPException:
        raise
    except Exception:
        state.logger.warning("Import : fichier illisible (%s)", file.filename, exc_info=True)
        raise HTTPException(
            400,
            "Fichier illisible : il est corrompu ou n'est pas au format attendu "
            "(rapport d'historique MetaTrader 5 en .html ou .xlsx, ou CSV).",
        )

    preview.target_login = account_login

    # Un rapport dont on ne reconnaît ni trade ni dépôt / retrait : soit ce
    # n'est pas un rapport d'historique MT5, soit sa mise en page n'est pas
    # lisible. Mieux vaut le dire que d'afficher un aperçu vide sans explication.
    if kind != "csv" and not preview.candidates and not preview.movements:
        import_service.discard_preview(preview.token)
        raise HTTPException(
            422,
            "Aucun trade ni dépôt/retrait reconnu dans ce fichier. Est-ce bien un rapport "
            "d'historique MetaTrader 5 (tables « Positions » et « Transactions ») ?",
        )

    return _preview_to_schema(preview)


@router.post("/commit", response_model=ImportCommitResult)
def commit_import(body: ImportCommitRequest, db: Session = Depends(get_db)):
    preview = import_service.get_preview(body.token)
    if not preview:
        raise HTTPException(404, "Aperçu introuvable ou expiré — relancez l'import")
    if preview.csv_needs_mapping:
        raise HTTPException(400, "Correspondance de colonnes non renseignée pour cet aperçu")

    # Contrôle d'intégrité AVANT toute écriture — y compris la création du
    # compte : un import refusé ne doit pas laisser un compte vide derrière lui.
    if not body.force:
        mismatches = [name for name, check in preview.integrity.items() if not check.get("matches", True)]
        if mismatches:
            raise HTTPException(
                409,
                "Écart d'intégrité détecté (" + ", ".join(mismatches) + ") — "
                "renvoyez la requête avec `force: true` pour importer quand même",
            )

    if body.new_account:
        # Nouveau compte manuel (accueil « Compte manuel (import) », ou aucun
        # compte manuel actif) : il devient le compte actif. Possible même si
        # un compte MT5 est actif — manuel/import et MT5 restent séparés.
        from routers.accounts import create_manual_account
        create_manual_account(body.new_account, db)
        account = state.get_active_account(db)
    else:
        account = state.get_active_account(db)
        if not account:
            raise HTTPException(
                400,
                "Aucun compte manuel actif — fournissez `new_account` pour en créer un, "
                "ou sélectionnez d'abord un compte manuel existant",
            )
        if not state.is_manual(account):
            raise HTTPException(
                400,
                "L'import n'est possible que dans un compte manuel — un compte MT5 "
                "synchronisé reçoit ses trades depuis MetaTrader 5. Créez un compte "
                "manuel (`new_account`) pour importer ce fichier",
            )
        if preview.target_login is not None and preview.target_login != account.login:
            raise HTTPException(
                409,
                "Le compte actif a changé depuis l'aperçu — relancez l'import pour "
                "écrire dans le bon compte",
            )

    result = import_service.commit_preview(db, preview, account)
    return ImportCommitResult(**result)


@router.get("/batches", response_model=List[ImportBatchOut])
def get_batches(db: Session = Depends(get_db)):
    account = state.get_active_account(db)
    if not account:
        return []
    return import_service.list_batches(db, account.login)


@router.delete("/batch/{batch_id}")
def delete_batch(batch_id: str, db: Session = Depends(get_db)):
    account = state.get_active_account(db)
    if not account or not state.is_manual(account):
        raise HTTPException(400, "Aucun compte manuel actif")
    result = import_service.cancel_batch(db, account, batch_id)
    if not (result["trades"] or result["movements"]):
        raise HTTPException(404, "Lot d'import introuvable")
    # `deleted` reste le nombre de trades (contrat d'origine) ; les
    # dépôts / retraits du lot sont annulés avec lui.
    return {"deleted": result["trades"], "movements_deleted": result["movements"]}
