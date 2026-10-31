"""Build Fyers_FnO_Data_Summary.pdf from summary_stats.json."""
import json
from datetime import date
from xml.sax.saxutils import escape
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from fyers_data.paths import REPORTS

S = json.loads((REPORTS / "summary_stats.json").read_text())
OUT = REPORTS / "Fyers_FnO_Data_Summary.pdf"

INK, MUTED, GRID = colors.HexColor("#1f2933"), colors.HexColor("#616e7c"), colors.HexColor("#d9dee3")
ACCENT, HEAD_BG, ZEBRA = colors.HexColor("#2a6fb0"), colors.HexColor("#eef2f6"), colors.HexColor("#f8fafb")
WARN_BG = colors.HexColor("#fff6e5")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=20, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=5)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=10.5, textColor=INK, spaceBefore=8, spaceAfter=3)
BODY = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9, leading=12.5, textColor=INK)
SMALL = ParagraphStyle("S", parent=BODY, fontSize=7.8, leading=10, textColor=MUTED)
CELL = ParagraphStyle("C", parent=BODY, fontSize=8, leading=10)
KPI = ParagraphStyle("K", parent=BODY, fontSize=8, leading=21)
BUL = ParagraphStyle("BU", parent=BODY, leftIndent=11, bulletIndent=2, spaceAfter=1.5)


def P(t, st=BODY):
    return Paragraph(t, st)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def n(x):
    return f"{int(x):,}"


def table(data, widths, align_right_from=1, header=True, zebra=True, font=8):
    rows = [[c if not isinstance(c, str) else Paragraph(c, CELL) for c in r] for r in data]
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONTSIZE", (0, 0), (-1, -1), font), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), HEAD_BG), ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED)]
    if zebra:
        st += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA])]
    t.setStyle(TableStyle(st))
    return t


def kpi_row(items):
    cells = [[Paragraph(f"<font size=16 color='#2a6fb0'><b>{v}</b></font><br/><br/>"
                        f"<font size=7.5 color='#616e7c'>{l}</font>", KPI) for v, l in items]]
    t = Table(cells, colWidths=[(A4[0] - 36 * mm) / len(items)] * len(items))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.5, GRID),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.white)]))
    return t


