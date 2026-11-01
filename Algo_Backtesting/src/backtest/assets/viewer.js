/* Trade viewer for backtest reports: filters + trade list (arrow keys) + meta + TradingView lightweight-charts + cost box.
   Generic: a trade is
     {id, date, title, sub, pnl, tag, meta:[[k,v,cls?]], cost:[[k,v,cls?,total?]],
      charts:[{title, legend:[[color,label]], kind:"candle"|"line", intraday:false, small:false,
               data:[{time,open,high,low,close}|{time,value}], markers:[{time,position,shape,color,text}],
               lines:[{price,color,title,style}]}]}
   Colours may be CSS variable names ("--pos"). Needs LightweightCharts (v4) on the page. */
(function () {
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const col = c => (typeof c === "string" && c.startsWith("--") ? css(c) : c);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const money = (v, plus) => (v < 0 ? "-" : plus && v > 0 ? "+" : "") + "Rs " + Math.round(Math.abs(v)).toLocaleString("en-IN");
  const sgn = v => (v > 0 ? "pos" : v < 0 ? "neg" : "");

  function TradeViewer(root, cfg) {
    cfg = cfg || {};
    const L = Object.assign({
      outcome: "Nateeja", all: "Sab", win: "Profit", loss: "Loss", exit: "Exit ka karan", allExit: "Saare", stock: "Stock khojo", from: "Se", to: "Tak",
      reset: "Reset", trades: "Trades", nav: "chalane ke liye", empty: "Koi trade nahi mila", emptyHint: "Filter badlo ya Reset dabao.", cost: "P&L ka hisaab",
      stockLabel: "Stock",
    }, cfg.labels || {});
    root.innerHTML = `
      <div class="tv-filters">
        <div class="tv-field"><label>${esc(L.stockLabel)}</label><input type="text" data-f="q" placeholder="${esc(L.stock)}"></div>
        <div class="tv-field"><label>${esc(L.outcome)}</label><select data-f="out"><option value="all">${esc(L.all)}</option><option value="win">${esc(L.win)}</option><option value="loss">${esc(L.loss)}</option></select></div>
        <div class="tv-field"><label>${esc(L.exit)}</label><select data-f="tag"></select></div>
        <div class="tv-field"><label>${esc(L.from)}</label><input type="date" data-f="from"></div>
        <div class="tv-field"><label>${esc(L.to)}</label><input type="date" data-f="to"></div>
        <button type="button" class="btn" data-f="reset">${esc(L.reset)}</button>
      </div>
      <div class="tv-ws">
        <aside class="tv-box tv-listbox">
          <div class="tv-head"><h2>${esc(L.trades)} <span class="tv-hint" data-r="count"></span></h2><span class="tv-hint"><kbd>&uarr;</kbd><kbd>&darr;</kbd> ${esc(L.nav)}</span></div>
          <div class="tv-list" data-r="list" tabindex="0" role="listbox" aria-label="${esc(L.trades)}"></div>
        </aside>
        <div class="tv-detail" data-r="detail"></div>
      </div>`;
    const $ = s => root.querySelector(s);
    const F = k => root.querySelector(`[data-f="${k}"]`);
    let all = [], view = [], sel = -1, charts = [];

    function applyFilters() {
      const q = F("q").value.trim().toLowerCase(), out = F("out").value, tag = F("tag").value, a = F("from").value, b = F("to").value;
      const keep = sel >= 0 && view[sel] ? view[sel].id : null;
      view = all.filter(t => (!q || t.title.toLowerCase().includes(q)) && (out === "all" || (out === "win" ? t.pnl > 0 : t.pnl <= 0))
        && (!tag || t.tag === tag) && (!a || t.date >= a) && (!b || t.date <= b));
      const i = keep === null ? -1 : view.findIndex(t => t.id === keep);
      renderList();
      select(i >= 0 ? i : (view.length ? 0 : -1), false);
    }

    function renderList() {
      const net = view.reduce((s, t) => s + t.pnl, 0);
      $('[data-r="count"]').innerHTML = `${view.length.toLocaleString("en-IN")} &middot; <span class="${sgn(net)}">${money(net, true)}</span>`;
      let last = "", h = "";
      view.forEach((t, i) => {
        if (t.date !== last) { last = t.date; h += `<div class="tv-day">${esc(t.date)}</div>`; }
        h += `<div class="tv-row" role="option" data-i="${i}" aria-selected="false"><span class="t">${esc(t.date.slice(5))}</span>`
          + `<span class="s">${esc(t.title)}</span><span class="p ${sgn(t.pnl)}">${money(t.pnl, true)}</span><span class="u">${esc(t.sub || "")}</span></div>`;
      });
      $('[data-r="list"]').innerHTML = h;
    }

    function killCharts() { charts.forEach(c => { try { c.chart.remove(); } catch (e) { /* already gone */ } }); charts = []; }

    function mkChart(el, spec) {
      const ch = LightweightCharts.createChart(el, {
        autoSize: true,
        layout: {background: {type: "solid", color: css("--card")}, textColor: css("--muted"), fontFamily: "inherit"},
        grid: {vertLines: {color: css("--grid")}, horzLines: {color: css("--grid")}},
        rightPriceScale: {borderColor: css("--line")},
        timeScale: {borderColor: css("--line"), timeVisible: !!spec.intraday, secondsVisible: false},
        crosshair: {mode: 0},
      });
      const lines = spec.lines || [];
      // fixed price range from the data and the lines, with room for the marker arrows (the default autoscale blows up on flat lines)
      let lo = Infinity, hi = -Infinity;
      spec.data.forEach(p => { lo = Math.min(lo, spec.kind === "candle" ? p.low : p.value); hi = Math.max(hi, spec.kind === "candle" ? p.high : p.value); });
      lines.forEach(l => { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); });
      const pad = Math.max(hi - lo, Math.abs(hi) * 0.01, 1e-6) * 0.22;
      const widen = isFinite(lo) ? () => ({priceRange: {minValue: lo - pad, maxValue: hi + pad}}) : undefined;
      const s = spec.kind === "candle"
        ? ch.addCandlestickSeries({upColor: css("--pos"), downColor: css("--neg"), borderVisible: false, wickUpColor: css("--pos"), wickDownColor: css("--neg"), autoscaleInfoProvider: widen})
        : ch.addLineSeries({color: css("--accent"), lineWidth: 2, autoscaleInfoProvider: widen});
      s.setData(spec.data);
      if (spec.markers && spec.markers.length) {
        s.setMarkers(spec.markers.map(m => ({time: m.time, position: m.position || "aboveBar", shape: m.shape || "circle", color: col(m.color), text: m.text || ""}))
          .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)));
      }
      lines.forEach(l => s.createPriceLine({price: l.price, color: col(l.color), lineWidth: 1, lineStyle: l.style === undefined ? 2 : l.style, axisLabelVisible: true, title: l.title || ""}));
      ch.timeScale().fitContent();
      charts.push({chart: ch, series: s});
    }

    function select(i, scroll) {
      sel = i;
      root.querySelectorAll(".tv-row").forEach(r => r.setAttribute("aria-selected", String(+r.dataset.i === i)));
      killCharts();
      const d = $('[data-r="detail"]');
      if (i < 0 || !view[i]) {
        d.innerHTML = `<div class="tv-box tv-empty"><strong>${esc(L.empty)}</strong>${esc(L.emptyHint)}</div>`;
        return;
      }
      const t = view[i];
      if (scroll !== false) { const r = root.querySelector(`.tv-row[data-i="${i}"]`); if (r) r.scrollIntoView({block: "nearest"}); }
      d.innerHTML = `<div class="tv-box tv-meta">${(t.meta || []).map(m => `<div><div class="k">${esc(m[0])}</div><div class="v ${m[2] || ""}">${esc(m[1])}</div></div>`).join("")}</div>`
        + `<div class="tv-box tv-chartbox">${(t.charts || []).map((c, n) => `<div class="tv-head${n ? " sub" : ""}"><h2>${esc(c.title)}</h2>`
          + `<div class="tv-legend">${(c.legend || []).map(g => `<span><i class="tv-dot" style="background:${esc(col(g[0]))}"></i>${esc(g[1])}</span>`).join("")}</div></div>`
          + `<div class="tv-chart${c.small ? " small" : ""}" data-c="${n}"></div>`).join("")}${t.note ? `<div class="tv-note">${esc(t.note)}</div>` : ""}</div>`
        + `<div class="tv-box tv-cost"><h2>${esc(L.cost)}</h2><div class="tv-costgrid">${(t.cost || []).map(c => `<div class="tv-cr${c[3] ? " total" : ""}"><span class="k">${esc(c[0])}</span><span class="${c[2] || ""}">${esc(c[1])}</span></div>`).join("")}</div></div>`;
      (t.charts || []).forEach((c, n) => mkChart(d.querySelector(`[data-c="${n}"]`), c));
    }

    root.addEventListener("click", e => { const r = e.target.closest(".tv-row"); if (r) select(+r.dataset.i, false); });
    $('[data-r="list"]').addEventListener("keydown", e => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const n = Math.max(0, Math.min(view.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1)));
      if (n !== sel) select(n);
    });
    root.querySelectorAll("[data-f]").forEach(el => {
      if (el.dataset.f === "reset") el.addEventListener("click", () => { ["q", "from", "to"].forEach(k => (F(k).value = "")); F("out").value = "all"; F("tag").value = ""; applyFilters(); });
      else el.addEventListener("input", applyFilters);
    });

    return {
      setTrades(trades) {
        all = trades;
        const tags = [...new Set(all.map(t => t.tag).filter(Boolean))].sort();
        const cur = F("tag").value;
        F("tag").innerHTML = `<option value="">${esc(L.allExit)}</option>` + tags.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
        if (tags.includes(cur)) F("tag").value = cur;
        const ds = all.map(t => t.date).sort();
        F("from").min = F("to").min = ds[0] || ""; F("from").max = F("to").max = ds[ds.length - 1] || "";
        applyFilters();
      },
      show() { select(sel, false); },      // call when the tab becomes visible (charts re-fit)
      restyle() { select(sel, false); },   // call after a theme change (colours are read again)
    };
  }

  // header stays on top: tabs / lists stick just below it (CSS uses --hdr-h)
  TradeViewer.stickyHeader = function (sel) {
    const h = document.querySelector(sel);
    if (!h) return;
    const set = () => document.documentElement.style.setProperty("--hdr-h", (getComputedStyle(h).position === "sticky" ? h.offsetHeight : 0) + "px");
    set();
    window.addEventListener("resize", set);
    if (window.ResizeObserver) new ResizeObserver(set).observe(h);
  };
  window.TradeViewer = TradeViewer;
})();
