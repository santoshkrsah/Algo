# Angel One (SmartAPI) - live trading

Files (VM par ye teeno + credentials ek hi folder me rakho, sirf python, kuch install nahi):
- AngelOne_Credentials.txt - login / API key / static IP / Telegram (git me nahi jaati)
- AngelOne_Config.py, AngelOne_Test.py - read-only check (login, profile, funds, positions)
- AngelOne_Test_Order.py - ek chhota test order + cancel + Telegram
- AngelOne_Straddle_Live_Nifty_Sensex.py - report 10 "Straddle + Risk Layer" ka LIVE trading (rules file ke upar docstring me)

Straddle live chalane ka tareeka (AWS VM par, 9:15 se pehle):
  py AngelOne_Straddle_Live_Nifty_Sensex.py --lots 1                       sirf plan dikhata hai
  py AngelOne_Straddle_Live_Nifty_Sensex.py --lots 1 --replay 2026-11-20   purane din par same rules (koi order nahi)
  py AngelOne_Straddle_Live_Nifty_Sensex.py --lots 1 --live                asli orders (YES likhna padega)
  py AngelOne_Straddle_Live_Nifty_Sensex.py --index SENSEX --lots 1 --live   SENSEX (doosre terminal me, NIFTY ke ~1 minute baad)
  py AngelOne_Straddle_Live_Nifty_Sensex.py --exit-now                        dono index ki khuli position band (ya --index SENSEX)