def bar_chart(title, data: dict, width=170 * mm, height=52 * mm):
    d = Drawing(width, height)
    d.add(String(0, height - 9, title, fontSize=8.5, fillColor=INK))
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 34, 22, width - 50, height - 42
    keys = list(data.keys())
    bc.data = [list(data.values())]
    bc.categoryAxis.categoryNames = keys
    bc.categoryAxis.labels.fontSize = 7.5
    bc.categoryAxis.labels.fillColor = MUTED
    bc.categoryAxis.strokeColor = GRID
    bc.valueAxis.labels.fontSize = 7.5
    bc.valueAxis.labels.fillColor = MUTED
    bc.valueAxis.valueMin = 0
    bc.valueAxis.strokeColor = GRID
    bc.valueAxis.visibleGrid = True
    bc.valueAxis.gridStrokeColor = GRID
    bc.valueAxis.gridStrokeWidth = 0.4
    bc.bars[0].fillColor = ACCENT
    bc.bars[0].strokeColor = None
    bc.barWidth = 10
    bc.groupSpacing = 8
    bc.barLabelFormat = "%d"
    bc.barLabels.fontSize = 7.5
    bc.barLabels.fillColor = INK
    bc.barLabels.nudge = 6
    mx = max(data.values()) if data else 1
    bc.valueAxis.valueMax = mx * 1.15
    d.add(bc)
    return d


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Fyers F&O historical data - download summary")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build():
    tf, q, fut, opt = S["timeframes"], S["quality"], S["fut"], S["opt"]
    W = A4[0] - 36 * mm
    el = []

    # ---------------- title
    el += [P("Fyers F&amp;O Historical Data - Download Summary", H1),
           P(f"Generated {date.today():%d %b %Y} &nbsp;|&nbsp; Project: Algo_Backtesting &nbsp;|&nbsp; "
             f"Source: Fyers API v3 (history endpoint) &nbsp;|&nbsp; Data as of 28 Oct 2026", SMALL),
           Spacer(1, 8)]
    total_files = sum(tf[t][k]["nonempty"] for t in tf for k in tf[t])
    total_mb = sum(tf[t][k]["mb"] for t in tf for k in tf[t])
    el += [kpi_row([(n(fut["n"]), "futures series"),
                    (n(opt["with_data"]), "option contracts with data"),
                    (n(S["n_under"]), "underlyings"),
                    (n(q["rows"]), "1-minute candles"),
                    (f"{total_mb / 1000:.1f} GB", "on disk, 5 timeframes")]), Spacer(1, 6)]

    # ---------------- 1 overview
    el += [P("1. Overview", H2),
           P("This dataset holds historical OHLCV candles for Indian equity derivatives (NSE and BSE), "
             "downloaded from Fyers for backtesting. It covers futures and options on all NSE F&amp;O stocks "
             "and five indices. Only 1-minute candles were requested from Fyers; the 5, 15, 30 and 60-minute "
             "files were produced by resampling those candles, anchored at the 09:15 session open."),
           Spacer(1, 4),
           table([["Item", "Detail"],
                  ["Underlyings", f"{S['n_stock_under']} NSE F&amp;O stocks + 5 indices "
                                  f"(NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY on NSE; SENSEX on BSE)"],
                  ["Futures", "One continuous series per underlying, keyed on the Nov-2026 contract "
                              "(Fyers stitches earlier expiries into it)"],
                  ["Options", "CE and PE, ATM ±5 strikes (11 strikes per side), every expiry listed up to "
                              "30 Nov 2026 (monthly for stocks; weekly + monthly for indices)"],
                  ["Timeframes", "1m, 5m, 15m, 30m, 60m"],
                  ["Requested period", "1 Jan 2026 to 28 Oct 2026 (download date)"],
                  ["Timestamps", "IST, naive (no timezone suffix), candle start time"],
                  ["Format", "CSV: datetime, open, high, low, close, volume"]],
                 [38 * mm, W - 38 * mm])]

    # ---------------- 2 folder structure
    el += [P("2. Files and naming", H2),
           P("Path pattern: <font face='Courier'>data/fno/&lt;timeframe&gt;/&lt;futures|options&gt;/&lt;SYMBOL&gt;.csv</font> "
             "where timeframe is 1m, 5m, 15m, 30m or 60m. The symbol is the Fyers ticker without the exchange prefix."),
           Spacer(1, 3)]
    el += bullets([
        "<font face='Courier'>RELIANCE26NOVFUT</font> - Reliance future, Nov 2026 (continuous series).",
        "<font face='Courier'>RELIANCE26NOV1300CE</font> - monthly option: underlying, year (26), month (NOV), strike (1300), CE/PE.",
        "<font face='Courier'>NIFTY26N0322450CE</font> - weekly option: year (26), month code (N = Nov), day (03), strike (22450), CE.",
        "<font face='Courier'>selected_contracts.csv</font> lists every chosen contract with spot, ATM and expiry. "
        "<font face='Courier'>summary_per_contract.csv</font> lists rows, first and last date for each contract."])

    # ---------------- 3 coverage by timeframe
    rows = [["Timeframe", "Fut. files", "Fut. rows", "Opt. files (with data)", "Opt. rows", "Size (MB)"]]
    for t in ["1m", "5m", "15m", "30m", "60m"]:
        f, o = tf[t]["futures"], tf[t]["options"]
        rows.append([t, n(f["nonempty"]), n(f["rows"]), f"{n(o['files'])} ({n(o['nonempty'])})",
                     n(o["rows"]), f"{f['mb'] + o['mb']:,.0f}"])
    el += [P("3. Coverage by timeframe", H2),
           table(rows, [22 * mm, 24 * mm, 28 * mm, 40 * mm, 30 * mm, W - 144 * mm]),
           Spacer(1, 3),
           P(f"1m options: {n(opt['selected'])} files written, {n(opt['with_data'])} contain data; the other "
             f"{n(opt['empty'])} are empty placeholders (Fyers returned no candles) and have no resampled files. "
             "Higher timeframes therefore hold " + n(tf['5m']['options']['files']) + " option files.", SMALL)]

    # ---------------- 4 futures
    el += [PageBreak(), P("4. Futures detail", H2),
           P(f"All {fut['n']} futures series have data. Each is a continuous series built by Fyers on the Nov-2026 "
             f"symbol, running from the first available date to {fut['end_max']}."),
           Spacer(1, 3),
           table([["Metric", "Value"],
                  ["Series", n(fut["n"])],
                  ["Series starting " + fut["start_min"], n(fut["start_jan"])],
                  ["Series starting later", n(fut["n"] - fut["start_jan"])],
                  ["Total 1m rows", n(fut["rows_total"])],
                  ["Trading days per series (median / max)", f"{fut['days_median']} / {fut['days_max']}"],
                  ["Last candle date", fut["end_max"]]], [70 * mm, 40 * mm]),
           P("Index futures", H3),
           table([["Series", "Start", "End", "1m rows", "Trading days"]] +
                 [[r[1], r[2], r[3], n(r[4]), str(r[5])] for r in fut["idx"]],
                 [50 * mm, 30 * mm, 30 * mm, 28 * mm, W - 138 * mm])]
    late = fut["late"]
    if late:
        el += [P(f"Stock futures starting after {fut['start_min']} ({len(late)})", H3),
               P("Fyers has no earlier candles for these contracts (first available after 1 Jan 2026), "
                 "so the series begins at that date.", SMALL), Spacer(1, 2)]
        cols = 2
        cells = [[escape(u), s, n(r)] for u, s, r in late]
        while len(cells) % cols:
            cells.append(["", "", ""])
        per = len(cells) // cols
        grid = [["Stock", "Start", "Rows"] * cols]
        for i in range(per):
            row = []
            for c in range(cols):
                row += cells[i + c * per]
            grid.append(row)
        el.append(table(grid, [W * 0.22, W * 0.14, W * 0.14] * 2, font=7.5))

    # ---------------- 5 options
    el += [PageBreak(), P("5. Options detail", H2),
           P(f"{n(opt['selected'])} option contracts were selected (ATM ±5 strikes, CE+PE, all expiries to "
             f"30 Nov 2026). {n(opt['with_data'])} returned data "
             f"({n(opt['ce'])} CE, {n(opt['pe'])} PE) with {n(opt['rows_total'])} 1m candles in total. "
             f"{n(opt['stock_n'])} contracts are stock options and {n(opt['index_n'])} are index options."),
           Spacer(1, 4),
           table([["Metric", "Value"],
                  ["Contracts selected / with data / empty", f"{n(opt['selected'])} / {n(opt['with_data'])} / {n(opt['empty'])}"],
                  ["Earliest first candle", opt["start_min"]],
                  ["Latest first candle", opt["start_max"]],
                  ["Last candle date (earliest / latest)", f"{opt['end_min']} / {opt['end_max']}"],
                  ["Trading days per contract (median / max)", f"{opt['days_median']} / {opt['days_max']}"]],
                 [75 * mm, 60 * mm]),
           Spacer(1, 6),
           bar_chart("Option contracts by month of first available candle", opt["start_by_month"]),
           Spacer(1, 3),
           P("Option history starts at each contract's listing date. Nothing is available before 26 Aug 2026 - "
             "Fyers does not keep expired contracts, and the contracts listed today were only created recently.", SMALL)]

    ex = opt["expiry_counts"]
    ex_rows = [["Expiry", "Contracts selected"]] + [[k, n(v)] for k, v in ex.items()]
    el += [P("Expiries included", H3), table(ex_rows, [40 * mm, 40 * mm]),
           P("Contracts per expiry: stock options expire monthly; index options also have weekly expiries.", SMALL)]

    ir = [["Index", "Selected", "With data", "Expiries", "Expiry range", "Strike range", "Spot", "First data"]]
    for r in S["idx_opts"]:
        ir.append([r[0], n(r[1]), n(r[2]), str(r[3]), r[4], r[5], f"{r[6]:,.0f}", r[7]])
    el += [KeepTogether([P("Index options", H3),
                         table(ir, [22 * mm, 16 * mm, 17 * mm, 15 * mm, 36 * mm, 24 * mm, 15 * mm, W - 145 * mm],
                               font=7.5)])]

    el += [P("Empty option contracts (no candles returned)", H3),
           P(f"{n(opt['empty'])} selected contracts returned no data. Largest counts by underlying: " +
             ", ".join(f"{k} ({v})" for k, v in opt["empty_by_under_top"].items()) +
             ". The cause was not investigated (likely no trades yet, or a very recent listing).", BODY),
           P("Underlyings with the most option candles", H3),
           table([["Underlying", "1m rows"]] + [[k, n(v)] for k, v in opt["top_rows"].items()],
                 [50 * mm, 40 * mm])]

    # ---------------- 6 quality
    zr = q["zero_vol_rows"] / q["rows"] * 100
    el += [P("6. Data quality checks", H2),
           P("Checks were run on all 1-minute files after the download."),
           Spacer(1, 3),
           table([["Check", "Result", "Comment"],
                  ["Duplicate timestamps", n(q["dup_ts"]), "None - each file has unique, sorted timestamps"],
                  ["Negative volume", n(q["neg_vol"]), "None"],
                  ["Inconsistent OHLC (high &lt; low, or close/open outside high-low)", n(q["bad_ohlc"]),
                   f"{q['bad_ohlc'] / q['rows'] * 100:.4f}% of rows - negligible vendor glitches; filter if needed"],
                  ["Zero-volume candles", f"{n(q['zero_vol_rows'])} ({zr:.0f}%)",
                   "Mostly illiquid option strikes: minutes with no trade are still returned"],
                  ["Candles outside 09:15-15:29", n(q["outside_session"]),
                   "Some files carry 15:30-15:39 candles from about 31 Jul 2026 (seen in RELIANCE futures; looks like a closing session). "
                   "Drop them if you want the regular session only"]],
                 [62 * mm, 32 * mm, W - 94 * mm])]

    # ---------------- 7 limitations
    el += [P("7. Limitations to keep in mind", H2)]
    el += bullets([
        "<b>No history for expired options.</b> Fyers only serves contracts that are listed now. A January-October "
        "options backtest cannot be built from this data. Option data starts 26 Aug 2026 at the earliest "
        "and mostly in October.",
        "<b>Strikes are chosen from today's spot.</b> The ATM ±5 window was computed from the 28 Oct 2026 price. "
        "If a stock traded far from that level earlier in its listing window, those historical ATM strikes are not in the files.",
        "<b>Futures are continuous, not per-contract.</b> Fyers stitches expired months into the Nov series. "
        "Whether prices are back-adjusted across expiries was not checked, so price gaps may exist at expiry boundaries.",
        "<b>Resampled bars.</b> 5/15/30/60m files come from 1m candles anchored at 09:15, so the 60m bars run "
        "09:15-10:15, ... and the last 60m bar starts at 15:15 and is shorter than an hour.",
        "<b>Empty contracts.</b> The 325 empty contracts have a header-only 1m file and no higher-timeframe files.",
        "<b>Rate limits.</b> The downloader keeps to about 150 history calls per minute to stay under the Fyers limit, "
        "and it is resumable."])

    # ---------------- 8 next steps
    el += [P("8. Suggested next steps", H2)]
    el += bullets([
        "Rotate the Fyers app secret and re-enroll the TOTP - they were shared in plain text during setup.",
        "Run the downloader daily (scheduled) to accumulate your own option history going forward; "
        "this is the only way to get a full options backtest window from Fyers.",
        "For past options data, use another source (for example a paid intraday vendor, or NSE bhavcopy for daily data).",
        "Add a loader that merges futures + option files per underlying for the backtest engine."])

    # ---------------- appendix
    names = [escape(x) for x in S["unders_stock"]]
    cols = 6
    while len(names) % cols:
        names.append("")
    per = len(names) // cols
    grid = [[names[i + c * per] for c in range(cols)] for i in range(per)]
    el += [PageBreak(), P(f"Appendix - NSE F&amp;O stock underlyings ({S['n_stock_under']})", H2),
           table(grid, [W / cols] * cols, header=False, zebra=True, font=7.5)]

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Fyers F&O Historical Data - Download Summary", author="Algo_Backtesting")
    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
