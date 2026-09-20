"""Fenêtres de session, en HEURE SERVEUR (décision D3).

Les heures des trades sont stockées TELLES QUE FOURNIES par MT5 et par les
rapports importés — c'est-à-dire l'heure serveur du courtier, sans fuseau
(voir le commentaire de `CapitalMovement.time` dans models.py). Le cahier v1
supposait un stockage UTC : ce n'est pas le cas ici, donc aucune conversion
n'est faite. Les fuseaux IANA sont explicitement repoussés en V2.

Un trade appartient à UNE SEULE session : la PREMIÈRE fenêtre de la liste
qui contient son heure d'ouverture. Les fenêtres peuvent donc se chevaucher
sans créer de double comptage — l'ordre de la liste EST l'ordre de priorité.
"""
from datetime import datetime
from typing import List, Optional

HORS_SESSION = "Hors session"


def _to_minutes(hhmm: str) -> Optional[int]:
    try:
        hours, minutes = hhmm.split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, AttributeError):
        return None


def session_of(moment: Optional[datetime], windows: List[dict]) -> str:
    """Libellé de session d'un instant, ou « Hors session »."""
    if moment is None:
        return HORS_SESSION
    minute_of_day = moment.hour * 60 + moment.minute
    for window in windows or []:
        start = _to_minutes(window.get("start", ""))
        end = _to_minutes(window.get("end", ""))
        if start is None or end is None:
            continue
        # Fenêtre qui franchit minuit (ex. 22:00 → 02:00) : deux intervalles.
        inside = (start <= minute_of_day < end) if start < end \
            else (minute_of_day >= start or minute_of_day < end)
        if inside:
            return window.get("label") or window.get("key") or HORS_SESSION
    return HORS_SESSION


def session_labels(windows: List[dict]) -> List[str]:
    """Libellés dans l'ordre de priorité, « Hors session » en dernier —
    sert à garder un ordre d'affichage stable même quand une session est
    vide sur la période filtrée."""
    labels = [w.get("label") or w.get("key") for w in (windows or []) if (w.get("label") or w.get("key"))]
    return labels + [HORS_SESSION]


def validate_windows(windows) -> List[dict]:
    """Nettoie une liste de fenêtres reçue de l'interface.

    Lève ValueError sur une entrée inutilisable plutôt que de l'ignorer
    silencieusement : un réglage de session mal enregistré se traduirait
    sinon par des trades « hors session » inexplicables.
    """
    if not isinstance(windows, list) or not windows:
        raise ValueError("Au moins une fenêtre de session est requise.")
    cleaned = []
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError("Fenêtre de session invalide.")
        label = (window.get("label") or window.get("key") or "").strip()
        start, end = window.get("start", ""), window.get("end", "")
        if not label:
            raise ValueError("Chaque fenêtre de session doit porter un nom.")
        if _to_minutes(start) is None or _to_minutes(end) is None:
            raise ValueError(f"Horaires invalides pour « {label} » (format attendu HH:MM).")
        if start == end:
            raise ValueError(f"La fenêtre « {label} » a une durée nulle.")
        cleaned.append({
            "key": (window.get("key") or label).strip().lower(),
            "label": label,
            "start": start,
            "end": end,
        })
    return cleaned
