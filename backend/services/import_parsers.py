"""Lecture des fichiers importés vers une structure intermédiaire commune.

Voir Recap-structure-manuel-import.md, section 6. Deux familles de sources :

- **CSV générique** : une ligne = un trade déjà clôturé (ou ouvert), mappée
  par l'utilisateur via un écran de correspondance de colonnes
  (``rows_to_trades_via_mapping``).
- **Rapport d'historique MT5** (HTML ou xlsx) : la table « Positions » du
  rapport contient déjà UNE LIGNE PAR POSITION avec l'agrégation faite par
  MT5 lui-même (prix moyen pondéré, S/L, T/P, profit/commission/swap) — elle
  sert de source primaire, directement mappable au modèle `Trade`
  (``rows_from_report`` + ``positions_to_trades``). La table « Transactions »
  (Deals) sert en complément : motif de sortie (StopLoss/TakeProfit via le
  commentaire de la ligne « out ») et contrôle d'intégrité (somme des
  transactions vs total du rapport) — voir import_service.py.

Important — pas de fichier d'exemple réel disponible à l'écriture de ce
module (le jeu de test de référence, section 9, décrit le résultat attendu
mais `ReportHistory-17163101.html/.xlsx` ne sont pas fournis) : les alias
d'en-têtes ci-dessous suivent le format documenté par MT5 (colonnes
« Time / Position / Symbol / Type / Volume / Price / S-L / T-P / Time / Price
/ Commission / Swap / Profit » pour Positions, et leurs équivalents français)
et les pièges relevés (BOM UTF-16, colonnes lues par NOM jamais par position,
ligne de totaux à ignorer). À CALIBRER dès que les fichiers réels sont
disponibles — voir la todo dans import_service.py.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional


# ── Décodage ─────────────────────────────────────────────────────────────

def decode_bytes(raw: bytes) -> str:
    """Décode un fichier texte en détectant un BOM UTF-16 (piège relevé sur
    les rapports MT5 en français, section 6 du récapitulatif)."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _norm(s: Optional[str]) -> str:
    """Normalise un en-tête pour comparaison : minuscules, sans accents, sans
    ponctuation ni espaces. « S / L » et « S-L » deviennent tous deux « sl »."""
    if not s:
        return ""
    s = s.strip().lower()
    trans = str.maketrans("éèêëàâäôöîïçûüù", "eeeeaaaooiicuuu")
    s = s.translate(trans)
    return re.sub(r"[^a-z0-9]+", "", s)


# ── CSV générique ────────────────────────────────────────────────────────

def read_csv_rows(text: str) -> tuple[list[str], list[dict]]:
    """Détecte le délimiteur (`,`, `;` ou tabulation) et lit le CSV en
    dictionnaires indexés par en-tête d'origine."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        class _Fallback(csv.excel):
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
        dialect = _Fallback
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    rows = [dict(r) for r in reader]
    return headers, rows


# Champs attendus côté trade — voir services/manual_trades.py et TradeCreate.
CSV_TARGET_FIELDS = (
    "ticket", "symbol", "direction", "volume", "open_price", "open_time",
    "close_price", "close_time", "sl", "tp", "profit", "commission", "swap",
    "comment",
)
CSV_NUMERIC_FIELDS = {"volume", "open_price", "close_price", "sl", "tp", "profit", "commission", "swap"}
CSV_DATETIME_FIELDS = {"open_time", "close_time"}
CSV_REQUIRED_FIELDS = {"symbol", "direction", "volume", "open_price", "open_time"}

_DATETIME_FORMATS = (
    "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    "%d/%m/%Y", "%Y-%m-%d", "%Y.%m.%d",
)


def parse_number(raw: object) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    # Décimales à la française (« 0,18 ») quand il n'y a pas déjà un point.
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    else:
        text = text.replace(" ", "").replace("\u202f", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_datetime(raw: object) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def rows_to_trades_via_mapping(rows: list[dict], mapping: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Applique la correspondance colonnes CSV → champs de trade choisie par
    l'utilisateur (écran de mapping, voir section 6). Une ligne invalide n'est
    pas rejetée ici : elle est renvoyée avec un champ `_row_error`, pour être
    affichée dans l'aperçu plutôt que de faire échouer tout l'import.
    """
    trades: list[dict] = []
    errors: list[str] = []
    for i, row in enumerate(rows, start=2):  # ligne 1 = en-têtes
        data: dict = {"_row_number": i}
        for field_name in CSV_TARGET_FIELDS:
            col = mapping.get(field_name)
            if not col:
                continue
            raw_value = row.get(col)
            if field_name in CSV_NUMERIC_FIELDS:
                data[field_name] = parse_number(raw_value)
            elif field_name in CSV_DATETIME_FIELDS:
                data[field_name] = parse_datetime(raw_value)
            elif field_name == "ticket":
                n = parse_number(raw_value)
                data[field_name] = int(n) if n is not None else None
            elif field_name == "direction":
                data[field_name] = _normalize_direction(raw_value)
            else:
                data[field_name] = (str(raw_value).strip() or None) if raw_value is not None else None
        missing = [f for f in CSV_REQUIRED_FIELDS if not data.get(f)]
        if missing:
            errors.append(f"Ligne {i} : champ(s) manquant(s) ou invalide(s) : {', '.join(missing)}")
            data["_row_error"] = "Champs requis manquants : " + ", ".join(missing)
        trades.append(data)
    return trades, errors


