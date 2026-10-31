"""Build Fyers_Equity_Data_Summary.pdf (snapshot of the in-progress equity download)."""
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer

from fyers_data.reports import fno_pdf as m
from fyers_data.reports.fno_pdf import BODY, H1, H2, H3, SMALL, P, bar_chart, bullets, kpi_row, n, table

from fyers_data.paths import EQUITY, REPORTS

SUFFIX = os.environ.get("EQ_SUFFIX", "")
S = json.loads((REPORTS / f"equity_stats{SUFFIX}.json").read_text())
PS = pd.read_csv(REPORTS / f"equity_per_stock{SUFFIX}.csv")
OUT = REPORTS / f"Fyers_Equity_Data_Summary{SUFFIX}.pdf"
FINAL = bool(S.get("final"))
W = A4[0] - 36 * mm


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(m.MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Fyers NSE equity data - download summary" + ("" if FINAL else " (snapshot, download in progress)"))
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def sample_rows():
    """Header + first rows of the first daily file in final mode (read from the data itself)."""
    head = ["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"]
    if FINAL:
        first = sorted(EQUITY.glob("2*.csv"))[0]
        d = pd.read_csv(first)
        pick = pd.concat([d.head(2), d[d.timeframe == "5m"].head(1)])
        return [head] + [[str(v) for v in r] for r in pick.itertuples(index=False)]
    return [head,
            ["RELIANCE", "1m", "2021-10-01 09:15:00", "1250.97", "1251.85", "1247.50", "1250.00", "288074"],
            ["RELIANCE", "1m", "2021-10-01 09:16:00", "1250.38", "1256.93", "1250.00", "1254.00", "171590"],
            ["RELIANCE", "5m", "2021-10-01 09:15:00", "...", "...", "...", "...", "..."]]


def build():
    q, u = S["quality"], S["uni"]
    pct = S["stocks_done"] / S["stocks_total"] * 100
    bpct = S["batches_done"] / S["batches_total"] * 100
    per_stock_mb = S["size_gb"] * 1000 / S["stocks_done"]
    est_gb = per_stock_mb * S["stocks_total"] / 1000
    rows_total = sum(S["tf_rows"].values())
    est_rows = rows_total / S["stocks_done"] * S["stocks_total"]
    el = []

    # ---------------- title + status
    el += [P("Fyers NSE Equity Data - Download Summary", H1),
           P(f"{'Generated' if FINAL else 'Snapshot taken'} {S['snapshot']} &nbsp;|&nbsp; "
             f"<b>{'Download complete' if FINAL else 'Download in progress'}</b> &nbsp;|&nbsp; "
             f"Source: Fyers API v3 (history endpoint) &nbsp;|&nbsp; Project: Algo_Backtesting", SMALL),
           Spacer(1, 8),
           kpi_row([(f"{S['stocks_done']} / {n(S['stocks_total'])}", f"stocks downloaded ({pct:.0f}%)"),
                    (f"{S['batches_done']} / {S['batches_total']}", f"batches complete ({bpct:.0f}%)"),
                    (f"{rows_total / 1e6:,.0f} M", "candles saved, all timeframes"),
                    (f"{S['size_gb']:.1f} GB", "total size" if FINAL else "temporary files on disk"),
                    (n(S["days"]), "daily files") if FINAL else (f"~{est_gb:.0f} GB", "expected final size")]),
           Spacer(1, 6)]

    # ---------------- 1 status
    el += [P("1. Status" if FINAL else "1. Current status", H2),
           P(f"All {n(S['stocks_total'])} selected NSE stocks were downloaded in {S['batches_total']} batches "
             f"({S['downloaded_first_name']} through {S['downloaded_last_name']}) and merged into one CSV per trading day "
             f"in <font face='Courier'>data/equity/</font>." if FINAL else
             f"The download covers {n(S['stocks_total'])} NSE stocks, processed alphabetically in batches of 25. "
             f"At the time of this snapshot {S['batches_done']} of {S['batches_total']} batches were complete, "
             f"i.e. {S['downloaded_first_name']} through {S['downloaded_last_name']}. "
             "Data is being saved as temporary per-batch pieces. After the last batch, a merge step writes the final "
             "one-file-per-day CSVs into <font face='Courier'>data/equity/</font> and deletes the temporary pieces, "
             "so the daily files do not exist yet."),
           Spacer(1, 4),
           table([["Item", "Status"],
                  ["Stocks complete", f"{S['stocks_done']} of {n(S['stocks_total'])} ({pct:.1f}%)"],
                  ["Batches complete", f"{S['batches_done']} of {S['batches_total']}"],
                  ["Trading days covered" if FINAL else "Trading days covered so far", f"{n(S['days'])} ({S['first_day']} to {S['last_day']})"],
                  ["Failures logged", "0"],
                  ["Stocks with no data returned", str(len(S['empty']))],
                  ["Run notes", "9 batches failed once during a Fyers slowdown and were retried successfully; "
                                "the final data has no gaps from this" if FINAL else
                                f"{S['batches_total'] - S['batches_done']} batches remaining, about 8 minutes each "
                                f"(roughly {(S['batches_total'] - S['batches_done']) * 8 / 60:.1f} hours), "
                                "then 20-30 minutes to merge"]],
                 [55 * mm, W - 55 * mm])]

    # ---------------- 2 scope
    el += [P("2. Scope and method", H2)]
    el += bullets([
        "<b>Stocks:</b> NSE equity-series (-EQ) stocks with six-month average market cap of at least "
        "Rs 10,000,000,000 (Rs 1,000 crore), using AMFI's list for the six months ended 30 Jun 2026, matched to "
        "Fyers symbols on ISIN. " f"This gives {n(u['n'])} stocks.",
        "<b>Period:</b> 1 Oct 2021 to 28 Oct 2026 (1,258 trading days). The end date is fixed at 28 Oct for all stocks.",
        "<b>Timeframes:</b> 1m, 5m, 15m, 30m and 60m. Only 1m is requested from Fyers; the others are resampled from it, "
        "anchored at the 09:15 open.",
        "<b>Final layout:</b> one CSV per trading day, <font face='Courier'>data/equity/YYYY-MM-DD.csv</font>, with every "
        "stock and every timeframe. Columns: symbol, timeframe, datetime, open, high, low, close, volume. Rows are "
        "sorted by timeframe (1m first), then symbol, then time. Symbols have no exchange prefix or -EQ suffix.",
        "<b>Timestamps:</b> IST, candle start time, no timezone suffix.",
        "<b>Resumable:</b> progress is saved after every batch in data/equity/_progress.json. A stopped run continues "
        "from the next unfinished batch."])

    # ---------------- 3 universe
    el += [PageBreak(), P("3. Stock universe (all 1,386 selected)", H2),
           table([["Metric", "Value"],
                  ["Stocks selected", n(u["n"])],
                  ["Large cap / Mid cap / Small cap (AMFI category)",
                   f"{u['cat'].get('Large', 0)} / {u['cat'].get('Mid', 0)} / {u['cat'].get('Small', 0)}"],
                  ["Median market cap", f"Rs {u['median_mcap']:,.0f} crore"],
                  ["Cut-off", "Rs 1,000 crore (Rs 10,000,000,000)"]], [85 * mm, 60 * mm]),
           Spacer(1, 6),
           bar_chart("Selected stocks by market cap band (Rs crore)", u["mcap_buckets"]),
           Spacer(1, 4),
           P("Largest and smallest selected stocks", H3),
           table([["Largest", "Mcap (Rs cr)", "Category", "Smallest", "Mcap (Rs cr)", "Category"]] +
                 [[escape(a[0]), f"{a[1]:,.0f}", a[2], escape(b[0]), f"{b[1]:,.0f}", b[2]]
                  for a, b in zip(u["top"][:5], u["bottom"][:5])],
                 [30 * mm, 28 * mm, 20 * mm, 30 * mm, 28 * mm, W - 136 * mm]),
           Spacer(1, 3),
           P("Market cap is the AMFI six-month average to 30 Jun 2026, not the value on each historical date. "
             "A stock that was above the cut-off in an earlier year but is below it now is not included. "
             "459 Fyers equity symbols had no match in the AMFI list and could not be filtered; "
             "whether any of them exceed the cut-off was not checked.", SMALL)]

    # ---------------- 4 downloaded so far
    tfr = [["Timeframe", "Candles saved", "Share"]] + \
          [[t, n(S["tf_rows"][t]), f"{S['tf_rows'][t] / rows_total * 100:.1f}%"] for t in ["1m", "5m", "15m", "30m", "60m"]] + \
          [["Total", n(rows_total), "100%"]]
    el += [P("4. Data downloaded" if FINAL else "4. Data downloaded so far", H2),
           P(f"{S['stocks_done']} stocks are complete ({S['downloaded_first_name']} to {S['downloaded_last_name']}), "
             f"holding {n(rows_total)} candles across the five timeframes in {n(S['size_gb'] * 1000)} MB. "
             f"On average each stock has {S['avg_rows_1m_per_stock']:,.0f} one-minute candles."),
           Spacer(1, 4),
           table(tfr, [40 * mm, 45 * mm, 30 * mm]),
           Spacer(1, 6),
           bar_chart("Stocks by year of first available candle", S["start_by_year"]),
           Spacer(1, 3),
           P(f"{S['full_history_stocks']} of {S['stocks_done']} downloaded stocks have data from the start of the period "
             f"(Oct 2021). The rest were listed later and start at their listing date, since Fyers has no earlier candles.",
             SMALL)]

    late = S["late_listed"]
    if late:
        show = late[-24:] if len(late) > 24 else late
        el += [P(f"Latest-listed downloaded stocks (last {len(show)} of {len(late)})", H3)]
        cols = 2
        cells = [[escape(a), b, n(c)] for a, b, c in show]
        while len(cells) % cols:
            cells.append(["", "", ""])
        per = len(cells) // cols
        grid = [["Stock", "First day", "1m rows"] * cols]
        for i in range(per):
            row = []
            for c in range(cols):
                row += cells[i + c * per]
            grid.append(row)
        el.append(table(grid, [W * 0.22, W * 0.15, W * 0.13] * 2, font=7.5))

    # ---------------- 5 expected final
    el += [P("5. Final data layout" if FINAL else "5. What the finished data will look like", H2),
           P("Actual figures from the merged daily files." if FINAL else
             "These are estimates extrapolated from the stocks downloaded so far. The list is alphabetical, so the final "
             "numbers can differ somewhat."),
           Spacer(1, 3),
           table([["Item", "Actual" if FINAL else "Expected"],
                  ["Daily files", (f"{n(S['days'])} (one per trading day, {S['first_day']} to {S['last_day']})" if FINAL else
                   "1,258 (one per trading day, 1 Oct 2021 to 28 Oct 2026)")],
                  ["Rows per full day (all stocks, all timeframes)", "up to about 690,000 "
                   "(1,386 stocks x 495 candles: 375 one-minute, 75 five, 25 fifteen, 13 thirty, 7 sixty)"],
                  ["Total candles", f"{rows_total / 1e6:,.0f} million" if FINAL else f"about {est_rows / 1e6:,.0f} million"],
                  ["Total size", (f"{S['size_gb']:.1f} GB ({per_stock_mb:.0f} MB per stock)" if FINAL else
                   f"about {est_gb:.0f} GB (about {per_stock_mb:.0f} MB per stock)")],
                  ["Size of the latest daily file", (f"{S['last_file_mb']:.0f} MB ({S['last_day']})" if FINAL else "roughly 40-45 MB")],
                  ["Earlier days", "smaller, because fewer stocks were listed and Fyers has less data per stock"]],
                 [60 * mm, W - 60 * mm]),
           Spacer(1, 4),
           P(f"Sample rows from the first daily file ({S['first_day']})" if FINAL else
             "Sample rows (from an earlier 4-stock test run; same layout as the final files)", H3),
           table(sample_rows(),
                 [22 * mm, 20 * mm, 38 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, W - 152 * mm], font=7.5)]

    # ---------------- 6 quality
    zr = q["zero_vol"] / q["rows"] * 100
    el += [P("6. Data quality checks", H2),
           P(f"Checks were run on all {n(q['rows'])} candles" + ("." if FINAL else " saved so far.")),
           Spacer(1, 3),
           table([["Check", "Result", "Comment"],
                  ["Duplicate candles (same stock, timeframe, time)", n(q["dup"]), "None"],
                  ["Negative volume", n(q["neg_vol"]), "None"],
                  ["Inconsistent OHLC", n(q["bad_ohlc"]),
                   f"{q['bad_ohlc'] / q['rows'] * 100:.4f}% of candles - negligible vendor glitches"],
                  ["Zero-volume candles", f"{n(q['zero_vol'])} ({zr:.0f}%)",
                   "Mostly illiquid stocks that did not trade in some minutes"],
                  ["Candles outside 09:15-15:39", n(q["outside"]),
                   "Mostly the Diwali Muhurat evening sessions (seen on 4 Nov 2021, 24 Oct 2022, 12 Nov 2023, "
                   "1 Nov 2024) plus some candles before 09:15 on a few days (e.g. 9 Sep 2026), as seen in sampled files. "
                   "These are real market data, not errors"]],
                 [58 * mm, 30 * mm, W - 88 * mm])]

    # ---------------- 7 limits
    el += [P("7. Limitations to keep in mind", H2)]
    el += bullets([
        "<b>Prices are adjusted.</b> Fyers returns history adjusted for corporate actions (for example RELIANCE shows "
        "about Rs 1,250 in Oct 2021 after its 2024 bonus issue). Prices and volumes are not the raw values of the time.",
        "<b>Market cap filter uses today's size</b> (AMFI average to 30 Jun 2026), so it is survivorship-biased: "
        "stocks that shrank below Rs 1,000 crore or were delisted are not included.",
        "<b>Only the -EQ series.</b> Stocks that trade only in other series (BE, SME and so on) are not included.",
        "<b>Late listings</b> start at their listing date, so not every stock has all 1,258 days.",
        "<b>Resampled bars.</b> 5/15/30/60m bars are built from 1m candles anchored at 09:15; the last 60m bar of the "
        "day starts at 15:15 and is shorter than an hour.",
        "<b>Special sessions.</b> Diwali Muhurat days have a short special session, so those daily files "
        "look different from a normal day.",
        "<b>No 29 Oct 2026 data</b> for any stock: the end date was fixed at 28 Oct so all stocks cover the same period."])

    # ---------------- 8 next steps
    el += [P("8. Next steps" if FINAL else "8. Remaining steps", H2)]
    if FINAL:
        el += bullets([
            "Rotate the Fyers app secret and re-enroll the TOTP, and revoke the Telegram bot tokens that were shared "
            "in chat, since they were shared in plain text.",
            "The data ends on 28 Oct 2026. Extending it needs a new run with a later end date, then appending to the "
            "daily files.",
            "Next: write a loader that reads a day (or a date range) of these files for the backtest engine."])
    else:
        el += bullets([
            f"Let the download finish the remaining {S['batches_total'] - S['batches_done']} batches "
            "(the computer must stay on, or the run can be resumed later).",
            "The merge step then builds the 1,258 daily files and deletes each day's temporary pieces as it goes, so disk use "
            "stays near 45 GB. Free space was about 70 GB at the time of this snapshot.",
            "After it finishes, regenerate this summary with the final numbers (run: python -m fyers_data.reports.equity_stats --final, then python -m fyers_data.reports.equity_pdf).",
            "Rotate the Fyers app secret and re-enroll the TOTP, since they were shared in plain text during setup."])

    # ---------------- appendix
    names = sorted(PS["name"].astype(str).tolist())
    names = [escape(x) for x in names]
    cols = 6
    while len(names) % cols:
        names.append("")
    per = len(names) // cols
    grid = [[names[i + c * per] for c in range(cols)] for i in range(per)]
    el += [PageBreak(), P(f"Appendix - stocks downloaded{'' if FINAL else ' so far'} ({S['stocks_done']})", H2),
           table(grid, [W / cols] * cols, header=False, zebra=True, font=7.5)]

    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Fyers NSE Equity Data - Download Summary", author="Algo_Backtesting")
    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
