# 5 Intraday Strategies (Groww video) - Rules

- Link: https://www.youtube.com/watch?v=XlryGjTWQjk
- Video: "Master These 5 Intraday Strategies Before Your Next Trade" - Trading with Groww (speaker: Aseem Singhal), upload 17 Jun 2025, 9:44 min, Hindi
- Rules nikaale: 15 Nov 2026 (Hindi auto-transcript poora padha + description + pinned comment + top 20 comments)
- Backtest code: `src/backtest/groww5/`, runner `tools/run_groww5.py`, report `results/NN_groww5_intraday/`

## Source me kya mila
- **Transcript**: haan (Hindi auto-captions). Video ek *overview* hai: 5 strategy ka idea + ek-ek chart example. Exact numbers (SL, target, qty, kitne trade) kahin nahi bataye.
- **Description**: sirf intro + Groww links + disclaimer. Koi rule nahi.
- **Pinned comment** (channel): "What's your favourite Intraday trading strategy?" - koi rule nahi. Baaki comments me bhi rule nahi (ek viewer: "not sure about stop loss how much").
- Isliye neeche **Video me bola** (V) aur **Assumption** (A) alag likha hai. Saare A ka backtest me default liya, report ke Rules tab me bhi likha hai, aur Ayush ko Telegram pe bheja.

## Common (sab 5 pe)
| | Rule |
|---|---|
| Market | NSE intraday (MIS). Video ke examples: Tata Motors, NIFTY 50, Infosys, ICICI Bank |
| Universe (A) | Stocks: AMFI Jun-2026 **Large cap 100** (examples sab large cap the). Index: NIFTY 50 -> trade **NIFTY near-month futures** (index khud trade nahi hota) |
| Capital (A) | Rs 10 lakh **har strategy** ka alag, **compounding nahi** (fixed capital) |
| Risk / qty (A) | Stocks: risk = 1% capital = Rs 10,000; qty = floor(10,000 / (entry - SL)), position value max Rs 5 lakh (MIS 5x margin ~ Rs 1 lakh). NIFTY: lots = round(10,000 / (SL points x lot size)), min 1, max floor(10 lakh / 1.5 lakh margin) = 6 |
| Positions (A) | Ek stock me ek waqt ek hi trade; max 5 open positions ek strategy me |
| Square-off (A) | Sab intraday: 15:15 pe jo bhi khula hai band (us minute ka open) |
| Trade nahi (A) | Muhurat / special session / adhoore din (data me 375 se bahut kam candle) |
| Costs | Rulebook wale: stocks brokerage min(Rs 20, 0.03%), STT 0.025% sell, NSE 0.00297%, SEBI, stamp 0.003% buy, GST 18%. NIFTY futures: Rs 20/order, STT sell (date ke hisaab se), NSE 0.00173%, stamp 0.002%, GST |
| Slippage | Stocks 0.03% har side, NIFTY futures 2 point har side. Stress = 2x |
| Fill (A) | Signal candle band hone ke baad **agle 1-min candle ka open** (look-ahead nahi). SL / target: 1-min candle me chhua to us level pe (gap ho to open pe, jo bura ho); ek hi minute me SL aur target dono -> SL maana |
| Data | Stocks 1-min 2021-10-01 se; NIFTY futures 1-min Feb 2018 se |

## 1) Scalping strategy (1-min, stocks)
- V: Chhota timeframe 1-min (ya 2-min). Jaldi entry, 3-4-5 candle hold, **max ~5 min**, jaldi exit. Risk management bahut zaroori.
- V: Strong momentum chahiye - example me **3 red candle** lagatar (strong downward move).
- V: Jaise hi **aaj ka low (din me ab tak ka low) toote -> SELL** on 1-min.
- V: Confirmation: **Parabolic SAR** - sell trade me PSAR price ke **upar**, buy trade me PSAR price ke **neeche**.
- A: BUY bhi ulta: 3 green candle + candle aaj ke high ke upar band + PSAR neeche.
- A: "Low toota" = signal candle ka **close** aaj ke pichhle low ke neeche (09:15 se pichhli candle tak ka low). Entry agle 1-min open.
- A: PSAR settings = TradingView default (start 0.02, step 0.02, max 0.2), har din 09:15 se.
- A: **SL = un 3 candles ka high** (short) / low (long). **Koi target nahi**, exit = 5 candle baad (entry ke 5 min baad open pe), ya SL pehle.
- A: Signal sirf 09:30-15:00. Ek stock pe din me max 2 trade, strategy me din me max 10 trade.

