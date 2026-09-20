import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from paths import get_app_root

logger = logging.getLogger(__name__)

# Voir paths.py : ancré sur get_app_root() (dossier de l'exe une fois
# compilé) plutôt que sur __file__, pour ne pas régénérer une nouvelle clé
# (et donc perdre l'accès aux mots de passe MT5 déjà chiffrés) à chaque
# redémarrage de l'app compilée.
_KEY_PATH = os.path.join(get_app_root(), "db", ".secret_key")


def _load_or_create_key() -> bytes:
    key_path = os.path.abspath(_KEY_PATH)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read().strip()

    key = Fernet.generate_key()
    # Permissions restreintes (best-effort — sans effet sous Windows via
    # os.chmod, mais inoffensif : c'est là que tourne principalement MT5).
    with open(key_path, "wb") as f:
        f.write(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def _init_fernet() -> Fernet:
    """Initialise Fernet à partir de la clé stockée.

    Correction : auparavant, un fichier `.secret_key` vide ou corrompu
    (ex. copie interrompue, édition accidentelle) faisait planter l'API
    entière au démarrage avec une exception brute de la librairie
    `cryptography`, sans indication de la cause ni de la solution. On
    intercepte maintenant cette erreur pour échouer avec un message
    explicite et une marche à suivre, plutôt qu'un crash opaque.
    """
    key_path = os.path.abspath(_KEY_PATH)
    try:
        return Fernet(_load_or_create_key())
    except (ValueError, TypeError) as exc:
        logger.error(
            "Clé de chiffrement invalide ou corrompue (%s) : %s. "
            "Les mots de passe MT5 déjà enregistrés ne seront plus "
            "déchiffrables avec une nouvelle clé. Pour repartir sur une "
            "nouvelle clé (il faudra ressaisir les mots de passe MT5 "
            "dans l'application), supprimez le fichier ci-dessus puis "
            "redémarrez l'API.",
            key_path, exc,
        )
        raise RuntimeError(
            f"Impossible de charger la clé de chiffrement ({key_path}) : "
            f"fichier vide ou corrompu. Supprimez ce fichier puis "
            f"redémarrez l'application (les mots de passe MT5 enregistrés "
            f"devront être ressaisis)."
        ) from exc


_fernet = _init_fernet()


def encrypt(plain: str) -> str:
    """Chiffre une chaîne (mot de passe MT5) avant écriture en base."""
    return _fernet.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Déchiffre une chaîne préalablement chiffrée par `encrypt`.

    Si le token n'est pas déchiffrable (clé changée, valeur en clair
    d'avant migration...), l'erreur est propagée telle quelle — mieux vaut
    échouer clairement que de se connecter à MT5 avec un mot de passe
    corrompu.
    """
    return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
