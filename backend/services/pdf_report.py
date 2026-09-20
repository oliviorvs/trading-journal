from typing import List, Optional, Tuple
from datetime import date
import os
import tempfile
from fpdf import FPDF

from models import Account, Trade
from services import stats
import state


_REPORT_NAVY = (30, 42, 58)
_REPORT_GOLD = (196, 160, 84)
_REPORT_RED = (192, 57, 43)
_REPORT_GREEN = (30, 132, 73)
_REPORT_GRAY = (110, 118, 130)
_REPORT_LIGHT_BG = (245, 246, 248)
_REPORT_BORDER = (222, 225, 230)

_REPORT_HEX_NAVY = "#1e2a3a"
_REPORT_HEX_RED = "#c0392b"
_REPORT_HEX_GREEN = "#1e8449"
_REPORT_HEX_GRAY = "#6e7682"
_REPORT_HEX_GRID = "#e9eaee"
_REPORT_HEX_AXIS = "#c9ccd1"


def _safe_pdf_text(text: Optional[str]) -> str:
    """Nettoie un texte libre avant écriture dans le PDF : les polices
    standard de fpdf2 ne couvrent que le Latin-1, donc tout caractère hors
    de ce jeu (emoji, alphabets non latins...) est remplacé plutôt que de
    faire planter la génération du PDF."""
    if not text:
        return ""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _fmt_money_pdf(n: Optional[float], sym: str) -> str:
    if n is None:
        return "N/A"
    sign = "+" if n > 0 else ("-" if n < 0 else "")
    return f"{sign}{sym}{abs(n):,.2f}"


def _report_color(n: Optional[float]):
    if n is None or n == 0:
        return _REPORT_NAVY
    return _REPORT_GREEN if n > 0 else _REPORT_RED


def _report_local_stats(trades: List[Trade], ref_capital: float, net_capital: bool = False, movements=None) -> dict:
    """Recalcule toutes les statistiques du rapport à partir d'UN SEUL jeu
    de trades (voir correction #12 ci-dessus) — logique alignée sur
    `get_stats()` mais volontairement autonome pour ne dépendre d'aucun
    filtre externe."""
    total_trades = len(trades)
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit < 0]
    breakeven = [t for t in trades if t.profit == 0]
    total_pnl = sum(t.profit for t in trades)
    win_rate = (len(wins) / total_trades * 100) if total_trades else 0.0

    avg_win = (sum(t.profit for t in wins) / len(wins)) if wins else 0.0
    sum_losses_abs = abs(sum(t.profit for t in losses)) if losses else 0.0
    avg_loss = (sum_losses_abs / len(losses)) if losses else 0.0
    rr_ratio = round(avg_win / avg_loss, 2) if avg_loss else None

    sum_wins = sum(t.profit for t in wins)
    profit_factor = round(sum_wins / sum_losses_abs, 2) if sum_losses_abs else None

    win_frac = (len(wins) / total_trades) if total_trades else 0.0
    loss_frac = (len(losses) / total_trades) if total_trades else 0.0
    expectancy = round((win_frac * avg_win) - (loss_frac * avg_loss), 2) if total_trades else 0.0

    max_dd_pct, max_dd_abs = stats.max_drawdown(trades, ref_capital, net=net_capital, movements=movements)

    return {
        "total_trades": total_trades, "wins": len(wins), "losses": len(losses),
        "breakeven": len(breakeven), "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1), "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2), "rr_ratio": rr_ratio,
        "profit_factor": profit_factor, "expectancy": expectancy,
        "max_drawdown_pct": max_dd_pct, "max_drawdown_abs": max_dd_abs,
    }