## 2) Momentum / trend following (5-min, NIFTY)
- V: Index / stock me achha momentum ho to trend follow karo (Dow theory). Example NIFTY 50, **5-min chart**.
- V: Market seedha nahi jaata, **staircase** pattern: upar - thoda neeche - upar - neeche (higher high + higher low).
- V: **Pullback** aaye (market neeche aa raha ho) tab **buying** ka socho, momentum carry karo.
- A: Swing high / low = 5-candle fractal (candle ka high dono taraf 2-2 candles se zyada; 2 candle baad confirm). Swings din ke paar bhi chalte hain.
- A: Staircase = aakhri 2 swing high badhte hue (HH) + aakhri 2 swing low badhte hue (HL).
- A: Entry: naya **Higher Low confirm** hote hi (pullback khatam) BUY, agle 1-min open. **SL = wo swing low**. Trailing: har naya higher low SL upar le jaata hai. Target nahi. 15:15 square-off.
- A: SHORT ulta (lower high + lower low, naya lower high pe sell) bhi chalaya, report me long aur short alag (video me sirf buy ka example).
- A: Signal chart = NIFTY near-month futures 5-min (order bhi futures me). Signal 09:30-14:30, din me max 3 entry.

## 3) Breakout trading + 4) Reversal trading (daily zone, intraday entry, stocks)
- V: Important zone **bade timeframe (daily)** pe dhoondo - jahan se market baar-baar palta (support / resistance). Bade TF ke zone strong hote hain.
- V: Zone pe **2-3 baar touch -> reversal** ke chances; **5-6 baar touch -> breakout** ke chances. Volume madad karta hai (bina rule ke).
- V: Entry **intraday 5-min ya 15-min** chart pe, jab price zone ke aas-paas aaye.
- V: Breakout ke 2 tarike: (a) **seedha** level toote tab trade, (b) **pullback** ka wait - toot ke wapas usi level pe aaye tab trade.
- A: Zone = daily **swing high/low** (5 din left + 5 din right fractal, sirf pichhle 250 din, aaj se pehle confirm). Swing prices ko sort karke cluster: ek zone ki chaudai max 1.5%. Zone = [cluster ka min, max] (kam se kam center +-0.25%). **Touch = zone me kitne swing point**.
- A: Aaj ke liye: prev close ke upar sabse paas wala zone = resistance, neeche wala = support (prev close zone ke andar ho to wo zone skip).
- A: **Reversal** (touch 2-3): resistance pe 15-min candle zone chhoo ke zone ke neeche **red** band -> SHORT; support pe zone chhoo ke upar **green** band -> LONG. SL = zone ka doosra kinara ya din ka high/low (jo door). Target = 2R. 15:15 exit.
- A: **Breakout** (touch >= 5): 15-min candle resistance zone ke **upar band** -> LONG (support ke neeche band -> SHORT). SL = zone ka doosra kinara. Target = 2R. **Pullback version**: breakout candle ke baad koi 15-min candle zone tak wapas aaye (low <= zone top) aur upar band ho -> LONG (short ulta).
- A: Touch 4 wala zone dono me skip (video me 2-3 aur 5-6 bola). Signal 09:30-14:45. Ek stock-din me ek trade.

## 5) News based (5-min, stocks)
- V: Roz kuch stocks news me hote hain (bada order, govt policy, quarterly / annual result) -> unme achha movement.
- V: Example ICICI Bank 5-min: news pe **bada gap up** (1436 pe khula), lekin **pehli candle me hi bhaari selling** -> euphoria tik nahi paaya -> **SELL** socho, poore din girta raha.
- A: News ka data hamare paas nahi -> proxy: **gap >= 2%** (open vs kal ka close). Ye report me saaf likha.
- A: Gap up + **pehli 5-min candle red** -> pehli candle ke **low ke neeche** (1-min) SHORT, 11:00 tak. SL = pehli candle ka high. Target nahi, **15:15 exit** ("poore din girta raha").
- A: Gap down + pehli candle green -> high ke upar LONG (ulta; video me nahi tha, alag dikhaya).
- A: Din me max 5 trade (sabse bade gap pehle).

## Kya test nahi ho sakta
- Asli "news" (sirf gap proxy). Volume se breakout / reversal ka faisla (video me koi rule nahi). Screen par haath se zone kheenchna (formula se banaya).
- Stock list = AMFI Jun-2026 large cap (survivorship bias: aaj ke bade stocks, 2021 me kuch chhote the).
