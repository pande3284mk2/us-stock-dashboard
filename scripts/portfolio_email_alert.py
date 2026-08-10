# -*- coding: utf-8 -*-
"""
ポートフォリオ 時間外・プレマーケット メール通知スクリプト
=====================================================

【このスクリプトは何をするもの？】
  GitHub Actions（GitHubが無料で提供している「決まった時刻に自動でプログラムを
  実行してくれる仕組み」）から5分おき（毎時0,5,10,15,20,25,30,35,40,45,50,55分）
  に呼び出され、portfolio.json に書かれている保有株について、
    1. 現在の株価（時間外取引・プレマーケットの値も含む）
    2. 前日の終値からの変化率(%)
    3. その変化を円換算した場合の損益（当日分）
  を計算し、条件に応じて m.pande3284mk2@gmail.com 宛にメールを送ります。

【メールが届くタイミング（重要）】
  - 5分ごとの実行のたび「必ず」チェックし、
    保有銘柄のどれかが前日比 ±3%以上動いていれば、その場でアラートメールを送る。
    （毎時何分の実行でも扱いは同じ）
  - それとは別に、毎時30分の実行の時「だけ」、値動きの大きさに関係なく、
    保有銘柄全体の状況をまとめた「定時サマリーメール」を必ず送る。
  → そのため毎時30分は、条件次第で「定時サマリー」と「アラート」の
    2通のメールが同時に届くことがあります。それ以外の5分刻みの実行は、
    条件に当てはまる銘柄がある場合のみ「アラート」の1通だけが届きます。

【個別銘柄の売買推奨は一切行いません。あくまで価格変動の事実をお知らせするだけです。】

【使っている言葉の説明】
  ・yfinance …… Yahoo!ファイナンスの株価データを無料で取得できるPythonの道具（ライブラリ）。
  ・時間外取引・プレマーケット …… 米国株の通常取引時間（日本時間の夜〜早朝）の
    前後に行われる取引のこと。値動きが大きくなりやすい。
  ・環境変数 (environment variable) …… プログラムの外側から渡す「設定値」のこと。
    ここではGmailのアドレスやアプリパスワードを、コードに直接書かずに
    GitHubの「Secrets（暗号化された設定値）」から環境変数として受け取っています。
  ・SMTP …… メールを送信するための世界共通の仕組み（プロトコル）。ここではGmailの
    SMTPサーバーを使って送信します。
"""

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import yfinance as yf

# =====================================================
# 設定値
# =====================================================

# このファイル（scripts/portfolio_email_alert.py）から見た portfolio.json の場所。
# GitHub Actions ではリポジトリのルートで実行されるため、ルート直下を指定する。
PORTFOLIO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portfolio.json")

# アラートを送るしきい値（前日比の変化率、％）。
ALERT_THRESHOLD_PCT = 3.0

# 送信先メールアドレス（固定）。
TO_ADDRESS = "m.pande3284mk2@gmail.com"

# 日本時間 (JST = UTC+9)。GitHub Actionsのサーバーは世界標準時(UTC)で動いているため、
# メール本文に表示する時刻は日本時間に変換する。
JST = timezone(timedelta(hours=9))


# =====================================================
# データ取得まわり
# =====================================================

def load_portfolio():
    """portfolio.json を読み込んで、保有銘柄のリストなどを取り出す。"""
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("holdings", []), data.get("cash_jpy", 0.0)


def get_usdjpy_rate():
    """ドル円レートを取得する。取得できない場合は None を返す。

    "JPY=X" は Yahoo!ファイナンス上でのドル円為替レートのティッカーコード。
    """
    try:
        info = yf.Ticker("JPY=X").info
        rate = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
        if rate:
            return float(rate)
    except Exception as e:
        print(f"[警告] ドル円レートの取得に失敗しました: {e}", file=sys.stderr)
    return None


# 時間外・プレマーケットの状態を、日本語の分かりやすい表示に変換するための対応表。
# yfinanceが返す marketState の値: PRE / PREPRE / REGULAR / POST / POSTPOST / CLOSED
MARKET_STATE_LABELS = {
    "PRE": "プレマーケット（取引開始前）",
    "PREPRE": "プレマーケット（取引開始前）",
    "REGULAR": "通常取引時間中",
    "POST": "時間外取引（取引終了後）",
    "POSTPOST": "時間外取引（取引終了後）",
    "CLOSED": "取引時間外",
}


