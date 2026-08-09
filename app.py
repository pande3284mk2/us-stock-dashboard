# -*- coding: utf-8 -*-
"""
米国株 大口投資家動向・セクター強弱ダッシュボード
=====================================================

このアプリは Streamlit というPythonの仕組みで作られたWebアプリです。
サイトを開くたびに、その場で最新の株式データを取得して表示します。

含まれる機能:
  1. 主要指数カード（ダウ30・S&P500・ナスダック100・ビットコイン・金）
  2. セクター強弱ランキング（11セクターETFの騰落率）
  3. 内部者クラスター買い（openinsider.com からスクレイピング）
  4. ARK Invest 売買動向（arkfunds.io の無料APIを利用）
  5. 資産相関マトリクス（主要資産の値動きの相関）
  6. 期間セレクター（1日 / 1週間 / 1ヶ月）

本サイトは事実整理であり、投資助言ではありません。
"""

import warnings
from datetime import datetime

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
    tickers = list(SECTOR_ETFS.values())
    price_data = fetch_prices(tuple(tickers), cfg["yf_period"])

    rows = []
    for name, ticker in SECTOR_ETFS.items():
        close = _extract_close(price_data, ticker)
        chg = _pct_change_from_series(close, cfg["lookback"])
        if chg is not None:
            rows.append({"セクター": f"{name} ({ticker})", "騰落率": chg})

    if not rows:
        st.warning("セクターデータの取得に失敗しました。しばらく待ってから再読み込みしてください。")
        return

    df = pd.DataFrame(rows).sort_values("騰落率", ascending=False)

    fig = go.Figure(
        go.Bar(
            x=df["騰落率"],
            y=df["セクター"],
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
    "データ提供元: Yahoo!ファイナンス (yfinance) / openinsider.com / arkfunds.io"
)


# =====================================================
# メイン画面
# =====================================================

st.title("📊 米国株 大口投資家動向・セクター強弱ダッシュボード")
st.caption("開くたびに最新データをその場で取得して表示します。")

st.markdown('<div class="section-title">🧭 主要指数・資産</div>', unsafe_allow_html=True)
render_index_cards(period_key)

st.markdown('<div class="section-title">🏭 セクター強弱ランキング</div>', unsafe_allow_html=True)
render_sector_strength(period_key)

col_left, col_right = st.columns(2)
with col_left:
    st.markdown('<div class="section-title">🕵️ 内部者クラスター買い</div>', unsafe_allow_html=True)
    render_cluster_buys()
with col_right:
    st.markdown('<div class="section-title">🚀 ARK Invest 売買動向</div>', unsafe_allow_html=True)
    render_ark_trades()

st.markdown('<div class="section-title">🔗 資産相関マトリクス</div>', unsafe_allow_html=True)
render_correlation(period_key)

st.markdown("---")
st.caption(
    "⚠️ 本サイトは事実整理であり、投資助言ではありません。"
    "個別銘柄の売買判断はご自身の責任で行ってください。"
)
