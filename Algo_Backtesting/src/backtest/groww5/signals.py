"""Candidate trades of the 4 stock strategies for ONE day (scalp, brk, rev, news) + the NIFTY momentum strategy (momo).

A candidate = dict(strat, var, date, sym, side, sig, e, x, entry, exit, sl, tgt, reason, prio, info). sig / e / x = 1m index
of the day (0 = 09:15): signal candle (its close is known at its end), entry minute (fill at that minute's OPEN), exit minute.
Money (qty, slippage, charges, position limits) is applied later in money.py.
"""
import numpy as np
import pandas as pd

from backtest.groww5.data import N_MIN, OPEN, Grid, resample_grid

SQ = 915 - OPEN          # 15:15 square-off index


def ffill_grid(G: Grid) -> Grid:
    """Missing minutes -> flat candle at the previous close (leading NaNs stay NaN)."""
    c = pd.DataFrame(G.c.T).ffill().to_numpy().T
    miss = np.isnan(G.o) & ~np.isnan(c)
    o, h, l = G.o.copy(), G.h.copy(), G.l.copy()
    for a in (o, h, l):
        a[miss] = c[miss]
    v = np.where(np.isnan(G.v), 0.0, G.v)
    return Grid(G.syms, G.days, o, h, l, c, v)


def walk(o, h, l, e: int, side: int, sl: float, tgt: float | None, end: int):
    """First minute from e (entry at o[e]) to end-1 that hits SL / target; else exit at o[end] (time exit).
    Returns (exit index, price, reason). SL before target in the same minute; a gap through a level fills at the open."""
    seg_h, seg_l = h[e:end], l[e:end]
    hit_sl = seg_l <= sl if side > 0 else seg_h >= sl
    hit_t = np.zeros(len(seg_h), bool) if tgt is None else (seg_h >= tgt if side > 0 else seg_l <= tgt)
    ks = np.flatnonzero(hit_sl)
    kt = np.flatnonzero(hit_t)
    k1 = ks[0] if len(ks) else 10 ** 9
    k2 = kt[0] if len(kt) else 10 ** 9
    if k1 <= k2 and k1 < 10 ** 9:
        k = e + k1
        px = min(o[k], sl) if side > 0 else max(o[k], sl)
        return k, px, "SL"
    if k2 < 10 ** 9:
        k = e + k2
        px = max(o[k], tgt) if side > 0 else min(o[k], tgt)
        return k, px, "Target"
    end = min(end, N_MIN - 1)
    return end, o[end], "Time"


# ------------------------------------------------------------------------------------------------ 1) scalping
def psar(h: np.ndarray, l: np.ndarray, start=0.02, step=0.02, mx=0.2) -> np.ndarray:
    """Parabolic SAR per row (TradingView ta.sar logic), vectorised over stocks, restarted every day at 09:15."""
    S, T = h.shape
    out = np.full((S, T), np.nan)
    up = np.ones(S, bool)
    sar = l[:, 0].copy()
    ep = h[:, 0].copy()
    af = np.full(S, start)
    out[:, 0] = sar
    for t in range(1, T):
        nxt = sar + af * (ep - sar)
        lo2 = np.fmin(l[:, t - 1], l[:, t - 2]) if t >= 2 else l[:, t - 1]
        hi2 = np.fmax(h[:, t - 1], h[:, t - 2]) if t >= 2 else h[:, t - 1]
        nxt = np.where(up, np.fmin(nxt, lo2), np.fmax(nxt, hi2))
        rev_dn = up & (l[:, t] < nxt)
        rev_up = ~up & (h[:, t] > nxt)
        new_ep_up = up & ~rev_dn & (h[:, t] > ep)
        new_ep_dn = ~up & ~rev_up & (l[:, t] < ep)
        nxt = np.where(rev_dn | rev_up, ep, nxt)
        ep = np.where(rev_dn, l[:, t], np.where(rev_up, h[:, t], np.where(new_ep_up, h[:, t], np.where(new_ep_dn, l[:, t], ep))))
        af = np.where(rev_dn | rev_up, start, np.where(new_ep_up | new_ep_dn, np.minimum(af + step, mx), af))
        up = np.where(rev_dn, False, np.where(rev_up, True, up))
        bad = np.isnan(nxt)                      # stock without candles yet -> start fresh on its first candle
        nxt[bad], ep[bad], af[bad] = l[bad, t], h[bad, t], start
        up[bad] = True
        sar = nxt
        out[:, t] = sar
    return out


