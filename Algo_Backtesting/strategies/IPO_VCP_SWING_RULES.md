# IPO base / VCP swing + ROC market cycle (Financially Free podcast) - Rules

- Link: https://www.youtube.com/watch?v=pcq6tj7FZv8
- Video: "My 2 Swing Trading Strategies which helped me DOUBLE my Portfolio in 6 Months!" - Financially Free (guest: Harsh Singh, hosts Shubham / Hemant), upload 30 Oct 2025, 37:13 min, Hindi
- Rules nikaale: 21 Nov 2026 (Hindi auto-transcript poora padha + description + chapters + pinned comment + top 40 comments)
- Backtest code: `src/backtest/ipovcp.py`, runner `tools/run_ipovcp.py`, report `results/NN_ipo_vcp_swing/`

## Source me kya mila
- **Transcript**: haan (Hindi auto-captions, poora). Ye ek *podcast* hai: Harsh apna process batata hai (IPO base trading / VCP, 21 EMA trailing, ROC indicator se market cycle, 10% stop loss, chhota portfolio). Exact numbers kuch hi hain (ROC 18/20 month, 0 / 45 / 100, 21 / 63 EMA, 10% SL, 5 stock).
- **Description**: webinar / course links, chapters (timestamps), disclaimer. Koi rule nahi.
- **Pinned comment** (channel): webinar link + Harsh ka channel link. Koi rule nahi. Top 40 comments me bhi koi naya rule nahi (log ROC indicator aur Nifty/Gold chart ko useful bata rahe hain).
- Isliye neeche **V** = video me bola, **A** = hamara assumption (video me saaf nahi). Saare A report ke Rules tab me bhi hain aur Ayush ko Telegram pe bheje.

## Common (sab strategies)
| | Rule |
|---|---|
| Market | NSE cash (delivery), sirf long. V: Zerodha equity account, swing trading |
| Data | Fyers daily candles (split / bonus adjusted). IPO list = NSE "past issues" (2016 se). Smallcap 100 index + GOLDBEES daily (Fyers) |
| Capital (A) | Rs 10 lakh, **compounding nahi** (har unit shuruaati capital ka fixed hissa). Compounding wala run Extra checks me |
| Portfolio | V: chhota, concentrated portfolio (pehle 30-50 stock the, ab kam; "5 stock ka portfolio" ka example). A: **max 5 stock** ek saath |
| Stop loss | V: "10% se zyada loss kisi bhi stock me nahi hone denge". A: stop = average buy price x 0.90; din ka low stop chhuye to stop pe exit (gap neeche khula to open pe) |
| Trailing exit | V: 21 EMA pe trail; "21 EMA ke neeche **lagatar do red candle** close" = exit; ek red candle ke baad agle din recover = exit nahi. A: red = close < open, dono din close < 21 EMA; exit agle din ke open pe |
| Re-entry | V: "jaise hi stock wapas 21 EMA reclaim kare, wapas enter" / "do green candle ya ek green + agle din achha move". A: EMA exit ke baad 60 trading din ke andar **lagatar do green candle 21 EMA ke upar close** -> agle din open pe wapas utne hi unit. Max 3 re-entry. Stop loss wale exit ke baad re-entry nahi (A) |
| 63 EMA | V: "63 EMA ya 21 EMA, jo timeframe suit kare". A: base = 21 EMA, 63 EMA wala Extra check |
| Losing streak | V: "5 trade liye, paanchon ke SL hit -> 30-40% capital se hi trade". A: lagatar 5 stop-loss exit ke baad naya unit 35% size ka, jab tak koi trade profit me band na ho |
| Fill (A) | Signal din ke close pe; order agle trading din ke open pe (look-ahead nahi) |
| Costs | Angel One delivery: brokerage min(Rs 20, 0.1%) har order, STT 0.1% buy + sell, NSE 0.00297%, SEBI 0.0001%, stamp 0.015% buy, GST 18%, DP Rs 23.60 har sell |
| Slippage (A) | Mainboard 0.10% har fill, SME 0.50% har fill. Stress = 2x |
| Ranking (A) | Ek din me jagah se zyada signal: pehle top-up (pehle se khule stock), phir naye stock 63 din ke return (relative strength) ke hisaab se |

