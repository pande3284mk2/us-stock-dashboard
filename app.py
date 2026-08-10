# -*- coding: utf-8 -*-
"""
米国株 大口投資家動向・セクター強弱ダッシュボード
=====================================================

このアプリは Streamlit というPythonの仕組みで作られたWebアプリです。
サイトを開くたびに、その場で最新の株式データを取得して表示します。

含まれる機能:
  0. サイドバーでのページ切り替え（📊 分析ダッシュボード ／ 📁 マイポートフォリオ ／ 📰 ニュースアーカイブ）
  1. 主要指数カード（ダウ30・S&P500・ナスダック100・ビットコイン・金、ページ最上部）
  2. テーマ強弱ランキング（半導体・AIインフラなど60以上のテーマの騰落率、代表銘柄スコア＋ヒートマップ）
  3. セクター強弱ランキング（11セクターETFの騰落率・参考情報として折りたたみ表示）
  4. 本日の相場考察（強気テーマ／弱気テーマを同ボリュームで併記、関連ニュースへの導線付き、
     Claude執筆版／GitHub Actionsによる自動簡易更新版を判別する更新状態バッジ付き）
  5. マイポートフォリオ（保有銘柄の登録・預り金・評価損益の円換算・4観点の見立て・日足チャート・
     ポジション調整の両論併記・ポートフォリオ強化のテーマ提案・追加投資を検討する場合の考察）
  6. 大口投資家の動き（ARK Invest / 内部者クラスター買い / SEC Form 13D / dataroma.com）
  7. 資産相関マトリクス（主要資産の値動きの相関）
  8. 期間セレクター（1日 / 1週間 / 1ヶ月）
  9. ニュースアーカイブ（Google Newsのリアルタイム簡易ニュース＋日次蓄積のニュースアーカイブ）

本サイトは事実整理であり、投資助言ではありません。
"""

import base64
import json
import re
import time
import warnings
import xml.etree.ElementTree as ET
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

# テーマ強弱ランキング用：セクターより細かいテーマ単位の代表銘柄（2〜5銘柄ずつ）。
# 市場全体を俯瞰できる粒度にするため、主要27テーマに加えて更に細かい業種・テーマを追加している。
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
    # --- ここから、市場全体をより広く俯瞰するために追加したテーマ ---
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

# ヒートマップ（全体構造の俯瞰表示）用：多数のテーマを大分類でグルーピングする。
# ランキングではなく「どのカテゴリが強くてどのカテゴリが弱いか」を一目で把握するために使う。
THEME_CATEGORIES = {
    "テクノロジー系": [
        "半導体", "メモリー", "量子コンピューティング", "光/フォトニクス",
        "AIインフラ/データセンター", "ソフトウェア/SaaS", "サイバーセキュリティ",
        "半導体製造装置", "クラウドインフラ/データセンター",
    ],
    "ヘルスケア系": [
        "バイオテック", "医療機器", "デジタルヘルス", "製薬大手", "肥満症治療薬(GLP-1)",
        "ジェネリック医薬品", "精密医療/遺伝子治療", "ヘルスケアサービス/保険",
    ],
    "金融系": ["大手銀行", "フィンテック", "保険", "暗号資産関連株", "決済/クレジットカード", "資産運用/取引所"],
    "消費系": [
        "Eコマース", "外食", "アパレル/小売", "自動車/EV",
        "ゲーム/eスポーツ", "ソーシャルメディア", "旅行/レジャー", "フードデリバリー",
        "ライドシェア", "Eラーニング/教育", "ペットケア", "玩具/レジャー用品",
        "家電/耐久消費財", "たばこ/アルコール", "化粧品/パーソナルケア",
    ],
    "エネルギー・素材系": [
        "石油ガス", "再生可能エネルギー", "電池材料/リチウム", "貴金属/鉱業",
        "水素/燃料電池", "原子力/ウラン", "太陽光発電", "LNG/天然ガス",
        "石油サービス/掘削", "EV充電インフラ", "特殊化学", "鉄鋼/金属",
    ],
    "その他": ["防衛/航空宇宙", "通信キャリア", "メディア/エンタメ", "宇宙関連", "防衛テック/インテリジェンス"],
    "資本財/物流系": ["産業オートメーション/ロボティクス", "物流/運輸", "空運", "農業/アグリテック"],
    "不動産系": ["商業用不動産REIT", "REIT物流施設", "住宅建設"],
}