def scalp(G: Grid, P: np.ndarray, c: dict, var: str) -> list:
    o, h, l, cl = G.o, G.h, G.l, G.c
    n = c["n_red"]
    red, grn = cl < o, cl > o
    r3, g3 = red.copy(), grn.copy()
    for k in range(1, n):
        r3[:, k:] &= red[:, :-k]
        g3[:, k:] &= grn[:, :-k]
        r3[:, :k] = g3[:, :k] = False
    with np.errstate(all="ignore"):
        plow = np.fmin.accumulate(np.where(np.isnan(l), np.inf, l), axis=1)
        phigh = np.fmax.accumulate(np.where(np.isnan(h), -np.inf, h), axis=1)
    plow = np.concatenate([np.full((len(l), 1), np.nan), plow[:, :-1]], axis=1)
    phigh = np.concatenate([np.full((len(h), 1), np.nan), phigh[:, :-1]], axis=1)
    t = np.arange(N_MIN) + OPEN
    win = (t >= c["from"]) & (t <= c["to"])
    with np.errstate(invalid="ignore"):
        s_sig = r3 & (cl < plow) & win
        b_sig = g3 & (cl > phigh) & win
        if c["use_psar"]:
            s_sig &= P > cl
            b_sig &= P < cl
    out = []
    hold = c["hold"]
    for si in np.flatnonzero(s_sig.any(1) | b_sig.any(1)):
        busy, cnt = -1, 0
        for ti in np.flatnonzero(s_sig[si] | b_sig[si]):
            if ti < busy or cnt >= c["per_stock_day"]:
                continue
            side = -1 if s_sig[si, ti] else 1
            e = ti + 1
            if e + hold >= N_MIN:
                break
            entry = o[si, e]
            sl = np.nanmax(h[si, ti - n + 1: ti + 1]) if side < 0 else np.nanmin(l[si, ti - n + 1: ti + 1])
            if not np.isfinite(entry) or (entry - sl) * side <= 0:
                continue
            x, px, why = walk(o[si], h[si], l[si], e, side, sl, None, e + hold)
            if why == "Time":
                why = f"{hold} min"
            move = abs(cl[si, ti] / o[si, ti - n + 1] - 1)
            out.append(dict(strat="scalp", var=var, date=G.days[si], sym=G.syms[si], side=side, sig=ti, e=e, x=x, entry=entry, exit=px, sl=sl,
                            tgt=np.nan, reason=why, prio=-move, info={"psar": round(float(P[si, ti]), 2)}))
            busy, cnt = x, cnt + 1
    return out


# ------------------------------------------------------------------------------------------------ 3/4) zones
def pivots(H, L, k):
    """Indices of daily swing highs / lows: the bar's high is the max (low the min) of the 2k+1 bars around it."""
    n = len(H)
    if n < 2 * k + 1:
        return np.array([], int), np.array([], int)
    wh = np.lib.stride_tricks.sliding_window_view(H, 2 * k + 1)
    wl = np.lib.stride_tricks.sliding_window_view(L, 2 * k + 1)
    ph = np.flatnonzero((wh[:, k] >= wh.max(1)) & (np.argmax(wh, 1) == k)) + k
    pl = np.flatnonzero((wl[:, k] <= wl.min(1)) & (np.argmin(wl, 1) == k)) + k
    return ph, pl


def clusters(prices: np.ndarray, span_pct: float, min_half_pct: float):
    """Greedy 1-D clusters: sorted prices, a zone is at most span_pct wide. Returns list of (lo, hi, touches)."""
    p = np.sort(prices)
    out, i = [], 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and p[j + 1] <= p[i] * (1 + span_pct / 100):
            j += 1
        grp = p[i: j + 1]
        cen = grp.mean()
        out.append((min(grp[0], cen * (1 - min_half_pct / 100)), max(grp[-1], cen * (1 + min_half_pct / 100)), len(grp)))
        i = j + 1
    return out


