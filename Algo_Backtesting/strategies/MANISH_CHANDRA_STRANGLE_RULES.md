# Manish Chandra - Next-week STBT Strangle (Wait&Trade + Hedge + Trailing SL) - Rules

- Link: https://youtu.be/YvmsB1KIcyg
- Video title: "After 10 Years I finally found this strategy" (channel SAHI, guest Manish Chandra, ~65 min, Hindi/English)
- Rules nikaale: 24 Nov 2026
- Report name ke last me video title: `<naam>_After 10 Years I finally found this strategy`

## Source
- Transcript: haan (hi auto-captions, poora padha). Description: haan (sirf chapter-type summary). Pinned comment: nahi padha (bina login nahi milta).
- Strategy sirf **13:30 - 25:00** me hai (StockMock screen pe dikhayi). Baaki video = journey, position sizing, risk dashboard, memes (koi trade rule nahi).
- Speaker bolta hai: strategy "as it is" copy mat karo, discovery khud karo; ye uski portfolio ki 40-50 strategies me se ek hai. Strategy 2-3 AI se banayi.
- V = video me bola, A = Assumption (saaf nahi).

## Rules
| | Rule |
|---|---|
| Instrument (A) | Index options. Video me "Nifty" ka zikr hai (indices se option writing), index ka naam saaf nahi. **Nifty** lenge |
| Expiry (V) | **Next week** ki expiry (7+ din door, gamma kam). Monday start, Tuesday close ("Monday start, Tuesday close" = STBT) |
| Entry time (V) | **9:20** |
| Strike (V) | Call aur Put dono me wo strike jiska premium **Rs 100 ke sabse paas** ("Rs 100 ka call / put") |
| Entry trigger = Wait&Trade (V) | Call premium Rs 100 se **5% gire (95)** to **call sell**. Put premium 100 se 5% gire (95) to **put sell**. Dono leg alag-alag, jab bhi trigger ho. Call girna = market neeche = direction confirm |
| Stop loss (V) | Entry ke baad **30% SL** (entry price ke upar 30%). SL entry hone ke baad lagta hai |
| Trailing (V) | Jaise leg 1% favour me jaye, SL 1 point kam: SL 30 -> 29 -> 28 -> 27 ... (kitna step / floor: A: har 1% premium girne pe SL% 1 kam, 0% se neeche nahi) |
| 2nd lot / pyramiding (V) | "2 lot" strategy. 2nd lot tab jab leg Rs 100 se **15% gire (85)**. Us time pe 2nd lot ke saath **hedge** bhi kharidte hain |
| Hedge (V/A) | 2nd leg aate hi hedge buy (cheap OTM option, same type). Hedge strike / premium video me nahi bataya. A: hedge premium ~ Rs 5-10 / ya 2% OTM. Pehle lot ka hedge bhi lagta hai (10 points profit lock) |
| Overnight (V) | **Extended day**: position agle din tak carry, trend continue hone ke liye |
| Exit (A) | Agle din (Tuesday) 9:35 - video me time nahi bataya. A: ya SL / trailing SL pehle lage. (STBT jaisa) |
| Days (V/A) | Monday entry, Tuesday exit. Dusre din (Tue-Fri entry) saaf nahi: A: sirf Monday |
| Qty (V/A) | Example 1-2 lot per leg. Position size = MDD se: 5% portfolio drawdown tak risk. A: **1 lot base, 2nd lot trigger pe**, capital Rs 10 lakh |
| Max trade / din (A) | Har leg ek baar; SL ke baad re-entry nahi (video me nahi) |
| Filter (V) | Koi IV filter nahi. Bola: "all weather" - IV 10-15 / 15-20 / 20+ me result alag dekhna chahiye (report me dikhayenge) |
| Video result (V) | Slippage + Rs5 + STT ke baad bhi 60 mahine me sirf 3 mahine loss; combined MDD ~82,000 (3 strategy). Cost me: "0.3 slippage, Rs 5 brokerage, STT" |

## Assumptions jo Ayush se puchne hain
1. Index: Nifty? (ya Sensex / BankNifty)
2. Exit: Tuesday 9:35? ya 15:15 / SL-only?
3. Hedge: kaun sa strike (premium Rs 5? 2% OTM?)
4. Trailing step exactly kya (1% premium = 1% SL)?
5. Sirf Monday ya har din?
6. 0.3 slippage = 0.3 point ya 0.3%?

## Data (apne PC me)
- NIFTY spot 1m: 2021-10-01 -> 2026-11-20. BANKNIFTY 1m same. SENSEX 1m: 2023-04-03 -> 2026-11-20. India VIX 1m: 2021-10-01 -> 2026-11-20.
- Index options **EOD (OHLC / close)**: bhavcopy_idx_hist (2021-10-01 -> 2024-10-31) + bhavcopy_idx (2024-11-01 -> 2026-11-20), saari expiry + strikes.
- Index options **1-minute**: sirf Nov-2026 contracts (88 NIFTY files). Purani expiry ka 1m option data Fyers se nahi milta (expired contracts ki history nahi).
- => Intraday option price **modelled** (Black-76, IV = k x realised vol, k = EOD real prices se calibrate), jaise stbt.py me. 9:20 entry / 5% trigger / SL minute-level model pe chalenge. Report me saaf likhenge.
