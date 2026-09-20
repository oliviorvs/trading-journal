"""Routes Trades (CRUD) et Pièces jointes. Extrait de main.py."""
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime
import os
import uuid
from PIL import Image, UnidentifiedImageError

from database import get_db
from models import Trade, Attachment
from schemas import TradeOut, TradeUpdate, TradeCreate, TradePage, AttachmentOut
from services import stats
from services import manual_trades
import state

router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades", response_model=TradePage)
def get_trades(
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    result: Optional[str] = Query(None, pattern="^(win|loss|breakeven)$"),
    # Origine du trade (étape 5) : utile sur un compte manuel, où saisies et
    # imports cohabitent ; sur un compte MT5, tous les trades sont « mt5 ».
    source: Optional[str] = Query(None, pattern="^(mt5|manual|import)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    q = db.query(Trade)
    q = state.filter_active(q, db)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    if direction:
        q = q.filter(Trade.direction == direction.lower())
    if source:
        q = q.filter(Trade.source == source)
    if date_from:
        q = q.filter(Trade.close_time >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(Trade.close_time <= datetime.combine(date_to, datetime.max.time()))
    if result == "win":
        q = q.filter(Trade.profit > 0)
    elif result == "loss":
        # profit == 0 (breakeven) n'est plus compté comme une perte
        q = q.filter(Trade.profit < 0)
    elif result == "breakeven":
        q = q.filter(Trade.profit == 0)
    total = q.count()
    trades = q.order_by(Trade.open_time.desc()).offset(offset).limit(limit).all()
    stats.attach_computed_fields(trades, db)
    return {"items": trades, "total": total, "limit": limit, "offset": offset}


@router.get("/trades/symbols", response_model=List[str])
def get_trade_symbols(db: Session = Depends(get_db)):
    """Liste des symboles distincts du compte actif, tous trades confondus.

    Correction : les filtres symbole (dashboard + page Trades) étaient
    auparavant remplis à partir de la première page de `/api/trades`
    seulement (`state.tradesPageSize`, 100 trades) — au-delà de 100 trades,
    les symboles apparus uniquement sur des trades plus anciens
    disparaissaient du menu déroulant. Cette route n'est pas paginée : elle
    ne renvoie que les valeurs distinctes de la colonne `symbol`, un
    ensemble typiquement restreint même avec des milliers de trades.
    """
    q = state.filter_active(db.query(Trade.symbol).distinct(), db)
    return sorted(s for (s,) in q.all() if s)


@router.get("/trades/{ticket}", response_model=TradeOut)
def get_trade(ticket: int, db: Session = Depends(get_db)):
    # Filtré sur le compte actif (cohérent avec `filter_active` utilisé
    # partout ailleurs) : sans ça, en changeant de compte MT5 enregistré,
    # il était possible de consulter un trade d'un compte non actif en
    # devinant/énumérant son ticket.
    trade = state.filter_active(db.query(Trade).filter(Trade.ticket == ticket), db).first()
    if not trade:
        raise HTTPException(404, "Trade introuvable")
    stats.attach_computed_fields([trade], db)
    return trade


@router.post("/trades", response_model=TradeOut, status_code=201)
def create_trade(body: TradeCreate, db: Session = Depends(get_db)):
    """Ajout manuel d'un trade — réservé aux comptes MANUELS.

    Un compte MT5 synchronisé n'accepte pas de trades saisis : manuel/import
    et MT5 restent dans des comptes séparés (le champ `source` permettra de
    l'ouvrir plus tard). Ticket : négatif, unique globalement, généré ici.
    """
    account = state.get_active_account(db)
    if not account:
        raise HTTPException(400, "Aucun compte actif — créez ou sélectionnez d'abord un compte manuel")
    if not state.is_manual(account):
        raise HTTPException(
            400,
            "L'ajout manuel de trades n'est possible que sur un compte manuel — "
            "un compte MT5 synchronisé reçoit ses trades depuis MetaTrader 5",
        )

    data = manual_trades.normalize_fields(body.model_dump())
    trade = Trade(
        ticket=manual_trades.next_manual_ticket(db),
        account_id=account.login,
        source="manual",
        import_batch=None,
        **data,
    )
    # SL initial = SL saisi : pas d'historique de déplacement du stop sur un
    # trade saisi à la main (voir le calcul du R-multiple dans services/stats).
    trade.initial_sl = trade.sl
    manual_trades.validate_trade(trade)
    manual_trades.recompute_derived(trade)
    db.add(trade)
    db.commit()
    db.refresh(trade)
    state.sync_manual_balance(db, account)
    stats.attach_computed_fields([trade], db)
    return trade


@router.patch("/trades/{ticket}", response_model=TradeOut)
def update_trade(ticket: int, body: TradeUpdate, db: Session = Depends(get_db)):
    """Met à jour un trade.

    - Trade `mt5` : uniquement les champs "journal" (notes, tags…). Prix,
      volume, profit, ticket et dates restent en lecture seule pour rester
      fidèles aux données du broker — les envoyer renvoie une erreur 400.
    - Trade `manual` / `import` : tous les champs sont éditables. L'état
      final est revalidé (volume > 0, prix > 0, clôture ≥ ouverture…) ; pips
      et statut ouvert/clôturé sont recalculés ; R-multiple et risque suivent
      automatiquement (SL + capital).
    """
    trade = state.filter_active(db.query(Trade).filter(Trade.ticket == ticket), db).first()
    if not trade:
        raise HTTPException(404, "Trade introuvable")
    data = body.model_dump(exclude_unset=True)

    if trade.source not in manual_trades.EDITABLE_SOURCES:
        forbidden = sorted(k for k in data if k not in manual_trades.JOURNAL_FIELDS)
        if forbidden:
            raise HTTPException(
                400,
                "Champs en lecture seule sur un trade MT5 : " + ", ".join(forbidden),
            )
        for field, value in data.items():
            setattr(trade, field, value)
        db.commit()
        db.refresh(trade)
        stats.attach_computed_fields([trade], db)
        return trade

    for field in manual_trades.REQUIRED_FIELDS:
        if field in data and data[field] is None:
            raise HTTPException(422, f"Le champ « {field} » ne peut pas être vide")
    data = manual_trades.normalize_fields(data)
    for field, value in data.items():
        setattr(trade, field, value)
    # Modifier le SL d'un trade saisi/importé modifie aussi le SL initial
    # (sauf si `initial_sl` est fourni explicitement dans la même requête).
    if "sl" in data and "initial_sl" not in data:
        trade.initial_sl = trade.sl
    manual_trades.validate_trade(trade)
    manual_trades.recompute_derived(trade)
    db.commit()
    db.refresh(trade)
    state.sync_manual_balance(db, state.get_active_account(db))
    stats.attach_computed_fields([trade], db)
    return trade


@router.delete("/trades/{ticket}", status_code=204)
def delete_trade(ticket: int, db: Session = Depends(get_db)):
    """Supprime un trade du journal (uniquement en local — ne touche pas MT5).

    - Trade `mt5` : utile pour retirer une ligne importée par erreur ou en
      double ; il reviendra si une resynchronisation MT5 le retrouve dans
      l'historique.
    - Trade `manual` / `import` : suppression DÉFINITIVE (aucune synchro ne
      peut le recréer).
    """
    trade = state.filter_active(db.query(Trade).filter(Trade.ticket == ticket), db).first()
    if not trade:
        raise HTTPException(404, "Trade introuvable")
    account_id = trade.account_id
    db.delete(trade)
    db.flush()
    # Même fuite que sur la suppression d'un compte : les pièces jointes du
    # trade restaient en base et sur le disque, définitivement inaccessibles
    # puisque plus aucun trade ne permettait d'y accéder. Le nettoyage ne
    # concerne que les pièces de CE compte (un autre compte portant le même
    # ticket garde les siennes).
    state.purge_orphan_attachments(db, account_id, [ticket])
    db.commit()
    state.sync_manual_balance(db, state.get_active_account(db))  # no-op hors compte manuel


# ── Pièces jointes ────────────────────────────────────────────────────────
# Captures d'écran liées à un trade (setup avant/après, graphique...).
# Référencées par ticket plutôt que par id interne : cohérent avec le reste
# de l'API trades, qui expose toujours le ticket MT5 dans ses routes.

@router.get("/trades/{ticket}/attachments", response_model=List[AttachmentOut])
def list_attachments(ticket: int, db: Session = Depends(get_db)):
    # Le trade doit appartenir au compte actif (voir `filter_active`) —
    # sinon on pouvait lister les pièces jointes d'un trade d'un compte non
    # actif en devinant/énumérant son ticket.
    trade = state.filter_active(db.query(Trade).filter(Trade.ticket == ticket), db).first()
    if not trade:
        raise HTTPException(404, "Trade introuvable")
    return (
        db.query(Attachment)
        .filter(Attachment.trade_ticket == ticket, Attachment.account_id == trade.account_id)
        .order_by(Attachment.created_at.desc())
        .all()
    )


@router.post("/trades/{ticket}/attachments", response_model=AttachmentOut)
async def upload_attachment(
    ticket: int,
    file: UploadFile = File(...),
    label: str = Form("autre"),
    db: Session = Depends(get_db),
):
    trade = state.filter_active(db.query(Trade).filter(Trade.ticket == ticket), db).first()
    if not trade:
        raise HTTPException(404, "Trade introuvable")
    if label not in ("avant", "apres", "autre"):
        label = "autre"
    # Le Content-Type envoyé par le client est déclaratif et peut être falsifié.
    # La signature binaire est la source de vérité pour le format accepté.
    first_chunk = await file.read(state.ATTACHMENT_CHUNK_SIZE)
    detected_type = state.detect_attachment_type(first_chunk)
    if detected_type not in state.ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(400, "Le contenu n'est pas une image PNG, JPEG, WEBP ou GIF valide")

    # Lecture par blocs avec arrêt dès que la taille max est dépassée, plutôt
    # que de charger tout le fichier en mémoire avant de vérifier sa taille
    # (combiné à une absence de limite de taille au niveau ASGI/Uvicorn, un
    # gros fichier pouvait consommer beaucoup de mémoire avant d'être rejeté).
    ext = state.ATTACHMENT_EXTENSIONS[detected_type]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(state.UPLOADS_DIR, stored_name)
    total_size = 0
    try:
        with open(dest_path, "wb") as f:
            f.write(first_chunk)
            total_size = len(first_chunk)
            if total_size > state.MAX_ATTACHMENT_SIZE:
                raise HTTPException(400, "Image trop volumineuse (8 Mo maximum)")
            while True:
                chunk = await file.read(state.ATTACHMENT_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > state.MAX_ATTACHMENT_SIZE:
                    raise HTTPException(400, "Image trop volumineuse (8 Mo maximum)")
                f.write(chunk)
    except HTTPException:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise

    try:
        with Image.open(dest_path) as image:
            if image.width > 10000 or image.height > 10000:
                raise HTTPException(400, "Dimensions d'image trop importantes (10000 px maximum)")
            image.verify()
    except HTTPException:
        os.remove(dest_path)
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise HTTPException(400, "Image invalide ou impossible à décoder")

    attachment = Attachment(
        trade_ticket=ticket,
        account_id=trade.account_id,
        filename=stored_name,
        original_name=file.filename,
        label=label,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    # La pièce jointe doit appartenir au compte ACTIF (colonne `account_id`) :
    # un autre compte, même porteur du même ticket, ne peut ni la voir ni
    # l'effacer.
    account_id = state.active_login(db)
    attachment = None if account_id is None else db.query(Attachment).filter(
        Attachment.id == attachment_id, Attachment.account_id == account_id
    ).first()
    if not attachment:
        raise HTTPException(404, "Pièce jointe introuvable")
    file_path = os.path.join(state.UPLOADS_DIR, attachment.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.delete(attachment)
    db.commit()


@router.get("/attachments/file/{filename}")
def get_attachment_file(filename: str, db: Session = Depends(get_db)):
    """Sert une capture seulement si elle appartient au compte actif."""
    if os.path.basename(filename) != filename:
        raise HTTPException(404, "Pièce jointe introuvable")
    account_id = state.active_login(db)
    if account_id is None:
        raise HTTPException(404, "Pièce jointe introuvable")
    attachment = (
        db.query(Attachment)
        .filter(Attachment.filename == filename, Attachment.account_id == account_id)
        .first()
    )
    if not attachment:
        raise HTTPException(404, "Pièce jointe introuvable")
    file_path = os.path.join(state.UPLOADS_DIR, attachment.filename)
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Fichier de pièce jointe introuvable")
    media_type = next(
        (content_type for content_type, extension in state.ATTACHMENT_EXTENSIONS.items()
         if extension == os.path.splitext(attachment.filename)[1].lower()),
        "application/octet-stream",
    )
    return FileResponse(file_path, media_type=media_type)