def zone_table(Z: pd.DataFrame, zc: dict, start: str) -> pd.DataFrame:
    """Per stock-day from `start`: nearest resistance zone above / support zone below the previous close (pivots
    confirmed before the day, last `lookback` days)."""
    k, lb = zc["pivot_k"], zc["lookback"]
    rows = []
    for sym, g in Z.groupby("symbol", sort=False):
        H, L, C = g["high"].to_numpy(), g["low"].to_numpy(), g["close"].to_numpy()
        dates = g["date"].to_numpy()
        ph, pl = pivots(H, L, k)
        piv_i = np.r_[ph, pl]
        piv_p = np.r_[H[ph], L[pl]]
        order = np.argsort(piv_i)
        piv_i, piv_p = piv_i[order], piv_p[order]
        for j in np.flatnonzero(dates >= start):
            if j == 0:
                continue
            m = (piv_i + k <= j - 1) & (piv_i >= j - lb)
            if not m.any():
                continue
            pc = C[j - 1]
            cl = clusters(piv_p[m], zc["span_pct"], zc["min_half_pct"])
            res = [z for z in cl if z[0] > pc]
            sup = [z for z in cl if z[1] < pc]
            r = min(res, key=lambda z: z[0]) if res else (np.nan, np.nan, 0)
            s = max(sup, key=lambda z: z[1]) if sup else (np.nan, np.nan, 0)
            rows.append((dates[j], sym, pc, r[0], r[1], r[2], s[0], s[1], s[2]))
    return pd.DataFrame(rows, columns=["date", "symbol", "prev_close", "r_lo", "r_hi", "r_n", "s_lo", "s_hi", "s_n"])


def zones(G: Grid, zt: pd.DataFrame, zc: dict, var: str) -> list:
    """Reversal (touch 2-3) and breakout (touch >= 5) candidates from 15-min (or zc tf) closes around today's zones."""
    if zt is None or not len(zt):
        return []
    m = zc["tf"]
    O, Hh, Ll, Cc = resample_grid(G, m)
    nb = O.shape[1]
    close_t = OPEN + m * (np.arange(nb) + 1)
    okb = (close_t >= zc["from"]) & (close_t <= zc["to"])
    idx = {d: i for i, d in enumerate(G.days)}
    rv_lo, rv_hi = zc["rev_touch"]
    out = []
    for r in zt.itertuples(index=False):
        si = idx.get(r.date)
        if si is None:
            continue
        o, h, l, c = O[si], Hh[si], Ll[si], Cc[si]
        if np.isnan(c).all():
            continue
        dh, dl = np.nanmax(G.h[si]), np.nanmin(G.l[si])
        cands = []
        for kind, lo, hi, n in (("R", r.r_lo, r.r_hi, r.r_n), ("S", r.s_lo, r.s_hi, r.s_n)):
            if not n or not np.isfinite(lo):
                continue
            if kind == "R" and dh < lo or kind == "S" and dl > hi:
                continue
            if zc["use_touch"]:
                is_rev, is_brk = rv_lo <= n <= rv_hi, n >= zc["brk_touch"]
            else:
                is_rev = is_brk = n >= 2
            info = {"zone": kind, "lo": round(float(lo), 2), "hi": round(float(hi), 2), "touch": int(n)}
            if is_rev:
                broken = False
                for b in range(nb):
                    if not np.isfinite(c[b]):
                        continue
                    if kind == "R":
                        if c[b] > hi:
                            broken = True
                        if okb[b] and not broken and h[b] >= lo and c[b] < lo and c[b] < o[b]:
                            sl = max(hi, np.nanmax(h[: b + 1]))
                            cands.append(("rev", -1, b, sl, info))
                            break
                    else:
                        if c[b] < lo:
                            broken = True
                        if okb[b] and not broken and l[b] <= hi and c[b] > hi and c[b] > o[b]:
                            sl = min(lo, np.nanmin(l[: b + 1]))
                            cands.append(("rev", 1, b, sl, info))
                            break
            if is_brk:
                side = 1 if kind == "R" else -1
                edge, other = (hi, lo) if kind == "R" else (lo, hi)
                b0 = None
                for b in range(nb):
                    if not np.isfinite(c[b]):
                        continue
                    crossed = c[b] > edge if side > 0 else c[b] < edge
                    if b0 is None:
                        if crossed:
                            b0 = b
                            if zc["brk_mode"] == "direct":
                                if okb[b]:
                                    cands.append(("brk", side, b, other, info))
                                break
                    else:      # pullback mode: back to the edge, close holds beyond it; a close back inside the other side cancels
                        if (c[b] < other) if side > 0 else (c[b] > other):
                            break
                        touched = l[b] <= edge if side > 0 else h[b] >= edge
                        if okb[b] and touched and crossed:
                            cands.append(("brk", side, b, other, {**info, "brk_bar": int(b0)}))
                            break
        for strat in ("rev", "brk"):
            cs = sorted([x for x in cands if x[0] == strat], key=lambda x: x[2])
            if not cs:
                continue
            _, side, b, sl, info = cs[0]
            e = (b + 1) * m
            if e >= SQ:
                continue
            entry = G.o[si, e]
            risk = (entry - sl) * side
            if not np.isfinite(entry) or risk <= 0:
                continue
            tgt = entry + side * zc["rr"] * risk
            x, px, why = walk(G.o[si], G.h[si], G.l[si], e, side, sl, tgt, SQ)
            if why == "Time":
                why = "15:15"
            out.append(dict(strat=strat, var=var, date=r.date, sym=r.symbol, side=side, sig=(b + 1) * m - 1, e=e, x=x, entry=entry, exit=px,
                            sl=sl, tgt=tgt, reason=why, prio=-int(info["touch"]), info=info))
    return out


