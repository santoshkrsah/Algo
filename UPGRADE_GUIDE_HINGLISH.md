# Upgrade guide (Hinglish): fast data + daily update + rulebook strategies

Ye guide batati hai is upgrade me kya badla, ek baar ka setup, roz ka kaam, aur Claude Code ke liye copy-paste prompts.
FYERS login/download wala code wahi purana hai (auth.py, download_fno.fetch_chunk) - naya code sirf usko reuse karta hai.

## 1. Review me kya mila (short)

Achha hai: data aur backtest alag, resumable downloader, secrets ka dhyan (check_secrets hook), numbered results + website + Telegram,
ORB v4 me point-in-time F&O/MIS list (NSE price bands), SL-first rule, TRAIN/TEST split. Ye sab professional level hai.

Problems (sabse bade pehle):
1. **Speed**: har backtest saari ~1,265 daily CSV files (har file ~45 MB, saare 5 timeframes) dobara padhta hai. Sirf padhne me ~8-20 min lagte hain, har run aur har variant me.
2. **Naya data**: `download_equity.py` me START/END fixed hai - naye din ke liye 5 saal dobara (~27,000 requests, ~3 ghante).
3. **Data quality sirf ek strategy me fix**: gaprsi me 13 unadjusted split/bonus aur 12 bad prints mile (jaise 2023-09-21 ko kai stocks), baaki engines (Fib R3 kal ke H/L/C use karta hai) ko woh kharab din abhi bhi dikhte hain.
4. **Survivorship bias**: stock list AMFI Jun-2026 (>= Rs 1,000 Cr). 2021-26 me delist/merge hue (HDFC Ltd, Jul 2023) ya chhote ho gaye stocks data me hain hi nahi. Long-only small-cap results sabse zyada phoole hue honge.
5. **Overfitting risk**: 13_gap_rsi_ema me 48 variants try hue aur best wala "profit" dikha (PF 1.7, 242 trades). Jab tak 2024+ par ek-baar test na ho, isse edge mat maano.
6. **Slippage**: sab stocks par flat 0.3-0.5% har fill - liquid F&O stocks ke liye bahut zyada, illiquid small caps ke liye theek. Turnover ke hisaab se rakho.
7. Fib R3 test me Rs 2 Cr per trade small caps me - itna paisa un stocks me fill hi nahi hoga.
8. Chhote bugs (fix kar diye): `Algo_Backtesting/requirements.txt` me Q&A text tha (pip install fail hota) -> ab `strategies/FIB_R3_QA.md`; `.env` ka `HISTORICAL_DATA_DIR` kabhi load hi nahi hota tha (config.py).

## 2. Kya badla

| File | Kaam |
|---|---|
| `Algo_Historical_Data/src/fyers_data/store.py` (naya) | CSV -> Parquet store: `day/` (har din), `sym/` (har stock), `daily.parquet` (har stock-din ki ek row), `verify`, `health`, `selftest` |
| `Algo_Historical_Data/src/fyers_data/update_equity.py` (naya) | Sirf missing din download (1 request/stock), split check, phir store build |
| `Algo_Backtesting/src/backtest/fast.py` (naya) | Store se tez padhna; store na ho to CSV se (kuch nahi tootega) |
| 7 engines (`orb, vol5l, cpr, vmu, engine, gaprsi_data, run_gap_volume_backtest`) + `data.py` | `pd.read_csv(day)` ki jagah `fast.read_day(day, ...)` - **result bilkul same** (test kiya) |
| `Algo_Backtesting/src/backtest/rulebook.py` + `tools/run_rulebook.py` (naye) | 4 rulebook strategies, aapke project style me (result_dir, Telegram, run log, DEV/FINAL guard) |
| `Algo_Backtesting/strategies/RULEBOOK.md` (naya) | Rules ka exact spec |
| `CLAUDE.md`, dono README, dono `pyproject.toml` | Naye commands + rules, `pyarrow` dependency |

Kaise test kiya: aapke hi downloader functions (`with_timeframes / write_parts / stage2`) se synthetic data (20 stocks, Oct 2021 - Mar 2024) banaya, phir:
store vs CSV saare 636 din x 5 timeframes exact same; 7 engines + `load_equity` purane code vs naye code ke results identical; rulebook CSV vs store vs parallel same;
updater ka resume + split case. Parquet khud (pyarrow) yahan install nahi ho saka, isliye aapke PC par pehla step `store selftest` hai.

## 3. Ek baar ka setup (~30 min, zyada time store build ka)

```powershell
# repo root par (patch file mili ho to) - pehle check, phir apply
git apply --check algo_upgrade.patch
git apply algo_upgrade.patch

# dono projects ke venv me
cd Algo_Historical_Data; .venv\Scripts\activate; pip install -r requirements.txt   # pyarrow aa jayega
python -m fyers_data.store selftest        # "SELFTEST OK" aana chahiye
python -m fyers_data.store build --notify  # pehli baar ~10-20 min (disk: lagbhag 15-25 GB extra, `info` asli size batayega)
python -m fyers_data.store verify --days 5 # har line "OK", last line "verify: OK"
python -m fyers_data.store health          # data/store/health/bad_days.csv dekho; NSE split match ho to: health --apply
python -m fyers_data.update_equity --nifty # 29 Oct se aaj tak ke din (~10 min)
deactivate
cd ..\Algo_Backtesting; .venv\Scripts\activate; pip install -r requirements.txt
```
Store build ke time backtest mat chalao (files replace hoti hain). CSVs source of truth hain - store kabhi bhi delete karke dobara `build` ho sakta hai.