## 1) IPO base trading (V: "IPO based trading bol lo ya VCP")
- V: Recently listed IPO (Chittorgarh site se list) -> listing ke baad stock neeche jaake **base** banata hai -> base se **breakout** aata hai to quick move.
- V: Pehle fundamental screen (niche product, starting PE, competitor PE, rerating chance) -> watchlist. **A: test nahi ho sakta** (fundamental data nahi), isliye saare IPO liye (sirf liquidity filter).
- V: Base me pehli entry thodi quantity; **listing day price ka breakout** aaye / retest ho to **top-up**. Example Stallion (Jan 2025 listing): 72 pe pehli buy, listing price breakout + retest pe aur buy, average ~100, phir 21 DMA pe trail.
- A: Universe = NSE **mainboard** IPO, listing 1 Jan 2016 se (NSE list 2016 se hi hai). Fyers ki pehli candle listing date ke 5 din ke andar honi chahiye. SME IPO alag Extra check me (V: Harsh SME bhi karta hai).
- A: "Recently listed" = listing ke **500 trading din (~2 saal)** tak naya setup; khuli position apne exit rule se hi band.
- A: Liquidity: pichhle 20 din ka average turnover >= Rs 1 crore.
- A: **Listing level (LH)** = listing day ka high (Fyers adjusted).
- A: **Entry 1 (VCP breakout)**: neeche wala VCP rule (listing ke kam se kam 60 din baad). 1 unit = 10% capital (Rs 1 lakh).
- A: **Entry 2 (listing breakout / top-up)**: close > LH aur pichhle 20 din ke saare close LH ke neeche the. Pehle se position ho to top-up (+1 unit), nahi to naya stock 1 unit. Ek stock me max 2 unit = 20%.
- A: Retest wala top-up (breakout ke baad 20 din me low <= LH x 1.03, green candle aur close > LH) Extra check me.

## 2) VCP (Mark Minervini) - V: watchlist ka "doosra tarika" = all-time high ke paas wale leaders
- V: Resistance tak jaake stock baar-baar thoda girta hai; **har baar girna (depth) pichhle se kam** (D1 > D2 > D3), price tight range me, **breakout se pehle tightness** (3-4% girte hi buying). Phir breakout = entry.
- V: Watchlist: jo stock / sector **all-time high ke paas** hain, khaaskar jab index sideways ho aur ye naya high banayein (relative strength vs Smallcap / Nifty). Fundamental (ROCE, ROE > 20%) - **A: test nahi ho sakta**.
- A (VCP formula, daily): pichhle 60 din ko 3 hisson me baanto: A = din t-60..t-31, B = t-30..t-11, C = t-10..t-1.
  depth = (max high - min low) / max high. VCP = depth A > depth B > depth C, depth C <= 10%, depth A >= 10%,
  low C >= low B >= low A (higher lows), C ka high >= 60-din high x 0.90 (tight area resistance ke paas).
  **Signal**: aaj close > C ka high (pivot) aur close > 21 EMA.
- A: Universe = saare mainboard stocks (AMFI Jun-2026 list + IPO stocks), 20-din average turnover >= Rs 5 crore, close >= all-time high (2010 se data) x 0.95, 63-din return > Smallcap 100 ka 63-din return.
- A: 1 position = 2 unit = 20% capital (Rs 2 lakh), top-up nahi. Max 5 stock.

## 3) ROC market cycle (V: "kab trade nahi karna")
- V: ROC indicator, **monthly** timeframe. NIFTY 50: length **18**, line **0** aur **45**: 0 ke paas = equity me aggressive (buy), 45 ke paas = equity kam karo, gold / corporate bond me shift.
- V: Harsh khud **Smallcap 100** dekhta hai: length **20**, monthly, **0 pe buy, 100 pe sell** ("backtested number").
- V: Host ka idea, Harsh ne haan kaha: ROC negative se **0 ke upar cross** kare = achhi entry.
- V: Mahine me ek baar (pehle weekend) dekhna.
- A: ROC = (is mahine ka close / N mahine pehle ka close - 1) x 100. Mahine ke close ka signal agle mahine ke pehle trading din se lagu.
- A: State: ROC >= 100 (Smallcap) / 45 (Nifty) -> **OFF**; OFF ke baad ROC <= 0 -> **ON**. Zero-cross version: OFF ke baad ROC < 0 se >= 0 hone pe ON.
- A: Stock strategies (1, 2) me: OFF = koi naya entry / re-entry nahi, khuli position apne rule se. Base filter = Smallcap 100 ROC20 (0/100); Nifty ROC18 (0/45), zero-cross aur filter OFF Extra checks me.
- A: **Allocation test (alag)**: ON = poora paisa index (NIFTY 50 / Smallcap 100, ETF jaisa), OFF = GOLDBEES. Switch agle mahine ke pehle din ke open pe, 0.2% kharcha har switch. Compounding haan (poora paisa ghoomta hai). Buy-hold se tulna.

## Kya test nahi ho sakta
- Fundamental screening (niche product, PE vs peers, ROCE/ROE, management guidance, ESM-2 se nikalna), AI / concall padhna.
- Chart pe haath se base / VCP pehchanna (formula se banaya - Harsh ki aankh jaisa nahi hoga).
- Nifty / Gold ratio channel (V me koi number nahi, sirf chart pe channel kheencha).
- "Capital ko mahine me 4 baar ghumana" / win-rate math = example, rule nahi.
- Survivorship: IPO list NSE ki poori hai, par delist / merge hue IPO (47, jinme 9 mainboard) ka data Fyers pe nahi. VCP universe = AMFI Jun-2026 list + abhi listed IPO stocks.
