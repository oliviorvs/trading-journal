"""
Point d'entrée de l'application desktop "Trading Journal".

Remplace, pour l'usage desktop, le tandem START.bat (deux fenêtres console,
deux serveurs : API sur :8000 + `python -m http.server` sur :5500) par un
seul process : le backend FastAPI (qui sert maintenant aussi le frontend,
voir le mount ajouté dans backend/main.py) tourne dans un thread, et une
fenêtre native (pywebview) s'ouvre dessus.

Fermer la fenêtre = quitter l'app : le thread serveur est démarré en
`daemon=True`, donc il s'arrête automatiquement avec le process principal
sans code de shutdown explicite à écrire.
"""

import os
import base64
import json
import logging
import socket
import sys
import threading
import time
import urllib.request

import uvicorn
import webview

HOST = "127.0.0.1"
PORT = 8000
WINDOW_TITLE = "Trading Journal"


class DesktopApi:
    """Pont minimal entre le frontend et la boîte de sauvegarde native."""

    def save_pdf(self, filename: str, encoded_pdf: str) -> bool:
        pdf_data = base64.b64decode(encoded_pdf)
        window = webview.windows[0]
        selected = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=("PDF files (*.pdf)",),
        )
        if not selected:
            return False
        target = selected[0] if isinstance(selected, (list, tuple)) else selected
        with open(target, "wb") as output:
            output.write(pdf_data)
        return True

    def save_file(self, filename: str, encoded_data: str, description: str = "Fichier") -> bool:
        """Généralise `save_pdf` à n'importe quel export (CSV, JSON, XLSX de
        l'Analyzer) : même mécanisme, extension et filtre déduits du nom de
        fichier plutôt que codés en dur.

        Sans ceci, l'Analyzer utilisait un simple lien `<a download>` : dans
        la fenêtre pywebview embarquée (contrairement à un vrai navigateur),
        ce mécanisme ne déclenche aucune boîte de dialogue et le clic
        n'aboutissait à rien de visible — c'est le bug remonté.
        """
        data = base64.b64decode(encoded_data)
        window = webview.windows[0]
        ext = os.path.splitext(filename)[1].lstrip(".") or "*"
        selected = window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=filename,
            file_types=(f"{description} (*.{ext})", "Tous les fichiers (*.*)"),
        )
        if not selected:
            return False
        target = selected[0] if isinstance(selected, (list, tuple)) else selected
        with open(target, "wb") as output:
            output.write(data)
        return True

    def open_html_report(self, html_content: str) -> bool:
        """Ouvre le rapport Analyzer dans une VRAIE fenêtre pywebview séparée.

        `window.open(url, '_blank')` n'a pas d'équivalent fiable dans une
        fenêtre pywebview embarquée : il n'y a pas d'onglets, et selon le
        moteur (WebView2, WebKit, GTK) l'appel est soit ignoré soit sans
        effet visible — c'est exactement ce qui produisait « n'affiche
        rien ». Le contenu est donc écrit dans un fichier temporaire (le
        rapport est déjà autonome, sans CDN) puis ouvert dans une fenêtre
        pywebview dédiée, qui fonctionne à l'identique sur les trois moteurs.
        """
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".html", prefix="analyzer-rapport-")
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(html_content)
        webview.create_window("Rapport Analyzer", path, width=1200, height=850)
        return True

# En dev : dossier de ce fichier. Une fois empaqueté avec PyInstaller (mode
# --onefile), sys._MEIPASS pointe vers le dossier temporaire d'extraction où
# atterrissent les fichiers ajoutés en --add-data (backend/, frontend/...).
# Cette ligne est ce qui permettra à ce même script de tourner tel quel une
# fois compilé, sans modification — préparation de l'étape empaquetage.
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)