## 4. Roz / hafte ka kaam
- Market band hone ke baad (4 pm ke baad): `python -m fyers_data.update_equity --nifty` (Algo_Historical_Data me). Kisi aur download ke saath parallel nahi.
- Backtest: jaise pehle. Purane runners ab apne aap store se padhte hain.

## 5. Rulebook strategies ka flow (imaandar tarika)
1. DEV (2021-10 -> 2023-12, 2023 = validation): `python tools/run_rulebook.py --strategy orb` (phir `noise`, `vwap`, `fh`).
2. Jo strategy DEV me theek lage, uspar chhota grid (sirf DEV): `--grid "stop_atr=0.1,0.25,0.5;rvol_min=1,1.5,2"`. Woh setting chuno jiske aas-paas wali settings bhi achhi hon (plateau) aur 2023 bhi positive ho.
3. FINAL (2024 -> aaj), **ek hi baar**: `python tools/run_rulebook.py --strategy orb --final --stop-atr 0.25 --rvol-min 1.5`. Runner same setting ka doosra FINAL mana karta hai.
4. FINAL PASS -> 1-2 mahine paper trading (roz `update_equity` + same rules), phir chhote size se live. FAIL -> us setting ko chhodo, naya idea DEV par.
Har run `results/RULEBOOK_RUNS.csv` me jaata hai (kharab wala bhi) - kitne variants try hue, ye hamesha team ko batana.
Speed: prefilter illiquid stocks ko load hi nahi karta (result same), `--workers` (default cores-1) processes chalata hai.

## 6. Claude Code prompts (VS Code me copy-paste)

**Prompt 1 - setup check**
> UPGRADE_GUIDE_HINGLISH.md padho. Algo_Historical_Data me `store selftest`, `store build --notify`, `store verify --days 5`, `store health` chalao (status heartbeat on). health ki bad_days.csv ka summary Telegram par bhejo; NSE split se match wale adjustments dikhao, apply karne se pehle mujhse poochho.

**Prompt 2 - rulebook HTML report (ek baar)**
> `src/backtest/rulebook_report.py` banao: `write_html(out_dir, data) -> Path`. `data` = `report_data.json` (run_rulebook.py likhta hai) + out_dir ki trades.csv / equity.csv / yearly.csv / breakdown.csv. CLAUDE.md ke report rules follow karo: opt_r3_template.html wala new look (viewer.py), "Test ki settings" chips = data["settings"], Rules tab = data["rules"], Formulas card = data["formulas"] (dono me "Copy karo"), compounding = haan, year/month tables me Return %, investment card = data["investment"], pass/fail checks, build vs validation/FINAL vs stress. Trade viewer me 1-minute candles `fast.day_1m(date, [symbol])` se, entry/exit/SL markers. Phir `python tools/run_rulebook.py --strategy orb` chala ke report check karo aur website update karo.

**Prompt 3 - chaaron strategies DEV par**
> `tools/run_rulebook.py` se orb, noise, vwap, fh chaaron DEV par chalao (defaults). Ek table do: build vs 2023 validation Sharpe, return, max DD, trades, PF, stress return, kaunse checks fail. Koi parameter mat badlo; sirf batao kaunsi strategy aage grid ke layak hai aur kyun.

**Prompt 4 - grid (sirf DEV)**
> [strategy] ke liye chhota DEV grid chalao (max 9-12 settings, RULEBOOK.md ke hisaab se sensible range). grid.csv se heatmap/table banao, plateau wali setting suggest karo. FINAL abhi mat chalao. RULEBOOK_RUNS.csv se batao kitne variants ho chuke.

**Prompt 5 - FINAL (mere bolne par hi)**
> [strategy] ko setting [...] ke saath `--final` se ek baar chalao. Result jo bhi ho, parameters mat badlo. PASS/FAIL aur agla step (paper trading ya drop) seedha batao.

**Prompt 6 - purani strategies tez + saaf**
> vol5l aur orb (VMU ORB V4) ko panel-first banao: `fast.panel()` + `fast.add_features()` se har din ke candidates chuno, phir sirf unke minutes `fast.day_1m(day, symbols)` se lo. Purane vs naye results identical hone chahiye (diff test likho). Phir gaprsi_data.build_daily ko `fast.panel()` par shift karo.

**Prompt 7 - swing results ka imaandar check**
> 13_gap_rsi_ema aur 14_gap_rsi_ema_smallcap: rules/settings sirf 2021-10 -> 2023-12 par chuno, phir 2024 -> aaj par ek baar test karo. Kitne variants try hue the woh report me likho. Small cap wale me survivorship bias ka note do. Slippage turnover ke hisaab se rakho (liquid kam, illiquid zyada).

## 7. Yaad rakhna
- Backtest ka profit future ki guarantee nahi; FINAL ek hi baar, costs/slippage kabhi kam nahi.
- Store ka disk size `python -m fyers_data.store info` batayega. Disk tight ho to verify OK hone ke baad purani CSVs external drive par archive kar sakte ho (store aur updater unke bina chal jaate hain), delete mat karo.
- Educational tool hai, investment advice nahi.
