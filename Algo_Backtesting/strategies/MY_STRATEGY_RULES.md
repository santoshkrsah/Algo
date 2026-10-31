# My strategy rules (from Ayush / Almax). Items marked CONFIRM are waiting for an answer.

Market: NSE equity (cash), intraday, signal timeframe 15m.

## 1. Entry
- Pivot type: Fibonacci pivots from the PREVIOUS day's High, Low, Close (CONFIRM)
  - P = (H+L+C)/3, R3 = P + 1.000 x (H-L)
- Signal: the FIRST 15m candle of the day (09:15-09:30) CLOSES ABOVE R3 -> BUY
- Entry price: close of that first candle (buy rate), i.e. bought at 09:30 (CONFIRM)
- Short side: NONE unless confirmed (mirror would be first candle closing below S3) (CONFIRM)

## 2. Exit
- Stop-loss: 3% below the buy rate
- Target: none by price; exit at 15:00 (square off), or earlier if the stock is about to hit upper circuit (CONFIRM how to detect)
- Trailing stop-loss: NONE

## 3. Filters
- None given (CONFIRM: any minimum price / volume filter?)

## 4. Stock selection (max 5 stocks per day)
- Fill order: small cap first, then mid cap, then large cap
- Inside a group, when more stocks qualify than slots remain: pick by (CONFIRM)

## 5. Money per trade
- Rs 2 cr per trade (buy value)
- Starting capital and leverage: not limited by the engine; total used is up to 5 x Rs 2 cr = Rs 10 cr per day
- Volume cap: (CONFIRM) limit each trade to a share of the first candle's volume

## 6. Costs
- STANDARD Fyers/NSE intraday rates (brokerage, STT, exchange, GST, stamp, SEBI) + slippage

## 7. Test periods
- Run A: October 2026
- Run B: January 2026 to October 2026
