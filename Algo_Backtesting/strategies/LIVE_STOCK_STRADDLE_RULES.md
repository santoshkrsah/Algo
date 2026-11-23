Live trade : Rule No 1
**Stock selection**
- Watchlist : fixed 10 stocks (backtest wale): RELIANCE, SBIN, HDFCBANK, ICICIBANK, TATASTEEL, MARUTI, APOLLOHOSP, ITC, BSE, LT (har stock ka apna straddle, apna alag hisaab)
- Instrument : us stock ka Option (CE + PE), sabse paas wali expiry (stock options monthly; expiry wale din bhi wahi expiry)
- Min price range : koi nahi (sirf stock F&O me ho)
- Min avg volume / liquidity filter : ATM CE aur PE dono me bid-ask tight aur volume ho, nahi to us din us stock ko skip (Telegram pe batao)
- Min volatility (ATR % ya gap %) : koi filter nahi (gap / VIX pause band hai, roz trade)

**Timing**
- Timeframe: 1m (stop ke liye 1-min candle close dekhna)
- Market open: 09:15
- Entry window: sirf 09:20 (ek hi entry)
- No new trade after: 09:20 (averaging / dobara entry / tactical buy band)
- Square-off time: 15:15

**Entry**
- Indicator + period : koi indicator nahi, time based
- Condition : 09:20 pe stock ke price ke sabse paas wala strike (ATM) lo, usi strike ka CE aur PE dono sell karo
- Side : short straddle (CE sell + PE sell, same strike, same lots)

**Exit**
- Stop loss (leg SL): kisi leg ka premium entry se 75% upar (>= 1.75 x entry), 2 lagatar 1-min close -> sirf wo leg buy karke band. Dusra leg chalta rahe. Band leg dobara nahi becha jaata
- Target: fixed target nahi (trailing + 15:15 exit se band hota hai)
- Trailing: trigger = profit becha hua premium (Rs) ka 50% ; uske baad profit apne peak ka aadha reh jaye (2 lagatar 1-min close) -> sab band
- Day loss exit: din ka P&L (band leg + khule leg) capital ke -1.5% par ya neeche, 2 lagatar 1-min close -> sab band
- Partial exit: nahi (50% quantity wala rule nahi)
- Time exit: 15:15 pe sab close

**Risk / sizing**
- Total capital aur leverage : Rs 30 lakh capital, usme se sirf 1/3 (Rs 10 lakh) margin me, baaki 2/3 buffer
- Risk per trade: direct % nahi, qty margin se nikalti hai; worst din ka limit = capital ka 1.5% (Rs 45,000)
- Quantity = lots x lot size ; lots = floor( (capital x 1/3) ÷ (10% x lot size x stock price) ), kam se kam 1 lot
  (example: 10,00,000 ÷ (10% x 500 x 1,300) = 15 lots)
- Compounding : nahi, capital hamesha starting capital maano
- Max open positions : har stock ka ek straddle (CE+PE), ek time pe sab 10 stocks tak, har ek apne alag capital se
- Max trades per stock per day : 1 (leg SL ke baad band leg dobara nahi bechna)
- Max daily loss : 1.5% capital (kill switch), hit hone pe us stock ka din ka trading band

**Execution**
- Product: MIS (Intraday)
- Order type: Limit (LTP ke aas-paas) entry me; stop / exit ke liye bot candle close pe check karke order bhejta hai (exchange pe resting SL-M nahi, kyunki rule "2 lagatar 1-min close" hai)
- Slippage + brokerage/STT cost ka buffer : option premium ka 0.5% har fill + brokerage Rs 20/order (Angel One) + STT (option sell side) + exchange charge ~0.035% + SEBI + stamp + GST 18%