def _normalize_direction(raw: object) -> Optional[str]:
    text = _norm(str(raw)) if raw is not None else ""
    if text in ("buy", "achat", "long"):
        return "buy"
    if text in ("sell", "vente", "short"):
        return "sell"
    return None


# ── Rapport MT5 (HTML) ───────────────────────────────────────────────────

class _TableExtractor(HTMLParser):
    """Extrait toutes les lignes de toutes les <table> d'un document HTML,
    sous forme de liste de cellules texte.

    Piège relevé sur le fichier réel `ReportHistory-*.html` : une cellule
    ``class="hidden"`` (ex. la colonne « Coût » des Transactions, ou les
    espaceurs des lignes de Positions) n'est PAS un espace à combler par son
    `colspan` — elle est simplement absente de la grille logique du rapport.
    On la retire donc entièrement de la ligne plutôt que de la remplacer par
    des cellules vides : les autres cellules de la ligne s'alignent alors
    1-pour-1 avec les colonnes visibles de l'en-tête (vérifié sur le jeu de
    test de référence, section 9). Le `colspan` n'est sinon pas développé :
    une ligne de titre de section (« Positions », colspan=14) reste une
    ligne à une seule cellule, ce qui sert justement à détecter les titres.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_cell = False
        self._cell_hidden = False
        self._cell_text: list[str] = []
        self._current_row: Optional[list[str]] = None
        self._depth_table = 0

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "table":
            self._depth_table += 1
        elif tag == "tr" and self._depth_table:
            self._current_row = []
        elif tag in ("td", "th") and self._depth_table and self._current_row is not None:
            self._in_cell = True
            self._cell_text = []
            classes = (attrs_d.get("class") or "").split()
            self._cell_hidden = "hidden" in classes

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth_table = max(0, self._depth_table - 1)
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None
        elif tag in ("td", "th") and self._in_cell:
            if not self._cell_hidden:
                text = " ".join("".join(self._cell_text).split())
                self._current_row.append(text)
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text.append(data)


def rows_from_html(text: str) -> list[list[str]]:
    parser = _TableExtractor()
    parser.feed(text)
    return parser.rows


def rows_from_xlsx(raw: bytes) -> list[list[str]]:
    """Lit toutes les feuilles d'un classeur xlsx en lignes de cellules
    texte, dans le même format que `rows_from_html` (un seul normalisateur
    en aval pour les deux formats, voir section 6). `openpyxl` lit le
    `workbook.xml` en UTF-16 (non standard sur ces rapports) avec un simple
    avertissement — ignoré ici, pas une erreur."""
    import warnings
    from openpyxl import load_workbook

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)

    all_rows: list[list[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = []
            for v in row:
                if v is None:
                    cells.append("")
                elif isinstance(v, str) and v.strip().startswith("="):
                    cells.append("")  # formule de la ligne de totaux, ignorée
                elif isinstance(v, datetime):
                    cells.append(v)  # conservé tel quel, typé — voir _cell_value
                else:
                    cells.append(v)
            all_rows.append(cells)
    return all_rows


# ── Sections du rapport (Positions / Transactions / en-tête compte) ───────

POSITIONS_SECTION_NAMES = {_norm(n) for n in ("Positions",)}
DEALS_SECTION_NAMES = {_norm(n) for n in ("Transactions", "Deals")}
RESULTS_SECTION_NAMES = {_norm(n) for n in ("Résultats", "Results")}

# alias d'en-tête normalisé -> champ cible. Vérifiés sur le rapport réel
# ReportHistory-17163101 (html + xlsx) : le swap s'appelle « Echange » (pas
# « Swap ») et le ticket de position est bien la colonne « Position ».
# La 1re "Heure"/"Time" est l'ouverture, la 2e la clôture ; idem pour "Prix"/
# "Price" (en-têtes dupliqués, non annotés dans le fichier).
POSITIONS_HEADER_ALIASES: dict[str, str] = {
    "heure": "time", "time": "time",
    "position": "ticket", "positionid": "ticket",
    "symbole": "symbol", "symbol": "symbol",
    "type": "direction",
    "volume": "volume",
    "prix": "price", "price": "price",
    "sl": "sl",
    "tp": "tp",
    "commission": "commission",
    "echange": "swap", "swap": "swap",
    "profit": "profit",
    "cout": "cost", "cost": "cost",  # colonne « Coût » — masquée (class="hidden"), donc jamais lue en pratique
}
# Champs qui apparaissent deux fois dans l'en-tête (ouverture puis clôture).
POSITIONS_DUAL_FIELDS = {"time": ("open_time", "close_time"), "price": ("open_price", "close_price")}

DEALS_HEADER_ALIASES: dict[str, str] = {
    "heure": "time", "time": "time",
    "operation": "deal_id", "deal": "deal_id",  # colonne réelle : « Opération »
    "symbole": "symbol", "symbol": "symbol",
    "type": "side",
    "direction": "entry",
    "volume": "volume",
    "prix": "price", "price": "price",
    "ordre": "order", "order": "order",
    "commission": "commission",
    "frais": "fees", "fees": "fees",
    "echange": "swap", "swap": "swap",
    "profit": "profit",
    "solde": "balance", "balance": "balance",
    "commentaire": "comment", "comment": "comment",
}


def _cell_value(cell) -> str:
    if isinstance(cell, datetime):
        return cell.isoformat(sep=" ")
    return "" if cell is None else str(cell)


def _find_section_start(rows: list[list[str]], names: set[str], after: int = 0) -> Optional[int]:
    """Renvoie l'index de la ligne d'EN-TÊTE (juste après le titre de
    section), ou None si la section est absente du rapport."""
    for i in range(after, len(rows)):
        row = rows[i]
        non_empty = [c for c in row if _cell_value(c).strip()]
        if len(non_empty) == 1 and _norm(_cell_value(non_empty[0])) in names:
            # Ligne suivante non vide = en-tête de colonnes.
            for j in range(i + 1, len(rows)):
                if any(_cell_value(c).strip() for c in rows[j]):
                    return j
    return None


def _build_column_map(header_row: list[str], aliases: dict[str, str], dual_fields: Optional[dict] = None) -> dict[int, str]:
    dual_fields = dual_fields or {}
    seen: dict[str, int] = {}
    col_map: dict[int, str] = {}
    for idx, raw_header in enumerate(header_row):
        key = _norm(raw_header)
        target = aliases.get(key)
        if not target:
            continue
        seen[target] = seen.get(target, 0) + 1
        if target in dual_fields:
            occurrence = seen[target]
            names = dual_fields[target]
            col_map[idx] = names[min(occurrence, len(names)) - 1]
        else:
            col_map[idx] = target
    return col_map


def _is_total_row(row: list[str]) -> bool:
    first = _norm(_cell_value(row[0]) if row else "")
    return first.startswith("total")


def _extract_section_rows(rows: list[list[str]], header_idx: int, col_map: dict[int, str]) -> list[dict]:
    """Lit les lignes de données d'une section jusqu'au prochain titre de
    section (ligne à une seule cellule non vide).

    Le vrai rapport MT5 n'annote pas toujours ses lignes de totaux avec le
    texte « Total: » (la ligne de totaux des Transactions, par exemple, est
    une cellule vide suivie de nombres en gras, sans aucun libellé). Le
    filtre fiable est donc : une ligne de données commence toujours par une
    date/heure valide dans sa colonne "Heure" — toute ligne où cette colonne
    ne se lit pas comme une date (totaux, séparateurs, résumé de compte) est
    ignorée sans pour autant terminer la section.
    """
    time_cols = [idx for idx, f in col_map.items() if f in ("open_time", "time")]
    time_idx = min(time_cols) if time_cols else None

    out: list[dict] = []
    for row in rows[header_idx + 1:]:
        non_empty = [c for c in row if _cell_value(c).strip()]
        if not non_empty:
            continue  # ligne vide (espaceur) : on continue à chercher des données
        if len(non_empty) == 1:
            break  # nouvelle section (ligne titre) : fin de celle-ci
        if time_idx is not None and time_idx < len(row):
            if parse_datetime(row[time_idx]) is None:
                continue  # ligne de totaux / résumé de compte : ignorée
        elif _is_total_row(row):
            continue
        record: dict = {}
        for idx, field_name in col_map.items():
            if idx < len(row):
                record[field_name] = row[idx]
        out.append(record)
    return out


@dataclass
class ParsedReport:
    account_header: dict = field(default_factory=dict)
    positions: list[dict] = field(default_factory=list)
    deals: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


ACCOUNT_HEADER_ALIASES = {
    "compte": "login", "account": "login",
    "nom": "name", "name": "name",
    "devise": "currency", "currency": "currency",
    "courtier": "broker", "company": "broker", "societe": "broker",
    "serveur": "server", "server": "server",
}
SUMMARY_ALIASES = {
    "profittotalnet": "total_net_profit", "totalnetprofit": "total_net_profit",
    "solde": "balance", "balance": "balance",
    "nbtrades": "trades_count", "trades": "trades_count",
    "totaltrades": "trades_count",
}


def _parse_account_header(rows: list[list[str]], limit: int) -> dict:
    header: dict = {}
    for row in rows[:limit]:
        cells = [_cell_value(c).strip() for c in row if _cell_value(c).strip()]
        for i in range(0, len(cells) - 1, 2):
            key = _norm(cells[i].rstrip(":"))
            target = ACCOUNT_HEADER_ALIASES.get(key)
            if target and target not in header:
                header[target] = cells[i + 1]
    if "login" in header:
        # Le compte réel se présente en une seule valeur composite, ex. :
        # « 17163101 (USD, FBS-Real, real, Hedge) » — login, devise et
        # serveur/courtier y sont mêlés plutôt que sur des lignes séparées.
        raw_login = header["login"]
        match = re.match(r"\s*(\d+)\s*(?:\(([^)]*)\))?", raw_login)
        if match:
            header["login"] = int(match.group(1))
            parenthetical = [p.strip() for p in (match.group(2) or "").split(",") if p.strip()]
            if parenthetical and "currency" not in header:
                header["currency"] = parenthetical[0]
            if len(parenthetical) > 1 and "server" not in header:
                header["server"] = parenthetical[1]
        else:
            digits = re.sub(r"[^0-9]", "", raw_login)
            header["login"] = int(digits) if digits else None
    return header


def _parse_summary(rows: list[list[str]], after: int) -> dict:
    summary: dict = {}
    for row in rows[after:]:
        cells = [_cell_value(c).strip() for c in row if _cell_value(c).strip()]
        for i in range(0, len(cells) - 1, 2):
            key = _norm(cells[i].rstrip(":"))
            target = SUMMARY_ALIASES.get(key)
            if not target:
                continue
            if target == "trades_count":
                n = parse_number(cells[i + 1])
                summary[target] = int(n) if n is not None else None
            else:
                summary[target] = parse_number(cells[i + 1])
    return summary


def parse_report_rows(rows: list[list[str]]) -> ParsedReport:
    """Normalise les lignes brutes (issues du HTML ou du xlsx) en structure
    intermédiaire commune. Voir le docstring de tête de module pour les
    limites connues (pas de fichier réel disponible pour calibrer)."""
    report = ParsedReport()

    pos_header_idx = _find_section_start(rows, POSITIONS_SECTION_NAMES)
    report.account_header = _parse_account_header(rows, pos_header_idx or len(rows))

    if pos_header_idx is not None:
        col_map = _build_column_map(rows[pos_header_idx], POSITIONS_HEADER_ALIASES, POSITIONS_DUAL_FIELDS)
        report.positions = _extract_section_rows(rows, pos_header_idx, col_map)

    deals_header_idx = _find_section_start(rows, DEALS_SECTION_NAMES, after=(pos_header_idx or 0))
    if deals_header_idx is not None:
        col_map = _build_column_map(rows[deals_header_idx], DEALS_HEADER_ALIASES)
        report.deals = _extract_section_rows(rows, deals_header_idx, col_map)

    results_idx = _find_section_start(rows, RESULTS_SECTION_NAMES, after=(deals_header_idx or pos_header_idx or 0))
    report.summary = _parse_summary(rows, results_idx if results_idx is not None else 0)
    return report


# ── Dépôts / retraits dans la table Transactions (phase 6) ───────────────
#
# Un dépôt ou un retrait est une ligne de la table Transactions dont le
# « Type » est « balance » (« Balance » dans le rapport MT5, y compris en
# français) : pas de symbole, pas de volume, le montant SIGNÉ est dans la
# colonne « Profit » (positif = dépôt, négatif = retrait) et le solde courant
# dans « Solde ». Le commentaire (« Deposit », « Withdrawal »…) est libre.
# À CALIBRER sur un rapport réel contenant un dépôt : le rapport d'exemple
# (ReportHistory-17163101) n'en contenait pas — voir tests/fixtures.

BALANCE_TYPE_NAMES = {
    _norm(n) for n in (
        "balance", "solde", "deposit", "withdrawal", "withdraw",
        "dépôt", "depot", "retrait", "versement",
    )
}
_BALANCE_COMMENT_HINTS = ("deposit", "withdraw", "depot", "retrait", "versement")
_TRADE_SIDE_NAMES = {_norm(n) for n in ("buy", "sell", "achat", "vente")}


def is_balance_deal(deal: dict) -> bool:
    """Vrai pour une ligne de Transactions qui est un dépôt / retrait de
    capital et non un trade. Repère principal : le type « balance ». Repli
    (type absent ou inconnu) : aucune donnée de trade (ni symbole, ni volume,
    ni sens achat/vente) ET un commentaire évoquant un dépôt ou un retrait."""
    side = _norm(str(deal.get("side") or ""))
    if side in BALANCE_TYPE_NAMES:
        return True
    if side in _TRADE_SIDE_NAMES:
        return False
    has_symbol = bool(str(deal.get("symbol") or "").strip())
    has_volume = bool(str(deal.get("volume") or "").strip())
    if has_symbol or has_volume:
        return False
    comment = _norm(str(deal.get("comment") or ""))
    return any(hint in comment for hint in _BALANCE_COMMENT_HINTS)


def deal_net(deal: dict) -> float:
    """profit + commission + échange + frais d'une ligne de Transactions."""
    return sum(
        (parse_number(deal.get(key)) or 0.0)
        for key in ("profit", "commission", "swap", "fees")
    )