def _report_symbol_breakdown(trades: List[Trade]) -> List[dict]:
    by_symbol: dict = {}
    for t in trades:
        d = by_symbol.setdefault(t.symbol, {"symbol": t.symbol, "total": 0.0, "wins": 0, "count": 0})
        d["total"] += t.profit
        d["count"] += 1
        if t.profit > 0:
            d["wins"] += 1
    result = [
        {**d, "total": round(d["total"], 2),
         "win_rate": round(d["wins"] / d["count"] * 100) if d["count"] else 0}
        for d in by_symbol.values()
    ]
    return sorted(result, key=lambda d: d["total"], reverse=True)


def _report_monthly_breakdown(trades: List[Trade]) -> List[Tuple[str, float]]:
    months: dict = {}
    for t in trades:
        key = t.open_time.strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + t.profit
    return [(k, round(v, 2)) for k, v in sorted(months.items())]


def _chart_equity_curve(trades: List[Trade], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    cumul = np.concatenate([[0.0], np.cumsum([t.profit for t in trades])])
    x = np.arange(len(cumul))

    fig, ax = plt.subplots(figsize=(7.2, 3.0), dpi=200)
    ax.plot(x, cumul, color=_REPORT_HEX_NAVY, linewidth=1.4)
    ax.fill_between(x, cumul, 0, where=(cumul <= 0), color=_REPORT_HEX_RED, alpha=0.12, interpolate=True)
    ax.fill_between(x, cumul, 0, where=(cumul >= 0), color=_REPORT_HEX_GREEN, alpha=0.12, interpolate=True)
    ax.axhline(0, color=_REPORT_HEX_AXIS, linewidth=0.8)
    ax.set_xlabel("N° de trade", fontsize=9, color=_REPORT_HEX_GRAY)
    ax.set_ylabel("P&L cumulé ($)", fontsize=9, color=_REPORT_HEX_GRAY)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(_REPORT_HEX_AXIS)
    ax.spines["bottom"].set_color(_REPORT_HEX_AXIS)
    ax.tick_params(colors=_REPORT_HEX_GRAY, labelsize=8)
    ax.grid(axis="y", color=_REPORT_HEX_GRID, linewidth=0.6)
    fig.tight_layout()
    fig.savefig(path, transparent=True)
    plt.close(fig)


def _chart_by_symbol(symbol_stats: List[dict], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    ordered = sorted(symbol_stats, key=lambda d: d["total"])  # pire en bas, meilleur en haut
    labels = [d["symbol"] for d in ordered]
    vals = [d["total"] for d in ordered]
    colors = [_REPORT_HEX_GREEN if v >= 0 else _REPORT_HEX_RED for v in vals]

    height = max(2.4, 0.32 * len(labels) + 0.6)
    fig, ax = plt.subplots(figsize=(7.2, height), dpi=200)
    y = np.arange(len(labels))
    ax.barh(y, vals, color=colors, height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8, color=_REPORT_HEX_NAVY)
    ax.axvline(0, color=_REPORT_HEX_AXIS, linewidth=0.8)
    ax.set_xlabel("P&L total ($)", fontsize=9, color=_REPORT_HEX_GRAY)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_REPORT_HEX_AXIS)
    ax.tick_params(colors=_REPORT_HEX_GRAY, labelsize=8)
    ax.grid(axis="x", color=_REPORT_HEX_GRID, linewidth=0.6)
    fig.tight_layout()
    fig.savefig(path, transparent=True)
    plt.close(fig)


def _chart_pie_and_monthly(wins: int, losses: int, monthly: List[Tuple[str, float]],
                            pie_path: str, monthly_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig1, ax1 = plt.subplots(figsize=(3.4, 3.2), dpi=200)
    if wins or losses:
        ax1.pie(
            [wins, losses], labels=["Gagnants", "Perdants"], autopct="%1.0f%%",
            colors=[_REPORT_HEX_GREEN, _REPORT_HEX_RED], startangle=90,
            textprops={"fontsize": 9, "color": _REPORT_HEX_NAVY},
            wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        )
    else:
        ax1.text(0.5, 0.5, "Aucun trade", ha="center", va="center", fontsize=10, color=_REPORT_HEX_GRAY)
        ax1.axis("off")
    fig1.tight_layout()
    fig1.savefig(pie_path, transparent=True)
    plt.close(fig1)

    fig2, ax2 = plt.subplots(figsize=(3.6, 3.2), dpi=200)
    if monthly:
        labels = [m for m, _ in monthly]
        vals = [v for _, v in monthly]
        colors = [_REPORT_HEX_GREEN if v >= 0 else _REPORT_HEX_RED for v in vals]
        ax2.bar(range(len(labels)), vals, color=colors, width=0.6)
        ax2.axhline(0, color=_REPORT_HEX_AXIS, linewidth=0.8)
        ax2.set_xticks(range(len(labels)))
        ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=6.5, color=_REPORT_HEX_GRAY)
        ax2.set_ylabel("P&L ($)", fontsize=8, color=_REPORT_HEX_GRAY)
        for spine in ("top", "right"):
            ax2.spines[spine].set_visible(False)
        ax2.tick_params(colors=_REPORT_HEX_GRAY, labelsize=7)
        ax2.grid(axis="y", color=_REPORT_HEX_GRID, linewidth=0.6)
    else:
        ax2.text(0.5, 0.5, "Aucune donnée", ha="center", va="center", fontsize=10, color=_REPORT_HEX_GRAY)
        ax2.axis("off")
    fig2.tight_layout()
    fig2.savefig(monthly_path, transparent=True)
    plt.close(fig2)


class _ReportPDF(FPDF):
    """FPDF avec pied de page automatique (numéro de page + compte),
    séparé du contenu par une fine ligne — reproduit sur chaque page."""

    def __init__(self, footer_label: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self._footer_label = footer_label
        self.set_margins(15, 14, 15)
        self.set_auto_page_break(auto=True, margin=20)

    def footer(self):
        content_w = self.w - self.l_margin - self.r_margin
        self.set_y(-15)
        self.set_draw_color(*_REPORT_BORDER)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_REPORT_GRAY)
        self.cell(content_w / 2, 5, _safe_pdf_text(self._footer_label), ln=0, align="L")
        self.cell(content_w / 2, 5, f"Page {self.page_no()}", ln=0, align="R")


def _draw_stat_box(pdf: "_ReportPDF", x: float, y: float, w: float, h: float,
                    label: str, value: str, color: tuple) -> None:
    pdf.set_fill_color(*_REPORT_LIGHT_BG)
    pdf.set_draw_color(*_REPORT_BORDER)
    pdf.set_line_width(0.2)
    pdf.rect(x, y, w, h, style="DF")
    pdf.set_xy(x + 3, y + 3)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*_REPORT_GRAY)
    pdf.cell(w - 6, 4, _safe_pdf_text(label), ln=0)
    pdf.set_xy(x + 3, y + 9.5)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*color)
    pdf.cell(w - 6, 8, _safe_pdf_text(value), ln=0)