# ヒートマップのセル表示用：狭い画面幅でも文字が重ならないよう、テーマ名を2〜6文字程度に短縮したもの。
# フルネームはホバー時にツールチップで表示する。
THEME_SHORT_NAMES = {
    "半導体": "半導体",
    "メモリー": "メモリ",
    "量子コンピューティング": "量子",
    "光/フォトニクス": "光学",
    "AIインフラ/データセンター": "AIインフラ",
    "ソフトウェア/SaaS": "SaaS",
    "サイバーセキュリティ": "セキュリティ",
    "バイオテック": "バイオ",
    "医療機器": "医療機器",
    "デジタルヘルス": "デジタル医療",
    "製薬大手": "製薬",
    "肥満症治療薬(GLP-1)": "GLP-1",
    "大手銀行": "銀行",
    "フィンテック": "フィンテック",
    "保険": "保険",
    "暗号資産関連株": "暗号資産",
    "Eコマース": "EC",
    "外食": "外食",
    "アパレル/小売": "小売",
    "自動車/EV": "EV",
    "石油ガス": "石油",
    "再生可能エネルギー": "再エネ",
    "電池材料/リチウム": "電池",
    "貴金属/鉱業": "鉱業",
    "防衛/航空宇宙": "防衛",
    "通信キャリア": "通信",
    "メディア/エンタメ": "メディア",
    "半導体製造装置": "製造装置",
    "クラウドインフラ/データセンター": "クラウド",
    "ゲーム/eスポーツ": "ゲーム",
    "ソーシャルメディア": "SNS",
    "旅行/レジャー": "旅行",
    "住宅建設": "住宅建設",
    "産業オートメーション/ロボティクス": "ロボット",
    "農業/アグリテック": "農業",
    "水素/燃料電池": "水素",
    "原子力/ウラン": "原子力",
    "宇宙関連": "宇宙",
    "商業用不動産REIT": "商業REIT",
    "決済/クレジットカード": "決済",
    "資産運用/取引所": "資産運用",
    "たばこ/アルコール": "嗜好品",
    "化粧品/パーソナルケア": "化粧品",
    "特殊化学": "化学",
    "鉄鋼/金属": "鉄鋼",
    "空運": "空運",
    "物流/運輸": "物流",
    "ヘルスケアサービス/保険": "医療保険",
    "ジェネリック医薬品": "ジェネリック",
    "精密医療/遺伝子治療": "遺伝子治療",
    "防衛テック/インテリジェンス": "防衛テック",
    "EV充電インフラ": "EV充電",
    "太陽光発電": "太陽光",
    "LNG/天然ガス": "LNG",
    "ペットケア": "ペット",
    "Eラーニング/教育": "教育",
    "フードデリバリー": "フード配達",
    "ライドシェア": "配車",
    "石油サービス/掘削": "油田サービス",
    "REIT物流施設": "物流REIT",
    "家電/耐久消費財": "家電",
    "玩具/レジャー用品": "玩具",
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
# SECは自動アクセス時、連絡先メール入りのUser-Agentを推奨しているが、本アプリは
# 個人利用の範囲であり、実際のブラウザと同じUser-Agentを送ることで安定してアクセスできている。
SEC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# dataroma.com（著名投資家の13F保有情報をまとめた無料サイト）のトップページ。
# 利用規約上、データの一括転載・再配布は禁止されているため、本アプリでは
# 「出典を明記した上でのごく小部分の参照利用」の範囲に限定して取得する。
DATAROMA_URL = "https://www.dataroma.com/"

# マイポートフォリオ機能：保有銘柄をportfolio.jsonとしてGitHubリポジトリに保存し、
# 次回アクセス時にはそこから読み込んで復元する（GitHub Contents APIを使用）。
# 保存にはStreamlit CloudのSecretsに設定したGITHUB_TOKEN（このリポジトリの
# Contentsのみ Read/Write 権限を持つFine-grained PAT）を使う。
GITHUB_REPO = "pande3284mk2/us-stock-dashboard"
GITHUB_PORTFOLIO_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/portfolio.json"
MAX_PORTFOLIO_HOLDINGS = 5

# レバレッジ型ETF・派生商品など、入力ティッカーが実体企業と異なる場合の変換辞書。
# 評価損益（保有株数×現在値）は入力ティッカー自体の価格を使うが、テクニカル/
# セクター・テーマ分類/ファンダメンタルズ/考察は、意味のある分析ができるよう
# 実体のある本体銘柄に変換してから行う（レバレッジ型ETFは日々の変動率を
# 増幅させただけの金融商品で、まともなファンダメンタルズ・テクニカル分析の対象にならないため）。
# 対応関係が確認できていない銘柄は無理に登録せず、確認でき次第ここに追記していく。
TICKER_ALIAS = {
    "NBIL": "NBIS",  # GraniteShares 2x Long NBIS Daily ETF → Nebius Group N.V.
    "IONL": "IONQ",  # GraniteShares 2x Long IONQ Daily ETF → IonQ, Inc.
    "AAOG": "AAOI",  # Leverage Shares 2x Long AAOI Daily ETF → Applied Optoelectronics
    # 例: "XYZL": "XYZ",  # 要確認：対応関係が確認できたら追記する
}


def resolve_analysis_ticker(ticker):
    """分析（テクニカル/セクター・テーマ/ファンダメンタルズ/考察）用に、
    レバレッジ型ETF等のティッカーを実体のある本体銘柄に変換する。
    TICKER_ALIASに無いティッカーはそのまま返す。
    """
    return TICKER_ALIAS.get(str(ticker).upper(), ticker)

# 毎朝配信しているHTMLダッシュボードの考察を、同じGitHubリポジトリに置いた
# commentary.json 経由でこのサイトにも表示する（このサイト自体はリアルタイムの
# ニュース分析はしない。事前に用意されたJSONを読み込んで表示するだけ）。
COMMENTARY_URL = (
    "https://raw.githubusercontent.com/pande3284mk2/us-stock-dashboard/main/commentary.json"
)

# 日次の自動更新タスク側で厳選・蓄積していくニュースアーカイブ（このアプリ自体は書き込みは行わず、
# 読み込んで一覧表示するだけ）。まだファイルが存在しない場合はNoneを返し、画面側でフォールバック表示する。
NEWS_ARCHIVE_URL = (
    "https://raw.githubusercontent.com/pande3284mk2/us-stock-dashboard/main/news_archive.json"
)

# 「📰 ニュースアーカイブ」ページのリアルタイム簡易ニュースで検索する経済関連キーワード。
# Google Newsの検索RSS（アカウント登録・APIキー不要・無料）を利用する。
NEWS_KEYWORDS = ["FRB", "利下げ", "半導体", "決算", "関税"]

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


def _http_get_with_retry(url, headers=None, timeout=25, retries=3, backoff=1.5):
    """一時的な混雑やタイムアウトに強くするため、GETリクエストを指数バックオフ付きで
    最大retries回まで試行する共通ヘルパー。openinsider.com や dataroma.com のような
    小規模サイトは、一時的な負荷やアクセス集中で単発のリクエストが失敗しやすいための対策。
    全て失敗した場合はNoneを返す。
    """
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception:
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    return None


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_cluster_buys():
    """openinsider.com の「クラスター買い」ページから、
    直近に複数の役員が同時期に自社株を購入した銘柄一覧を取得する。

    「売り」は10b5-1プラン（あらかじめ決めたスケジュールでの機械的売却）が
    多く含まれ、経営陣の相場観を反映しないことが多いため、原則として除外する。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        "Referer": "http://openinsider.com/",
    }
    resp = _http_get_with_retry(OPENINSIDER_URL, headers=headers)
    if resp is None:
        return None
    try:
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
        headers = {
            "User-Agent": SEC_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.sec.gov/cgi-bin/browse-edgar",
        }
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        "Referer": "https://www.dataroma.com/",
    }
    resp = _http_get_with_retry(DATAROMA_URL, headers=headers)
    if resp is None:
        return None
    try:
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


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_realtime_news(keywords=None, max_per_keyword=5):
    """Google Newsの検索RSS（アカウント登録・APIキー不要・無料）から、
    経済関連キーワードに関する直近のニュース見出しを取得する。

    RSSのパースはPython標準ライブラリのxml.etreeのみを使用し、
    追加の外部ライブラリ（feedparser等）には依存しない実装にしている。
    """
    keywords = keywords or NEWS_KEYWORDS
    headers = {"User-Agent": SEC_USER_AGENT}
    results = []
    for kw in keywords:
        try:
            url = (
                "https://news.google.com/rss/search?q="
                f"{requests.utils.quote(kw)}&hl=ja&gl=JP&ceid=JP:ja"
            )
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall("./channel/item")[:max_per_keyword]
            for item in items:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub_date = item.findtext("pubDate") or ""
                results.append(
                    {"keyword": kw, "title": title, "link": link, "published": pub_date}
                )
        except Exception:
            continue
    return results


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_news_archive():
    """GitHubリポジトリ直下の news_archive.json（日次の自動更新タスク側で厳選・蓄積していく
    ニュースアーカイブ）を取得する。このアプリ自体は書き込みは行わず、読み込んで一覧表示するだけ。
    ファイルがまだ存在しない場合や取得に失敗した場合はNoneを返し、画面側でフォールバック表示する。
    """
    try:
        resp = requests.get(NEWS_ARCHIVE_URL, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("entries", [])
    except Exception:
        return None


def _confidence_class(confidence):
    mapping = {"高": "confidence-high", "中": "confidence-mid", "低": "confidence-low"}
    return mapping.get(str(confidence), "confidence-mid")


def _get_github_token():
    """Streamlit CloudのSecretsに設定されたGitHubトークンを取得する。未設定ならNone。"""
    try:
        return st.secrets["GITHUB_TOKEN"]
    except Exception:
        return None


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def load_portfolio_from_github():
    """GitHubリポジトリ直下の portfolio.json を読み込む。

    ファイルがまだ存在しない場合や取得に失敗した場合は、空のポートフォリオを返す
    （エラーにはしない。初回利用時は誰でもファイルがまだ無い状態のため）。
    """
    try:
        resp = requests.get(GITHUB_PORTFOLIO_API_URL, headers=_github_headers(), timeout=15)
        if resp.status_code == 404:
            return {"holdings": [], "cash_jpy": 0, "updated_at": None}
        resp.raise_for_status()
        data = resp.json()
        content_b64 = data.get("content", "")
        decoded = base64.b64decode(content_b64).decode("utf-8")
        portfolio = json.loads(decoded)
        if "holdings" not in portfolio:
            portfolio["holdings"] = []
        if "cash_jpy" not in portfolio:
            portfolio["cash_jpy"] = 0
        return portfolio
    except Exception:
        return {"holdings": [], "cash_jpy": 0, "updated_at": None}


def save_portfolio_to_github(holdings, cash_jpy=0.0):
    """保有銘柄リストと預り金（現金、円建て）を portfolio.json としてGitHubリポジトリに保存する。

    GitHub Contents APIの仕様上、既存ファイルを更新する場合は現在のsha（版を示す識別子）
    を事前に取得してPUTリクエストに含める必要があるため、
    (1) GET で既存ファイルの有無とshaを確認 → (2) PUT で新規作成/更新、という順で行う。
    """
    token = _get_github_token()
    if not token:
        return False, "GitHubトークン（GITHUB_TOKEN）がStreamlit CloudのSecretsに設定されていません。"
    try:
        sha = None
        get_resp = requests.get(GITHUB_PORTFOLIO_API_URL, headers=_github_headers(), timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        payload_dict = {
            "holdings": holdings,
            "cash_jpy": cash_jpy,
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        content_str = json.dumps(payload_dict, ensure_ascii=False, indent=2)
        content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        put_payload = {
            "message": "Update portfolio.json via dashboard",
            "content": content_b64,
        }
        if sha:
            put_payload["sha"] = sha

        put_resp = requests.put(
            GITHUB_PORTFOLIO_API_URL,
            headers=_github_headers(),
            json=put_payload,
            timeout=15,
        )
        put_resp.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)


def init_portfolio_state():
    """セッション開始時（ページを開いた/リロードした時）に1度だけ、
    GitHub上のportfolio.jsonを読み込んで入力フォームの初期値として復元する。
    """
    if "portfolio_loaded" in st.session_state:
        return
    data = load_portfolio_from_github()
    holdings = data.get("holdings", [])[:MAX_PORTFOLIO_HOLDINGS]
    for i in range(MAX_PORTFOLIO_HOLDINGS):
        if i < len(holdings):
            h = holdings[i]
            st.session_state[f"pf_ticker_{i}"] = str(h.get("ticker", "")).strip().upper()
            st.session_state[f"pf_shares_{i}"] = float(h.get("shares", 0) or 0)
            st.session_state[f"pf_cost_{i}"] = float(h.get("cost_basis", 0) or 0)
        else:
            st.session_state[f"pf_ticker_{i}"] = ""
            st.session_state[f"pf_shares_{i}"] = 0.0
            st.session_state[f"pf_cost_{i}"] = 0.0
    st.session_state["pf_cash_jpy"] = float(data.get("cash_jpy", 0) or 0)
    st.session_state["portfolio_loaded"] = True


def _current_portfolio_holdings():
    """入力フォーム（st.session_state）から、有効な保有銘柄（ティッカー・株数が入力済み）
    のリストを取り出す。"""
    holdings = []
    for i in range(MAX_PORTFOLIO_HOLDINGS):
        ticker = str(st.session_state.get(f"pf_ticker_{i}", "")).strip().upper()
        shares = st.session_state.get(f"pf_shares_{i}", 0.0) or 0.0
        cost = st.session_state.get(f"pf_cost_{i}", 0.0) or 0.0
        if ticker and shares > 0:
            holdings.append({"ticker": ticker, "shares": shares, "cost_basis": cost})
    return holdings


def _current_cash_jpy():
    """入力フォーム（st.session_state）から、預り金（投資に使っていない現金、円建て）を取り出す。"""
    return st.session_state.get("pf_cash_jpy", 0.0) or 0.0


def _find_theme_for_ticker(ticker):
    """指定ティッカーが属するテーマ名の一覧を返す（複数のテーマに属する場合もある）。"""
    return [name for name, stocks in THEME_STOCKS.items() if ticker in stocks]


def _find_sector_for_ticker(ticker):
    """指定ティッカーが属するセクター名を返す（見つからなければNone）。"""
    for name, stocks in SECTOR_STOCKS.items():
        if ticker in stocks:
            return name
    return None


def _find_related_commentary(commentary_data, ticker, theme_names, sector_name):
    """commentary.jsonのthemes/insightsの中から、指定銘柄のテーマ名・セクター名・
    ティッカー自体のいずれかが本文に含まれているものを、簡易的な部分一致で探す。

    これは高度なAI分析ではなく、あらかじめ人が書いたcommentary.jsonの文章の中から
    キーワードが一致する箇所を機械的に抜き出しているだけである点に注意。
    """
    if not commentary_data:
        return []
    keywords = {k for k in list(theme_names) + [sector_name, ticker] if k}
    matches = []
    for th in commentary_data.get("themes", []):
        title = th.get("title", "")
        text = th.get("text", "")
        combined = f"{title}{text}"
        if any(kw in combined for kw in keywords):
            matches.append(
                {
                    "type": "テーマ",
                    "title": title,
                    "text": text,
                    "confidence": th.get("confidence", "中"),
                }
            )
    for ins in commentary_data.get("insights", []):
        text = ins.get("text", "")
        if any(kw in text for kw in keywords):
            matches.append(
                {
                    "type": "考察",
                    "title": None,
                    "text": text,
                    "confidence": ins.get("confidence", "中"),
                }
            )
    return matches


def _find_institutional_mentions(ticker):
    """大口投資家コーナー（ARK・内部者クラスター買い・SEC Form 13D）のデータの中に、
    指定ティッカーに関する言及がないか探す。既にキャッシュ済みのデータを再利用するため、
    追加のネットワークアクセスは発生しない（同じ関数を同一セッション内で再度呼ぶだけ）。
    """
    mentions = []

    buys, sells = get_ark_trades()
    if buys is not None and not buys.empty and "ticker" in buys.columns:
        if (buys["ticker"] == ticker).any():
            mentions.append(f"🚀 ARK Investが直近、{ticker}を買い増ししています。")
    if sells is not None and not sells.empty and "ticker" in sells.columns:
        if (sells["ticker"] == ticker).any():
            mentions.append(f"🚀 ARK Investが直近、{ticker}を売却しています。")

    cluster_df = get_cluster_buys()
    if cluster_df is not None and not cluster_df.empty and "Ticker" in cluster_df.columns:
        if (cluster_df["Ticker"] == ticker).any():
            mentions.append(f"🕵️ 直近、{ticker}で複数役員による自社株クラスター買いが確認されています。")

    filings = get_sec_13d_filings()
    if filings:
        for f in filings:
            issuer = f.get("対象企業", "")
            if f"({ticker})" in issuer or issuer == ticker:
                investor = f.get("投資家(提出者)", "-")
                mentions.append(f"📜 SEC Form 13Dで、{investor} が {ticker} について5%超保有の提出を行っています。")

    return mentions


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_stock_history(ticker, period="6mo"):
    """個別銘柄の日足データ（始値・高値・安値・終値）を取得する。
    テクニカル分析（移動平均線など）とローソク足チャートの両方に使う。
    """
    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1d")
        if hist is None or hist.empty:
            return pd.DataFrame()
        return hist
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_usdjpy_rate():
    """USDJPY=X（相関マトリクスと同じyfinanceティッカー）から、直近のドル円レートを取得する。
    ポートフォリオの評価損益を円換算表示するために使う。取得できない場合はNoneを返す。
    """
    try:
        hist = yf.Ticker("USDJPY=X").history(period="5d", interval="1d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception:
        return None


def compute_technical_view(ticker):
    """20日・50日移動平均線との位置関係や、直近のゴールデンクロス/デッドクロス、
    直近5営業日のモメンタムから、簡易的なテクニカル分析コメントを機械的に組み立てる。

    これはAIによる予測ではなく、移動平均線の計算結果を条件分岐で文章化しているだけである点に注意。
    """
    hist = get_stock_history(ticker, "6mo")
    close = hist["Close"].dropna() if not hist.empty else pd.Series(dtype=float)
    if len(close) < 25:
        return None

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean() if len(close) >= 50 else pd.Series(dtype=float)
    last_close = close.iloc[-1]
    last_sma20 = sma20.dropna().iloc[-1] if not sma20.dropna().empty else None
    last_sma50 = sma50.dropna().iloc[-1] if not sma50.dropna().empty else None

    cross = None
    if not sma20.dropna().empty and not sma50.dropna().empty:
        diff = (sma20 - sma50).dropna()
        recent = diff.tail(10)
        if len(recent) >= 2:
            sign = (recent > 0).astype(int)
            changes = sign.diff().dropna()
            if (changes == 1).any():
                cross = "ゴールデンクロス"
            elif (changes == -1).any():
                cross = "デッドクロス"

    momentum_5d = None
    if len(close) > 5:
        momentum_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100

    above20 = last_sma20 is not None and last_close >= last_sma20
    above50 = last_sma50 is not None and last_close >= last_sma50
    below20 = last_sma20 is not None and last_close < last_sma20
    below50 = last_sma50 is not None and last_close < last_sma50

    lines = []
    if last_sma20 is not None:
        pos20 = "上" if above20 else "下"
        lines.append(f"20日移動平均線（¥VAL20）の{pos20}に位置しています。")
    if last_sma50 is not None:
        pos50 = "上" if above50 else "下"
        lines.append(f"50日移動平均線（¥VAL50）の{pos50}に位置しています。")
    if cross:
        lines.append(f"直近10営業日以内に{cross}（20日線と50日線の交差）が発生しています。")
    if momentum_5d is not None:
        direction = "上昇" if momentum_5d >= 0 else "下落"
        lines.append(f"直近5営業日の値動きは{momentum_5d:+.2f}%と{direction}基調です。")

    if not lines:
        return None

    text = "テクニカル的には、" + "".join(lines)
    text = text.replace("¥VAL20", f"${last_sma20:,.2f}" if last_sma20 is not None else "-")
    text = text.replace("¥VAL50", f"${last_sma50:,.2f}" if last_sma50 is not None else "-")

    return {
        "text": text,
        "above_sma20": above20,
        "above_sma50": above50,
        "below_sma20": below20,
        "below_sma50": below50,
        "cross": cross,
        "momentum_5d": momentum_5d,
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def get_fundamentals(ticker):
    """yfinanceのticker.infoから、株価指標（PER・利益率・売上成長率・時価総額）を取得する。
    無料のyfinance経由のため、銘柄によっては一部の項目が取得できないことがある
    （その場合は呼び出し側で「データなし」と表示する）。
    """
    try:
        info = yf.Ticker(ticker).info or {}
        return {
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "profitMargins": info.get("profitMargins"),
            "revenueGrowth": info.get("revenueGrowth"),
            "marketCap": info.get("marketCap"),
        }
    except Exception:
        return {}


def render_fundamentals_text(ticker):
    """ファンダメンタルズ指標を、取得できたものだけ簡潔な文章にまとめる。
    取得できない項目は無理に埋めず「データなし」と表示する。
    """
    f = get_fundamentals(ticker)
    if not f:
        return "ファンダメンタルズデータを取得できませんでした。"

    tpe = f.get("trailingPE")
    fpe = f.get("forwardPE")
    pm = f.get("profitMargins")
    rg = f.get("revenueGrowth")
    mc = f.get("marketCap")

    parts = [
        f"実績PER: {tpe:.1f}倍" if tpe else "実績PER: データなし",
        f"予想PER: {fpe:.1f}倍" if fpe else "予想PER: データなし",
        f"利益率: {pm * 100:+.1f}%" if pm is not None else "利益率: データなし",
        f"売上成長率: {rg * 100:+.1f}%" if rg is not None else "売上成長率: データなし",
        (f"時価総額: ¥MC" if mc else "時価総額: データなし"),
    ]
    text = " ／ ".join(parts)
    text = text.replace("¥MC", f"${mc / 1e9:,.1f}B" if mc else "")
    return text


def render_stock_chart(ticker):
    """保有銘柄の日足チャート（ローソク足、直近6ヶ月）を表示する。"""
    hist = get_stock_history(ticker, "6mo")
    if hist is None or hist.empty:
        st.caption(f"{ticker}のチャートデータを取得できませんでした。")
        return
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=hist.index,
                open=hist["Open"],
                high=hist["High"],
                low=hist["Low"],
                close=hist["Close"],
                increasing_line_color="#00cc96",
                decreasing_line_color="#ef553b",
                name=ticker,
            )
        ]
    )
    fig.update_layout(
        template="plotly_dark",
        height=320,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _theme_institutional_overlap(theme_name):
    """テーマの代表銘柄のいずれかが、大口投資家データ（ARK・クラスター買い・SEC 13D）に
    登場していないか確認する（既にキャッシュ済みのデータを再利用）。
    """
    hits = []
    for stk in THEME_STOCKS.get(theme_name, []):
        hits.extend(_find_institutional_mentions(stk))
    return hits


def classify_commentary_themes(commentary_data):
    """commentary.jsonのthemes配列を、本文中の簡易キーワードの出現数によって
    「強気寄り」「弱気寄り」に機械的に振り分ける（AIによる判定ではない）。
    """
    bullish_words = ["上昇", "強い", "堅調", "資金流入", "買い増し", "追い風", "上振れ", "強含み", "好調", "高値"]
    bearish_words = ["下落", "軟調", "弱い", "売り", "下振れ", "逆風", "懸念", "リスク", "軟化", "低調", "安値"]
    bullish, bearish = [], []
    for th in (commentary_data.get("themes", []) if commentary_data else []):
        text = f"{th.get('title', '')}{th.get('text', '')}"
        b = sum(1 for w in bullish_words if w in text)
        r = sum(1 for w in bearish_words if w in text)
        if r > b:
            bearish.append(th)
        else:
            bullish.append(th)
    return bullish, bearish


# =====================================================
# 画面表示用の補助関数

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


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _theme_price_data(period_key):
    """全テーマの代表銘柄をまとめて1回のfetch_pricesで取得する（内部用）。
    THEME_STOCKSの値はテーマ間で銘柄が重複することがあるが、ここでset内包表記により
    重複を除去してからfetch_pricesに渡しているため、同じ銘柄を何度も取得することはない。
    """
    cfg = PERIOD_OPTIONS[period_key]
    all_tickers = sorted({t for stocks in THEME_STOCKS.values() for t in stocks})
    return fetch_prices(tuple(all_tickers), cfg["yf_period"])


def compute_theme_ranking(period_key):
    """全テーマ（THEME_STOCKSに定義された全業種・テーマ）の強弱ランキングを計算する。

    各テーマの代表銘柄（3〜5銘柄）の期間内騰落率を単純平均してスコア化し、
    強い順にソートした上で「順位」列を付与したDataFrameを返す。
    マイポートフォリオの「見立て」コーナーからも同じ計算結果を再利用する。
    """
    cfg = PERIOD_OPTIONS[period_key]
    price_data = _theme_price_data(period_key)

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
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("騰落率", ascending=False).reset_index(drop=True)
    df["順位"] = df.index + 1
    return df


def render_theme_heatmap(df):
    """全テーマを大分類（テクノロジー系・ヘルスケア系など）でグルーピングした横長の
    格子状ヒートマップとして表示する。これはランキングではなく、「どの大分類が強くて
    どの大分類が弱いか」という全体の構造を一目で俯瞰するための表示である。

    横軸=大分類、縦軸=各分類内での相対順位（強い順）というレイアウトにすることで、
    PC・スマホともに横長のレイアウトを維持できるようにしている。狭い画面幅でも文字が
    重ならないよう、テーマ名は2〜6文字程度に短縮表示し、フルネームと正確なスコアは
    ホバー時のツールチップで確認できるようにしている。
    """
    if df.empty:
        return
    score_map = dict(zip(df["テーマ名"], df["騰落率"]))
    categories = list(THEME_CATEGORIES.keys())
    max_rows = max(len(v) for v in THEME_CATEGORIES.values())

    nan = float("nan")
    grid_z = [[nan for _ in categories] for _ in range(max_rows)]
    grid_text = [["" for _ in categories] for _ in range(max_rows)]
    grid_full = [["" for _ in categories] for _ in range(max_rows)]

    for c_idx, cat in enumerate(categories):
        theme_names = THEME_CATEGORIES[cat]
        ranked = sorted(
            (t for t in theme_names if t in score_map),
            key=lambda t: score_map[t],
            reverse=True,
        )
        for r_idx, theme_name in enumerate(ranked):
            score = score_map[theme_name]
            short = THEME_SHORT_NAMES.get(theme_name, theme_name[:3])
            grid_z[r_idx][c_idx] = score
            grid_text[r_idx][c_idx] = f"{short}<br>{score:+.1f}%"
            grid_full[r_idx][c_idx] = f"{theme_name}: {score:+.2f}%"

    fig = px.imshow(
        grid_z,
        color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        aspect="auto",
        x=categories,
    )
    fig.update_traces(
        text=grid_text,
        texttemplate="%{text}",
        textfont_size=10,
        customdata=grid_full,
        hovertemplate="%{customdata}<extra></extra>",
    )
    fig.update_layout(
        template="plotly_dark",
        height=42 * max_rows + 80,
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis_visible=False,
        xaxis=dict(side="top", tickfont=dict(size=11)),
        coloraxis_colorbar=dict(title="騰落率(%)"),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "横軸は大分類（テクノロジー系・ヘルスケア系など）、各列内は上ほどそのカテゴリ内で"
        "相対的に強いテーマです。色が濃い緑ほど強く、濃い赤ほど弱いことを示します。"
        "セルにカーソルを合わせるとテーマのフルネームと正確なスコアを確認できます。"
    )


def _theme_bar_fig(sub_df):
    """テーマ強弱ランキングの一部（Top15またはWorst10）を横棒グラフにする内部用ヘルパー。"""
    fig = go.Figure(
        go.Bar(
            x=sub_df["騰落率"],
            y=sub_df["テーマ名"],
            orientation="h",
            marker_color=["#00cc96" if v >= 0 else "#ef553b" for v in sub_df["騰落率"]],
            text=[f"{v:+.2f}%" for v in sub_df["騰落率"]],
            textposition="outside",
            customdata=sub_df["構成銘柄"],
            hovertemplate="<b>%{y}</b><br>平均騰落率: %{x:+.2f}%<br>構成銘柄: %{customdata}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        height=max(320, 34 * len(sub_df) + 60),
        xaxis_title="平均騰落率 (%)",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=10, r=30, t=20, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def render_theme_strength(period_key):
    """セクターより細かい「テーマ」単位（半導体・AIインフラなど）の強弱ランキングを、
    このダッシュボードの主役セクションとして表示する。市場全体を広く俯瞰できるよう
    THEME_STOCKSには60以上のテーマを定義しており、その全テーマの中から
    「🟢 強いテーマ Top15」「🔴 弱いテーマ Worst10」の2グループに絞って表示し、
    間の順位のテーマは表示しない。各テーマは展開すると代表銘柄ごとの個別スコアも確認できる。
    続けて、全テーマを大分類でグルーピングしたヒートマップ（全体構造の俯瞰用）を表示する。
    """
    df = compute_theme_ranking(period_key)
    if df.empty:
        st.warning("テーマ強弱データの取得に失敗しました。しばらく待ってから再読み込みしてください。")
        return

    price_data = _theme_price_data(period_key)
    cfg = PERIOD_OPTIONS[period_key]

    top_df = df.head(15)
    remaining_df = df[~df["テーマ名"].isin(top_df["テーマ名"])]
    bottom_df = remaining_df.tail(10)

    st.markdown("**🟢 強いテーマ Top15**")
    st.plotly_chart(_theme_bar_fig(top_df), use_container_width=True)

    st.markdown("**🔴 弱いテーマ Worst10**")
    st.plotly_chart(_theme_bar_fig(bottom_df), use_container_width=True)

    st.caption(
        "各テーマの代表銘柄（3〜5銘柄）の期間内騰落率を単純平均したスコアです。"
        "バーにカーソルを合わせると構成銘柄を確認できます。間の順位のテーマは表示していません。"
    )

    st.caption("テーマごとの代表銘柄別スコアを見るには、下の項目をクリックして展開してください。")
    display_df = pd.concat([top_df, bottom_df])
    for _, row in display_df.iterrows():
        theme_name = row["テーマ名"]
        stocks = THEME_STOCKS.get(theme_name, [])
        if not stocks:
            continue
        with st.expander(f"第{int(row['順位'])}位　{theme_name}（{row['騰落率']:+.2f}%）の代表銘柄スコア"):
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

    st.markdown("**🗺️ 全体構造ヒートマップ（大分類別）**")
    render_theme_heatmap(df)


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


def _sec_13d_insight(rows):
    """SEC Form 13Dの提出データから、簡単な傾向を機械的に読み取る。

    AIによる分析ではなく、件数の集計・投資家名の重複チェックといった
    機械的な処理だけで組み立てている点に注意。
    """
    if not rows:
        return None
    investor_counts = {}
    for r in rows:
        inv = r.get("投資家(提出者)", "-")
        if inv and inv != "-":
            investor_counts[inv] = investor_counts.get(inv, 0) + 1
    repeat_investors = [inv for inv, c in investor_counts.items() if c > 1]

    lines = [f"直近30日間で{len(rows)}件のSchedule 13D提出が確認されています。"]
    if repeat_investors:
        names = "、".join(repeat_investors[:3])
        lines.append(f"「{names}」は複数の企業で新規5%超保有を提出しており、積極的な投資行動が見られます。")
        confidence = "中"
    else:
        lines.append("今回は特筆すべき傾向（同一投資家による複数件の提出など）は見られません。")
        confidence = "低"
    return " ".join(lines), confidence


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

    insight = _sec_13d_insight(rows)
    if insight:
        text, confidence = insight
        st.markdown(
            f'<div class="insight-box">{text}'
            f'<span class="confidence-badge {_confidence_class(confidence)}">確度: {confidence}</span></div>',
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
    """「大口投資家の動き」を、4つの情報源をタブで切り替えて俯瞰できるようにする。
    最も取得が安定しているARK Investを先頭タブにしている。
    """
    tab1, tab2, tab3, tab4 = st.tabs(
        ["🚀 ARK Invest", "🕵️ 内部者クラスター買い", "📜 SEC Form 13D", "💎 著名投資家(Dataroma)"]
    )
    with tab1:
        render_ark_trades()
    with tab2:
        render_cluster_buys()
    with tab3:
        render_sec_13d()
    with tab4:
        render_dataroma_highlights()


def render_portfolio_form():
    """保有銘柄の入力フォーム（最大5銘柄）。「保存する」を押すとGitHub上の
    portfolio.jsonに書き込む。
    """
    st.caption(
        f"最大{MAX_PORTFOLIO_HOLDINGS}銘柄まで登録できます。"
        "ティッカー・保有株数・取得単価（1株あたり）を入力して「保存する」を押してください。"
    )
    for i in range(MAX_PORTFOLIO_HOLDINGS):
        cols = st.columns([2, 2, 2])
        with cols[0]:
            st.text_input(f"ティッカー {i + 1}", key=f"pf_ticker_{i}", placeholder="例: NVDA")
        with cols[1]:
            st.number_input(
                f"保有株数 {i + 1}",
                key=f"pf_shares_{i}",
                min_value=0.0,
                step=1.0,
                format="%.4f",
            )
        with cols[2]:
            st.number_input(
                f"取得単価(1株) {i + 1}",
                key=f"pf_cost_{i}",
                min_value=0.0,
                step=0.01,
                format="%.2f",
            )

    st.number_input(
        "💰 預り金（円）— 投資に使っていない現金",
        key="pf_cash_jpy",
        min_value=0.0,
        step=10000.0,
        format="%.0f",
        help="株式等に投資していない現金の金額を円建てで入力してください。",
    )

    if st.button("💾 保存する"):
        holdings = _current_portfolio_holdings()
        cash_jpy = _current_cash_jpy()
        ok, err = save_portfolio_to_github(holdings, cash_jpy)
        if ok:
            st.success("✅ ポートフォリオをGitHubに保存しました。次回このアプリを開いた時にも復元されます。")
        else:
            st.error(f"😔 保存に失敗しました：{err}")


def render_portfolio_holdings(period_key):
    """保有銘柄それぞれについて、現在値・評価損益（金額／％、USD建てと円換算の併記）を計算して表示する。
    次の「見立て」コーナーで使うため、各銘柄の現在値・期間内騰落率も含めて返す。
    """
    cash_jpy = _current_cash_jpy()
    if cash_jpy > 0:
        st.metric("💰 預り金（円）", f"¥{cash_jpy:,.0f}")

    holdings = _current_portfolio_holdings()
    if not holdings:
        st.info("📭 保有銘柄が登録されていません。上のフォームから入力して保存してください。")
        return []

    cfg = PERIOD_OPTIONS[period_key]
    tickers = [h["ticker"] for h in holdings]
    price_data = fetch_prices(tuple(tickers), cfg["yf_period"])
    usdjpy_rate = get_usdjpy_rate()

    results = []
    for h in holdings:
        close = _extract_close(price_data, h["ticker"])
        period_chg = _pct_change_from_series(close, cfg["lookback"])
        current_price = None
        if close is not None and not close.dropna().empty:
            current_price = close.dropna().iloc[-1]

        cols = st.columns(4)
        with cols[0]:
            st.metric("銘柄", h["ticker"])
        with cols[1]:
            st.metric("取得単価", f"${h['cost_basis']:,.2f}")
        if current_price is None:
            with cols[2]:
                st.metric("現在値", "取得失敗")
            with cols[3]:
                st.metric("評価損益", "-")
        else:
            pl_amount = (current_price - h["cost_basis"]) * h["shares"]
            pl_pct = (current_price / h["cost_basis"] - 1) * 100 if h["cost_basis"] else None
            with cols[2]:
                st.metric(
                    "現在値",
                    f"${current_price:,.2f}",
                    delta=f"{period_chg:+.2f}%" if period_chg is not None else None,
                )
            with cols[3]:
                pl_value_text = f"${pl_amount:,.2f}"
                if usdjpy_rate:
                    pl_amount_jpy = pl_amount * usdjpy_rate
                    pl_value_text += f"（約{pl_amount_jpy / 10000:+.1f}万円）"
                st.metric(
                    "評価損益",
                    pl_value_text,
                    delta=f"{pl_pct:+.2f}%" if pl_pct is not None else None,
                )

        results.append(
            {
                **h,
                "current_price": current_price,
                "period_chg": period_chg,
            }
        )

    return results


def render_position_view(h, best_theme_row, tech, mentions, total_themes):
    """「積み増し方向の材料」と「慎重方向の材料」を、既に持っている事実データから
    機械的に両論併記する。断定的な結論（増やすべき/減らすべき）は書かない。
    """
    ticker = h["ticker"]
    pros, cons = [], []

    if best_theme_row is not None:
        rank = int(best_theme_row["順位"])
        if rank <= max(1, total_themes // 2):
            pros.append(
                f"所属テーマ「{best_theme_row['テーマ名']}」は本日{total_themes}テーマ中{rank}位と上位につけています（確度：中）。"
            )
        else:
            cons.append(
                f"所属テーマ「{best_theme_row['テーマ名']}」は本日{total_themes}テーマ中{rank}位と下位に位置しています（確度：中）。"
            )

    for m in mentions:
        clean = m.split(" ", 1)[1] if " " in m else m
        if "買い" in m:
            pros.append(f"{clean}（確度：中）")
        elif "売却" in m:
            cons.append(f"{clean}（確度：中）")

    if tech:
        if tech.get("above_sma20") and tech.get("above_sma50"):
            pros.append("テクニカル面では20日線・50日線ともに上に位置しており、短中期の上昇基調が続いているという見方もできます（確度：中）。")
        if tech.get("below_sma20") and tech.get("below_sma50"):
            cons.append("テクニカル面では20日線・50日線をともに下回っており、短中期の調整局面にあるという見方もできます（確度：中）。")
        if tech.get("cross") == "ゴールデンクロス":
            pros.append("直近でゴールデンクロス（20日線が50日線を上抜け）が発生しています（確度：中）。")
        if tech.get("cross") == "デッドクロス":
            cons.append("直近でデッドクロス（20日線が50日線を下抜け）が発生しています（確度：中）。")

    if not pros:
        pros.append("本日時点で、積み増し方向を積極的に裏付ける材料は特に見当たりませんでした（確度：低）。")
    if not cons:
        cons.append("本日時点で、慎重姿勢を積極的に裏付ける材料は特に見当たりませんでした（確度：低）。")

    st.caption("⚠️ 以下は投資助言ではなく、公開情報を組み合わせた参考情報です。最終的な判断はご自身で行ってください。")
    st.markdown(
        f'<div class="fact-box"><b>[積み増し方向で見た場合の材料]</b> {" ".join(pros)}</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="fact-box"><b>[慎重に見た場合の材料]</b> {" ".join(cons)}</div>',
        unsafe_allow_html=True,
    )


def _pick_expansion_theme_candidates(holdings, period_key, commentary_data):
    """保有銘柄がカバーしていないテーマの中から、モメンタム・大口投資家の動き・マクロ材料の
    重なりが強いものを1〜2個ピックアップする（内部用のヘルパー。複数箇所から再利用する）。
    """
    held_themes = set()
    for h in holdings:
        held_themes.update(_find_theme_for_ticker(resolve_analysis_ticker(h["ticker"])))

    theme_df = compute_theme_ranking(period_key)
    if theme_df.empty:
        return [], theme_df

    candidates = []
    for _, row in theme_df.iterrows():
        name = row["テーマ名"]
        if name in held_themes:
            continue
        inst_hits = _theme_institutional_overlap(name)
        macro_hits = _find_related_commentary(commentary_data, "", [name], None)
        overlap = (1 if inst_hits else 0) + (1 if macro_hits else 0)
        candidates.append((row, inst_hits, macro_hits, overlap))

    if not candidates:
        return [], theme_df

    candidates.sort(key=lambda c: (-c[3], c[0]["順位"]))
    picks = [c for c in candidates if c[3] > 0][:2]
    if not picks:
        picks = candidates[:1]
    return picks, theme_df


def _render_expansion_picks(picks, theme_df):
    """_pick_expansion_theme_candidates()のピックアップ結果を、確度バッジ付きの
    insight-boxとして表示する（内部用のヘルパー）。
    """
    for row, inst_hits, macro_hits, overlap in picks:
        name = row["テーマ名"]
        rank = int(row["順位"])
        chg = row["騰落率"]
        stocks = THEME_STOCKS.get(name, [])
        example = "、".join(stocks[:3])
        parts = [
            f"「{name}」は本日{len(theme_df)}テーマ中{rank}位（平均騰落率 {chg:+.2f}%）と、モメンタムの強さが見られます。"
        ]
        if inst_hits:
            parts.append("大口投資家の動きの中にも、このテーマの関連銘柄への言及が見られます。")
        if macro_hits:
            parts.append("本日の考察の中にも、このテーマに関連する言及があります。")
        confidence = "中" if overlap > 0 else "低"
        text = (
            " ".join(parts)
            + f" 現在の保有銘柄ではカバーされていないテーマです。テーマの代表銘柄の例：{example} など。"
        )
        st.markdown(
            f'<div class="insight-box">{text}'
            f'<span class="confidence-badge {_confidence_class(confidence)}">確度: {confidence}</span></div>',
            unsafe_allow_html=True,
        )


def render_portfolio_expansion_view(holdings, period_key, commentary_data):
    """現在の保有銘柄がカバーしていないテーマの中から、モメンタム・大口投資家の動き・
    マクロ材料の重なりが強いものを1〜2個ピックアップして紹介する（銘柄の名指し推奨はしない）。
    """
    picks, theme_df = _pick_expansion_theme_candidates(holdings, period_key, commentary_data)
    if not picks:
        return
    st.caption("⚠️ 以下は投資助言ではなく、公開情報を組み合わせた参考情報です。特定銘柄の売買を推奨するものではありません。")
    _render_expansion_picks(picks, theme_df)


def render_investment_consideration(holdings_with_price, period_key, commentary_data, cash_jpy, usdjpy_rate):
    """預り金（現金）と現在の保有状況を踏まえて、「もし追加投資を検討するなら」という
    一つの考察例を、a. 追加投入する場合／b. 利益確定・縮小する場合／c. 新規テーマへの配分、
    という3つの観点から機械的に整理する。

    「〜すべき」という断定や一人称での推奨は行わず、あくまで複数の見方を事実ベースで
    提示するだけである点に注意（両論併記のrender_position_viewと同じ設計思想）。
    """
    st.caption(
        "⚠️ これは投資助言ではありません。以下は公開情報を組み合わせた一つの考察例であり、"
        "実際の投資判断はご自身の責任で行ってください。"
    )
    if cash_jpy <= 0 and not holdings_with_price:
        st.info("預り金・保有銘柄がいずれも登録されていないため、この考察は表示できません。")
        return

    theme_df = compute_theme_ranking(period_key)
    total_themes = len(theme_df)

    holding_theme_rows = []
    for h in holdings_with_price:
        analysis_ticker = resolve_analysis_ticker(h["ticker"])
        theme_names = _find_theme_for_ticker(analysis_ticker)
        if theme_names and not theme_df.empty:
            cand = theme_df[theme_df["テーマ名"].isin(theme_names)]
            if not cand.empty:
                holding_theme_rows.append((h, cand.sort_values("順位").iloc[0]))
    holding_theme_rows.sort(key=lambda x: x[1]["順位"])

    # --- a. 預り金を今のポジションに追加投入する場合の考え方 ---
    st.markdown("**a. 預り金を今のポジションに追加投入する場合の考え方**")
    if cash_jpy > 0:
        cash_note = f"現在登録されている預り金は約{cash_jpy:,.0f}円です。"
        if usdjpy_rate:
            cash_note += f"（1ドル={usdjpy_rate:,.2f}円換算で約${cash_jpy / usdjpy_rate:,.2f}相当）"
    else:
        cash_note = "現在、預り金は登録されていません。"

    if holding_theme_rows:
        top_h, top_row = holding_theme_rows[0]
        text_a = (
            f"{cash_note} 保有銘柄の中では「{top_h['ticker']}」が属する「{top_row['テーマ名']}」テーマが"
            f"本日{total_themes}テーマ中{int(top_row['順位'])}位と相対的に強く、"
            "一つの考え方として、既存ポジションに資金を追加する対象として着目することもできます。"
        )
        conf_a = "中"
    else:
        text_a = f"{cash_note} ただし、テーマの強弱を判定できる保有銘柄が見当たらないため、この観点からの具体的な材料は挙げられません。"
        conf_a = "低"
    st.markdown(
        f'<div class="insight-box">{text_a}'
        f'<span class="confidence-badge {_confidence_class(conf_a)}">確度: {conf_a}</span></div>',
        unsafe_allow_html=True,
    )

    # --- b. 一部を利益確定・縮小する場合の考え方 ---
    st.markdown("**b. 一部を利益確定・縮小する場合の考え方**")
    parts_b = []
    gain_candidates = [
        (h["ticker"], (h["current_price"] / h["cost_basis"] - 1) * 100)
        for h in holdings_with_price
        if h.get("current_price") is not None and h.get("cost_basis")
    ]
    if gain_candidates:
        gain_candidates.sort(key=lambda x: -x[1])
        top_gain_ticker, top_gain_pct = gain_candidates[0]
        if top_gain_pct > 0:
            parts_b.append(
                f"「{top_gain_ticker}」は含み益が{top_gain_pct:+.1f}%と大きく、"
                "利益確定や一部縮小を検討する視点もあります。"
            )
    lagging = [x for x in holding_theme_rows if x[1]["順位"] > max(1, total_themes // 2)]
    if lagging:
        lag_h, lag_row = lagging[-1]
        parts_b.append(
            f"「{lag_h['ticker']}」が属する「{lag_row['テーマ名']}」テーマは"
            f"本日{total_themes}テーマ中{int(lag_row['順位'])}位と勢いが鈍化しており、"
            "こちらも縮小方向の一つの材料として見ることもできます。"
        )
    if parts_b:
        text_b = " ".join(parts_b)
        conf_b = "中"
    else:
        text_b = "本日時点で、利益確定や縮小を積極的に裏付ける材料は特に見当たりませんでした。"
        conf_b = "低"
    st.markdown(
        f'<div class="insight-box">{text_b}'
        f'<span class="confidence-badge {_confidence_class(conf_b)}">確度: {conf_b}</span></div>',
        unsafe_allow_html=True,
    )

    # --- c. 新しいテーマ・銘柄に配分する場合の考え方 ---
    st.markdown("**c. 新しいテーマ・銘柄に配分する場合の考え方**")
    picks, exp_theme_df = _pick_expansion_theme_candidates(holdings_with_price, period_key, commentary_data)
    if picks:
        _render_expansion_picks(picks, exp_theme_df)
    else:
        st.markdown(
            '<div class="fact-box">本日時点で、新規配分先として紹介できる候補テーマは見当たりませんでした。</div>',
            unsafe_allow_html=True,
        )


def render_portfolio_assessment(holdings_with_price, period_key):
    """保有銘柄ごとに、テクニカル・テーマ/セクター・マクロ・ファンダメンタルズの4観点から
    「見立て」を組み立て、既存の事実(水色)/考察(紫・確度バッジ)デザインで表示する。
    続けて、ポジション調整の両論併記・ポートフォリオ強化のテーマ提案・日足チャートも表示する。

    これは投資助言ではなく、あくまで既に取得済みの公開情報を組み合わせて機械的に文章化した
    参考情報である。関連情報が見つからない銘柄については、その旨を正直に表示する。
    """
    disclaimer = (
        "⚠️ 本コーナーは投資助言ではなく、公開情報を組み合わせた参考情報です。"
        "売買判断はご自身の責任で行ってください。"
    )
    st.caption(disclaimer)

    if not holdings_with_price:
        st.info("保有銘柄が登録されていないため、見立てを表示できません。")
        return

    theme_df = compute_theme_ranking(period_key)
    total_themes = len(theme_df)
    commentary_data = get_commentary()

    for h in holdings_with_price:
        ticker = h["ticker"]
        analysis_ticker = resolve_analysis_ticker(ticker)
        is_alias = analysis_ticker != ticker
        theme_names = _find_theme_for_ticker(analysis_ticker)
        sector_name = _find_sector_for_ticker(analysis_ticker)

        header = f"**📌 {ticker}**"
        if is_alias:
            header += f"　（レバレッジ型ETF等のため、分析は本体銘柄「{analysis_ticker}」換算）"
        st.markdown(header)

        best_theme_row = None
        if theme_names and not theme_df.empty:
            candidates = theme_df[theme_df["テーマ名"].isin(theme_names)]
            if not candidates.empty:
                best_theme_row = candidates.sort_values("順位").iloc[0]

        mentions = _find_institutional_mentions(analysis_ticker)

        # --- a. テクニカル分析 ---
        tech = compute_technical_view(analysis_ticker)
        if tech:
            st.markdown(f'<div class="fact-box"><b>[a. テクニカル]</b> {tech["text"]}</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="fact-box"><b>[a. テクニカル]</b> データ不足のため判定できませんでした。</div>',
                unsafe_allow_html=True,
            )

        # --- b. セクター/テーマの流れ ---
        display_name = f"{ticker}（本体: {analysis_ticker}）" if is_alias else ticker
        theme_facts = []
        if best_theme_row is not None:
            theme_facts.append(
                f"{display_name}は「{best_theme_row['テーマ名']}」テーマに分類され、"
                f"本日のテーマ強弱ランキングでは全{total_themes}テーマ中"
                f"{int(best_theme_row['順位'])}位（平均騰落率 {best_theme_row['騰落率']:+.2f}%）です。"
            )
        if sector_name:
            theme_facts.append(f"伝統的な11セクター分類では「{sector_name}」セクターに属します。")
        if h.get("period_chg") is not None:
            theme_facts.append(f"{ticker}自体（保有している銘柄そのもの）の選択期間内の騰落率は {h['period_chg']:+.2f}% でした。")
        theme_facts.extend(mentions)
        if not theme_facts:
            theme_facts.append(f"{display_name}について、本日時点でこのダッシュボードが把握している分類情報はありませんでした。")
        st.markdown(
            f'<div class="fact-box"><b>[b. テーマ/セクターの流れ]</b> {" ".join(theme_facts)}</div>',
            unsafe_allow_html=True,
        )

        # --- c. マクロ要因（commentary.jsonとの関連付け） ---
        related = _find_related_commentary(commentary_data, analysis_ticker, theme_names, sector_name)
        if related:
            for rel in related:
                badge_cls = _confidence_class(rel["confidence"])
                label = f"<b>[c. マクロ] [{rel['type']}] {rel['title']}</b><br>" if rel.get("title") else f"<b>[c. マクロ] [{rel['type']}]</b> "
                st.markdown(
                    f'<div class="insight-box">{label}{rel["text"]}'
                    f'<span class="confidence-badge {badge_cls}">確度: {rel["confidence"]}</span></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="insight-box"><b>[c. マクロ]</b> 本日はこの銘柄に関する直接的なマクロ材料が見当たりません。'
                '<span class="confidence-badge confidence-low">確度: 低</span></div>',
                unsafe_allow_html=True,
            )

        # --- d. ファンダメンタルズ ---
        fundamentals_text = render_fundamentals_text(analysis_ticker)
        st.markdown(
            f'<div class="fact-box"><b>[d. ファンダメンタルズ]</b> {fundamentals_text}</div>',
            unsafe_allow_html=True,
        )

        # --- 総合的な位置づけ ---
        if best_theme_row is not None:
            theme_chg = best_theme_row["騰落率"]
            stock_chg = h.get("period_chg")
            if stock_chg is not None:
                same_direction = (theme_chg >= 0) == (stock_chg >= 0)
                alignment = "この流れに沿っている" if same_direction else "この流れとはやや逆行している"
            else:
                alignment = "この流れに位置づけられそうです"
            summary = (
                f"保有銘柄「{ticker}」は本日「{best_theme_row['テーマ名']}」というテーマの動きと関連しており、"
                f"あなたの保有は{alignment}可能性があります。"
            )
            st.markdown(
                f'<div class="insight-box">{summary}'
                f'<span class="confidence-badge confidence-mid">確度: 中</span></div>',
                unsafe_allow_html=True,
            )

        # --- 日足チャート ---
        chart_note = f"（本体銘柄「{analysis_ticker}」のチャートを表示）" if is_alias else ""
        st.markdown(f"**📈 {ticker} 日足チャート（直近6ヶ月）**{chart_note}")
        render_stock_chart(analysis_ticker)

        # --- ポジション調整の両論併記 ---
        st.markdown("**⚖️ ポジション調整に関する参考材料（両論併記）**")
        render_position_view(h, best_theme_row, tech, mentions, total_themes)

        st.markdown("<hr style='border-color:#2a2f3d; margin: 8px 0 16px 0;'>", unsafe_allow_html=True)

    # --- ポートフォリオ強化の考察（テーマ単位） ---
    st.markdown("**🧭 ポートフォリオ強化の考察（テーマ単位）**")
    render_portfolio_expansion_view(holdings_with_price, period_key, commentary_data)

    st.caption(disclaimer)


def render_commentary(period_key):
    """毎朝配信しているHTMLダッシュボードの考察(commentary.json)を表示する。

    事実(facts)は水色系の枠、考察(insights)は紫系の枠＋確度バッジで視覚的に区別する。
    「今後の注目テーマ」は、強気（強い/資金が向かっている）・弱気（弱い/資金が抜けている）を
    同じボリュームで併記する。commentary.json側に弱気テーマの記載がない場合は、
    本日のテーマ強弱ランキング（下位テーマ）から機械的に弱気コメントを補って表示する。
    ファイルが取得できない場合は準備中と案内する。
    """
    data = get_commentary()
    if not data or not isinstance(data, dict):
        st.info("📝 本日の考察はまだ準備中です。しばらくしてから再度ご確認ください。")
        return

    headline = data.get("headline", "")
    lead = data.get("lead", "")
    date_str = data.get("date", "")
    generated_by = data.get("generated_by")

    # 更新状態バッジ：今日の考察が「Claudeによる詳細分析」か、PCが閉じていた場合の
    # 「GitHub Actionsによる自動簡易更新」かを一目で判別できるようにする。
    # generated_byフィールドが無い場合（従来形式のファイル）はバッジを表示しない。
    if generated_by == "github-actions":
        st.markdown(
            '<div style="display:inline-block; background:rgba(234,179,8,0.15); '
            'border:1px solid #eab308; color:#fde68a; border-radius:999px; '
            'padding:4px 14px; font-size:0.85rem; margin-bottom:10px;">'
            '🤖 自動簡易更新版（Claudeによる詳細分析はまだ未反映です。数値は正確ですが、考察文は簡易的なものです）'
            "</div>",
            unsafe_allow_html=True,
        )
    elif generated_by == "claude":
        st.markdown(
            '<div style="display:inline-block; background:rgba(34,197,94,0.15); '
            'border:1px solid #22c55e; color:#86efac; border-radius:999px; '
            'padding:4px 14px; font-size:0.85rem; margin-bottom:10px;">'
            "✨ Claudeが分析・執筆した本日版です"
            "</div>",
            unsafe_allow_html=True,
        )

    if headline:
        st.subheader(f"🗞️ {headline}")
    if date_str:
        st.caption(f"考察日: {date_str}")
    if lead:
        st.write(lead)

    facts = data.get("facts", [])
    if facts:
        st.markdown("**🔵 事実（ファクト・テーマ単位の動きを中心に記載）**")
        for f in facts:
            text = f.get("text", "")
            cause = f.get("cause")
            src = f.get("source_url")
            cause_html = (
                f'<br><span class="small-note">💡 要因: {cause}</span>' if cause else ""
            )
            src_html = (
                f' <a href="{src}" target="_blank" style="color:#7dd3fc;">[出典]</a>'
                if src
                else ""
            )
            st.markdown(
                f'<div class="fact-box">{text}{cause_html}{src_html}</div>',
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

    st.markdown("**🎯 今後の注目テーマ（強気・弱気）**")
    bullish_curated, bearish_curated = classify_commentary_themes(data)
    theme_df = compute_theme_ranking(period_key)
    n_theme = len(theme_df)
    top_n = theme_df.head(3) if not theme_df.empty else pd.DataFrame()
    bottom_n = theme_df.tail(3) if not theme_df.empty else pd.DataFrame()
    curated_titles = {th.get("title", "") for th in (bullish_curated + bearish_curated)}

    col_bull, col_bear = st.columns(2)
    with col_bull:
        st.markdown("🟢 強気テーマ")
        for th in bullish_curated:
            title = th.get("title", "")
            conf = th.get("confidence", "中")
            text = th.get("text", "")
            badge_cls = _confidence_class(conf)
            st.markdown(
                f'<div class="insight-box"><b>{title}</b>'
                f'<span class="confidence-badge {badge_cls}">確度: {conf}</span><br>{text}</div>',
                unsafe_allow_html=True,
            )
        for _, row in top_n.iterrows():
            if row["テーマ名"] in curated_titles:
                continue
            st.markdown(
                f'<div class="fact-box">「{row["テーマ名"]}」は本日のテーマ強弱ランキングで'
                f'全{n_theme}テーマ中{int(row["順位"])}位（平均騰落率 {row["騰落率"]:+.2f}%）と'
                f'上位につけています。</div>',
                unsafe_allow_html=True,
            )
        if bullish_curated == [] and top_n.empty:
            st.caption("本日、強気材料は見当たりませんでした。")

    with col_bear:
        st.markdown("🔴 弱気テーマ")
        for th in bearish_curated:
            title = th.get("title", "")
            conf = th.get("confidence", "中")
            text = th.get("text", "")
            badge_cls = _confidence_class(conf)
            st.markdown(
                f'<div class="insight-box"><b>{title}</b>'
                f'<span class="confidence-badge {badge_cls}">確度: {conf}</span><br>{text}</div>',
                unsafe_allow_html=True,
            )
        for _, row in bottom_n.iterrows():
            if row["テーマ名"] in curated_titles:
                continue
            st.markdown(
                f'<div class="fact-box">「{row["テーマ名"]}」は本日のテーマ強弱ランキングで'
                f'全{n_theme}テーマ中{int(row["順位"])}位（平均騰落率 {row["騰落率"]:+.2f}%）と'
                f'下位に沈んでおり、資金が抜けている可能性があります。</div>',
                unsafe_allow_html=True,
            )
        if bearish_curated == [] and bottom_n.empty:
            st.caption("本日、弱気材料は見当たりませんでした。")

    st.caption(
        "※ 強気・弱気テーマは、commentary.jsonの記述と本日のテーマ強弱ランキング（上位/下位）を"
        "組み合わせて機械的に整理したものです。"
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


def render_recent_news_widget():
    """news_archive.json（蓄積ニュースアーカイブ）の直近数件を、ダッシュボードの
    「本日の考察」セクション付近に簡単なリスト形式で表示する。まだファイルが無い場合や
    取得に失敗した場合は、エラーにせず何も表示しない（詳細はニュースアーカイブページを参照）。
    """
    entries = get_news_archive()
    if not entries:
        return
    st.markdown("**📰 関連ニュース**")
    sorted_entries = sorted(entries, key=lambda e: e.get("date", ""), reverse=True)[:3]
    for e in sorted_entries:
        headline = e.get("headline", "")
        date_str = e.get("date", "")
        src = e.get("source_url")
        link_html = (
            f' <a href="{src}" target="_blank" style="color:#7dd3fc;">[出典]</a>' if src else ""
        )
        st.markdown(f"- {date_str}　{headline}{link_html}", unsafe_allow_html=True)
    st.caption("詳しくは「📰 ニュースアーカイブ」ページをご覧ください。")


def render_portfolio_related_news():
    """news_archive.jsonのentriesの中から、現在のポートフォリオ保有銘柄が属するテーマに
    関連するニュース（relevance_themesと保有銘柄のテーマが一致するもの）だけを抽出して表示する。

    ポートフォリオが未登録の場合や、該当するニュースが無い場合はその旨を案内する。
    各ニュースには、どの保有銘柄のどのテーマに関連しているかを一言添える。
    """
    holdings = _current_portfolio_holdings()
    entries = get_news_archive()
    if not holdings or not entries:
        st.info("現在、保有銘柄に直接関連するニュースはありません。")
        return

    # 保有銘柄ごとに、属するテーマ名とその根拠となったティッカーを紐付ける
    theme_to_tickers = {}
    for h in holdings:
        analysis_ticker = resolve_analysis_ticker(h["ticker"])
        for theme_name in _find_theme_for_ticker(analysis_ticker):
            theme_to_tickers.setdefault(theme_name, []).append(h["ticker"])

    if not theme_to_tickers:
        st.info("現在、保有銘柄に直接関連するニュースはありません。")
        return

    matched = []
    for e in entries:
        hit_themes = [t for t in e.get("relevance_themes", []) if t in theme_to_tickers]
        if hit_themes:
            matched.append((e, hit_themes))

    if not matched:
        st.info("現在、保有銘柄に直接関連するニュースはありません。")
        return

    matched.sort(key=lambda m: m[0].get("date", ""), reverse=True)
    for e, hit_themes in matched:
        headline = e.get("headline", "")
        summary = e.get("summary", "")
        date_str = e.get("date", "")
        src = e.get("source_url")
        src_html = (
            f' <a href="{src}" target="_blank" style="color:#7dd3fc;">[出典]</a>' if src else ""
        )
        reasons = []
        for t in hit_themes:
            tickers = "、".join(sorted(set(theme_to_tickers[t])))
            reasons.append(f"保有銘柄{tickers}が属する「{t}」テーマに関連しています。")
        reason_text = " ".join(reasons)
        st.markdown(
            f'<div class="insight-box"><b>{headline}</b>{src_html}<br>{summary}<br>'
            f'<span class="small-note">{date_str}</span><br>'
            f'<span class="small-note">💡 このニュースは、{reason_text}</span></div>',
            unsafe_allow_html=True,
        )


def render_news_archive_page():
    """「📰 ニュースアーカイブ」ページ：保有銘柄関連ニュース・Google Newsのリアルタイム
    簡易ニュース・日次の自動更新タスク側で厳選・蓄積しているニュースアーカイブをまとめて表示する。
    """
    st.title("📰 ニュースアーカイブ")
    st.caption("経済ニュースのリアルタイム簡易検索と、日次で厳選・蓄積しているニュースアーカイブをまとめて確認できます。")

    st.markdown('<div class="section-title">📌 保有銘柄関連ニュース</div>', unsafe_allow_html=True)
    st.caption("あなたのマイポートフォリオの保有銘柄が属するテーマに関連するニュースだけを抽出しています。")
    render_portfolio_related_news()

    st.markdown('<div class="section-title">🔴 リアルタイム簡易ニュース</div>', unsafe_allow_html=True)
    st.caption(
        "Google Newsの検索RSS（アカウント登録・APIキー不要・無料）から、"
        "「FRB」「利下げ」「半導体」「決算」「関税」等の経済関連キーワードで直近ニュースを取得しています。"
    )
    news_items = get_realtime_news()
    if not news_items:
        st.info("😔 リアルタイムニュースを取得できませんでした。しばらく待ってから再度お試しください。")
    else:
        by_keyword = {}
        for item in news_items:
            by_keyword.setdefault(item["keyword"], []).append(item)
        for kw, items in by_keyword.items():
            with st.expander(f"🔎 「{kw}」関連ニュース（{len(items)}件）"):
                for it in items:
                    link_html = (
                        f' <a href="{it["link"]}" target="_blank" style="color:#7dd3fc;">[記事]</a>'
                        if it.get("link")
                        else ""
                    )
                    st.markdown(
                        f'<div class="fact-box">{it["title"]}{link_html}<br>'
                        f'<span class="small-note">{it.get("published", "")}</span></div>',
                        unsafe_allow_html=True,
                    )

    st.markdown('<div class="section-title">🗂️ 蓄積ニュースアーカイブ</div>', unsafe_allow_html=True)
    st.caption("日次の自動更新タスクで、重要と判断されたニュースを厳選して積み上げているアーカイブです。")
    entries = get_news_archive()
    if not entries:
        st.info("📭 まだ蓄積されたニュースがありません。")
        return

    by_date = {}
    for e in entries:
        d = e.get("date", "不明")
        by_date.setdefault(d, []).append(e)
    for d in sorted(by_date.keys(), reverse=True):
        st.markdown(f"**📅 {d}**")
        for e in by_date[d]:
            headline = e.get("headline", "")
            summary = e.get("summary", "")
            themes = e.get("relevance_themes", [])
            src = e.get("source_url")
            theme_tags = "　".join(f"#{t}" for t in themes) if themes else ""
            src_html = (
                f' <a href="{src}" target="_blank" style="color:#7dd3fc;">[出典]</a>' if src else ""
            )
            tags_html = (
                f'<br><span class="small-note">{theme_tags}</span>' if theme_tags else ""
            )
            st.markdown(
                f'<div class="fact-box"><b>{headline}</b>{src_html}<br>{summary}{tags_html}</div>',
                unsafe_allow_html=True,
            )


def render_dashboard_page(period_key):
    """「📊 分析ダッシュボード」ページ：市場全体の考察・指数・テーマ/セクター強弱・
    大口投資家の動き・相関マトリクスをまとめて表示する。"""
    st.title("📊 米国株 大口投資家動向・セクター強弱ダッシュボード")
    st.caption("開くたびに最新データをその場で取得して表示します。")

    st.markdown('<div class="section-title">🧭 主要指数・資産</div>', unsafe_allow_html=True)
    render_index_cards(period_key)

    st.markdown('<div class="section-title">🗞️ 今日の相場考察</div>', unsafe_allow_html=True)
    render_commentary(period_key)
    render_recent_news_widget()

    st.markdown('<div class="section-title">🎯 テーマ強弱ランキング</div>', unsafe_allow_html=True)
    st.caption(
        f"半導体・AIインフラ・GLP-1など{len(THEME_STOCKS)}テーマ単位の強弱ランキングです。"
        "市場全体を広く俯瞰できるよう、業種・テーマを細かく分けています。"
        "各テーマを展開すると代表銘柄ごとのスコアも確認できます。"
    )
    render_theme_strength(period_key)

    st.markdown('<div class="section-title">🏭 セクター強弱ランキング（参考情報）</div>', unsafe_allow_html=True)
    st.caption("11セクターETF単位の、より粗い粒度のランキングです。参考情報として折りたたんでいます。")
    if "show_sector_section" not in st.session_state:
        st.session_state["show_sector_section"] = False
    _sector_toggle_label = (
        "▼ セクター強弱ランキングを閉じる"
        if st.session_state["show_sector_section"]
        else "▶ セクター強弱ランキングを表示する"
    )
    if st.button(_sector_toggle_label):
        st.session_state["show_sector_section"] = not st.session_state["show_sector_section"]
        st.rerun()
    if st.session_state["show_sector_section"]:
        render_sector_strength(period_key)

    st.markdown('<div class="section-title">🏦 大口投資家の動き</div>', unsafe_allow_html=True)
    st.caption("ARK Invest・内部者クラスター買い・SEC Form 13D・著名投資家(dataroma)の4つの情報源をタブで切り替えて確認できます。")
    render_institutional_investors()

    st.markdown('<div class="section-title">🔗 資産相関マトリクス</div>', unsafe_allow_html=True)
    render_correlation(period_key)

    st.markdown("---")
    st.caption(
        "⚠️ 本サイトは事実整理であり、投資助言ではありません。"
        "個別銘柄の売買判断はご自身の責任で行ってください。"
    )


def render_portfolio_page(period_key):
    """「📁 マイポートフォリオ」ページ：保有銘柄・預り金の登録・評価損益（円換算併記）・
    4観点の見立て・チャート・ポジション調整の参考材料・ポートフォリオ強化のテーマ提案・
    「もし追加投資を検討するなら」の考察を表示する。"""
    st.title("📁 マイポートフォリオ")
    st.caption("保有銘柄・預り金を登録すると、評価損益や複数の観点からの見立てが確認できます。")

    init_portfolio_state()
    render_portfolio_form()
    st.markdown("**💰 保有状況**")
    _portfolio_holdings = render_portfolio_holdings(period_key)
    st.markdown("**🔍 見立て（テクニカル / テーマ・セクター / マクロ / ファンダメンタルズ）**")
    render_portfolio_assessment(_portfolio_holdings, period_key)

    st.markdown("**💡 もし追加投資を検討するなら**")
    render_investment_consideration(
        _portfolio_holdings,
        period_key,
        get_commentary(),
        _current_cash_jpy(),
        get_usdjpy_rate(),
    )


# =====================================================
# サイドバー
# =====================================================

st.sidebar.title("⚙️ 設定")
page = st.sidebar.radio(
    "📌 ページ",
    options=["📊 分析ダッシュボード", "📁 マイポートフォリオ", "📰 ニュースアーカイブ"],
    index=0,
)
st.sidebar.markdown("---")
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
# メイン画面（ページ切り替え）
# =====================================================

if page == "📊 分析ダッシュボード":
    render_dashboard_page(period_key)
elif page == "📁 マイポートフォリオ":
    render_portfolio_page(period_key)
else:
    render_news_archive_page()
