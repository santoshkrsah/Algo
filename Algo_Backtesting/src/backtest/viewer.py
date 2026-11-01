"""New report look (sticky header, navy theme, trade viewer with charts). A template only has to contain these placeholders:

    <style>__VIEWER_CSS__</style>            after the old CSS
    <script>__LWC__</script>                 TradingView lightweight-charts v4 (Apache 2.0)
    <script>__VIEWER_JS__</script>           TradeViewer(root, cfg) + TradeViewer.stickyHeader(selector)

and call `viewer.apply(template)` before filling __DATA__. See assets/opt_r3_template.html for a full example.
"""
from backtest.report import ASSETS


def apply(tpl: str) -> str:
    return (tpl.replace("__VIEWER_CSS__", (ASSETS / "viewer.css").read_text(encoding="utf-8"))
               .replace("__LWC__", (ASSETS / "lightweight-charts.js").read_text(encoding="utf-8"))
               .replace("__VIEWER_JS__", (ASSETS / "viewer.js").read_text(encoding="utf-8")))