# ------------------------------------------------------------------------------------------------ 5) news (gap proxy)
def news(G: Grid, pday: pd.DataFrame, nc: dict, var: str) -> list:
    """pday: panel rows (date, symbol, gap_pct, o5_open/high/low/close) of this grid's stock."""
    idx = {d: i for i, d in enumerate(G.days)}
    out = []
    g = pday[pday["gap_pct"].abs() >= nc["gap_pct"]]
    lim = nc["until"] - OPEN
    for r in g.itertuples(index=False):
        si = idx.get(r.date)
        if si is None or not np.isfinite(r.o5_low):
            continue
        side = -1 if r.gap_pct > 0 else 1
        red = r.o5_close < r.o5_open
        if nc["need_color"] and ((side < 0 and not red) or (side > 0 and (red or r.o5_close == r.o5_open))):
            continue
        trig, sl = (r.o5_low, r.o5_high) if side < 0 else (r.o5_high, r.o5_low)
        o, h, l = G.o[si], G.h[si], G.l[si]
        seg = l[5:lim + 1] < trig if side < 0 else h[5:lim + 1] > trig
        ks = np.flatnonzero(seg)
        if not len(ks):
            continue
        e = 5 + ks[0]
        entry = min(o[e], trig) if side < 0 else max(o[e], trig)
        if (entry - sl) * side <= 0:
            continue
        if e + 1 >= SQ:
            continue
        x, px, why = walk(o, h, l, e + 1, side, sl, None, SQ)        # stop checked from the minute after the entry minute
        if why == "Time":
            why = "15:15"
        out.append(dict(strat="news", var=var, date=r.date, sym=r.symbol, side=side, sig=4, e=e, x=x, entry=entry, exit=px, sl=sl, tgt=np.nan,
                        reason=why, prio=-abs(r.gap_pct), info={"gap": round(float(r.gap_pct), 2), "o5": [r.o5_open, r.o5_high, r.o5_low, r.o5_close]}))
    return out


