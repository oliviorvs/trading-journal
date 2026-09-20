from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import auth_utils
from database import get_db
from models import AppAuth

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Longueur minimale portée de 4 à 6 (audit sécurité) : le champ est saisi au
# pavé numérique, 4 chiffres ne font que 10 000 combinaisons. Le verrouillage
# progressif (voir auth_utils) tient en ligne, mais ses compteurs vivent en
# mémoire process et repartent à zéro à chaque redémarrage du backend — la
# longueur du secret est donc la vraie défense.
# Ne s'applique qu'à la CRÉATION et à la RÉINITIALISATION : un code plus court
# déjà enregistré continue de fonctionner (LoginBody n'impose aucun minimum).
MIN_CODE_LENGTH = 6


class SetupBody(BaseModel):
    code: str = Field(min_length=MIN_CODE_LENGTH, max_length=200)


class LoginBody(BaseModel):
    code: str = Field(max_length=200)


class RecoverVerifyBody(BaseModel):
    recovery_code: str = Field(max_length=100)


class RecoverResetBody(BaseModel):
    recovery_code: str
    new_code: str = Field(min_length=MIN_CODE_LENGTH, max_length=200)


def _get_auth_row(db: Session) -> Optional[AppAuth]:
    return db.query(AppAuth).first()


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Indique si un code d'accès a déjà été créé, sans jamais rien révéler
    d'autre — c'est tout ce dont le gate a besoin pour choisir entre
    l'écran de création et l'écran de saisie."""
    return {"configured": _get_auth_row(db) is not None}


@router.post("/setup")
def setup(body: SetupBody, db: Session = Depends(get_db)):
    """Tout premier lancement uniquement : refuse d'écraser un code déjà
    existant (utiliser /recover/reset pour en changer un existant)."""
    if _get_auth_row(db) is not None:
        raise HTTPException(409, "Un code d'accès existe déjà.")
    code_hash, code_salt = auth_utils.hash_new_secret(body.code)
    recovery_code = auth_utils.generate_recovery_code()
    recovery_hash, recovery_salt = auth_utils.hash_new_secret(recovery_code)
    db.add(AppAuth(
        code_hash=code_hash, code_salt=code_salt,
        recovery_hash=recovery_hash, recovery_salt=recovery_salt,
    ))
    db.commit()
    return {"token": auth_utils.create_session(), "recovery_code": recovery_code}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    locked_for = auth_utils.is_locked("code")
    if locked_for:
        raise HTTPException(429, f"Trop de tentatives. Réessayez dans {locked_for}s.")
    row = _get_auth_row(db)
    if row is None:
        raise HTTPException(409, "Aucun code d'accès configuré.")
    if not auth_utils.verify_secret(body.code, row.code_hash, row.code_salt):
        auth_utils.register_failure("code")
        raise HTTPException(401, "Code incorrect.")
    auth_utils.register_success("code")
    return {"token": auth_utils.create_session()}


@router.post("/recover/verify")
def recover_verify(body: RecoverVerifyBody, db: Session = Depends(get_db)):
    """Vérifie le code de récupération sans rien modifier — permet à l'UI
    d'afficher l'écran de réinitialisation seulement si le code saisi est
    correct, sans lui faire porter la moindre logique de sécurité."""
    locked_for = auth_utils.is_locked("recovery")
    if locked_for:
        raise HTTPException(429, f"Trop de tentatives. Réessayez dans {locked_for}s.")
    row = _get_auth_row(db)
    code = body.recovery_code.strip().upper()
    if row is None or not auth_utils.verify_secret(code, row.recovery_hash, row.recovery_salt):
        auth_utils.register_failure("recovery")
        raise HTTPException(401, "Code de récupération incorrect.")
    auth_utils.register_success("recovery")
    return {"valid": True}


@router.post("/recover/reset")
def recover_reset(body: RecoverResetBody, db: Session = Depends(get_db)):
    """Revérifie le code de récupération (jamais fait confiance à un simple
    passage préalable par /recover/verify, sans état à conserver entre les
    deux appels) puis remplace le code d'accès ET le code de récupération
    (celui-ci ayant potentiellement été vu/tapé, il est prudent d'en
    fournir un nouveau plutôt que de le réutiliser)."""
    locked_for = auth_utils.is_locked("recovery")
    if locked_for:
        raise HTTPException(429, f"Trop de tentatives. Réessayez dans {locked_for}s.")
    row = _get_auth_row(db)
    code = body.recovery_code.strip().upper()
    if row is None or not auth_utils.verify_secret(code, row.recovery_hash, row.recovery_salt):
        auth_utils.register_failure("recovery")
        raise HTTPException(401, "Code de récupération incorrect.")
    auth_utils.register_success("recovery")

    code_hash, code_salt = auth_utils.hash_new_secret(body.new_code)
    new_recovery = auth_utils.generate_recovery_code()
    recovery_hash, recovery_salt = auth_utils.hash_new_secret(new_recovery)
    row.code_hash, row.code_salt = code_hash, code_salt
    row.recovery_hash, row.recovery_salt = recovery_hash, recovery_salt
    db.commit()
    auth_utils.revoke_all_sessions()
    return {"token": auth_utils.create_session(), "recovery_code": new_recovery}


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    """Invalide explicitement la session (bouton "Fermer le journal"),
    plutôt que de la laisser vivre jusqu'à son expiration naturelle."""
    auth_utils.revoke_session(_extract_bearer(authorization))
    return {"ok": True}