def get_holding_snapshot(ticker):
    """1銘柄について、現在値・前日終値・変化率などをまとめて取得する。

    Yahoo!ファイナンスの情報には、通常の株価(regularMarketPrice)に加えて、
    プレマーケットの株価(preMarketPrice)・時間外取引の株価(postMarketPrice)が
    含まれている場合があるため、今の相場状況(marketState)に応じて
    「一番今に近い株価」を選んで使う。
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info:
            return None

        market_state = info.get("marketState", "CLOSED")
        prev_close = info.get("regularMarketPreviousClose")
        regular_price = info.get("regularMarketPrice") or info.get("currentPrice")
        pre_price = info.get("preMarketPrice")
        post_price = info.get("postMarketPrice")

        # 今の相場状況に応じて、表示すべき「現在値」を選ぶ。
        if market_state in ("PRE", "PREPRE") and pre_price:
            current_price = pre_price
        elif market_state in ("POST", "POSTPOST") and post_price:
            current_price = post_price
        elif regular_price:
            current_price = regular_price
        else:
            current_price = post_price or pre_price

        if current_price is None or prev_close in (None, 0):
            return None

        pct_change = (current_price / prev_close - 1) * 100

        return {
            "ticker": ticker,
            "market_state": market_state,
            "market_state_label": MARKET_STATE_LABELS.get(market_state, "取引時間外"),
            "current_price": float(current_price),
            "prev_close": float(prev_close),
            "pct_change": float(pct_change),
        }
    except Exception as e:
        print(f"[警告] {ticker} の株価取得に失敗しました: {e}", file=sys.stderr)
        return None


def build_holdings_report(holdings, usdjpy_rate):
    """保有銘柄それぞれについて、当日の円換算損益まで含めたレポート用データを作る。"""
    report = []
    for h in holdings:
        ticker = h.get("ticker")
        shares = float(h.get("shares", 0))
        snap = get_holding_snapshot(ticker)
        if snap is None:
            report.append({"ticker": ticker, "shares": shares, "error": True})
            continue

        usd_change = shares * (snap["current_price"] - snap["prev_close"])
        jpy_change = usd_change * usdjpy_rate if usdjpy_rate else None

        snap.update({
            "shares": shares,
            "usd_change": usd_change,
            "jpy_change": jpy_change,
            "error": False,
        })
        report.append(snap)
    return report


# =====================================================
# メール本文づくり
# =====================================================

def now_jst_str():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")


def format_summary_email(report, usdjpy_rate):
    """毎時30分に必ず送る「定時サマリー」メールの件名・本文を作る。"""
    total_jpy_change = sum(r["jpy_change"] for r in report if not r["error"] and r["jpy_change"] is not None)

    lines = []
    lines.append("🕒 ポートフォリオ 定時サマリー（時間外・プレマーケット状況）")
    lines.append(f"日時: {now_jst_str()}")
    if usdjpy_rate:
        lines.append(f"参考ドル円レート: {usdjpy_rate:,.2f} 円/ドル")
    lines.append("")
    lines.append("―― 銘柄別 ――")

    for r in report:
        if r["error"]:
            lines.append(f"・{r['ticker']}: データ取得に失敗しました（次回の実行で再取得します）")
            continue
        arrow = "🔺" if r["pct_change"] >= 0 else "🔻"
        jpy_text = f"{r['jpy_change']:+,.0f} 円" if r["jpy_change"] is not None else "円換算不可"
        lines.append(
            f"・{r['ticker']}（{r['market_state_label']}）: "
            f"{r['current_price']:.2f}ドル（前日比 {arrow}{r['pct_change']:+.2f}%）"
            f" ｜ 当日の円換算損益: {jpy_text}"
        )

    lines.append("")
    lines.append(f"―― 合計 ――")
    lines.append(f"保有銘柄の当日の円換算損益 合計: {total_jpy_change:+,.0f} 円")
    lines.append("")
    lines.append("※ このメールは毎時30分に届く定時レポートです。前日比±3%以上の急な値動きがあった場合は、")
    lines.append("　 5分ごとのチェックで別途「アラート」メールが届きます。")
    lines.append("")
    lines.append("⚠️ このメールは価格変動の事実をお知らせするものであり、投資助言ではありません。")
    lines.append("　 個別銘柄の売買判断はご自身の責任で行ってください。")

    subject = f"【定時サマリー】ポートフォリオ時間外レポート {datetime.now(JST).strftime('%m/%d %H:%M')}"
    return subject, "\n".join(lines)


def format_alert_email(triggered):
    """前日比±3%以上動いた銘柄がある時だけ送る「アラート」メールの件名・本文を作る。"""
    lines = []
    lines.append(f"🚨 前日比 ±{ALERT_THRESHOLD_PCT:.0f}%以上の値動きを検知しました")
    lines.append(f"日時: {now_jst_str()}")
    lines.append("")

    ticker_labels = []
    for r in triggered:
        arrow = "🔺" if r["pct_change"] >= 0 else "🔻"
        jpy_text = f"{r['jpy_change']:+,.0f} 円" if r["jpy_change"] is not None else "円換算不可"
        lines.append(
            f"・{r['ticker']}（{r['market_state_label']}）: "
            f"{r['current_price']:.2f}ドル（前日比 {arrow}{r['pct_change']:+.2f}%）"
            f" ｜ 当日の円換算損益: {jpy_text}"
        )
        ticker_labels.append(f"{r['ticker']}{arrow}{r['pct_change']:+.1f}%")

    lines.append("")
    lines.append("⚠️ このメールは価格変動の事実をお知らせするものであり、投資助言ではありません。")
    lines.append("　 個別銘柄の売買判断はご自身の責任で行ってください。")

    subject = f"【アラート】{' / '.join(ticker_labels)}"
    return subject, "\n".join(lines)


# =====================================================
# メール送信
# =====================================================

def send_email(subject, body):
    """Gmailのメール送信サーバー（SMTP）を使って、指定した件名・本文のメールを送る。

    GMAIL_ADDRESS: 送信元のGmailアドレス（GitHub Secretsから環境変数として渡される）
    GMAIL_APP_PASSWORD: Googleアカウントで発行した「アプリパスワード」
                        （通常のログインパスワードとは別物。GitHub Secretsから渡される）
    """
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_address or not gmail_app_password:
        print(
            "[エラー] GMAIL_ADDRESS または GMAIL_APP_PASSWORD が設定されていません。"
            "GitHubリポジトリの Secrets 登録が完了しているか確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = TO_ADDRESS

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [TO_ADDRESS], msg.as_string())

    print(f"[送信完了] {subject}")


# =====================================================
# メイン処理
# =====================================================

def main():
    # IS_SUMMARY_RUN は .github/workflows/portfolio_alert.yml の中で、
    # 実行時刻（UTC・分）が30分かどうかに応じて "true" / "false" が渡される環境変数。
    is_summary_run = os.environ.get("IS_SUMMARY_RUN", "false").strip().lower() == "true"

    holdings, _cash_jpy = load_portfolio()
    if not holdings:
        print("portfolio.json に保有銘柄がないため、処理を終了します。")
        return

    usdjpy_rate = get_usdjpy_rate()
    report = build_holdings_report(holdings, usdjpy_rate)

    # ---- ①アラートチェック：5分ごとの実行のたび必ず行う ----
    triggered = [
        r for r in report
        if not r["error"] and abs(r["pct_change"]) >= ALERT_THRESHOLD_PCT
    ]
    if triggered:
        subject, body = format_alert_email(triggered)
        send_email(subject, body)
    else:
        print("しきい値(±3%)を超えた銘柄はありませんでした。アラートメールは送信しません。")

    # ---- ②定時サマリー：毎時30分の実行のときだけ、値動きに関係なく必ず送る ----
    if is_summary_run:
        subject, body = format_summary_email(report, usdjpy_rate)
        send_email(subject, body)
    else:
        print("今回は毎時30分の実行ではないため、定時サマリーメールは送信しません（アラートのみ判定）。")


if __name__ == "__main__":
    main()
