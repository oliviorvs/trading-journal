"""Rapport HTML autonome.

Contrainte du §11 : un FICHIER UNIQUE qui s'ouvre hors ligne. Aucun CDN,
aucune ressource externe — l'application est locale et le rapport contient
des données de trading : il ne doit provoquer aucune requête réseau à
l'ouverture, y compris sur une machine qui n'est pas celle de l'utilisateur.

Conséquence assumée : pas de Chart.js ici. Les graphiques sont des barres en
HTML/CSS pur. Embarquer les ~200 Ko du vendor dans chaque rapport pour
quelques barres horizontales serait un mauvais échange.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import List, Optional, Sequence

from services.analyzer import reliability

_STATUS_CLASS = {
    reliability.INSUFFICIENT: "st-low",
    reliability.WATCH: "st-watch",
    reliability.INTERESTING: "st-ok",
    reliability.ROBUST: "st-strong",
}

_INSIGHT_LABEL = {"risque": "Point d'attention", "force": "Point fort", "info": "Constat"}

# ─────────────────────────────────────────────────────────────────────────────
# SOURCE DE VÉRITÉ DU THÈME.
# Ces variables et cette typographie ne valent plus seulement pour le rapport :
# elles ont été reprises telles quelles dans l'interface de l'application
# (frontend/css/base/variables.css et reset.css). Toute modification ici doit
# être répercutée là-bas — et inversement — sinon les deux divergent.
# La pile système et les chiffres tabulaires y remplacent les anciennes
# polices Google : l'app ne télécharge plus aucune police.
# ─────────────────────────────────────────────────────────────────────────────
_CSS = """
:root{--bg:#0f141c;--surface:#161d28;--surface2:#1c2533;--border:#27303f;
--text:#e6ebf2;--muted:#8b97a8;--pos:#2fb67c;--neg:#e2574c;--accent:#4a7dff}
@media print{:root{--bg:#fff;--surface:#fff;--surface2:#f5f6f8;--border:#d8dce3;
--text:#111;--muted:#666}body{padding:0}.card{break-inside:avoid}}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.sub{color:var(--muted);font-size:13px}
.wrap{max-width:1100px;margin:0 auto}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px}
.kpi-label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.kpi-value{font-size:20px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;text-align:right;border-bottom:1px solid var(--border);font-size:13px}
th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;text-align:right}
th:first-child,td:first-child{text-align:left}
tr.low{opacity:.5}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;
font-weight:600;letter-spacing:.4px;border:1px solid var(--border)}
.st-low{color:var(--muted)}.st-watch{color:#d8a13a;border-color:#d8a13a}
.st-ok{color:var(--accent);border-color:var(--accent)}.st-strong{color:var(--pos);border-color:var(--pos)}
.warn{background:rgba(216,161,58,.12);border:1px solid #d8a13a;border-radius:8px;
padding:12px 14px;margin-bottom:18px;font-size:13px}
.bar{height:6px;background:var(--surface2);border-radius:3px;overflow:hidden;min-width:60px}
.bar span{display:block;height:100%;background:var(--accent)}
.rule{border-left:3px solid var(--accent);padding:8px 12px;margin-bottom:10px;background:var(--surface2);border-radius:0 6px 6px 0}
.rule .caveat{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}
"""


def _money(value: Optional[float], currency: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f'<span class="{"pos" if value >= 0 else "neg"}">{sign}{value:,.2f} {currency}</span>'.replace(",", " ")


def _num(value: Optional[float], suffix: str = "", digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _badge(status: Optional[str]) -> str:
    if not status:
        return ""
    return f'<span class="badge {_STATUS_CLASS.get(status, "st-low")}">{escape(status)}</span>'


def _rows_table(rows: Sequence[dict], key_label: str, currency: str) -> str:
    if not rows:
        return '<p class="muted">Aucune donnée — renseignez ce champ depuis la fiche d\'un trade.</p>'
    head = (f"<tr><th>{escape(key_label)}</th><th>n</th><th>Part</th><th>Win rate</th>"
            f"<th>Résultat</th><th>Expectancy</th><th>R moy.</th><th>Fiabilité</th></tr>")
    body = []
    for row in rows:
        ci = row.get("win_rate_ci") or {}
        ci_text = f' <span class="muted">[{ci["low"]:.0f}–{ci["high"]:.0f}]</span>' if ci else ""
        coverage = row.get("r_coverage")
        r_text = _num(row.get("avg_r"), "R")
        if row.get("avg_r") is not None and coverage is not None:
            r_text += f' <span class="muted">({coverage:.0f}%)</span>'
        body.append(
            f'<tr class="{"low" if row.get("low_sample") else ""}">'
            f"<td>{escape(str(row.get('key', '')))}</td>"
            f"<td>{row.get('trades', 0)}</td>"
            f"<td>{_num(row.get('pct_of_total'), '%', 1)}</td>"
            f"<td>{_num(row.get('win_rate'), '%', 1)}{ci_text}</td>"
            f"<td>{_money(row.get('pnl'), currency)}</td>"
            f"<td>{_money(row.get('expectancy'), currency)}</td>"
            f"<td>{r_text}</td>"
            f"<td>{_badge(row.get('status'))}</td></tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


def build_report(bundle: dict, account_label: str = "", currency: str = "") -> str:
    overview = bundle.get("overview") or {}
    analyzer = overview.get("analyzer") or {}
    journal = overview.get("journal") or {}
    filters = bundle.get("filters") or {}
    dimensions = bundle.get("dimensions") or {}

    period = " · ".join(
        part for part in [
            f"du {filters['date_from']}" if filters.get("date_from") else "",
            f"au {filters['date_to']}" if filters.get("date_to") else "",
            f"symbole {filters['symbol']}" if filters.get("symbol") else "",
            f"source {filters['source']}" if filters.get("source") else "",
        ] if part
    ) or "Historique complet"

    sections: List[str] = []

    # Résumé
    kpis = [
        ("Trades", str(analyzer.get("trades", 0))),
        ("Win rate", _num(analyzer.get("win_rate"), "%", 1)),
        ("Résultat", _money(analyzer.get("pnl"), currency)),
        ("Profit factor", _num(analyzer.get("profit_factor"))),
        ("Expectancy", _money(analyzer.get("expectancy"), currency)),
        ("Expectancy (R)", _num(analyzer.get("expectancy_r"), "R")),
        ("Couverture R", _num(analyzer.get("r_coverage"), "%", 1)),
        ("Drawdown max", _num(journal.get("max_drawdown"), "%", 2)),
    ]
    sections.append(
        '<div class="card"><h2>Résumé</h2><div class="kpis">'
        + "".join(
            f'<div class="kpi"><div class="kpi-label">{escape(label)}</div>'
            f'<div class="kpi-value">{value}</div></div>' for label, value in kpis
        )
        + "</div></div>"
    )

    # Dimensions
    titles = [
        ("setup", "Setups"), ("symbol", "Instruments"), ("session", "Sessions"),
        ("weekday", "Jours de la semaine"), ("hour", "Heures"),
        ("exit_reason", "Sorties"), ("emotion", "Émotions"), ("error", "Erreurs"),
    ]
    for key, title in titles:
        block = dimensions.get(key)
        if not block:
            continue
        sections.append(f'<div class="card"><h2>{title}</h2>'
                        f'{_rows_table(block.get("rows", []), title.rstrip("s"), currency)}</div>')

    # Constats automatiques (insights.py) — placés tôt : ce sont les phrases
    # qu'on veut lire avant de plonger dans les tableaux détaillés.
    insights = bundle.get("insights") or []
    if insights:
        items = "".join(
            f'<div class="rule"><div class="caveat">{escape(_INSIGHT_LABEL.get(item["kind"], "Constat"))}</div>'
            f'<div><strong>{escape(item["title"])}</strong><br>{escape(item["text"])}</div></div>'
            for item in insights
        )
        sections.append(f'<div class="card"><h2>Constats automatiques</h2>{items}</div>')

    # Séries et drawdown du groupe — déjà calculés (voir behaviour.series)
    # mais jusqu'ici jamais rendus dans ce rapport.
    series = bundle.get("series") or {}
    if series.get("longest_win") or series.get("longest_loss") or series.get("drawdowns"):
        rows_html = "".join(
            f"<tr><td>Du {escape(ep['from'])} au {escape(ep['trough'])}</td>"
            f"<td>{_money(ep['depth'], currency)}</td>"
            f"<td>{'Récupéré en ' + str(ep['trades_to_recover']) + ' trades' if ep['recovered'] else 'En cours'}</td></tr>"
            for ep in (series.get("drawdowns") or [])
        )
        sections.append(
            '<div class="card"><h2>Séries et drawdown</h2><div class="kpis">'
            f'<div class="kpi"><div class="kpi-label">Plus longue série de gains</div>'
            f'<div class="kpi-value">{series.get("longest_win", 0)}</div></div>'
            f'<div class="kpi"><div class="kpi-label">Plus longue série de pertes</div>'
            f'<div class="kpi-value">{series.get("longest_loss", 0)}</div></div>'
            f'</div>'
            + (f'<table style="margin-top:14px"><tr><th>Épisode</th><th>Creux</th><th>Récupération</th></tr>{rows_html}</table>' if rows_html else '')
            + '</div>'
        )

    # Coût des erreurs
    errors = bundle.get("errors") or {}
    if errors.get("rows"):
        lines = "".join(
            f"<tr><td>{escape(row['key'])}</td><td>{row['trades']}</td>"
            f"<td>{_money(row.get('cost'), currency)}</td>"
            f"<td>{_num(row.get('cost_r'), 'R')}</td>"
            f"<td>{_money(row.get('delta_expectancy'), currency)}</td></tr>"
            for row in errors["rows"]
        )
        sections.append(
            '<div class="card"><h2>Coût des erreurs</h2>'
            "<table><tr><th>Erreur</th><th>n</th><th>Coût cumulé</th><th>Coût (R)</th>"
            "<th>Écart d'espérance</th></tr>" + lines + "</table>"
            f'<p class="muted">Groupe de référence : {errors.get("reference_trades", 0)} trades '
            "déclarés sans erreur.</p></div>"
        )

    # Patterns
    patterns = bundle.get("patterns") or {}
    if patterns.get("rows"):
        multiplicity = patterns.get("multiplicity") or {}
        lines = "".join(
            f'<tr class="{"low" if row.get("inconclusive") else ""}">'
            f"<td>{escape(row['condition'])}</td><td>{row['trades']}</td>"
            f"<td>{_num(row.get('win_rate'), '%', 1)}</td>"
            f"<td>{_money(row.get('expectancy'), currency)}</td>"
            f"<td>{_num(row.get('expectancy_r'), 'R')}</td>"
            f"<td>{_badge(row.get('status'))}</td></tr>"
            for row in patterns["rows"][:25]
        )
        sections.append(
            '<div class="card"><h2>Patterns</h2>'
            f'<div class="warn">{escape(multiplicity.get("text", ""))}</div>'
            "<table><tr><th>Condition</th><th>n</th><th>Win rate</th><th>Expectancy</th>"
            "<th>Exp. (R)</th><th>Fiabilité</th></tr>" + lines + "</table>"
            f'<p class="muted">{escape(patterns.get("note", ""))}</p></div>'
        )

    # Règles proposées
    rules = bundle.get("rules") or []
    if rules:
        items = "".join(
            f'<div class="rule"><div class="caveat">{escape(rule.get("caveat", ""))}</div>'
            f"<div>{escape(rule['text'])}</div></div>" for rule in rules
        )
        sections.append(f'<div class="card"><h2>Règles proposées</h2>{items}</div>')

    # Qualité des données — volontairement en dernier et toujours présente.
    quality = bundle.get("data_quality") or {}
    if quality.get("coverage"):
        lines = "".join(
            f"<tr><td>{escape(row['field'])}</td><td>{row['filled']}</td>"
            f"<td>{row['missing']}</td><td>{row['coverage']:.1f}%</td>"
            f'<td><div class="bar"><span style="width:{row["coverage"]:.0f}%"></span></div></td></tr>'
            for row in quality["coverage"]
        )
        anomalies = "".join(
            f"<li>{escape(a['label'])} : <strong>{a['count']}</strong> — "
            f'<span class="muted">{escape(a.get("hint", ""))}</span></li>'
            for a in quality.get("anomalies", [])
        )
        sections.append(
            '<div class="card"><h2>Qualité des données</h2>'
            "<table><tr><th>Champ</th><th>Renseignés</th><th>Manquants</th>"
            "<th>Couverture</th><th></th></tr>" + lines + "</table>"
            + (f"<h2 style='margin-top:18px'>Anomalies</h2><ul>{anomalies}</ul>" if anomalies else "")
            + '<p class="muted">La fiabilité des analyses ci-dessus dépend directement '
              "de ces taux de remplissage.</p></div>"
        )

    generated = datetime.now().strftime("%d/%m/%Y à %H:%M")
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rapport Analyzer — {escape(account_label or 'Journal')}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>Rapport Analyzer</h1>
<p class="sub">{escape(account_label or 'Journal')} · {escape(period)} · généré le {generated}</p>
<div class="warn">Ce document contient vos données de trading. Il est autonome
(aucune connexion réseau à l'ouverture) — conservez-le en conséquence.</div>
{''.join(sections)}
<footer>MLxPhantom Journal — Analyzer · Les analyses statistiques sont des observations
à vérifier, jamais des recommandations d'investissement.</footer>
</div></body></html>"""
