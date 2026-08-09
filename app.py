# -*- coding: utf-8 -*-
"""
米国株 大口投資家動向・セクター強弱ダッシュボード
=====================================================

このアプリは Streamlit というPythonの仕組みで作られたWebアプリです。
サイトを開くたびに、その場で最新の株式データを取得して表示します。

含まれる機能:
  1. 主要指数カード（ダウ30・S&P500・ナスダック100・ビットコイン・金）
  2. セクター強弱ランキング（11セクターETFの騰落率、代表銘柄スコア付き）
  3. テーマ強弱ランキング（半導体・AIインフラなど、より細かいテーマ単位の騰落率）
  4. 大口投資家の動き（内部者クラスター買い / ARK Invest / SEC Form 13D / dataroma.com）
  5. 資産相関マトリクス（主要資産の値動きの相関）
  6. 期間セレクター（1日 / 1週間 / 1ヶ月）

本サイトは事実整理であり、投資助言ではありません。
"""

import re
import warnings
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

warnings.filterwarnings("ignore")

# =====================================================
# ページ全体の設定
# =====================================================
st.set_page_config(
    page_title="米国株 大口投資家動向・セクター強弱ダッシュボード",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ダークテーマ・カード風の見た目にするためのCSS
# （.streamlit/config.toml でも基本のダークテーマを設定していますが、
#   ここではカードの角丸や影など、細かい装飾を追加しています）
st.markdown(
    """
    <style>
    .stApp {
        background-color: #0e1117;
    }
    .metric-card {
        background: linear-gradient(145deg, #161a25, #1c2130);
        border: 1px solid #2a2f3d;
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 10px;
    }
    .section-title {
        font-size: 1.35rem;
        font-weight: 700;
        margin-top: 1.2rem;
        margin-bottom: 0.6rem;
    }
    .small-note {
        color: #9aa0ac;
        font-size: 0.85rem;
    }
    .buy-card {
        background: linear-gradient(145deg, #12241c, #16281f);
        border: 1px solid #1f4d34;
        border-radius: 12px;
        padding: 12px 14px;
        margin-bottom: 8px;
    }
    .sell-card {
        background: linear-gradient(145deg, #241212, #281616);
        border: 1px solid #4d1f1f;
        border-radius: 12px;
        padding: 12px 14px;
        margin-bottom: 8px;
    }
    .fact-box {
        background: linear-gradient(145deg, #0d2436, #123049);
        border: 1px solid #1f6fa8;
        border-left: 4px solid #38bdf8;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 8px;
        line-height: 1.6;
    }
    .insight-box {
        background: linear-gradient(145deg, #241b36, #2c2140);
        border: 1px solid #7c5cbf;
        border-left: 4px solid #a78bfa;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 8px;
        line-height: 1.6;
    }
    .confidence-badge {
        display: inline-block;
        font-size: 0.72rem;
        font-weight: 700;
        padding: 2px 9px;
        border-radius: 999px;
        margin-left: 8px;
        vertical-align: middle;
        white-space: nowrap;
    }
    .confidence-high {
        background: rgba(124, 58, 237, 0.25);
        color: #c4b5fd;
        border: 1px solid #a78bfa;
    }
    .confidence-mid {
        background: rgba(234, 179, 8, 0.2);
        color: #fde68a;
        border: 1px solid #eab308;
    }
    .confidence-low {
        background: rgba(100, 116, 139, 0.25);
        color: #cbd5e1;
        border: 1px solid #64748b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =====================================================
# 定数（銘柄コードなどの一覧）
# =====================================================

# セクター別ETF（11セクター）
SECTOR_ETFS = {
    "情報技術": "XLK",
    "ヘルスケア": "XLV",
    "金融": "XLF",
    "一般消費財": "XLY",
    "生活必需品": "XLP",
    "エネルギー": "XLE",
    "資本財": "XLI",
    "素材": "XLB",
    "公益": "XLU",
    "不動産": "XLRE",
    "通信サービス": "XLC",
}

# 各セクターの代表的な大型株（5銘柄）。セクターETFの動きを個別銘柄レベルでも確認できるようにする。
SECTOR_STOCKS = {
    "情報技術": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM"],
    "ヘルスケア": ["UNH", "JNJ", "LLY", "ABBV", "MRK"],
    "金融": ["JPM", "BAC", "WFC", "GS", "MS"],
    "一般消費財": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "生活必需品": ["PG", "KO", "PEP", "WMT", "COST"],
    "エネルギー": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "資本財": ["CAT", "HON", "UNP", "RTX", "BA"],
    "素材": ["LIN", "SHW", "FCX", "ECL", "APD"],
    "公益": ["NEE", "DUK", "SO", "D", "AEP"],
    "不動産": ["PLD", "AMT", "EQIX", "PSA", "O"],
    "通信サービス": ["GOOGL", "META", "NFLX", "DIS", "VZ"],
}

# テーマ強弱ランキング用：セクターより細かいテーマ単位の代表銘柄（3〜5銘柄ずつ）
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
    "保険": ["PGR", "AIG", "MET"],
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
}

# 主要指数・資産カード
INDEX_TICKERS = {
    "ダウ30": ("^DJI", "💵"),
    "S&P500": ("^GSPC", "📈"),
    "ナスダック100": ("^NDX", "💻"),
    "ビットコイン": ("BTC-USD", "🪙"),
    "金 (ゴールド)": ("GC=F", "🥇"),
}

# 資産相関マトリクス用ティッカー
CORR_TICKERS = {
    "QQQ (ハイテク)": "QQQ",
    "SMH (半導体)": "SMH",
    "BTC-USD (ビットコイン)": "BTC-USD",
    "GLD (金)": "GLD",
    "XLP (生活必需品)": "XLP",
    "米10年金利 (^TNX)": "^TNX",
    "ドル円 (USDJPY)": "USDJPY=X",
}

# ARK Invest のETF一覧（arkfunds.io API 用）
ARK_ETFS = "ARKK,ARKW,ARKG,ARKQ,ARKF,ARKX"

# openinsider.com のクラスター買い一覧ページ
OPENINSIDER_URL = "http://openinsider.com/latest-cluster-buys"

# SEC（米国証券取引委員会）EDGAR の全文検索API。
# SEC EDGARのデータは米国政府による公開情報であり、著作権上の制約なく誰でも無料で利用できる。
# SECは自動アクセス時、連絡先入りのUser-Agentを送ることを推奨しているため、それに従っている。
# （SEC公式ガイド: https://www.sec.gov/os/webmaster-faq#developers ）
SEC_EDGAR_FULLTEXT_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_USER_AGENT = "us-stock-dashboard personal-use-app (contact: github.com/pande3284mk2)"

# dataroma.com（著名投資家の13F保有情報をまとめた無料サイト）のトップページ。
# 利用規約上、データの一括転載・再配布は禁止されているため、本アプリでは
# 「出典を明記した上でのごく小部分の参照利用」の範囲に限定して取得する。
DATAROMA_URL = "https://www.dataroma.com/"

# 毎朝配信しているHTMLダッシュボードの考察を、同じGitHubリポジトリに置いた
# commentary.json 経由でこのサイトにも表示する（このサイト自体はリアルタイムの
# ニュース分析はしない。事前に用意されたJSONを読み込んで表示するだけ）。
COMMENTARY_URL = (
    "https://raw.githubusercontent.com/pande3284mk2/us-stock-dashboard/main/commentary.json"
)

# 期間セレクターの選択肢
# yf_period   : 騰落率計算に必要な株価データを取得する期間
# lookback    : 何営業日前と比較して騰落率を計算するか
# corr_period : 相関マトリクス計算に使うデータの取得期間
PERIOD_OPTIONS = {
    "1日 (直近1営業日)": {"yf_period": "5d", "lookback": 1, "corr_period": "1mo"},
    "1週間 (直近5営業日)": {"yf_period": "1mo", "lookback": 5, "corr_period": "3mo"},
    "1ヶ月 (直近21営業日)": {"yf_period": "3mo", "lookback": 21, "corr_period": "6mo"},
}

CACHE_TTL = 900  # データキャッシュの有効期限（秒）= 15分


# =====================================================
# データ取得関数（すべてキャッシュして無駄なアクセスを避ける）
# =====================================================

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_prices(tickers, yf_period):
    """複数の銘柄の株価データをまとめて取得する。

    yfinance（Yahoo!ファイナンスのデータを取得するライブラリ）を使い、
    一度のアクセスで複数銘柄をまとめて取得することで、
    サーバーへの負荷とアクセス回数を減らしている。
    """
    try:
        data = yf.download(
            tickers=list(tickers),
            period=yf_period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        return data
    except Exception:
        return pd.DataFrame()


def _pct_change_from_series(close_series, lookback):
    """終値の時系列データから、指定した営業日数前と比較した騰落率(%)を計算する。"""
    if close_series is None:
        return None
    close_series = close_series.dropna()
    if len(close_series) < 2:
        return None
    lb = lookback
    if len(close_series) < lb + 1:
        lb = len(close_series) - 1
    last = close_series.iloc[-1]
    prev = close_series.iloc[-1 - lb]
    if prev in (0, None) or pd.isna(prev) or pd.isna(last):
        return None
    return (last / prev - 1) * 100


def _extract_close(price_data, ticker):
    """fetch_prices() が返すデータから、特定銘柄の終値だけを取り出す。"""
    try:
        if isinstance(price_data.columns, pd.MultiIndex):
            return price_data[ticker]["Close"]
        return price_data["Close"]
    except Exception:
        return None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_cluster_buys():
    """openinsider.com の「クラスター買い」ページから、
    直近に複数の役員が同時期に自社株を購入した銘柄一覧を取得する。

    「売り」は10b5-1プラン（あらかじめ決めたスケジュールでの機械的売却）が
    多く含まれ、経営陣の相場観を反映しないことが多いため、原則として除外する。
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(OPENINSIDER_URL, headers=headers, timeout=20)
        resp.raise_for_status()

        try:
            tables = pd.read_html(resp.text)
        except ValueError:
            tables = []

        target_df = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            if "Ticker" in cols and "Trade Type" in cols:
                target_df = t
                break

        if target_df is None or target_df.empty:
            return None

        df = target_df.copy()

        # 「Purchase（購入）」のみを残す（売りは除外）
        if "Trade Type" in df.columns:
            df = df[df["Trade Type"].astype(str).str.contains("Purchase", na=False)]

        keep_cols = [
            "Filing Date", "Trade Date", "Ticker", "Company Name",
            "Industry", "Ins", "Trade Type", "Price", "Qty",
            "Owned", "ΔOwn", "Value",
        ]
        present_cols = [c for c in keep_cols if c in df.columns]
        df = df[present_cols]

        return df.head(25).reset_index(drop=True)
    except Exception:
        return None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_ark_trades():
    """ARK Investの日次売買データを取得する。

    arkfunds.io という無料の非公式API（ARK Investとは無関係の第三者提供）を利用。
    取得に失敗した場合は (None, None) を返し、画面側でその旨を案内する。
    """
    try:
        url = f"https://arkfunds.io/api/v2/etf/trades?symbol={ARK_ETFS}"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        trades = data.get("trades", [])
        if not trades:
            return None, None
        df = pd.DataFrame(trades)
        buys = df[df["direction"] == "Buy"].sort_values("etf_percent", ascending=False)
        sells = df[df["direction"] == "Sell"].sort_values("etf_percent", ascending=False)
        return buys.reset_index(drop=True), sells.reset_index(drop=True)
    except Exception:
        return None, None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_sec_13d_filings(days_back=30, max_results=15):
    """SEC EDGARの全文検索APIを使い、直近のSchedule 13D（5%超保有の新規取得・変更等）の
    提出一覧を取得する。

    Schedule 13Dは「対象企業の議決権株式を5%超保有し、経営に関与する意図がある投資家」が
    提出する書類で、アクティビスト投資家など「大口投資家の新たな動き」を知る手がかりになる。
    直近30日間に該当する提出がない場合は空リストになる（13Dは提出頻度自体が高くないため、
    「取得失敗」ではなく単に「該当なし」の場合もある）。
    """
    try:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days_back)
        params = {
            "q": '""',
            "forms": "SCHEDULE 13D",
            "dateRange": "custom",
            "startdt": start_date.isoformat(),
            "enddt": end_date.isoformat(),
        }
        headers = {"User-Agent": SEC_USER_AGENT}
        resp = requests.get(
            SEC_EDGAR_FULLTEXT_URL, params=params, headers=headers, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        rows = []
        for h in hits:
            src = h.get("_source", {})
            names = src.get("display_names", [])
            file_date = src.get("file_date", "")
            doc_id = h.get("_id", "")
            if not names or ":" not in doc_id:
                continue

            issuer_raw = names[0]
            filers_raw = "、".join(names[1:]) if len(names) > 1 else "-"
            issuer = re.sub(r"\s*\(CIK \d+\)", "", issuer_raw).strip()
            filers = re.sub(r"\s*\(CIK \d+\)", "", filers_raw).strip()

            accession, filename = doc_id.split(":", 1)
            doc_url = None
            cik_match = re.search(r"CIK (\d+)", issuer_raw)
            if cik_match:
                cik_nolead = str(int(cik_match.group(1)))
                accession_nodash = accession.replace("-", "")
                doc_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik_nolead}/{accession_nodash}/{filename}"
                )

            rows.append(
                {
                    "対象企業": issuer,
                    "投資家(提出者)": filers,
                    "提出日": file_date,
                    "url": doc_url,
                }
            )

        rows.sort(key=lambda r: r["提出日"], reverse=True)
        return rows[:max_results]
    except Exception:
        return None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_dataroma_highlights():
    """dataroma.com（著名投資家=スーパーインベスターの13F保有情報をまとめた無料サイト）の
    トップページから、直近のインサイダー買い情報を取得する。

    dataroma.comの利用規約では、出典を明記した「小部分の参照利用」は認められている一方、
    データの一括転載・再配布は禁止されている。そのため本アプリでは、全件ではなく
    上位5件だけを出典リンク付きで表示する（過度なスクレイピングを避けるための意図的な制限）。
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(DATAROMA_URL, headers=headers, timeout=20)
        resp.raise_for_status()

        try:
            tables = pd.read_html(resp.text)
        except ValueError:
            tables = []

        target_df = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            if "Stock" in cols and any("Value" in c for c in cols):
                target_df = t
                break

        if target_df is None or target_df.empty:
            return None

        return target_df.head(5).reset_index(drop=True)
    except Exception:
        return None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_commentary():
    """同じGitHubリポジトリ直下の commentary.json を取得する。

    このファイルは毎朝配信しているHTMLダッシュボード用に人が書いた考察を
    JSON化したもので、このサイト自体が自動でニュース分析しているわけではない。
    取得できない場合は None を返し、画面側で「準備中」メッセージを表示する。
    """
    try:
        resp = requests.get(COMMENTARY_URL, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _confidence_class(confidence):
    mapping = {"高": "confidence-high", "中": "confidence-mid", "低": "confidence-low"}
    return mapping.get(str(confidence), "confidence-mid")


# =====================================================
# 画面表示用の補助関数
# =====================================================

def render_index_cards(period_key):
    cfg = PERIOD_OPTIONS[period_key]
    tickers = [t for t, _ in INDEX_TICKERS.values()]
    price_data = fetch_prices(tuple(tickers), cfg["yf_period"])

    cols = st.columns(len(INDEX_TICKERS))
    for col, (name, (ticker, emoji)) in zip(cols, INDEX_TICKERS.items()):
        close = _extract_close(price_data, ticker)
        chg = _pct_change_from_series(close, cfg["lookback"])
        with col:
            if chg is None:
                st.metric(label=f"{emoji} {name}", value="取得失敗", delta=None)
            else:
                last_price = close.dropna().iloc[-1]
                st.metric(
                    label=f"{emoji} {name}",
                    value=f"{last_price:,.2f}",
                    delta=f"{chg:+.2f}%",
                )


def render_sector_strength(period_key):
    cfg = PERIOD_OPTIONS[period_key]
    etf_tickers = list(SECTOR_ETFS.values())
    # 代表銘柄もまとめて1回のfetch_pricesで取得し、yfinanceへのアクセス回数を増やさない
    stock_tickers = [t for stocks in SECTOR_STOCKS.values() for t in stocks]
    all_tickers = etf_tickers + stock_tickers
    price_data = fetch_prices(tuple(all_tickers), cfg["yf_period"])

    rows = []
    for name, ticker in SECTOR_ETFS.items():
        close = _extract_close(price_data, ticker)
        chg = _pct_change_from_series(close, cfg["lookback"])
        if chg is not None:
            rows.append({"セクター名": name, "表示名": f"{name} ({ticker})", "騰落率": chg})

    if not rows:
        st.warning("セクターデータの取得に失敗しました。しばらく待ってから再読み込みしてください。")
        return

    df = pd.DataFrame(rows).sort_values("騰落率", ascending=False)

    fig = go.Figure(
        go.Bar(
            x=df["騰落率"],
            y=df["表示名"],
            orientation="h",
            marker_color=["#00cc96" if v >= 0 else "#ef553b" for v in df["騰落率"]],
            text=[f"{v:+.2f}%" for v in df["騰落率"]],
            textposition="outside",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=480,
        xaxis_title="騰落率 (%)",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=10, r=30, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption("セクターごとの代表銘柄別スコアを見るには、下の項目をクリックして展開してください。")
    for _, row in df.iterrows():
        sector_name = row["セクター名"]
        stocks = SECTOR_STOCKS.get(sector_name, [])
        if not stocks:
            continue
        with st.expander(f"{row['表示名']} の代表銘柄スコア"):
            stock_cols = st.columns(len(stocks))
            for col, stk in zip(stock_cols, stocks):
                stk_close = _extract_close(price_data, stk)
                stk_chg = _pct_change_from_series(stk_close, cfg["lookback"])
                with col:
                    if stk_chg is None or stk_close is None or stk_close.dropna().empty:
                        st.metric(label=stk, value="取得失敗")
                    else:
                        last_price = stk_close.dropna().iloc[-1]
                        st.metric(
                            label=stk,
                            value=f"${last_price:,.2f}",
                            delta=f"{stk_chg:+.2f}%",
                        )


def render_theme_strength(period_key):
    """セクターより細かい「テーマ」単位（半導体・AIインフラなど）の強弱ランキングを表示する。

    各テーマの代表銘柄（3〜5銘柄）をすべてまとめて1回のfetch_pricesで取得し、
    テーマごとに代表銘柄の期間内騰落率を単純平均してスコア化する。
    """
    cfg = PERIOD_OPTIONS[period_key]
    all_tickers = sorted({t for stocks in THEME_STOCKS.values() for t in stocks})
    price_data = fetch_prices(tuple(all_tickers), cfg["yf_period"])

    rows = []
    for theme_name, stocks in THEME_STOCKS.items():
        changes = []
        for t in stocks:
            close = _extract_close(price_data, t)
            chg = _pct_change_from_series(close, cfg["lookback"])
            if chg is not None:
                changes.append(chg)
        if changes:
            rows.append(
                {
                    "テーマ名": theme_name,
                    "騰落率": sum(changes) / len(changes),
                    "構成銘柄": "/".join(stocks),
                    "取得できた銘柄数": f"{len(changes)}/{len(stocks)}",
                }
            )

    if not rows:
        st.warning("テーマ強弱データの取得に失敗しました。しばらく待ってから再読み込みしてください。")
        return

    df = pd.DataFrame(rows).sort_values("騰落率", ascending=False)

    fig = go.Figure(
        go.Bar(
            x=df["騰落率"],
            y=df["テーマ名"],
            orientation="h",
            marker_color=["#00cc96" if v >= 0 else "#ef553b" for v in df["騰落率"]],
            text=[f"{v:+.2f}%" for v in df["騰落率"]],
            textposition="outside",
            customdata=df["構成銘柄"],
            hovertemplate="<b>%{y}</b><br>平均騰落率: %{x:+.2f}%<br>構成銘柄: %{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=800,
        xaxis_title="平均騰落率 (%)",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=10, r=30, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "各テーマの代表銘柄（3〜5銘柄）の期間内騰落率を単純平均したスコアです。"
        "バーにカーソルを合わせると構成銘柄を確認できます。11セクターより細かい粒度の目安としてご利用ください。"
    )


def render_cluster_buys():
    df = get_cluster_buys()
    if df is None or df.empty:
        st.info(
            "😔 内部者クラスター買いのデータを取得できませんでした。\n\n"
            "openinsider.com 側の一時的な混雑や仕様変更の可能性があります。"
            "しばらく待ってから再度お試しください。"
        )
        return

    st.caption(f"直近の複数役員による同時期の自社株購入（上位{len(df)}件）")
    for _, row in df.iterrows():
        ticker = row.get("Ticker", "-")
        company = row.get("Company Name", "-")
        trade_date = row.get("Trade Date", "-")
        value = row.get("Value", "-")
        ins = row.get("Ins", "-")
        st.markdown(
            f"""
            <div class="buy-card">
            <b>🟢 {ticker}</b> — {company}<br>
            <span class="small-note">取引日: {trade_date} ｜ 参加役員数: {ins} ｜ 取引額: {value}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_ark_trades():
    buys, sells = get_ark_trades()
    if buys is None and sells is None:
        st.info(
            "😔 ARK Investの売買データを取得できませんでした。\n\n"
            "データ提供元（arkfunds.io、非公式API）が一時的に利用できない可能性があります。"
            "しばらく待ってから再度お試しください。"
        )
        return

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**🟢 買い増し上位**")
        if buys is None or buys.empty:
            st.caption("本日の買いデータはありません。")
        else:
            for _, row in buys.head(8).iterrows():
                st.markdown(
                    f"""
                    <div class="buy-card">
                    <b>{row.get('ticker', '-')}</b> — {row.get('company', '-')}<br>
                    <span class="small-note">ファンド: {row.get('fund', '-')} ｜ 株数: {row.get('shares', 0):,} ｜
                    ETF比率: {row.get('etf_percent', 0):.2f}%</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    with col2:
        st.markdown("**🔴 売却上位**")
        if sells is None or sells.empty:
            st.caption("本日の売りデータはありません。")
        else:
            for _, row in sells.head(8).iterrows():
                st.markdown(
                    f"""
                    <div class="sell-card">
                    <b>{row.get('ticker', '-')}</b> — {row.get('company', '-')}<br>
                    <span class="small-note">ファンド: {row.get('fund', '-')} ｜ 株数: {row.get('shares', 0):,} ｜
                    ETF比率: {row.get('etf_percent', 0):.2f}%</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def render_sec_13d():
    rows = get_sec_13d_filings()
    if rows is None:
        st.info(
            "😔 SEC Form 13Dのデータを取得できませんでした。\n\n"
            "SEC EDGAR側の一時的な混雑や仕様変更の可能性があります。しばらく待ってから再度お試しください。"
        )
        return
    if len(rows) == 0:
        st.info("📭 直近30日間に該当するSchedule 13Dの提出はありませんでした。")
        return

    st.caption(f"直近30日間に提出されたSchedule 13D（5%超保有の新規取得・変更等）（最大{len(rows)}件）")
    for r in rows:
        link = (
            f' <a href="{r["url"]}" target="_blank" style="color:#7dd3fc;">[提出書類]</a>'
            if r.get("url")
            else ""
        )
        st.markdown(
            f"""
            <div class="buy-card">
            <b>🏛️ {r['投資家(提出者)']}</b> → {r['対象企業']}<br>
            <span class="small-note">提出日: {r['提出日']}{link}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption("出典: SEC EDGAR Full Text Search (efts.sec.gov) ／ 米国政府の公開データです。")


def render_dataroma_highlights():
    df = get_dataroma_highlights()
    if df is None or df.empty:
        st.info(
            "😔 dataroma.comのデータを取得できませんでした。\n\n"
            "サイト側の一時的な混雑や構成変更の可能性があります。しばらく待ってから再度お試しください。"
        )
        return

    st.caption(
        "著名投資家（スーパーインベスター）が保有する銘柄における、直近のインサイダー買い"
        "（利用規約に配慮し上位5件のみ抜粋）"
    )
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        "出典: [dataroma.com](https://www.dataroma.com/) "
        "（利用規約に基づき、一括転載ではなく小部分のみを引用しています）"
    )


def render_institutional_investors():
    """「大口投資家の動き」を、4つの情報源をタブで切り替えて俯瞰できるようにする。"""
    tab1, tab2, tab3, tab4 = st.tabs(
        ["🕵️ 内部者クラスター買い", "🚀 ARK Invest", "📜 SEC Form 13D", "💎 著名投資家(Dataroma)"]
    )
    with tab1:
        render_cluster_buys()
    with tab2:
        render_ark_trades()
    with tab3:
        render_sec_13d()
    with tab4:
        render_dataroma_highlights()


def render_commentary():
    """毎朝配信しているHTMLダッシュボードの考察(commentary.json)を表示する。

    事実(facts)は水色系の枠、考察(insights)・注目テーマ(themes)は紫系の枠＋
    確度バッジで視覚的に区別する。ファイルが取得できない場合は準備中と案内する。
    """
    data = get_commentary()
    if not data or not isinstance(data, dict):
        st.info("📝 本日の考察はまだ準備中です。しばらくしてから再度ご確認ください。")
        return

    headline = data.get("headline", "")
    lead = data.get("lead", "")
    date_str = data.get("date", "")

    if headline:
        st.subheader(f"🗞️ {headline}")
    if date_str:
        st.caption(f"考察日: {date_str}")
    if lead:
        st.write(lead)

    facts = data.get("facts", [])
    if facts:
        st.markdown("**🔵 事実（ファクト）**")
        for f in facts:
            text = f.get("text", "")
            src = f.get("source_url")
            src_html = (
                f' <a href="{src}" target="_blank" style="color:#7dd3fc;">[出典]</a>'
                if src
                else ""
            )
            st.markdown(
                f'<div class="fact-box">{text}{src_html}</div>',
                unsafe_allow_html=True,
            )

    insights = data.get("insights", [])
    if insights:
        st.markdown("**🟣 考察（インサイト）**")
        for ins in insights:
            text = ins.get("text", "")
            conf = ins.get("confidence", "中")
            badge_cls = _confidence_class(conf)
            st.markdown(
                f'<div class="insight-box">{text}'
                f'<span class="confidence-badge {badge_cls}">確度: {conf}</span></div>',
                unsafe_allow_html=True,
            )

    themes = data.get("themes", [])
    if themes:
        st.markdown("**🎯 今後の注目テーマ**")
        for th in themes:
            title = th.get("title", "")
            conf = th.get("confidence", "中")
            text = th.get("text", "")
            badge_cls = _confidence_class(conf)
            st.markdown(
                f'<div class="insight-box"><b>{title}</b>'
                f'<span class="confidence-badge {badge_cls}">確度: {conf}</span><br>{text}</div>',
                unsafe_allow_html=True,
            )

    st.caption(
        "※ この考察セクションは、サイトがリアルタイムでニュースを分析しているわけではなく、"
        "あらかじめ用意された commentary.json の内容を表示しています。"
    )


def render_correlation(period_key):
    cfg = PERIOD_OPTIONS[period_key]
    tickers = list(CORR_TICKERS.values())
    price_data = fetch_prices(tuple(tickers), cfg["corr_period"])

    returns = {}
    for name, ticker in CORR_TICKERS.items():
        close = _extract_close(price_data, ticker)
        if close is not None:
            ret = close.dropna().pct_change().dropna()
            if len(ret) > 2:
                returns[name] = ret

    if len(returns) < 2:
        st.warning("相関マトリクスの計算に十分なデータを取得できませんでした。")
        return

    ret_df = pd.DataFrame(returns).dropna()
    if ret_df.empty or len(ret_df) < 3:
        st.warning("相関マトリクスの計算に十分なデータを取得できませんでした。")
        return

    corr = ret_df.corr()

    fig = px.imshow(
        corr,
        color_continuous_scale="RdBu",
        zmin=-1,
        zmax=1,
        text_auto=".2f",
        aspect="auto",
    )
    fig.update_layout(
        template="plotly_dark",
        height=520,
        margin=dict(l=10, r=10, t=20, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    # 実際に計算されたデータの中から、最も相関が強い（絶対値が大きい）組み合わせを
    # 具体例として動的に抽出する（数値を決め打ちにせず、その時点の事実に基づかせる）
    example_html = ""
    cols = corr.columns.tolist()
    pairs = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            pairs.append((cols[i], cols[j], corr.iloc[i, j]))
    if pairs:
        a, b, v = max(pairs, key=lambda p: abs(p[2]))
        if v >= 0:
            direction = "一方が上がるともう一方も上がりやすい（同じ方向に動きやすい）"
        else:
            direction = "一方が上がるともう一方は下がりやすい（逆方向に動きやすい）"
        example_html = (
            f"例えば、今回のデータでは <b>{a}</b> と <b>{b}</b> の相関係数が "
            f"<b>{v:+.2f}</b> です。これは、{direction}という意味です。"
        )

    st.markdown(
        f"""
        <div class="fact-box" style="margin-top:12px;">
        <b>📖 相関マトリクスの読み方</b><br>
        ・値は −1.0 〜 +1.0 の範囲で、2つの資産の値動きがどれくらい同じ方向に動くかを表します。<br>
        ・+1.0 に近いほど「同じ方向に動きやすい」、−1.0 に近いほど「逆方向に動きやすい」、0 に近いと「関連性が薄い」ことを意味します。<br>
        ・対角線（同じ資産どうしが交わるマス）は必ず 1.0 になります（自分自身との相関のため）。<br>
        ・配色は、<b>青系が正の相関</b>、<b>赤系が負の相関</b>を表しており、色が濃いほど相関が強いことを示します。<br><br>
        {example_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# =====================================================
# サイドバー
# =====================================================

st.sidebar.title("⚙️ 設定")
period_key = st.sidebar.selectbox(
    "📅 期間 (Focus Period)",
    options=list(PERIOD_OPTIONS.keys()),
    index=0,
    help="騰落率・相関の計算に使う期間を切り替えます。",
)

if st.sidebar.button("🔄 最新データに更新する"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    f"データは取得後 {CACHE_TTL // 60} 分間キャッシュされます。\n\n"
    "最終読み込み時刻: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
)
st.sidebar.caption(
    "データ提供元: Yahoo!ファイナンス (yfinance) / openinsider.com / arkfunds.io / "
    "SEC EDGAR / dataroma.com"
)


# =====================================================
# メイン画面
# =====================================================

st.title("📊 米国株 大口投資家動向・セクター強弱ダッシュボード")
st.caption("開くたびに最新データをその場で取得して表示します。")

st.markdown('<div class="section-title">🗞️ 今日の相場考察</div>', unsafe_allow_html=True)
render_commentary()

st.markdown('<div class="section-title">🧭 主要指数・資産</div>', unsafe_allow_html=True)
render_index_cards(period_key)

st.markdown('<div class="section-title">🏭 セクター強弱ランキング</div>', unsafe_allow_html=True)
render_sector_strength(period_key)

st.markdown('<div class="section-title">🎯 テーマ強弱ランキング</div>', unsafe_allow_html=True)
st.caption("11セクターよりさらに細かい「テーマ」単位（半導体・AIインフラ・GLP-1など27テーマ）の強弱ランキングです。")
render_theme_strength(period_key)

st.markdown('<div class="section-title">🏦 大口投資家の動き</div>', unsafe_allow_html=True)
st.caption("内部者クラスター買い・ARK Invest・SEC Form 13D・著名投資家(dataroma)の4つの情報源をタブで切り替えて確認できます。")
render_institutional_investors()

st.markdown('<div class="section-title">🔗 資産相関マトリクス</div>', unsafe_allow_html=True)
render_correlation(period_key)

st.markdown("---")
st.caption(
    "⚠️ 本サイトは事実整理であり、投資助言ではありません。"
    "個別銘柄の売買判断はご自身の責任で行ってください。"
)