# ------------------------------------------------------------------------------------------------ 2) momentum (NIFTY futures)
def momo(F, mc: dict, var: str) -> list:
    """5-min swings on NIFTY near-month futures (built from 1m, 09:15-anchored), staircase + pullback entries, trailing swing stop."""
    o1, h1, l1 = F.o, F.h, F.l
    minute, di = F.minute, F.di
    bucket = di.astype(np.int64) * 100 + (minute - OPEN) // 5
    starts = np.r_[0, np.flatnonzero(np.diff(bucket)) + 1]
    ends = np.r_[starts[1:], len(bucket)] - 1                   # last 1m index of each 5m candle
    H5 = np.maximum.reduceat(h1, starts)
    L5 = np.minimum.reduceat(l1, starts)
    C5 = F.c[ends]
    k = mc["k"]
    n5 = len(H5)
    is_sh = np.zeros(n5, bool)
    is_sl = np.zeros(n5, bool)
    for i in range(k, n5 - k):
        w = H5[i - k: i + k + 1]
        if H5[i] == w.max() and np.argmax(w) == k:
            is_sh[i] = True
        w = L5[i - k: i + k + 1]
        if L5[i] == w.min() and np.argmin(w) == k:
            is_sl[i] = True
    end_of = {int(ends[j]): j for j in range(n5)}
    sh, sl_ = [], []                 # confirmed swings: (5m index, price)
    pos = None
    trades = []
    day_cnt = {}
    pending = None
    sides = mc["sides"]
    n1 = len(o1)
    for m in range(n1):
        mt = minute[m]
        if pending is not None:
            side, stop, sig_j = pending
            pending = None
            if di[m] == di[sig_j_end[0]] and mt < 915 and (o1[m] - stop) * side > 0:
                pos = dict(side=side, e=m, entry=o1[m], sl0=stop, stop=stop, sig=sig_j_end[0])
                day_cnt[di[m]] = day_cnt.get(di[m], 0) + 1
        if pos is not None:
            side = pos["side"]
            why = None
            if di[m] != di[pos["e"]]:                        # should not happen (15:15 exit), safety
                why, px = "15:15", o1[m]
            elif mt >= 915:
                why, px = "15:15", o1[m]
            elif (l1[m] <= pos["stop"]) if side > 0 else (h1[m] >= pos["stop"]):
                px = min(o1[m], pos["stop"]) if side > 0 else max(o1[m], pos["stop"])
                if m == pos["e"]:
                    px = pos["stop"]
                why = "SL" if pos["stop"] == pos["sl0"] else "Trail SL"
            if why:
                trades.append(dict(pos, x=m, exit=px, reason=why))
                pos = None
        j = end_of.get(m)
        if j is None:
            continue
        i = j - k                                             # swing candle confirmed by this close
        new_low = new_high = False
        if i >= k and is_sh[i]:
            sh.append((i, H5[i]))
            new_high = True
        if i >= k and is_sl[i]:
            sl_.append((i, L5[i]))
            new_low = True
        if pos is not None:                                    # trail to the newest swing in the trade's favour
            if pos["side"] > 0 and new_low and sl_[-1][1] > pos["stop"]:
                pos["stop"] = sl_[-1][1]
            if pos["side"] < 0 and new_high and sh[-1][1] < pos["stop"]:
                pos["stop"] = sh[-1][1]
            continue
        if not (mc["from"] <= minute[starts[j]] + 5 <= mc["to"]) or day_cnt.get(di[m], 0) >= mc["per_day"] or m + 1 >= n1:
            continue
        if new_low and sides in ("both", "long") and len(sh) >= 2 and len(sl_) >= 2:
            (i1, h1_), (i2, h2_) = sh[-2], sh[-1]
            (j1, l1_), (j2, l2_) = sl_[-2], sl_[-1]
            if h2_ > h1_ and l2_ > l1_ and i2 < j2 and C5[j] > l2_:
                pending = (1, l2_, m)
                sig_j_end = [m]
        if pending is None and new_high and sides in ("both", "short") and len(sh) >= 2 and len(sl_) >= 2:
            (i1, h1_), (i2, h2_) = sh[-2], sh[-1]
            (j1, l1_), (j2, l2_) = sl_[-2], sl_[-1]
            if h2_ < h1_ and l2_ < l1_ and j2 < i2 and C5[j] < h2_:
                pending = (-1, h2_, m)
                sig_j_end = [m]
    out = []
    for t in trades:
        d = str(F.days[di[t["e"]]])
        out.append(dict(strat="momo", var=var, date=d, sym="NIFTY FUT", side=t["side"], sig=int(minute[t["sig"]] - OPEN), e=int(minute[t["e"]] - OPEN),
                        x=int(minute[t["x"]] - OPEN), entry=t["entry"], exit=t["exit"], sl=t["sl0"], tgt=np.nan, reason=t["reason"], prio=0,
                        info={"m_e": int(t["e"]), "m_x": int(t["x"]), "off": float(F.off[t["e"]]), "trail": round(float(t["stop"]), 1)}))
    return out