def _write_paragraph(pdf: "_ReportPDF", segments: List[Tuple[str, bool]], line_height: float = 5.2) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(40, 40, 40)
    for text, bold in segments:
        pdf.set_font("Helvetica", "B" if bold else "", 10)
        pdf.write(line_height, _safe_pdf_text(text))
    pdf.ln(line_height + 3)


def _draw_section_heading(pdf: "_ReportPDF", title: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_REPORT_NAVY)
    pdf.cell(0, 8, _safe_pdf_text(title), ln=1)


def _draw_table(pdf: "_ReportPDF", headers: List[str],
                 rows: List[List[Tuple[str, Optional[float]]]],
                 col_widths: List[float], aligns: List[str]) -> None:
    """Dessine un tableau paginé. Chaque cellule d'une ligne est un tuple
    (texte_affiché, valeur_numérique_ou_None) — la valeur numérique, quand
    elle est fournie, sert uniquement à colorer le texte (rouge/vert/navy),
    ce qui évite de re-parser une chaîne déjà formatée (symbole monétaire,
    séparateur de milliers...) pour en déduire son signe."""
    x0 = pdf.l_margin
    row_h = 6.5
    header_h = 7.5

    def draw_header():
        pdf.set_x(x0)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_fill_color(*_REPORT_NAVY)
        pdf.set_text_color(255, 255, 255)
        for w, h, a in zip(col_widths, headers, aligns):
            pdf.cell(w, header_h, _safe_pdf_text(h), border=0, ln=0, align=a, fill=True)
        pdf.ln(header_h)

    draw_header()
    for i, row in enumerate(rows):
        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
            draw_header()
        pdf.set_x(x0)
        fill = (i % 2 == 1)
        pdf.set_fill_color(*_REPORT_LIGHT_BG)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_draw_color(*_REPORT_BORDER)
        for w, (text, num), a in zip(col_widths, row, aligns):
            pdf.set_text_color(*(_report_color(num) if num is not None else (30, 30, 30)))
            pdf.cell(w, row_h, _safe_pdf_text(text), border="B", ln=0, align=a, fill=fill)
        pdf.ln(row_h)


