# Abhishek Kadam F2F - Intraday + Positional Option Selling - Rules

- Link: https://www.youtube.com/watch?v=Ib9u18tXTJc
- Video title: "The Intraday Options Strategies Pro Traders Actually Use !! #Face2Face with Mr. Abhishek Kadam"
- Channel: Elearnmarkets / Vivek Bajaj. Upload 10 Jul 2026, 1h 12m, Hindi + English
- Rules nikaale: 25 Nov 2026
- Report ka naam: `Abhishek Kadam F2F Option Selling - <video title>`

## Source me kya mila
- Transcript: haan (Hindi auto-captions). Strategy 0:45 ke baad; risk / position sizing rules 0:14-0:35 me.
- Description: sirf chapters + links, koi rule nahi.
- Pinned comment: sirf course ka promo. Top comments me VaR, hedge, brokerage / slippage pe sawaal hain, channel / speaker ne jawab me koi rule nahi diya (47 comments dekhe).
- Speaker khud kehta hai: "strategy copy mat karo, thought process lo, khud backtest karo"; backtest ka return 50% aur drawdown double maano.
- **V** = video me bola, **A** = Assumption.

## Portfolio / risk rules (V)
- 79% intraday (zyadatar 0DTE) option selling, 20% positional option selling, 1% option buying (aadha intraday, aadha positional).
- Position sizing example: qty = risk amount / SL points (Rs 2,000 risk, SL 20 point = 100 qty).
- Din ka max loss aur mahine ka max loss pehle se tay; mahine ka limit lage to band. Strategy ka drawdown 2x ho jaye to band.
- Risk : reward kam se kam 1:1; bade event pe badi position nahi.
- **Har trade hedged** (intraday aur positional dono): "VaR hamesha manage hona chahiye" (hedge kitna door: nahi bataya).
- Test hui strategy: single lot, Rs 3 lakh margin + Rs 1 lakh buffer = **Rs 4 lakh** har strategy.

## S1: Intraday strangle (9:50)
| | Rule |
|---|---|
| Instrument (V) | Sensex weekly options |
| Timeframe / entry time (V) | 9:50 AM |
| Entry (V) | Rs 60 ke sabse paas wala CALL aur PUT dono SELL |
| Stop loss (V) | 50% premium, har leg pe |
| Re-entry (V) | Ek re-entry (kab / kaise: nahi bataya) |
| Din (V) | 0DTE, 1DTE, 2DTE, 3DTE; Monday-Thursday |
| Target / trailing | Nahi bataya (nahi lagaya) |
| Qty (V) | 1 lot, capital Rs 4 lakh |
| Video ka result | Max drawdown ~1-1.3%; Jan, April kharab (cost ka zikr nahi) |

## S2: Positional STBT (range breakout)
| | Rule |
|---|---|
| Range (V) | 9:15 - 11:15 ka High / Low |
| Entry (V) | High break = ITM PUT sell; Low break = ITM CALL sell |
| Stop loss (V) | Underlying pe 0.5% |
| Re-entry (V) | Sirf ek, "at cost" |
| Exit (V) | Agle din 9:35 (speaker: 9:45 / 10 / 11 / 12 bhi test karo) |
| Qty (V) | 1 lot, capital Rs 4 lakh |
| Index | Naam nahi liya ("22,000" ka example) |
| Video ka result | Return 46% / 26% / 44%, drawdown bada; Jan, April positive |

## Filters / indicators
Koi indicator, VIX filter ya skip-day (event / holiday) rule video me nahi hai.

## Assumptions (A) - OK se pehle badal sakte hain
1. S1 time 9:50 (transcript me ek jagah "9:15" bhi suna jata hai).
2. S1 din = expiry se 0-3 trading din pehle wale din. Aaj (Thursday expiry) ye Mon-Thu hi hai; purane Friday / Tuesday expiry me bhi DTE se chalega.
3. Rs 60: 9:50 pe jis strike ka premium 60 ke sabse paas, CALL aur PUT alag.
4. S1 SL = entry x 1.5. Re-entry: SL ke baad 14:30 se pehle premium wapas entry price pe aaye to ek baar dobara SELL. Exit 15:15.
5. S2 index: NIFTY main, SENSEX alag se. ITM = 1 strike ITM. Expiry = nearest weekly; entry din expiry ho to agli.
6. S2 breakout: 11:15 ke baad 1-min candle CLOSE range ke bahar; din ka pehla signal; 15:15 ke baad naya entry nahi.
7. S2 re-entry: SL ke baad index wapas entry level pe aaye (usi din 15:15 se pehle).
8. Hedge: dono version chalenge - bina hedge, aur OTM option hedge ke saath (S2: index se ~2% door; S1: sold strike se door sasta option).
9. Qty 1 lot, capital Rs 4 lakh har strategy, **compounding nahi**. Din / mahine ka loss limit ka number S1 / S2 ke liye nahi bataya, isliye nahi lagaya.
10. Costs: Rs 20 / order, STT, exchange, SEBI, stamp, GST. Slippage 0.5% premium har fill (0% aur 1% bhi).
11. Test nahi honge: 1% option buying, 79/20/1 allocation, 3 intraday + 1 positional basket (rules nahi bataye).

## Data check (25 Nov 2026)
| Data | Kahan se | Kab tak |
|---|---|---|
| Nifty 1m | index/NIFTY50_FROM2020_1m.csv (Jul 2020 - 11 Nov 2026) + NIFTY50_1m.csv (1 Oct 2021 ->) | 23 Nov 2026 (25 Nov adhoora) |
| Sensex 1m | index/SENSEX_1m.csv, 3 Apr 2023 -> | 23 Nov 2026 |
| Nifty option daily (bhavcopy) | fno/bhavcopy_idx_hist + bhavcopy_idx, 1 Jan 2021 -> (Jan-Sep 2021 aaj download) | 23 Nov 2026 |
| Sensex option daily (BSE bhavcopy) | fno/bhavcopy_bse, 15 May 2023 -> | 23 Nov 2026 |
| Asli option 1m (Fyers) | fno/1m/options: Nifty 26 Aug - 20 Nov 2026, Sensex 15 Sep - 25 Nov 2026, kuch hi strike | - |

- 24 Nov 2026 ko bhavcopy / candles nahi (chhutti).
- Equity data is strategy me nahi lagta.
- **Limit**: expired option ka 1-min data Fyers nahi deta. Isliye option premium **model (Black-76, IV asli daily close se calibrate)** se banega. S1 premium-SL pe tika hai, uska result kam bharosemand hai.
- Sensex weekly options 15 May 2023 se (S1 aur S2-Sensex wahi se). Expiry din: Friday (2023-24), Tuesday (Jan-Aug 2025), Thursday (Sep 2025 ->).

## Period
- S2 NIFTY: 1 Jan 2021 -> 23 Nov 2026. S1 aur S2 SENSEX: 15 May 2023 -> 23 Nov 2026.
- Saal-wise alag dikhega: 2021-22 / 2023 / 2024 -> latest.
