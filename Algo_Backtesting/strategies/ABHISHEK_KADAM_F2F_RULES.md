# Abhishek Kadam (Capita Engineers) - Intraday Options Strategies - Rules

- Link: https://www.youtube.com/watch?v=Ib9u18tXTJc
- Video title: "The Intraday Options Strategies Pro Traders Actually Use !! #Face2Face with Mr. Abhishek Kadam" (Elearnmarkets / Vivek Bajaj, ~1h 7min, Hindi + English)
- Rules nikaale: 24 Nov 2026
- Report name ke last me video title: `<naam>_The Intraday Options Strategies Pro Traders Actually Use Face2Face Abhishek Kadam`

## Source me kya mila
- Transcript: haan (Hindi auto-captions + en-IN, poora padha). Description: haan (sirf chapters + links). Pinned comment / comments: **nahi padhe** (YouTube comments bina login ke nahi mile).
- Strategy sirf **0:45 - 1:07** (chapters "0DTE regime shifts", "Options based strategy", "Price action strategy") me hai. Baaki video = journey, risk management, psychology, VaR (koi trade rule nahi).
- Speaker khud bolta hai: "ye strategy mat chalao, thought process samjho; backtest khud karo" aur "backtest ka return 50% kam, drawdown double maano".
- V = video me bola, A = Assumption (video me saaf nahi).

## Portfolio idea (V)
- Allocation: **79% intraday 0DTE** option selling, **20% positional** option selling, **1% option buying** (50% intraday + 50% positional; max loss = us 1%).
- 10 lakh basket: 3 intraday strategy + 1 positional. Value-at-Risk se positional ki size itni ki poore portfolio pe asar na pade.
- Test sirf **2 strategy** ka dikhaya (neeche S1, S2), har ek ka single lot, capital Rs 3 lakh margin + Rs 1 lakh buffer = **Rs 4 lakh** har strategy.

## S1: Intraday option selling (9:50 AM)
| | Rule |
|---|---|
| Instrument (V) | **Sensex** options (video me "ससेक्स") |
| Strategy (V) | 9:50 baje (V: "950 pe") closest-premium **Rs 60 ka CALL aur Rs 60 ka PUT dono sell** (short strangle) |
| Timing (V) | 9:50 AI ne 1-min data se dhoondha; speaker bolta hai "ho sakta hai optimized time ho" |
| DTE (V) | 0DTE, 1DTE, 2DTE, 3DTE ek hi rule se (video me result ek hi combined) |
| Stop loss (V) | **50% premium** (har leg alag) |
| Re-entry (V) | **ek re-entry** SL lagne ke baad (kaise / kab: nahi bataya) |
| Din (V) | Monday, Tuesday, Wednesday, Thursday |
| Exit (A) | Intraday: din ke end pe band (15:15 / 15:20) |
| Qty (V) | 1 lot, capital Rs 4 lakh |
| Video result | ~1-1.3% max drawdown (backtest, cost ke baare me nahi bataya); April, Jan losing months |

## S2: Positional (STBT) option selling - price action
| | Rule |
|---|---|
| Instrument (V) | Index options. Example "22,000" (Nifty jaisa level). Konsa index nahi bataya |
| Range (V) | **9:15 se 11:15** (2 ghante) ka High / Low |
| Entry (V) | Range **High break** -> **ITM PUT sell**. Range **Low break** -> **ITM CALL sell** |
| Stop loss (V) | **0.5% underlying** par (22,000 pe ~Rs 100) |
| Re-entry (V) | **Ek baar** "re-entry at cost" (SL lagne ke baad price wapas entry pe aaye to dobara) |
| Exit (V) | **Agle din 9:35 AM** (STBT: aaj sell, kal buy). Speaker: 9:35 ko 9:45 / 10 / 11 / 12 try karo |
| Hedge | Video me nahi bola (A) |
| Video result | Returns 46% / 26% / 44% (saal-wise), drawdown **bada** -> position sizing se handle; April aur Jan positive |

## Assumptions / saaf nahi (Ayush se pucha jaye)
1. S1 me "9:15 vs 9:50": transcript me pehle "9:15" bola, phir "9:50 ka timing". **9:50 liya**.
2. S1 ka "0DTE-3DTE, Mon-Thu": Sensex weekly expiry 2023-25 me Friday/Thursday, **Sep 2025 se Tuesday** (A). Isliye kitne DTE kis din, expiry-day pe trade hoga ya nahi - saaf nahi. Default: har din **nearest weekly expiry**, jo bhi DTE ho (0-3 DTE jaisa video ne kaha, DTE > 3 wale din skip).
3. "Closest premium Rs 60": jis strike ka 9:50 pe price Rs 60 ke sabse paas ho, call aur put alag-alag.
4. S1 SL = 50% premium (entry price x 1.5), **re-entry rule** (A): SL ke baad agar din 14:30 se pehle us option ka price wapas entry price pe aaye to ek baar dobara sell.
5. S1 exit time (A) 15:15. Hedge (A) nahi.
6. S2 index (A): **NIFTY** (video ka 22,000 example) - dono Nifty + Sensex alag chala ke dekh sakte hain.
7. S2 "ITM" (A): 1 strike ITM; hedge nahi (video me nahi).
8. S2 breakout (A): 11:15 ke baad pehli 1-min candle ka **close** range ke bahar; din me ek signal; 15:15 ke baad naya entry nahi.
9. S2 me "9:15-11:15" ya "9:15-9:50" (video me ek jagah 9:50 range bhi mention): 9:15-11:15 liya.
10. Qty: **1 lot** har strategy (A) + paisa Rs 4 lakh/strategy (V). Combined: dono ek saath, 10 lakh basket ka version alag (3 intraday + 1 positional) - **extra**, main test nahi.
11. Costs + slippage hamare standard (option charges, STT, GST, 0.5% / 1% premium slippage).
12. "Value at Risk", 79/20/1 allocation, option buying wala 1% -> **test nahi** (video me rules nahi).

## Data ki sachchai (backtest ke liye sabse badi dikkat)
- Fyers expired option ka intraday data nahi deta. Hamare PC me **asli 1-min option price sirf 15 Sep 2026 - 20 Nov 2026** (kuch strike) hai.
- S1 (Rs 60 premium wala strike, SL premium pe) = **option ke 1-min price chahiye**. 2021-2026 poore ke liye sirf **model (Black-76, realized-vol IV)** se ban sakta hai (jaise 09_stbt_options me kiya) ya BSE/NSE EOD bhavcopy se day-level (9:50 ka price nahi).
- S2 ka SL **underlying** pe hai, to index 1-min se hota hai; sirf premium (entry / exit price) model se.