def build_journal_pdf(
    trades: List[Trade], account: Optional[Account],
    date_from: Optional[date], date_to: Optional[date], currency: str,
    ref_capital: float,
    net_capital: bool = False,
    movements=None,
) -> bytes:
    """Construit le rapport de performance PDF (cartes de stats, courbe
    d'équité, performance par instrument, répartition gagnants/perdants,
    P&L mensuel, tableau récapitulatif et détail des trades)."""
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")

    if date_from and date_to:
        period_label = f"du {date_from.strftime('%d/%m/%Y')} au {date_to.strftime('%d/%m/%Y')}"
    elif date_from:
        period_label = f"depuis le {date_from.strftime('%d/%m/%Y')}"
    elif date_to:
        period_label = f"jusqu'au {date_to.strftime('%d/%m/%Y')}"
    else:
        period_label = "historique complet"

    if account and account.mode == "manual":
        # Compte manuel : pas de serveur, et le login est un identifiant
        # interne négatif — on affiche le nom du compte.
        account_line = f"Compte manuel {account.label or account.name or ''}".strip()
        footer_label = f"Journal de trading - {account.label or account.name or 'compte manuel'}"
    elif account:
        account_line = f"Compte {account.server} #{account.login}"
        footer_label = f"Journal de trading - {account.server} #{account.login}"
    else:
        account_line = "Compte non connecté (mode démo)"
        footer_label = "Journal de trading"

    generated_str = state.utcnow().strftime("%d/%m/%Y")

    pdf = _ReportPDF(footer_label)
    pdf.add_page()
    content_w = pdf.w - pdf.l_margin - pdf.r_margin

    # ── En-tête ──────────────────────────────────────────────────────────
    pdf.set_xy(pdf.l_margin, 14)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*_REPORT_NAVY)
    pdf.cell(0, 9, _safe_pdf_text("Rapport de performance de trading"), ln=1)

    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(*_REPORT_GRAY)
    subtitle = f"{account_line}  |  Période analysée : {period_label}  |  Rapport généré le {generated_str}"
    pdf.cell(0, 6, _safe_pdf_text(subtitle), ln=1)

    pdf.ln(2)
    pdf.set_draw_color(*_REPORT_GOLD)
    pdf.set_line_width(0.6)
    y_line = pdf.get_y()
    pdf.line(pdf.l_margin, y_line, pdf.w - pdf.r_margin, y_line)
    pdf.ln(6)

    if not trades:
        pdf.set_font("Helvetica", "I", 11)
        pdf.set_text_color(*_REPORT_GRAY)
        pdf.cell(0, 8, "Aucun trade clôturé sur cette période.", ln=1)
        return bytes(pdf.output())

    s = _report_local_stats(trades, ref_capital, net_capital, movements)
    symbol_stats = _report_symbol_breakdown(trades)
    monthly = _report_monthly_breakdown(trades)

    # ── Cartes de statistiques ───────────────────────────────────────────
    gap = 4.0
    box_w = (content_w - 3 * gap) / 4
    box_h = 19.0
    row1 = [
        ("P&L TOTAL", _fmt_money_pdf(s["total_pnl"], sym), _report_color(s["total_pnl"])),
        ("TRADES", str(s["total_trades"]), _REPORT_NAVY),
        ("WIN RATE", f"{s['win_rate']:.1f}%", _REPORT_NAVY),
        ("PROFIT FACTOR", f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "N/A", _REPORT_NAVY),
    ]
    row2 = [
        ("ESPÉRANCE / TRADE", _fmt_money_pdf(s["expectancy"], sym), _report_color(s["expectancy"])),
        ("MAX DRAWDOWN", _fmt_money_pdf(s["max_drawdown_abs"], sym), _report_color(s["max_drawdown_abs"])),
        ("RATIO GAIN/PERTE", f"{s['rr_ratio']:.2f}" if s["rr_ratio"] is not None else "N/A", _REPORT_NAVY),
        ("GAGNANTS / PERDANTS", f"{s['wins']} / {s['losses']}", _REPORT_NAVY),
    ]
    y0 = pdf.get_y()
    for i, (label, value, color) in enumerate(row1):
        _draw_stat_box(pdf, pdf.l_margin + i * (box_w + gap), y0, box_w, box_h, label, value, color)
    y1 = y0 + box_h + gap
    for i, (label, value, color) in enumerate(row2):
        _draw_stat_box(pdf, pdf.l_margin + i * (box_w + gap), y1, box_w, box_h, label, value, color)
    pdf.set_y(y1 + box_h + 8)

    # ── Synthèse ─────────────────────────────────────────────────────────
    _draw_section_heading(pdf, "Synthèse")
    sens = "une perte nette" if s["total_pnl"] < 0 else ("un gain net" if s["total_pnl"] > 0 else "un résultat nul")
    if s["profit_factor"] is None:
        pf_txt = "n'est pas calculable (aucun trade perdant)"
    elif s["profit_factor"] < 1:
        pf_txt = f"{s['profit_factor']:.2f} (inférieur à 1) indique que les pertes cumulées dépassent les gains cumulés"
    else:
        pf_txt = f"{s['profit_factor']:.2f} (supérieur ou égal à 1) indique que les gains cumulés dépassent les pertes cumulées"
    rr_txt = f"{s['rr_ratio']:.2f}" if s["rr_ratio"] is not None else "N/A"
    conclusion = (
        "la taille des pertes n'est pas compensée par la fréquence des gains."
        if (s["rr_ratio"] is not None and s["rr_ratio"] < 1)
        else "les gains réalisés compensent bien la fréquence des pertes."
    )
    segments = [
        (f"Le compte affiche {sens} de ", False),
        (_fmt_money_pdf(s["total_pnl"], sym), True),
        (f" sur {s['total_trades']} trades, avec un taux de réussite de ", False),
        (f"{s['win_rate']:.1f}%", True),
        (f" ({s['wins']} gagnants, {s['losses']} perdants). ", False),
        (f"Le profit factor de {pf_txt}. ", False),
        ("Le drawdown maximal observé atteint ", False),
        (_fmt_money_pdf(abs(s["max_drawdown_abs"]), sym), True),
        (f", soit {abs(s['max_drawdown_pct']):.1f}% du solde du compte. ", False),
        ("Le ratio gain moyen / perte moyenne est de ", False),
        (rr_txt, True),
        (f" pour un gain moyen de {_fmt_money_pdf(s['avg_win'], sym)} contre une perte moyenne de "
         f"{_fmt_money_pdf(-s['avg_loss'], sym)}, ce qui signifie que {conclusion}", False),
    ]
    _write_paragraph(pdf, segments)

    # ── Courbe d'équité ──────────────────────────────────────────────────
    _draw_section_heading(pdf, "Courbe d'équité")

    with tempfile.TemporaryDirectory() as tmpdir:
        equity_path = os.path.join(tmpdir, "equity.png")
        _chart_equity_curve(trades, equity_path)
        pdf.image(equity_path, x=pdf.l_margin, w=content_w)

        # ── Performance par instrument ───────────────────────────────────
        pdf.add_page()
        _draw_section_heading(pdf, "Performance par instrument")
        by_symbol_path = os.path.join(tmpdir, "by_symbol.png")
        _chart_by_symbol(symbol_stats, by_symbol_path)
        pdf.image(by_symbol_path, x=pdf.l_margin, w=content_w)

        # ── Répartition + P&L mensuel + tableau récap ────────────────────
        pdf.add_page()
        _draw_section_heading(pdf, "Répartition des trades et performance mensuelle")
        pie_path = os.path.join(tmpdir, "pie.png")
        monthly_path = os.path.join(tmpdir, "monthly.png")
        _chart_pie_and_monthly(s["wins"], s["losses"], monthly, pie_path, monthly_path)
        y_charts = pdf.get_y()
        half_w = (content_w - 6) / 2
        pdf.image(pie_path, x=pdf.l_margin, y=y_charts, w=half_w)
        pdf.image(monthly_path, x=pdf.l_margin + half_w + 6, y=y_charts, w=half_w)
        pdf.set_y(y_charts + half_w * (3.2 / 3.4) + 8)

        pdf.ln(2)
        _draw_section_heading(pdf, "Tableau récapitulatif par instrument")
        headers = ["Symbole", "Trades", "Win rate", "P&L total", "P&L moyen"]
        col_widths = [content_w * w for w in (0.25, 0.15, 0.20, 0.20, 0.20)]
        aligns = ["L", "C", "C", "R", "R"]
        rows = []
        for d in symbol_stats:
            avg = round(d["total"] / d["count"], 2) if d["count"] else 0.0
            rows.append([
                (d["symbol"], None), (str(d["count"]), None), (f"{d['win_rate']}%", None),
                (_fmt_money_pdf(d["total"], sym), d["total"]),
                (_fmt_money_pdf(avg, sym), avg),
            ])
        _draw_table(pdf, headers, rows, col_widths, aligns)

        # ── Détail des trades ─────────────────────────────────────────────
        pdf.add_page()
        _draw_section_heading(pdf, f"Détail des trades ({len(trades)})")
        headers2 = ["Ticket", "Symbole", "Sens", "Lot", "Ouverture", "Clôture", "P&L"]
        col_widths2 = [content_w * w for w in (0.13, 0.16, 0.10, 0.10, 0.185, 0.185, 0.14)]
        aligns2 = ["L", "L", "C", "C", "C", "C", "R"]
        rows2 = []
        for t in trades:
            open_str = t.open_time.strftime("%d/%m/%y %H:%M")
            close_str = t.close_time.strftime("%d/%m/%y %H:%M") if t.close_time else "-"
            rows2.append([
                (str(t.ticket), None), (t.symbol, None), (t.direction.upper(), None),
                (f"{t.volume:.2f}", None), (open_str, None), (close_str, None),
                (_fmt_money_pdf(t.profit, sym), t.profit),
            ])
        _draw_table(pdf, headers2, rows2, col_widths2, aligns2)

        pdf.ln(4)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_REPORT_GRAY)
        pdf.multi_cell(
            content_w, 4.2,
            _safe_pdf_text(
                "Rapport généré automatiquement à partir de l'export du journal de trading. "
                "Les indicateurs (win rate, profit factor, espérance, drawdown) sont calculés "
                "sur l'ensemble des trades listés ci-dessus."
            ),
        )

        return bytes(pdf.output())
