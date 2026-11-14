# Intraday Trading Rulebook — NSE Stocks (Backtest 2021–2026)

Nov 1, 2026 · @Almax

## 1. Pehle sach: kya expect karein

Koi bhi formula "maximum returns" ki guarantee nahi deta. SEBI ke study mein FY23 mein 70% se zyada intraday traders ne loss kiya. Isliye ye rulebook ek "test karne layak system" hai, paisa chhapne ki machine nahi.

- **SEBI study (July 2024, FY23, \~70 lakh traders, equity cash):** 70%+ traders loss mein the, average loss ₹5,371. Jo saal mein 500+ trades karte the, unme 80% loss mein the. [Business Standard](https://www.business-standard.com/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html)
- **Overtrading sabse bada dushman hai:** loss karne walon ne profit walon se zyada trades kiye. Isliye hamare rules din mein kam aur filtered trades lete hain.
- **Research ka edge bhi patla hai:** US ke 7,000+ stocks par basic 5-min ORB ne 2016–2023 mein sirf 29% total diya (Sharpe 0.48). Relative Volume filter lagate hi wahi system 1,637% (Sharpe 2.81) ho gaya. Asli edge filter mein hai. [Zarattini, Barbon & Aziz](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content)
- **India mein US results copy nahi honge:** yahan STT aur baaki charges per trade kaafi zyada hain. Har rule ko India ke cost model ke saath test karna hai (Section 10).
- **Order hamesha yahi:** pehle backtest, phir 1–2 mahine paper trading, phir chhota capital. Ye educational material hai, financial advice nahi; main SEBI-registered advisor nahi hoon.

## 2. Setup aur assumptions

Saare rules NSE cash segment ke liquid stocks par MIS intraday ke liye hain. Entry 9:20 ke baad, saari positions 15:15 tak band, leverage max 5x.

| Cheez | Default value | Kyun / note |
| --- | --- | --- |
| Market | NSE cash, sirf EQ series, MIS | BE/T2T jaise delivery-only stocks universe se bahar |
| Session | 9:15–15:30 IST | 9:15–9:20 ka pehla 5-min candle = opening range |
| Exit | 15:15 par sab band | Broker ka auto square-off \~15:20 hota hai; Zerodha auto square-off order par ₹50 extra leta hai ([charges](https://zerodha.com/charges/)) |
| Data | 1-min OHLCV, 5-min mein resample | Fills ko realistic rakhne ke liye 1-min zaroori |
| Universe (har din) | Pichhle 20 din ka avg turnover ≥ ₹50 crore, price ≥ ₹100 | Point-in-time filter, taaki aaj ki list ko 2021 par na thopein |
| Leverage | Max 5x (20% margin) | Zerodha MIS par 5x deta hai ([Zerodha](https://support.zerodha.com/category/trading-and-markets/margin-leverage-and-product-and-order-types/articles/how-much-margins-leverage-does-zerodha-provide)) |
| Risk per trade | 0.5%–1% of capital | Section 9 ka formula |
| Example capital | ₹5,00,000 | Rules risk-% par chalte hain, capital badalne se logic nahi badalta |
| Backtest period | 1 Jan 2021 – 30 Oct 2026 | 2021–2023 = develop, 2024–2026 = out-of-sample test |

Sept 2021 se pehle peak-margin rules phase-wise aa rahe the, isliye poore backtest mein 5x hi maano. Price ≥ ₹100 isliye ki bahut saste stocks mein tick size aur spread ka cost % mein bahut bada ho jata hai.

## 3. Core formulas

Har formula sirf pichhle data se banta hai. Aaj ke din ka aage ka data kabhi use nahi hoga, warna backtest jhootha (lookahead bias) ho jayega. Neeche t = aaj ka din, d = koi bhi din, m = din ka minute.

**ATR(14)** — volatility ka naap, daily candles se, aaj ko chhod ke:

```latex
TR_d = \max\left(H_d - L_d,\ |H_d - C_{d-1}|,\ |L_d - C_{d-1}|\right)
```

```latex
ATR14_t = \frac{1}{14}\sum_{i=1}^{14} TR_{t-i}
```

**Opening Range (OR)** — 9:15–9:20 ka pehla 5-min candle: OR\_high, OR\_low, OR\_open, OR\_close, OR\_vol.

**Relative Volume (RVOL)** — aaj ke pehle 5 minute ka volume, pichhle 14 din ke pehle 5 minute ke average se kitna guna hai:

```latex
RVOL_t = \frac{ORVol_t}{\frac{1}{14}\sum_{i=1}^{14} ORVol_{t-i}}
```

**VWAP** — 9:15 se reset, har minute update:

```latex
VWAP_{t,m} = \frac{\sum_{k \le m} TP_k \cdot V_k}{\sum_{k \le m} V_k}, \qquad TP_k = \frac{H_k + L_k + C_k}{3}
```

**Noise band** — din ke har minute m ke liye, pichhle 14 din mein open se average kitna move hua (VM = volatility multiplier, default 1):

```latex
\sigma_{t,m} = \frac{1}{14}\sum_{i=1}^{14}\left|\frac{C_{t-i,m}}{O_{t-i}} - 1\right|
```

```latex
UB_{t,m} = \max(O_t, C_{t-1}) \cdot (1 + VM \cdot \sigma_{t,m}), \qquad LB_{t,m} = \min(O_t, C_{t-1}) \cdot (1 - VM \cdot \sigma_{t,m})
```

**Daily volatility** — sizing ke liye, pichhle 14 daily returns ka standard deviation:

```latex
\sigma^{daily}_t = \mathrm{stdev}\left(\frac{C_{t-i}}{C_{t-i-1}} - 1\right)_{i=1..14}
```

**Gap aur turnover filter:**

```latex
Gap_t = \frac{O_t}{C_{t-1}} - 1, \qquad AvgTurnover20_t = \frac{1}{20}\sum_{i=1}^{20} C_{t-i} \cdot V_{t-i}
```

**CPR** (sirf optional filter ke liye, pichhle din ke H, L, C se):

```latex
P = \frac{H+L+C}{3}, \qquad BC = \frac{H+L}{2}, \qquad TC = 2P - BC, \qquad CPRwidth\% = \frac{|TC - BC|}{P} \times 100
```

## 4. Strategy 1: ORB-5 "Stocks in Play" (main strategy)

Ye sabse strong research wali strategy hai. US mein 2016–2023 mein iska net return 1,637% tha (Sharpe 2.81, max drawdown 12%). Idea: jis stock mein pehle 5 minute mein asamanya volume aaye, uske breakout ki direction mein trade karo, chhota stop rakho, aur din ke end tak trend ko chalne do. [Paper](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content)

**Exact rules (har trading din):**

1. **9:20 par filter:** open price ≥ ₹100, AvgTurnover20 ≥ ₹50 crore, aur ATR14 ≥ 1% of price.
2. **Stocks in Play:** sirf RVOL ≥ 1.0 wale stocks. Inhe RVOL ke hisaab se rank karo, top N = 5 lo.
3. **Direction:** OR\_close > OR\_open to sirf long. OR\_close < OR\_open to sirf short. Dono barabar (doji) to us stock mein trade nahi.
4. **Entry:** long ke liye OR\_high par stop-buy, short ke liye OR\_low par stop-sell. Order 9:20 se 14:30 tak active; din mein ek stock par ek hi trade.
5. **Stop loss:** entry se k × ATR14 door. Paper mein k = 0.10; India ke liye shuruaat k = 0.25 se karo (wajah Section 10 mein).
6. **Target:** koi fixed target nahi. Stop hit nahi hua to 15:15 par exit.
7. **Size:** Section 9 ka risk formula, risk = 1% of capital per trade, total exposure ≤ 5x capital.

**Paper se 3 zaroori sabak:**

- RVOL filter hi asli edge hai: RVOL < 1 par average trade −0.02R tha, RVOL > 1 par +0.08R, aur RVOL > 30 par +0.38R.
- 5-min OR sabse best tha. 15-min OR theek tha (272%, Sharpe 1.43), lekin 30-min aur 60-min kamzor nikle (Sharpe 0.21 aur 0.40).
- Win rate sirf \~48% tha. Paisa kuch bade trend days se aata hai, isliye target lagakar profit jaldi mat kaato.

**India mein kya test karna hai:**

| Parameter | Paper (US) | India start | Test grid |
| --- | --- | --- | --- |
| OR length | 5 min | 5 min | 5, 15 min |
| RVOL min | 1.0 | 1.0 | 1.0, 1.5, 2.0 |
| Top N stocks | 20 (7,000 mein se) | 5 | 3, 5, 10 |
| Stop distance | 0.10 × ATR | 0.25 × ATR | 0.10, 0.20, 0.30, 0.50 × ATR, aur OR ka doosra end |
| Risk per trade | 1% | 1% | 0.5%, 1% |
| Max leverage | 4x | 5x | 3x, 5x |
| Exit | 16:00 (US close) | 15:15 | 15:15 |

India mein liquid stocks ki ginti US ke 7,000 se bahut kam hai, isliye top N chhota rakha hai. Ek baar mein ek hi parameter badlo aur har result 2024–2026 ke unseen data par check karo.

## 5. Strategy 2: Noise-band intraday momentum

Jab price apne "normal" daily move ke band se bahar nikle, to trend ke saath chalo. US ke SPY par 2007–2024 mein isne 1,985% net return diya (19.6% saalana, Sharpe 1.33, max drawdown 25%). [Paper](https://alexandria.unisg.ch/server/api/core/bitstreams/a99aba00-f967-49b3-aceb-f544dc386e0b/content)

**Exact rules (har stock par alag, Section 3 ke UB/LB se):**

1. **Kis par:** har din AvgTurnover20 ke hisaab se top 10 stocks, jinka ATR14 ≥ 1.5% of price ho.
2. **Signal sirf in times par check:** 9:45, 10:15, 10:45 … 14:45 (open ke baad har 30 minute). Beech ke minutes ignore — isse fake breakouts aur overtrading kam hote hain.
3. **Entry:** check time par close > UB to long, close < LB to short. Fill agle 1-min candle ke open par.
4. **Trailing stop (check times par hi):** long tab band jab close < max(UB, VWAP). Short tab band jab close > min(LB, VWAP). Band hote hi agar price doosre band ke paar ho, to ulti position le lo.
5. **Exit:** 15:15 par sab band.
6. **Size (volatility target):** har stock ka exposure = Equity × min(5, 0.02 / σ\_daily) / 10. Matlab shaant stock mein zyada, volatile stock mein kam paisa.

**Paper ke results jo India mein test karne hain:**

- Sirf opposite-band stop se Sharpe 0.61 tha. VWAP + current band ka trailing stop lagane se Sharpe 1.24 ho gaya, aur volatility sizing se 1.33.
- VM = 1.5 par sabse accha Sharpe mila (1.55), aur 14 ki jagah 90 din ke lookback par 1.50. Test grid: VM 1.0/1.5, lookback 14/90.
- 33 futures markets par same rules ka average Sharpe sirf 0.60 tha. Har market mein edge ek jaisa nahi hota.

**India ke liye bada warning:** US mein average trade sirf \~$0.09/share tha, jab SPY ka average price \~$247 tha (≈0.04%). India mein ek round-trip ka cost \~0.1% hai (Section 10). Isliye ye strategy shayad sirf zyada volatile stocks par aur VM 1.5 ke saath chale. Pehle backtest se confirm karo, maan ke mat chalo.

## 6. Strategy 3: VWAP trend (India costs ke hisaab se)

Rule simple hai: price VWAP ke upar to long, neeche to short. US mein QQQ par Jan 2018 – Sep 2023 mein isne 671% diya (Sharpe 2.1, max drawdown 9.4%), jabki buy-and-hold ne 126%. [Concretum](https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/)

Lekin independent replications utni acchi nahi nikli: result VWAP ke aas-paas ke minimum-gap filter par bahut depend karte the, aur out-of-sample plus realistic spread ke saath kaafi kamzor the ([QuantConnect forum](https://www.quantconnect.com/forum/discussion/16706/bear-bull-traders-039-paper-on-quot-the-holy-grail-quot-not-so-fast/)). Original version din bhar har VWAP cross par position palatta hai — itne trades India ke costs mein nahi tikenge. Isliye India version mein buffer aur trade-limit joda gaya hai.

**Exact rules (India version):**

1. **Kis par:** sirf us din ke Stocks in Play — RVOL ≥ 1.5 wale top 5 (Strategy 1 ka filter). Trend wahi dete hain.
2. **Timeframe:** 5-min candles, pehla signal 9:30 ke baad.
3. **Entry (buffer b = 0.2%):** 5-min close > VWAP × (1 + b) to long. 5-min close < VWAP × (1 − b) to short.
4. **Exit:** long tab band jab 5-min close < VWAP. Short tab band jab close > VWAP. Exit ke baad nayi entry tabhi jab rule 3 phir se poora ho — beech ka zone "no trade" hai.
5. **Limit:** ek stock mein din ki max 3 entries, 14:30 ke baad nayi entry nahi, 15:15 par sab band.
6. **Size:** har stock ka exposure = Equity × min(5, 0.02 / σ\_daily) / 5.

**Test grid:** timeframe 1 / 5 / 15 min · buffer 0 / 0.1 / 0.2 / 0.3% · max entries 1 / 2 / 3 · universe: Stocks in Play vs top 10 liquid. Original paper wala setting (1-min, buffer 0, unlimited) bhi chalao — sirf ye dekhne ke liye ki costs kitna kha jaate hain.

## 7. Strategy 4: First half-hour → last half-hour momentum

Ye sabse simple aur sabse kamzor edge hai, isliye ise add-on ki tarah test karo. US ke SPY par pehle half-hour ka return (pichhle close se) last half-hour ko predict karta tha, lekin R² sirf \~1.6% tha aur strategy \~6.7% saalana deti thi (Sharpe 1.08). [Gao, Han, Li & Zhou](https://www.smallake.kr/wp-content/uploads/2015/01/SSRN-id2440866.pdf) Ye effect high-volatility aur high-volume days par zyada strong tha. [Alpha Architect](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)

**Exact rules (top 10 liquid stocks, har stock alag):**

1. **Signal:** r\_FH = Close(9:45) / Close(pichhla din) − 1. Isme overnight gap shaamil hai.
2. **Filter:** pehle 30 minute ka volume ≥ pichhle 14 din ke usi window ke average, aur |r\_FH| ≥ 0.5 × σ\_daily.
3. **Entry 14:45 par (us minute ke open par):** r\_FH > 0 to long, r\_FH < 0 to short.
4. **Exit:** 15:15 par (MIS ki limit). Agar NRML wala instrument (jaise Nifty futures) use karte ho, to original window 15:00 → 15:29 bhi test karo.
5. **Size:** Strategy 2 jaisa volatility sizing, total exposure ≤ 3x.

Sirf 30 minute ke trade mein bhi \~0.1% cost lagta hai. Backtest mein average trade agar cost ka 2 guna se kam nikle, to ise chhod do.

## 8. Optional filters (A/B test karo)

Filters tabhi rakho jab 2021–23 aur 2024–26 dono periods mein result behtar ho. Sirf ek period mein behtar hona = overfitting, filter reject. Ek baar mein ek hi filter jodo.

| Filter | Rule | Evidence | Kaise test karein |
| --- | --- | --- | --- |
| NR4 / NR7 din | Kal ki range (H−L) pichhle 4 / 7 din mein sabse chhoti | SPY momentum paper: NR4 din par avg 22 bps/din vs 12 bps normal (t-stat 5.14); NR7 par 16 bps ([paper](https://alexandria.unisg.ch/server/api/core/bitstreams/a99aba00-f967-49b3-aceb-f544dc386e0b/content)) | Sirf NR4 din vs saare din |
| Narrow CPR | Kal ka CPRwidth% < 0.2% | India mein popular, koi academic test nahi mila | NR4 jaisa hi idea — dono ko compare karo |
| Gap size | \|Gap\| ≥ 0.5% wale stocks hi | Gap aksar news ka sanket hota hai; RVOL ke saath overlap ho sakta hai | Gap filter on/off |
| India VIX | VIX apne 1-saal ke median se upar | SPY par momentum ka Sharpe VIX ke saath badha (VIX \~40 tak) | High-VIX vs low-VIX din alag report karo |
| Nifty trend | Short sirf jab Nifty 50-DMA ke neeche | US mein shorts ko bear market tak seemit karne se kaafi profit chhoot gaya | Shayad mat lagao, par ek baar test karo |
| Expiry day | Nifty weekly expiry: Aug 2025 tak Thursday, 1 Sep 2025 se Tuesday ([Business Today](https://www.businesstoday.in/markets/stocks/story/nse-receives-sebi-nod-for-tuesday-expiry-bse-thursday-480736-2025-06-17)) | Expiry par index mein alag tarah ki volatility | Expiry din include vs exclude, dono periods alag |
| Event days | Budget, RBI policy, election result (jaise 4 June 2024), Muhurat session | Abnormal din, results bigaad sakte hain | Inhe tag karo, alag se dekho; Muhurat session hamesha skip |
| Weekday | Mon–Fri alag report | US mein Wed sabse accha tha | Sirf dekhne ke liye — weekday par optimize mat karo |

Script mein har filter ek flag se on hota hai (jaise `--filters nr4`), aur weekday / expiry-din ka breakdown har run mein khud banta hai — Section 13 dekho.

## 9. Risk management aur position sizing

Ek trade mein max 1% capital ka risk, din mein max 3% ka loss, aur peak se 15% drawdown par system band. Ye limits strategy se zyada zaroori hain.

**Risk-based size (Strategy 1 — ORB):** E = din ki shuruaat ki equity, r = 1%, L = 5 (max leverage), N = max positions.

```latex
Shares = \left\lfloor \min\left(\frac{r \cdot E}{|P_{entry} - P_{stop}|},\ \frac{L \cdot E}{N \cdot P_{entry}}\right) \right\rfloor
```

Example: E = ₹5,00,000, entry ₹1,000, stop ₹993.75 (0.25 × ATR ₹25). Risk formula se 800 shares (₹8 lakh), lekin leverage cap se max 500 shares (₹5 lakh). To 500 shares, asli risk ₹3,125 = 0.63%.

**Volatility-target size (Strategy 2, 3, 4):** σ\_target = 2% daily.

```latex
Notional_i = \frac{E \cdot \min\left(L,\ \sigma_{target} / \sigma^{daily}_i\right)}{N}
```

**Hard limits (har strategy par):**

- **Daily loss limit:** din ka P&L −3% of E pahunche to us din koi nayi entry nahi.
- **Max open risk:** saare open trades ka total risk ≤ 5% of E.
- **Liquidity cap:** ek order ≤ us stock ke average daily volume ka 1%.
- **Drawdown kill-switch:** equity peak se 15% gire to live trading band, wapas paper trading aur review.
- **Stop discipline:** entry ke turant baad SL order, stop kabhi door mat khiskao, averaging down nahi.
- **Compounding:** E har din subah update karo (pichhle din ke close ki equity).

## 10. Cost model: har trade ka asli kharcha

₹5 lakh ki ek intraday round-trip par brokerage aur taxes \~₹225 (0.045%) hain, slippage alag. Asli khatra ye hai ki stop jitna tight, cost utna bada hissa R ka kha jaata hai.

| Charge | Rate (Zerodha, Nov 2026) | ₹5 lakh round-trip par |
| --- | --- | --- |
| Brokerage | 0.03% ya ₹20 per order, jo kam ho | ₹40.00 |
| STT | 0.025%, sirf sell side | ₹125.00 |
| NSE transaction | 0.00307%, dono side | ₹30.70 |
| SEBI fee | ₹10 / crore, dono side | ₹1.00 |
| Stamp duty | 0.003%, sirf buy side | ₹15.00 |
| GST | 18% of (brokerage + transaction + SEBI) | ₹12.91 |
| **Total** |  | **₹224.61 (0.045%)** |

Rates: [Zerodha charges](https://zerodha.com/charges/). Budget 2026 ne sirf F&O ka STT badhaya; intraday equity ka STT 0.025% hi hai ([Zerodha STT](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated)). ₹1 lakh ki trade par yahi total ₹82.68 (0.083%) aata hai, kyunki ₹20 ka brokerage chhoti trade par bhaari padta hai. NSE ka transaction charge pehle 0.00297% tha; farak itna chhota hai ki poore backtest mein current rate chalega.

**Slippage:** liquid stocks mein entry aur exit, dono par 0.03% maano. Robustness ke liye 0.05% per side par bhi chalao — profit tab bhi bache tabhi strategy pass.

```latex
Cost_R = \frac{c_{rt} + 2s}{k \cdot ATR\%}
```

Yahan c\_rt = round-trip cost % (0.045%), s = slippage per side, aur k × ATR% = stop ki doori %.

&#91;embedded content: Calculated from Zerodha charges (Nov 2026) · example: ₹5 lakh trade, ATR 2.5% of price, slippage 0.03% per side\]

US paper mein poora edge sirf +0.08R per trade tha. 0.10×ATR stop par India ka cost uska \~5 guna hai. Isliye woh setting chuno jahan Cost\_R ≤ 0.2R — 2.5% ATR wale stock par iska matlab k ≥ 0.25.

## 11. Backtest kaise karein

2021–2023 par rules develop karo. 2024–2026 ka data tab tak mat chhuo jab tak final parameters fix na ho jayein — warna "maximum return" dhoondhte-dhoondhte system past ko ratt lega aur live mein fail hoga. Optimize total return par nahi, Sharpe aur drawdown par karo.

**Step by step:**

1. **Data saaf karo:** sirf 9:15–15:29 IST ke candles. Jis din 5% se zyada minutes missing hon, woh din skip. Muhurat aur special sessions hatao.
2. **Corporate actions:** split/bonus ke liye prices adjust karo, ya event ke ±14 din us stock ko skip karo (warna ATR aur gap nakli dikhenge).
3. **Point-in-time universe:** har din ka turnover filter sirf us din se pehle ke data se. Aaj ki Nifty list 2021 par mat lagao.
4. **No lookahead:** ATR, RVOL ka average, σ — sab pichhle din tak ke data se. Intraday signal sirf us minute tak ke data se.
5. **Realistic fills:** stop order trigger price par fill; agar candle trigger ke paar khula to us candle ke open par. Ek hi 1-min candle mein entry aur stop dono choo jaayein to maano stop laga. Exit par slippage jodo.
6. **Costs:** Section 10 ka poora model, har fill par.
7. **Split:** in-sample 1 Jan 2021 – 31 Dec 2023, out-of-sample 1 Jan 2024 – 30 Oct 2026. Chaho to walk-forward: pichhle 24 mahine par tune, agle 6 mahine par test, aage khiskao.
8. **Parameter grid:** Section 4 jaisa chhota grid. Woh setting chuno jiske aas-paas ki settings bhi acchi hon (plateau), akela peak nahi.
9. **Stress test:** slippage 2x, costs 1.5x, entry ek candle late, aur 10% trades random hata ke dobara chalao. Trades ka order shuffle karke (Monte Carlo) max drawdown ka range dekho.
10. **Report:** har saal alag, long vs short alag, aur Section 8 ke tags (expiry, event days) alag.

**Formulas:**

```latex
Expectancy_R = p_{win} \cdot \overline{R}_{win} - (1 - p_{win}) \cdot |\overline{R}_{loss}|
```

```latex
Sharpe = \frac{\overline{r}_{daily}}{\sigma(r_{daily})} \cdot \sqrt{252}, \qquad t = \frac{\overline{R}_{trade}}{\sigma(R_{trade})} \cdot \sqrt{n}
```

**Pass criteria (out-of-sample 2024–2026 par, mera suggestion):**

| Metric | Pass |
| --- | --- |
| Sharpe (daily, net) | ≥ 1.0 |
| Max drawdown | ≤ 20% |
| Profit factor (gross profit ÷ gross loss) | ≥ 1.3 |
| Expectancy | ≥ +0.10R per trade, net |
| Trades | ≥ 300, aur t-stat ≥ 2 |
| Avg gross trade ÷ cost | ≥ 2 |
| Out-of-sample Sharpe ÷ in-sample Sharpe | ≥ 0.5 |
| Stress test (slippage 2x) | Ab bhi profitable |

Ek bhi row fail ho to strategy live nahi jaayegi — parameters badal ke dobara out-of-sample check karna bhi "cheating" hai.

## 12. Data: kya chahiye aur kahan se milega

2021 se aaj tak ka poora 1-minute stock data documented roop se do jagah milta hai: Zerodha Kite Connect (₹500/mahina, 2015 se) aur FYERS API (free, July 2017 se). Dhan aur Upstox 2021 ka kuch ya poora hissa miss karte hain.

**Kaunsa data chahiye:**

| Data | Detail | Kis kaam ka |
| --- | --- | --- |
| 1-min OHLCV | \~200 liquid NSE stocks, Aug 2020 se aaj tak (shuru ke mahine warm-up ke liye) | Signals aur fills |
| Daily OHLCV + turnover | NSE bhavcopy, free aur official ([NSE reports](https://www.nseindia.com/all-reports)) | ATR, universe filter |
| Corporate actions | Split, bonus, symbol change, merger ki dates | Adjust ya skip |
| Trading calendar | NSE holidays, Muhurat aur special sessions | Data cleaning |
| Nifty 50 (1-min) + India VIX (daily) | Index aur VIX history | Section 8 ke filters |
| Price band / circuit (optional) | Stock-wise price band | Circuit wale din flag |
| Event dates (optional) | Budget, RBI policy, election result | Tagging |

**CSV format** — har stock ki ek file, naam SYMBOL.csv (jaise RELIANCE.csv). Timestamp = candle shuru hone ka time, IST. Neeche ke numbers sirf format dikhane ke liye hain:

```csv
datetime,open,high,low,close,volume
2021-01-04 09:15:00,1990.00,1995.50,1986.10,1993.20,48213
2021-01-04 09:16:00,1993.20,1994.00,1989.75,1990.40,21877
```

**Data sources (14 Oct 2026 tak documented):**

| Source | 1-min history | Ek request mein | Cost | 2021 cover? |
| --- | --- | --- | --- | --- |
| Zerodha Kite Connect | 2015 se | 60 din | ₹500/mahina (Personal plan mein history nahi) | Haan |
| FYERS API | 3 July 2017 se | 100 din | Free | Haan |
| DhanHQ | Pichhle 5 saal, sirf active instruments | 90 din | ₹499 + tax / 30 din | Aadha (late 2021 se) |
| Upstox API v3 | Jan 2022 se | 1 mahina | Free | Nahi |
| ICICI Breeze | 3 ya 10 saal — docs mein conflict | 1,000 candles | Free (ICICI account) | Pakka nahi; data corporate actions ke liye adjusted nahi |
| Authorized vendors (TrueData, Global Datafeeds, Accelpix) | Lambi history | — | Paid, quote lo | Haan; delisted stocks ka data bhi maango |

Sources: [OneTradeJournal comparison](https://onetradejournal.com/trading-apis/historical-data-api-india), [Kite staff reply](https://kite.trade/forum/discussion/comment/52670/), [Upstox announcement](https://community.upstox.com/t/get-historical-data-in-any-time-frame-you-wished-we-built-new-releases-at-upstox-api/8845), [Zerodha charges](https://zerodha.com/charges/).

**Teen bade data traps:**

- **Survivorship bias:** Kite Connect par delisted securities ka historical data nahi milta ([Kite forum](https://kite.trade/forum/discussion/comment/50548/)). Jo stocks merge ya delist hue (jaise 2023 mein HDFC Ltd), woh backtest se gayab honge aur result thoda zyada accha dikhega. Ise report mein likho, ya vendor se delisted data lo.
- **Symbol change:** purane naam ko naye naam se jodo (jaise ZOMATO → ETERNAL), warna ek stock do aadhe stocks ban jaayega.
- **Adjustment check:** kisi bhi source ka data split/bonus ki date par khud check karo — price mein achanak 50% ka gap matlab data adjusted nahi.

**Download ka andaza (Kite, 3 requests/second):** \~200 stocks × \~6 saal ≈ 7,600 requests ≈ 45 minute, theory mein. Total \~11 crore rows, CSV mein \~6 GB — Parquet mein save karo.

## 13. Python backtester: kaise chalayein

Do files saath mein hain: **intraday\_backtest.py** (charon strategies, Section 10 ka cost model, in-sample/out-of-sample report, Section 11 ka pass/fail) aur **download\_kite.py** (Kite Connect se 1-min data). Script synthetic data par test ki gayi hai: cost formula, ORB entry/stop/exit, sizing aur no-lookahead checks pass hain. Asli NSE data par result abhi kisi ne nahi dekha — woh aapka backtest batayega.

**1. Install:** `pip install pandas numpy matplotlib` (Kite se data lena ho to `kiteconnect` bhi).

**2. Pehle pipeline check karo (fake data par):**

```bash
python intraday_backtest.py --make-demo-data demo_data
python intraday_backtest.py --data-dir demo_data --strategy orb --start 2021-01-01 --end 2021-12-31 --oos-start 2021-07-01
```

Demo data ke numbers ka market se koi lena-dena nahi — sirf ye dekhna hai ki code chal raha hai.

**3. Asli data (Kite, Connect plan):**

```bash
python download_kite.py --api-key XXX --api-secret YYY --request-token ZZZ --fno --from 2020-08-01 --to 2026-10-30 --out data
```

Doosre source ka data ho to bas Section 12 wale CSV format mein `data/` folder mein rakh do.

**4. Backtest chalao:**

```bash
python intraday_backtest.py --data-dir data --strategy orb --stop-atr 0.25
python intraday_backtest.py --data-dir data --strategy noise --vm 1.5
python intraday_backtest.py --data-dir data --strategy vwap
python intraday_backtest.py --data-dir data --strategy fh
```

**5. Parameter grid (Section 4 ka test grid):**

```bash
python intraday_backtest.py --data-dir data --strategy orb --grid "stop_atr=0.1,0.2,0.25,0.3,0.5;rvol_min=1,1.5,2;top_n=3,5,10"
```

Grid ka result in-sample Sharpe se sort hota hai — choose wahin se karo, out-of-sample sirf confirm karne ke liye.

| Output file | Kya hai |
| --- | --- |
| trades.csv | Har trade: time, price, shares, fees, slippage, net P&L, R |
| equity.csv | Roz ki equity aur P&L |
| yearly.csv | Saal-wise return, Sharpe, drawdown, win rate |
| summary.json | Saare metrics, IS/OOS/stress, pass/fail |
| equity.png | Equity curve, slippage 2x wali stress line ke saath |
| grid.csv | Har parameter combination ka IS aur OOS result |

**Script khud kya sambhalta hai:** har din point-in-time universe; har fill par slippage aur poora cost; slippage 2x wala stress run; 3% daily loss limit; order ≤ 1% of 20-din avg volume; 95% se kam minutes wale din (Muhurat, adhoore din) drop; 25% se bade overnight gap ke baad us stock ke kuch din skip (unadjusted split ka shak).

**Section 8 ke filters:** `--filters nr4` (ya `nr7`, `cpr`, `gap`, `noexpiry`), `--filters vixhigh --vix-file INDIAVIX.csv`, `--filters trendshort --nifty-file NIFTY50.csv` — dono files mein bas `date,close` columns chahiye. Event days ke liye `--skip-dates-file events.txt` (ek line mein ek date). Har run ek **breakdown.csv** bhi banata hai: weekday, Nifty expiry din, long/short aur exit reason ke hisaab se result.

**Kya nahi sambhalta (dhyan rakho):** circuit/price-band wale din ka exit optimistic ho sakta hai; daily close = 15:29 candle ka close; har EQ stock mein intraday short maana gaya hai; delisted stocks data mein na hon to survivorship bias rahega.

## 14. Live jaane se pehle checklist

Backtest pass hona sirf pehla darwaza hai. Asli paisa lagane se pehle ye sab tick hona chahiye:

- [ ] Out-of-sample (2024–2026) par Section 11 ke saare pass criteria PASS
- [ ] Slippage 2x wala stress test bhi profitable
- [ ] 1–2 mahine paper trading, real time mein, bilkul same rules se
- [ ] Paper trading ka expectancy backtest ke kam se kam 50% ke barabar
- [ ] Har trade ka expected vs actual fill note karke asli slippage naapa
- [ ] Shuru mein capital ka sirf 10–20%, aur risk per trade 0.5%
- [ ] 3% daily loss limit aur 15% drawdown kill-switch likh ke rakha, aur follow kiya
- [ ] Har mahine review: live expectancy 3 mahine tak backtest ke 50% se kam rahe to system band
- [ ] Tax: intraday equity ka profit speculative business income maana jaata hai — apne CA se confirm karo

## 15. Sources

- [SEBI intraday study (Business Standard, 24 July 2024)](https://www.business-standard.com/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html)
- [Zarattini, Barbon & Aziz — A Profitable Day Trading Strategy For The U.S. Equity Market (2024)](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content)
- [Zarattini, Aziz & Barbon — Beat the Market: An Effective Intraday Momentum Strategy for SPY (2024/2025)](https://alexandria.unisg.ch/server/api/core/bitstreams/a99aba00-f967-49b3-aceb-f544dc386e0b/content)
- [Zarattini & Aziz — VWAP: The Holy Grail for Day Trading Systems](https://concretumgroup.com/volume-weighted-average-price-vwap-the-holy-grail-for-day-trading-systems/)
- [QuantConnect forum — VWAP paper ki independent replications](https://www.quantconnect.com/forum/discussion/16706/bear-bull-traders-039-paper-on-quot-the-holy-grail-quot-not-so-fast/)
- [Gao, Han, Li & Zhou — Intraday Momentum: The First Half-Hour Return Predicts the Last Half-Hour Return](https://www.smallake.kr/wp-content/uploads/2015/01/SSRN-id2440866.pdf)
- [Alpha Architect — summary of the intraday momentum paper](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/)
- [Zerodha — brokerage and statutory charges](https://zerodha.com/charges/)
- [Zerodha — STT rates after Budget 2026](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated)
- [Zerodha — intraday leverage (5x)](https://support.zerodha.com/category/trading-and-markets/margin-leverage-and-product-and-order-types/articles/how-much-margins-leverage-does-zerodha-provide)
- [Kite Connect forum — minute data from 2015, limits](https://kite.trade/forum/discussion/comment/52670/)
- [Kite Connect forum — delisted instruments](https://kite.trade/forum/discussion/comment/50548/)
- [Upstox — v3 historical data (minutes since Jan 2022)](https://community.upstox.com/t/get-historical-data-in-any-time-frame-you-wished-we-built-new-releases-at-upstox-api/8845)
- [OneTradeJournal — Indian historical data API comparison (checked 14 Oct 2026)](https://onetradejournal.com/trading-apis/historical-data-api-india)
- [Business Today — NSE expiry moves to Tuesday from 1 Sep 2025](https://www.businesstoday.in/markets/stocks/story/nse-receives-sebi-nod-for-tuesday-expiry-bse-thursday-480736-2025-06-17)
