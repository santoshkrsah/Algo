# Manish Chandra - Directional Wait & Trade Strangle (STBT, next week) - Rules

- Link: https://www.youtube.com/watch?v=YvmsB1KIcyg
- Video title: "After 10 Years I finally found this strategy"
- Channel: SAHI. Upload 14 Nov 2026, 65 min, Hindi + English. Speaker: Manish Chandra
- Rules nikaale: 25 Nov 2026
- Report ka naam: `Manish Chandra Wait & Trade Strangle - After 10 Years I finally found this strategy`

## Source me kya mila
- Transcript: haan (Hindi auto-captions, poora padha). Strategy 09:00 - 24:00, backtest / stability / sizing 24:00 - 55:00.
- Description: sirf topics, koi number nahi.
- Pinned comment: koi nahi (22 comments dekhe; strategy ka koi extra rule nahi).
- Speaker ne StockMock screen pe backtest dikhaya; **screen ki settings jo bolke nahi batayi wo yahan nahi hain** (video frames nahi padhe, sirf awaaz).
- Speaker khud kehta hai: strategy copy mat karo, apna variation banao.
- **V** = video me bola, **A** = Assumption.

## Strategy
| | Rule |
|---|---|
| Market / instrument | Index options, SELL (option writing). Index **NIFTY** (A: naam saaf nahi liya) |
| Universe | Sirf index, koi stock nahi |
| Timeframe | 1 minute premium |
| Expiry (V) | **Agle hafte (next week)** ki expiry; gamma risk kam karne ke liye 7 din se zyada door |
| Din (V/A) | STBT: aaj sell, kal buy (V). Har trading din naya trade (A) |
| Entry time (V) | **9:20** pe strike chuno (V: "entry time 9:20"; ek jagah example 9:30) |
| Strike (V) | Premium **Rs 100** wala CALL aur PUT (dono side alag) |
| Leg 1 entry (V) | Wait & trade **5%**: premium 100 se gir ke **95** ho tab sell (1 lot) |
| Leg 2 entry (V) | Same strike, wait & trade **15%**: premium **85** ho tab ek aur lot sell. Leg 1 ke baad hi |
| Stop loss (V) | Har sold leg pe **30%** |
| Trailing SL (V) | Premium 1% favour me jaye to SL 1% neeche (30 -> 29 -> 28 ...) |
| Hedge (V) | Overnight hai isliye hedge. **Leg 2 aate hi hedge BUY** (2 sell = 2 hedge). Leg 2 na aaye to hedge nahi (risk position size se) |
| Exit (V/A) | SL / trailing SL, warna **agle din** band (V: "extended day"). Exit time nahi bataya |
| Target | Nahi bataya |
| Re-entry | Nahi bataya (nahi lagaya) |
| Max trade / din | Ek side me 2 lot sell; dono side milake 4 sell + 4 hedge (rules se) |
| Kaunse din nahi | Koi skip rule nahi |
| Filter / indicator | Koi nahi. IV filter nahi: speaker "all weather" chahta hai |

## Costs, sizing, risk (video)
- Backtest me (V): slippage "0.3" (0.3% maana), taxes Rs 5, STT; data Jan 2021 se (StockMock).
- Position sizing (V): drawdown pehle dekho; kitna loss seh sakte ho (apna example: capital ka **5%**, speaker ka current drawdown 6% -> 8% tak gaya). Strategies ka **MDD jod** ke size karo (combined MDD pe nahi). Example capital Rs 10 lakh.
- Chhote size se shuru, equity badhe to size badhao, ghate to kam. Review har Friday. Strategy history se zyada drawdown de to size kam / hatao; 6 mahine tak rules mat chhedo.
- Stability check (V): entry time **9:20 - 9:30 har minute** (11 variant) sab ek jaise hone chahiye.
- IV check (V): India VIX 10-15, 15-20, 20+ me result alag-alag dekho.
- Video ke result: ~60+ mahine me ~9 loss wale; Return / MDD 3 se upar (~4.2); 3 strategy ke MDD 33k + 54k + 37k = ~1.2 L, combined 82k. Baaki 2 strategy (AI wali) ke rules nahi bataye; lot size bhi saaf nahi.

## Assumptions (A) - OK se pehle badal sakte hain
1. Index NIFTY.
2. Next week expiry = aaj ke baad ki **doosri** sabse paas weekly expiry.
3. Har trading din 9:20 pe naya setup; pichhle din ki position saath me chal sakti hai.
4. Wait & trade sirf entry wale din 15:15 tak valid; agle din naya leg nahi.
5. 5% / 15% dono 9:20 wale premium se (95 aur 85). Fill us level pe (gap ho to candle open).
6. SL = us leg ke entry price x 1.30; trailing: premium har 1% (entry ka) gire to SL 1% (entry ka) neeche, upar kabhi nahi.
7. Hedge strike: same expiry, premium lagbhag **Rs 30** (check: Rs 20 aur 50). Har hedge apne sell leg ke saath band.
8. Exit: agle trading din **15:15** (check: agle din 9:20).
9. Capital Rs 10 lakh, **1 lot har leg**, compounding nahi. Report me 5% drawdown ke hisaab se kitne lot, wo alag card.
10. Slippage 0.5% main (video 0.3% aur stress 1% bhi), kharcha hamara standard (Rs 20 / order, STT, exchange, GST, stamp, SEBI).
11. Test nahi honge: strategy rotation / health card / weekly sizing (rules number me nahi), AI wali 2 strategy.

## Data check (25 Nov 2026)
| Data | Kahan se | Kab se - kab tak |
|---|---|---|
| Nifty 1m | NIFTY50_FROM2020_1m.csv + NIFTY50_1m.csv | Jul 2020 - 25 Nov 2026 (aaj adhoora) |
| India VIX 1m | INDIAVIX_FROM2021_1m.csv (aaj download) | 1 Jan 2021 - 25 Nov 2026 |
| Nifty option daily (bhavcopy) | fno/bhavcopy_idx_hist + bhavcopy_idx | 1 Jan 2021 - 23 Nov 2026 (24 chhutti, 25 abhi nahi aayi) |
| Asli option 1m (Fyers) | fno/1m/options | 26 Aug - 20 Nov 2026, lekin **next week ka Rs 100 wala strike ek bhi din nahi** |
| Equity / futures | Is strategy me nahi lagta | - |

- **Fyers limit**: expired option ka 1-min data nahi milta. Poora premium **model (Black-76)** se banega, IV asli daily close se calibrate.
- Is strategy me entry, SL, trailing sab **premium** pe hai (5%, 15%, 30%, 1%). Isliye result model pe bahut tika hoga: model me din ke andar IV ghat-badh nahi hoti. Result ko "andaza" maano.
- Har trading din ka next week Rs 100 wala CE + PE bhavcopy me traded mila (146 / 146 sample din), to calibration achhe se ho sakti hai.

## Period
1 Jan 2021 -> 23 Nov 2026. Saal-wise: 2021-22 / 2023 / 2024 -> latest.
