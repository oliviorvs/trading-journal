"""
Authentification de l'écran de verrouillage — gérée côté backend (correctif
audit UI/UX : le message "ESPACE PRIVÉ" laissait entendre une vraie
protection, alors que tout se passait avant côté navigateur — hash SHA-256
non salé calculé en JS, comparé à une valeur lue dans le localStorage — et
que l'API /api/* répondait sans aucun contrôle à quiconque pouvait
l'atteindre sur 127.0.0.1:8000, y compris en contournant la page (curl, un
autre onglet, DevTools...).

Ce module :
- hache le code d'accès et le code de récupération avec PBKDF2-HMAC-SHA256
  salé (jamais de SHA-256 nu : sans salage ni ralentissement délibéré, un
  hash de code court se retrouve/se force en un temps négligeable) ; le
  code en clair ne transite qu'une fois, sur la requête de vérification,
  et n'est jamais stocké ;
- délivre un jeton de session opaque une fois le code vérifié, gardé en
  mémoire process (perdu si le backend redémarre — cohérent avec le
  comportement précédent, qui redemandait déjà le code à chaque lancement
  de l'app, puisque rien ne persistait la classe "déverrouillé" elle-même) ;
- expose verify_session(), utilisé par le middleware global (voir main.py)
  qui bloque désormais tout /api/* (hors /api/auth/*) et /uploads/* sans
  jeton valide — c'est ce qui manquait avant : la vraie faille n'était pas
  la solidité du hash, mais l'absence totale de contrôle côté serveur ;
- limite grossièrement les tentatives successives (code d'accès et code de
  récupération), avec un recul exponentiel plafonné.

Limite assumée et documentée, comme dans crypto_utils.py : cette app tourne
en local, mono-utilisateur. Ce mécanisme protège contre un tiers qui
ouvrirait l'app ou enverrait des requêtes à 127.0.0.1:8000 par-dessus
l'épaule de l'utilisateur ou depuis un autre programme sur la même
machine — pas contre quelqu'un ayant un accès complet et durable au poste
(aucune protection purement locale ne peut s'en prémunir).
"""
import hashlib
import hmac
import os
import secrets
import time
from typing import Dict, Optional, Tuple

PBKDF2_ITERATIONS = 260_000

# Durée de session glissante : prolongée à chaque requête validée par le
# middleware, tant que l'app reste ouverte. Expire si l'app reste inactive
# au-delà (le backend tournant en continu tant que l'app est ouverte, ce
# n'est normalement jamais atteint en usage courant).
SESSION_TTL_SECONDS = 12 * 60 * 60

# Alphabet sans caractères ambigus (0/O, 1/I) pour le code de récupération —
# recopié depuis l'ancienne implémentation frontend (voir git blame).
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

LOCKOUT_THRESHOLD = 5
LOCKOUT_BASE_SECONDS = 20
LOCKOUT_MAX_SECONDS = 300

# jeton -> expiration (epoch seconds). En mémoire process uniquement — voir
# docstring du module pour la justification.
_sessions: Dict[str, float] = {}

# Anti-brute-force basique : un seul code d'accès et un seul code de
# récupération existent dans cette app mono-utilisateur, donc un compteur
# global par "type" suffit (pas besoin de le suivre par IP/session).
_failed_attempts: Dict[str, int] = {"code": 0, "recovery": 0}
_locked_until: Dict[str, float] = {"code": 0.0, "recovery": 0.0}


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS).hex()


def hash_new_secret(secret: str) -> Tuple[str, str]:
    """Retourne (hash_hex, salt_hex) pour un nouveau secret (code d'accès ou
    code de récupération). Un sel aléatoire différent est généré à chaque
    appel — deux utilisateurs (ou deux réinitialisations) avec le même code
    n'auront jamais le même hash stocké."""
    salt = os.urandom(16)
    return _hash_secret(secret, salt), salt.hex()


def verify_secret(secret: str, hash_hex: str, salt_hex: str) -> bool:
    """Comparaison en temps constant (hmac.compare_digest) pour ne pas
    laisser fuiter d'information via le temps de réponse."""
    try:
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False
    candidate = _hash_secret(secret, salt)
    return hmac.compare_digest(candidate, hash_hex)


def generate_recovery_code() -> str:
    groups = ["".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "MLX-" + "-".join(groups)


def _purge_expired_sessions() -> None:
    """Retire les jetons périmés du dictionnaire en mémoire.

    verify_session() n'en supprimait un que s'il était présenté à nouveau
    APRÈS expiration : un jeton jamais réutilisé (fermeture de l'app, page
    rechargée, reconnexion sous un autre jeton) restait indéfiniment en
    mémoire. Sans conséquence de sécurité — il est bien refusé — mais le
    dictionnaire ne faisait que croître pour la durée de vie du process.
    Purge faite à la création d'une session : rare, et c'est exactement le
    moment où un jeton de plus est ajouté.
    """
    now = time.time()
    for token in [t for t, expires in _sessions.items() if expires < now]:
        _sessions.pop(token, None)


def create_session() -> str:
    _purge_expired_sessions()
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL_SECONDS
    return token


def verify_session(token: Optional[str]) -> bool:
    if not token:
        return False
    expires = _sessions.get(token)
    if expires is None:
        return False
    if expires < time.time():
        _sessions.pop(token, None)
        return False
    # Session glissante : chaque usage valide prolonge son expiration, tant
    # que l'app reste effectivement utilisée.
    _sessions[token] = time.time() + SESSION_TTL_SECONDS
    return True


def revoke_session(token: Optional[str]) -> None:
    _sessions.pop(token, None) if token else None


def revoke_all_sessions() -> None:
    """Révoque toutes les sessions après une réinitialisation du secret."""
    _sessions.clear()


def is_locked(kind: str) -> Optional[int]:
    """Renvoie le nombre de secondes restantes si `kind` ("code" ou
    "recovery") est actuellement verrouillé après trop d'échecs, sinon
    None."""
    remaining = _locked_until.get(kind, 0.0) - time.time()
    return int(remaining) + 1 if remaining > 0 else None


def register_failure(kind: str) -> None:
    _failed_attempts[kind] = _failed_attempts.get(kind, 0) + 1
    count = _failed_attempts[kind]
    if count >= LOCKOUT_THRESHOLD:
        exponent = count - LOCKOUT_THRESHOLD
        delay = min(LOCKOUT_BASE_SECONDS * (2 ** exponent), LOCKOUT_MAX_SECONDS)
        _locked_until[kind] = time.time() + delay


def register_success(kind: str) -> None:
    _failed_attempts[kind] = 0
    _locked_until[kind] = 0.0