def _port_is_open(host: str, port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _is_our_api(host: str, port: int) -> bool:
    """Un port ouvert ne veut pas dire que c'est NOTRE API : n'importe quel
    autre programme peut occuper 8000. On interroge une route connue et non
    protégée (/api/auth/status) pour le vérifier.

    Avant, le launcher se contentait de constater que le port répondait et
    ouvrait la fenêtre dessus — l'utilisateur se retrouvait devant la page
    d'un autre logiciel, ou devant une fenêtre blanche, sans explication.
    """
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/api/auth/status", timeout=1.5
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return "configured" in payload
    except Exception:
        return False


def _pick_port() -> int:
    """Port d'écoute : PORT par défaut, sinon le premier port libre juste
    au-dessus. Corrige le cas — fréquent — où l'app refusait de démarrer (ou
    s'affichait vide) simplement parce que 8000 était déjà pris par un autre
    programme. Le frontend n'a plus de port codé en dur : il déduit l'API de
    l'origine de la page (voir frontend/js/config.js)."""
    for candidate in range(PORT, PORT + 20):
        if not _port_is_open(HOST, candidate):
            return candidate
    raise RuntimeError(
        f"Aucun port libre entre {PORT} et {PORT + 19} pour démarrer le serveur local."
    )


# Erreur éventuelle remontée depuis le thread serveur. Sans ce relais,
# l'exception réelle (dépendance manquante, base verrouillée, dossier
# frontend absent...) mourait dans le thread et l'utilisateur ne voyait
# qu'un « le serveur n'a pas répondu après 15s » — inexploitable, d'autant
# que l'exe est compilé sans console.
_server_error: "list[BaseException]" = []


def _run_server(port: int) -> None:
    # Import fait ici (et pas en haut du fichier) : il ne doit se produire
    # qu'une fois sys.path pointé vers backend/, et depuis ce thread — pas
    # depuis le process principal, pour ne pas retarder l'ouverture de la
    # fenêtre pendant les migrations/init DB au chargement de main.py.
    try:
        import main  # noqa: F401  (déclenche la création de `app`, migrations DB incluses)

        uvicorn.run(main.app, host=HOST, port=port, log_level="info")
    except BaseException as exc:  # noqa: BLE001 — on veut TOUT remonter
        _server_error.append(exc)
        logging.getLogger(__name__).exception("Démarrage du serveur backend échoué")


def _wait_for_server(thread: threading.Thread, port: int, timeout: float = 60.0) -> None:
    """Attend que l'API réponde, ou échoue avec la VRAIE cause.

    Trois changements par rapport à la version précédente :
    - on surveille le thread : s'il meurt, on lève immédiatement l'exception
      d'origine au lieu d'attendre le délai complet pour rien ;
    - on attend une réponse HTTP, pas seulement un port ouvert (le socket
      s'ouvre avant que l'application ne soit prête) ;
    - le délai passe de 15 s à 60 s : sur un premier lancement, les
      migrations SQLite et l'initialisation peuvent être lentes sur un
      disque chargé. Ce délai ne concerne plus MetaTrader5, dont le
      chargement est désormais asynchrone côté backend.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_error:
            raise RuntimeError(
                "Le serveur backend n'a pas pu démarrer. Voir logs/backend.log."
            ) from _server_error[0]
        if not thread.is_alive() and not _port_is_open(HOST, port):
            raise RuntimeError(
                "Le serveur backend s'est arrêté pendant son démarrage. "
                "Voir logs/backend.log."
            )
        if _is_our_api(HOST, port):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"Le serveur backend n'a pas répondu sur {HOST}:{port} après "
        f"{int(timeout)}s. Voir logs/backend.log pour le détail de l'erreur."
    )


def _error_window(message: str) -> None:
    """Affiche l'erreur DANS une fenêtre, et pas seulement dans les logs.

    L'exécutable est compilé sans console : jusqu'ici, si le backend ne
    démarrait pas, `main_entry` levait une exception qui n'allait nulle part —
    l'utilisateur double-cliquait sur l'icône et il ne se passait absolument
    rien, sans le moindre indice. On ouvre donc une petite fenêtre qui dit ce
    qui s'est passé et où regarder.
    """
    safe = (
        message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    html = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<style>
 body {{ margin:0; padding:28px; background:#0B0F14; color:#E6EDF5;
        font-family:system-ui,-apple-system,'Segoe UI',sans-serif; line-height:1.55; }}
 h1 {{ font-size:17px; margin:0 0 12px; }}
 p {{ font-size:13px; color:#94A3B8; margin:0 0 10px; }}
 pre {{ white-space:pre-wrap; background:#111827; border:1px solid #1F2A3A;
        padding:12px; font-size:12px; color:#E6EDF5; }}
</style></head><body>
<h1>Trading Journal n'a pas pu démarrer</h1>
<pre>{safe}</pre>
<p>Le détail complet se trouve dans <strong>logs/backend.log</strong>, à côté de l'application.</p>
<p>Cette erreur ne concerne pas MetaTrader 5 : l'application s'ouvre normalement
   sans MT5 et sans connexion internet.</p>
</body></html>"""
    webview.create_window(WINDOW_TITLE, html=html, width=680, height=420)
    webview.start()


def main_entry() -> None:
    if _is_our_api(HOST, PORT):
        # Une instance de l'API tourne déjà (ex. lancée à la main pour
        # débugger) : on ouvre simplement la fenêtre dessus.
        port = PORT
    else:
        port = _pick_port()
        server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
        server_thread.start()
        try:
            _wait_for_server(server_thread, port)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).exception("Démarrage impossible")
            cause = _server_error[0] if _server_error else exc
            _error_window(f"{exc}\n\n{type(cause).__name__}: {cause}")
            return

    webview.create_window(
        WINDOW_TITLE,
        f"http://{HOST}:{port}/",
        width=1400,
        height=900,
        min_size=(1024, 700),
        js_api=DesktopApi(),
    )
    webview.start()


if __name__ == "__main__":
    main_entry()
