# -*- coding: utf-8 -*-
"""
GitHub Actions から日次実行する、commentary.json のベースライン自動更新スクリプト。

このスクリプトは Claude による手動の詳細分析を「補完」するものであり、置き換えるものではない。
PCが閉じていてClaude側の朝の更新が行われなかった日でも、最低限の機械的なテーマ強弱サマリーが
commentary.json に反映されるようにするための、数値ベースの簡易版フォールバックである。

AIによる文章生成は行わず、あらかじめ用意したテンプレート文にテーマ名・数値を機械的に
埋め込んでいるだけである点に注意（本サイト全体の設計方針と同じく、機械的な処理に限定している）。

上書き判定ロジック:
  - 既存の commentary.json の "date" が今日の日付で、かつ "generated_by" が
    "github-actions" でない（= Claude が生成した、または generated_by フィールド自体が
    無い従来形式のファイル）場合は、Claude の手動更新を上書きしないよう、何もせず終了する。
  - それ以外（今日の分がまだ無い、前日以前のまま、または既に github-actions 版がある）
    場合は、このスクリプトで生成した簡易版で上書きする。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMENTARY_PATH = os.path.join(REPO_ROOT, "commentary.json")

JST = timezone(timedelta(hours=9))

# app.py の THEME_STOCKS と同じ内容（62テーマ）。app.py 自体は streamlit に依存しており
# このスクリプト単体では import できないため、同じ辞書をここに独立してコピーしている。
# app.py側でテーマを追加・変更した場合は、こちらも合わせて更新すること。
THEME_STOCKS = {
    "半導体": ["NVDA", "AMD", "TSM", "AVGO", "ASML"],
    "メモリー": ["MU", "WDC", "STX"],
    "量子コンピューティング": ["IONQ", "RGTI", "QBTS", "IBM"],
    "光/フォトニクス": ["COHR", "LITE", "AAOI", "CIEN"],
    "AIインフラ/データセンター": ["SMCI", "DELL", "VRT", "EQIX", "DLR"],
    "ソフトウェア/SaaS": ["MSFT", "CRM", "NOW", "ADBE", "ORCL"],
    "サイバーセキュリティ": ["CRWD", "PANW", "FTNT", "ZS"],
    "バイオテック": ["VRTX", "REGN", "AMGN", "BIIB"],
    "医療機器": ["MDT", "ISRG", "BSX", "SYK"],
    "デジタルヘルス": ["TDOC", "DOCS", "HIMS"],
    "製薬大手": ["LLY", "JNJ", "PFE", "MRK", "ABBV"],
    "肥満症治療薬(GLP-1)": ["LLY", "NVO", "VKTX"],
    "大手銀行": ["JPM", "BAC", "WFC", "C"],
    "フィンテック": ["SQ", "PYPL", "SOFI", "AFRM"],
    "保険": ["PGR", "AIG", "MET", "TRV"],
    "暗号資産関連株": ["COIN", "MSTR", "MARA", "RIOT"],
    "Eコマース": ["AMZN", "SHOP", "MELI"],
    "外食": ["MCD", "SBUX", "CMG", "YUM"],
    "アパレル/小売": ["NKE", "LULU", "TJX"],
    "自動車/EV": ["TSLA", "RIVN", "GM", "F"],
    "石油ガス": ["XOM", "CVX", "COP"],
    "再生可能エネルギー": ["FSLR", "ENPH"],
    "電池材料/リチウム": ["ALB", "SQM"],
    "貴金属/鉱業": ["NEM", "FCX", "GOLD"],
    "防衛/航空宇宙": ["LMT", "RTX", "NOC", "BA"],
    "通信キャリア": ["VZ", "T", "TMUS"],
    "メディア/エンタメ": ["DIS", "NFLX", "WBD"],
    "半導体製造装置": ["AMAT", "LRCX", "KLAC", "ASML"],
    "クラウドインフラ/データセンター": ["MSFT", "AMZN", "GOOGL", "EQIX"],
    "ゲーム/eスポーツ": ["EA", "TTWO", "RBLX"],
    "ソーシャルメディア": ["META", "SNAP", "PINS"],
    "旅行/レジャー": ["BKNG", "ABNB", "MAR"],
    "住宅建設": ["DHI", "LEN", "PHM"],
    "産業オートメーション/ロボティクス": ["ROK", "ABB", "IRBT"],
    "農業/アグリテック": ["DE", "CTVA", "MOS"],
    "水素/燃料電池": ["PLUG", "BE", "FCEL"],
    "原子力/ウラン": ["CCJ", "UEC", "NNE"],
    "宇宙関連": ["RKLB", "ASTS"],
    "商業用不動産REIT": ["SPG", "O", "VNO"],
    "決済/クレジットカード": ["V", "MA", "AXP"],
    "資産運用/取引所": ["BLK", "ICE", "CME"],
    "たばこ/アルコール": ["PM", "MO", "STZ"],
    "化粧品/パーソナルケア": ["EL", "PG", "CL"],
    "特殊化学": ["DD", "DOW", "LYB"],
    "鉄鋼/金属": ["NUE", "STLD", "X"],
    "空運": ["DAL", "UAL", "LUV"],
    "物流/運輸": ["FDX", "UPS", "UNP"],
    "ヘルスケアサービス/保険": ["UNH", "CI", "HUM"],
    "ジェネリック医薬品": ["TEVA", "VTRS"],
    "精密医療/遺伝子治療": ["CRSP", "NTLA", "BEAM"],
    "防衛テック/インテリジェンス": ["PLTR", "LDOS", "LHX"],
    "EV充電インフラ": ["CHPT", "BLNK"],
    "太陽光発電": ["FSLR", "SEDG", "ENPH"],
    "LNG/天然ガス": ["LNG", "EQT"],
    "ペットケア": ["CHWY", "ZTS"],
    "Eラーニング/教育": ["CHGG", "DUOL"],
    "フードデリバリー": ["DASH", "UBER"],
    "ライドシェア": ["UBER", "LYFT"],
    "石油サービス/掘削": ["SLB", "HAL", "BKR"],
    "REIT物流施設": ["PLD", "DLR"],
    "家電/耐久消費財": ["WHR", "MHK"],
    "玩具/レジャー用品": ["HAS", "MAT"],
}

OPENINSIDER_URL = "http://openinsider.com/latest-cluster-buys"
ARK_ETFS = "ARKK,ARKW,ARKG,ARKQ,ARKF,ARKX"


def _http_get(url, headers=None, timeout=20):
    """GETリクエストを行い、失敗した場合はNoneを返す（エラーで落とさない）。"""
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"[warn] GET failed: {url}: {e}")
        return None


def compute_theme_ranking():
    """全テーマの直近1営業日騰落率を計算し、強い順に並べたリストを返す。
    取得に失敗した場合は空リストを返す。
    """
    import pandas as pd

    all_tickers = sorted({t for stocks in THEME_STOCKS.values() for t in stocks})
    try:
        data = yf.download(
            tickers=all_tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        print(f"[error] yfinance download failed: {e}")
        return []

    def extract_close(ticker):
        try:
            if isinstance(data.columns, pd.MultiIndex):
                return data[ticker]["Close"].dropna()
            return data["Close"].dropna()
        except Exception:
            return None

    rows = []
    for theme_name, stocks in THEME_STOCKS.items():
        changes = []
        for t in stocks:
            close = extract_close(t)
            if close is None or len(close) < 2:
                continue
            chg = (close.iloc[-1] / close.iloc[-2] - 1) * 100
            changes.append(chg)
        if changes:
            rows.append({"theme": theme_name, "change": sum(changes) / len(changes)})

    rows.sort(key=lambda r: r["change"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def get_cluster_buys_summary():
    """openinsider.comから直近のクラスター買い（購入）銘柄を取得し、簡潔な文言用に
    ティッカーのリストを返す。GitHub Actionsのサーバーは通常のクラウドIPのため、
    Streamlit Cloud側で起きているブロックとは別条件であり、取得できる可能性がある。
    失敗した場合はNoneを返す（エラーで落とさない）。
    """
    import pandas as pd

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        "Referer": "http://openinsider.com/",
    }
    resp = _http_get(OPENINSIDER_URL, headers=headers)
    if resp is None:
        return None
    try:
        tables = pd.read_html(resp.text)
        target_df = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            if "Ticker" in cols and "Trade Type" in cols:
                target_df = t
                break
        if target_df is None or target_df.empty:
            return None
        df = target_df[target_df["Trade Type"].astype(str).str.contains("Purchase", na=False)]
        if df.empty:
            return None
        tickers = df["Ticker"].dropna().unique().tolist()[:5]
        if not tickers:
            return None
        return "、".join(tickers)
    except Exception as e:
        print(f"[warn] cluster buys parse failed: {e}")
        return None


def get_ark_summary():
    """arkfunds.io（無料の非公式API）からARK Investの直近売買トップを取得し、
    (最大買い増しティッカー, 最大売却ティッカー) を返す。失敗した場合は (None, None)。
    """
    try:
        url = f"https://arkfunds.io/api/v2/etf/trades?symbol={ARK_ETFS}"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        trades = data.get("trades", [])
        if not trades:
            return None, None
        buys = [t for t in trades if t.get("direction") == "Buy"]
        sells = [t for t in trades if t.get("direction") == "Sell"]
        buys.sort(key=lambda t: t.get("etf_percent", 0), reverse=True)
        sells.sort(key=lambda t: t.get("etf_percent", 0), reverse=True)
        buy_ticker = buys[0]["ticker"] if buys else None
        sell_ticker = sells[0]["ticker"] if sells else None
        return buy_ticker, sell_ticker
    except Exception as e:
        print(f"[warn] ARK fetch failed: {e}")
        return None, None


def should_skip(existing, today_str):
    """既存のcommentary.jsonを上書きすべきでない場合にTrueを返す。

    「今日の日付」かつ「generated_byがgithub-actionsではない」（=Claudeが生成した、
    または従来形式でgenerated_byフィールド自体が無い）場合は、Claudeの手動更新を
    誤って上書きしないよう、安全側に倒してスキップする。
    """
    if not existing:
        return False
    if existing.get("date") != today_str:
        return False
    generated_by = existing.get("generated_by")
    if generated_by == "github-actions":
        return False
    return True


def build_commentary(ranking, cluster_summary, ark_buy, ark_sell, today_str, now_iso):
    """数値データだけを使い、機械的なテンプレート文でcommentary.json相当のオブジェクトを作る。
    AIによる文章生成・要因分析は一切行わない。
    """
    if not ranking:
        return None

    top = ranking[:3]
    bottom = list(reversed(ranking[-3:])) if len(ranking) >= 3 else list(reversed(ranking))

    facts = []
    if top:
        top_names = "、".join(f"{r['theme']}（{r['change']:+.2f}%）" for r in top)
        facts.append(
            {
                "text": f"本日のテーマ強弱ランキングでは、{top_names} が上位でした。",
                "cause": "未分析（自動生成のため要因分析なし）",
                "source_url": None,
            }
        )
    if bottom:
        bottom_names = "、".join(f"{r['theme']}（{r['change']:+.2f}%）" for r in bottom)
        facts.append(
            {
                "text": f"一方、{bottom_names} は下位に沈みました。",
                "cause": "未分析（自動生成のため要因分析なし）",
                "source_url": None,
            }
        )
    if cluster_summary:
        facts.append(
            {
                "text": f"内部者クラスター買いでは、{cluster_summary} などで直近の購入が確認されています。",
                "cause": "未分析（自動生成のため要因分析なし）",
                "source_url": "http://openinsider.com/latest-cluster-buys",
            }
        )
    if ark_buy or ark_sell:
        parts = []
        if ark_buy:
            parts.append(f"{ark_buy}を買い増し")
        if ark_sell:
            parts.append(f"{ark_sell}を売却")
        facts.append(
            {
                "text": "ARK Investは直近、" + "・".join(parts) + "しています。",
                "cause": "未分析（自動生成のため要因分析なし）",
                "source_url": "https://ark-funds.com/",
            }
        )

    themes = []
    for r in top:
        themes.append(
            {
                "title": f"{r['theme']}が上昇・堅調",
                "stance": "bullish",
                "confidence": "低",
                "text": f"{r['theme']}は本日{r['change']:+.2f}%と上昇しています。自動生成の簡易情報です。",
            }
        )
    for r in bottom:
        themes.append(
            {
                "title": f"{r['theme']}が下落・軟調",
                "stance": "bearish",
                "confidence": "低",
                "text": f"{r['theme']}は本日{r['change']:+.2f}%と下落しています。自動生成の簡易情報です。",
            }
        )

    headline = "本日のテーマ強弱サマリー（機械生成）"
    if top and bottom:
        headline = f"{top[0]['theme']}が上昇、{bottom[0]['theme']}が軟調（GitHub Actionsによる機械生成の簡易版）"

    lead = (
        "このサマリーはGitHub Actionsによる自動生成の簡易版です。"
        "数値（テーマ別騰落率・大口投資家動向）は実際のデータに基づきますが、"
        "詳細な要因分析やニュースとの関連付けは行っていません。"
        "Claudeによる詳細分析が行われ次第、この内容は上書きされます。"
    )

    return {
        "date": today_str,
        "generated_by": "github-actions",
        "generated_at": now_iso,
        "headline": headline,
        "lead": lead,
        "facts": facts,
        "insights": [],
        "themes": themes,
    }


def main():
    now_jst = datetime.now(JST)
    today_str = now_jst.strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = None
    if os.path.exists(COMMENTARY_PATH):
        try:
            with open(COMMENTARY_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            print(f"[warn] failed to read existing commentary.json: {e}")
            existing = None

    if should_skip(existing, today_str):
        print(
            f"[info] commentary.json for {today_str} already exists and is not "
            "github-actions generated (likely Claude-authored). Skipping to avoid overwrite."
        )
        sys.exit(0)

    print("[info] computing theme ranking for 62 themes...")
    ranking = compute_theme_ranking()
    if not ranking:
        print("[error] could not compute theme ranking (yfinance may have failed); aborting without writing.")
        sys.exit(1)
    print(f"[info] computed ranking for {len(ranking)} themes.")

    print("[info] fetching cluster buys from openinsider.com (best effort)...")
    cluster_summary = get_cluster_buys_summary()
    print(f"[info] cluster_summary = {cluster_summary!r}")

    print("[info] fetching ARK trades from arkfunds.io (best effort)...")
    ark_buy, ark_sell = get_ark_summary()
    print(f"[info] ark_buy={ark_buy!r}, ark_sell={ark_sell!r}")

    commentary = build_commentary(ranking, cluster_summary, ark_buy, ark_sell, today_str, now_iso)
    if commentary is None:
        print("[error] failed to build commentary object; aborting without writing.")
        sys.exit(1)

    with open(COMMENTARY_PATH, "w", encoding="utf-8") as f:
        json.dump(commentary, f, ensure_ascii=False, indent=2)

    print(f"[info] wrote {COMMENTARY_PATH} for {today_str} (generated_by=github-actions)")


if __name__ == "__main__":
    main()
