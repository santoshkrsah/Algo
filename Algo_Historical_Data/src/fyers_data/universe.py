"""Build equity_universe.csv: NSE -EQ stocks with AMFI 6-month avg market cap >= threshold.

Source: AMFI 'Average Market Capitalization of listed companies' (six months ended 30 Jun 2026),
saved as data/reference/amfi/amfi_mcap_30Jun2026.pdf. Joined to the Fyers NSE_CM symbol master on ISIN.
"""
import re
from pathlib import Path

import pandas as pd
import pypdfium2 as pdfium

from fyers_data.paths import AMFI_DIR, UNIVERSE_CSV

PDF = AMFI_DIR / "amfi_mcap_30Jun2026.pdf"
MIN_MCAP_CR = 1000.0            # Rs 10,000,000,000 = Rs 1,000 crore
NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")
ROW = re.compile(r"^(\d+)\s+(.+?)\s*(IN[EF][A-Z0-9]{5}\d{4})\s*(.*?)\s*(Large|Mid|Small) Cap\s*$")


def parse_amfi() -> pd.DataFrame:
    pdf = pdfium.PdfDocument(str(PDF))
    text = "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    rows = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        sr, name, isin, rest, cat = m.groups()
        nums = [t for t in rest.split() if NUM.match(t)]
        if not nums:
            continue
        # last numeric token is "Average of All Exchanges (Rs. Cr.)"
        rows.append((int(sr), name, isin, float(nums[-1].replace(",", "")), cat))
    return pd.DataFrame(rows, columns=["sr", "name", "isin", "mcap_cr", "category"])


def main():
    amfi = parse_amfi()
    print(f"AMFI rows parsed: {len(amfi)} (last sr no {amfi.sr.max()})")
    cm = pd.read_csv("https://public.fyers.in/sym_details/NSE_CM.csv", header=None)
    eq = cm[cm[9].str.endswith("-EQ")][[9, 1, 5]]
    eq.columns = ["symbol", "fyers_name", "isin"]
    uni = eq.merge(amfi[["isin", "name", "mcap_cr", "category"]], on="isin", how="inner")
    uni = uni.sort_values("mcap_cr", ascending=False)
    print(f"Fyers -EQ symbols: {len(eq)} | matched to AMFI: {len(uni)}")
    sel = uni[uni.mcap_cr >= MIN_MCAP_CR].copy()
    sel.to_csv(UNIVERSE_CSV, index=False)
    print(f"Selected (mcap >= {MIN_MCAP_CR:,.0f} Cr): {len(sel)}")
    print(sel.category.value_counts().to_string())
    print("smallest selected:", sel.tail(3)[["symbol", "mcap_cr"]].values.tolist())
    nomatch = eq[~eq["isin"].isin(amfi["isin"])]
    print(f"Fyers -EQ symbols with no AMFI match: {len(nomatch)} (e.g. {nomatch.symbol.head(8).tolist()})")


if __name__ == "__main__":
    main()
