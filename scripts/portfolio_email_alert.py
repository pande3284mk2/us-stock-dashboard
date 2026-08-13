# -*- coding: utf-8 -*-
"""
ポートフォリオ 時間外・プレマーケット メール通知スクリプト
=====================================================

【このスクリプトは何をするもの？】
  GitHub Actions（GitHubが無料で提供している「決まった時刻に自動でプログラムを
  実行してくれる仕組み」）から、複数のワークフローによって呼び出されます。
  portfolio.json に書かれている保有株について、現在の株価（時間外取引・
  プレマーケットの値も含む）を取得し、m.pande3284mk2@gmail.com 宛にメールを送ります。

【2026-08-11 追記：ワークフローを分離しました】
  以前は1つのワークフロー（5分間隔）が「アラート判定」と「毎時30分の定時サマリー」の
  両方を担っていましたが、GitHub Actions側の制約により、5分間隔のような高頻度
  スケジュールは実際にはGitHub側で大幅に間引かれてしまい（数時間に1回程度しか
  実行されない）、結果として定時サマリーがほとんど届かない状態になっていました。
  そこで、実行頻度が異なる2つのワークフローに分離し、このスクリプトはコマンドライン
  引数（第1引数）で「どちらの処理を行うか」を切り替えられるようにしています。
    ・python scripts/portfolio_email_alert.py alert   … アラート判定のみ行う
    ・python scripts/portfolio_email_alert.py summary … 定時サマリーのみ行う
  引数を省略した場合は、後方互換のため "alert" として扱います。

【2026-08-13 追記：自己ループ式ワークフローを追加】
  上記の「2つのワークフローに分離」した後も、実際の実行間隔は改善せず、
  5分間隔・30分間隔のどちらで設定しても実際には数時間に1回程度しか実行されない
  ことが分かりました（GitHub側が、個々のワークフローの設定頻度とは関係なく、
  スケジュール実行の総数自体を絞っているとみられる）。
  そこで、scripts/portfolio_loop.py という別スクリプトを追加し、1回のジョブの中で
  Python自身がtime.sleep()を使って5分おきにこのモジュールの関数を直接呼び出す
  「自己ループ式」の仕組みに切り替えました。build_alert_check() に
  last_prices 引数を追加しているのはこのためです（ループ側は前回価格を
  ファイルではなくメモリ上に保持し、5.5時間ごとにまとめてファイルへ保存します）。
  詳しくは scripts/portfolio_loop.py と .github/workflows/portfolio_loop.yml を
  参照してください。

【2種類の判定基準を使い分けています（重要）】
  ・アラート……「前回チェックの価格からどれだけ動いたか」を基準に判定します。
    通常実行（alertモード）では前回チェック時の価格を data/last_check_prices.json
    というファイルから読み込んで比較し、実行の最後に今回の価格で上書き保存
    （→GitHubへコミット）します。ループ実行では、このファイルの読み書きを
    毎回行う代わりに、呼び出し側がメモリ上の辞書を直接渡します。
    こうすることで、急な値動きが起きた「その瞬間」だけアラートが届き、いったん動いた後
    その水準にとどまっている間は再送されません（前日終値を基準にすると、一度3%を超えた
    まま値が動かなくても、同じアラートが届き続けてしまうため）。
  ・定時サマリー……従来通り「前日終値からの変化率」を基準にします。
    実行されるたびに、値動きの大きさに関係なく必ず送信します。

【個別銘柄の売買推奨は一切行いません。あくまで価格変動の事実をお知らせするだけです。】

【価格の正確性について】
  Yahoo!ファイナンス（yfinance経由）のデータには、プレマーケット中の
  regularMarketPreviousClose（「前々日」の終値を指してしまうことがある）を
  そのまま前日終値として使うと、実際より大きく・的外れな変化率になることがある
  ため、このスクリプトでは以下のように補正しています。
  ・プレマーケット中／アフターマーケット中は、Yahoo!ファイナンス自身が計算する
    preMarketChangePercent／postMarketChangePercent（あれば）を最優先で使う。
  ・それらが無い場合のみ自前で計算するが、その際の基準値は
    regularMarketPreviousClose ではなく、直近の通常取引終値である
    regularMarketPrice を使う（詳しくは get_reference_snapshot() のコメント参照）。

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

# このファイル（scripts/portfolio_email_alert.py）から見たリポジトリのルート。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_PATH = os.path.join(REPO_ROOT, "portfolio.json")

# 「前回チェック時の価格」を保存しておくファイル。
LAST_CHECK_PRICES_PATH = os.path.join(REPO_ROOT, "data", "last_check_prices.json")

# アラートを送るしきい値（％）。
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

def load_last_check_prices():
    """前回チェック時の価格（銘柄→価格の辞書）を読み込む。ファイルが無い場合は空の辞書。"""
    try:
        with open(LAST_CHECK_PRICES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_last_check_prices(prices):
    """今回チェックした価格を、次回チェック用にファイルへ保存する。"""
    os.makedirs(os.path.dirname(LAST_CHECK_PRICES_PATH), exist_ok=True)
    with open(LAST_CHECK_PRICES_PATH, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2, sort_keys=True)

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

def get_reference_snapshot(ticker):
    """1銘柄について、現在値・前日終値からの変化率（定時サマリー用）を取得する。

    【前日終値の扱いに関する重要な注意】
    Yahoo!ファイナンスの regularMarketPreviousClose は、「regularMarketPrice が
    示す取引セッションの、さらに1つ前のセッションの終値」を意味する。
    ・通常取引中（REGULAR）は regularMarketPrice=本日の現在値、
      regularMarketPreviousClose=前営業日の終値 となり、両者を比べれば
      正しく「本日の前日比」になる。
    ・しかしプレマーケット中（PRE、本日の取引開始前）は、regularMarketPrice が
      まだ「直近の終値（＝前営業日の終値）」を指したままのため、
      regularMarketPreviousClose は前営業日のさらに1つ前（前々営業日）の
      終値を指してしまう。この状態で regularMarketPreviousClose を「前日終値」
      として使うと、実際には2営業日分の値動きを1日分であるかのように計算して
      しまい、変化率が大きくずれる（本アプリで実際に発生した不具合）。
    ・アフターマーケット中（POST）も同様に、regularMarketPrice が「本日の
      終値」に更新された直後は問題ないが、念のため前営業日の終値
      （＝プレマーケット同様 regularMarketPrice）を基準にする方が安全。
    このため、プレマーケット／アフターマーケット中は、まず Yahoo!ファイナンス
    自身が計算済みの preMarketChangePercent／postMarketChangePercent を優先して
    使い、それが無い場合のみ regularMarketPrice を基準に自前で計算する
    （regularMarketPreviousClose は使わない）。
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info:
            return None

        market_state = info.get("marketState", "CLOSED")
        regular_previous_close = info.get("regularMarketPreviousClose")
        regular_price = info.get("regularMarketPrice") or info.get("currentPrice")
        pre_price = info.get("preMarketPrice")
        pre_change_pct = info.get("preMarketChangePercent")
        pre_change_usd = info.get("preMarketChange")
        post_price = info.get("postMarketPrice")
        post_change_pct = info.get("postMarketChangePercent")
        post_change_usd = info.get("postMarketChange")

        current_price = None
        reference_price = None  # 「前日終値」として扱う基準値
        pct_change = None
        usd_change_per_share = None

        if market_state in ("PRE", "PREPRE") and pre_price:
            current_price = pre_price
            reference_price = regular_price or regular_previous_close
            if pre_change_pct is not None:
                pct_change = float(pre_change_pct)
                usd_change_per_share = float(pre_change_usd) if pre_change_usd is not None else None
        elif market_state in ("POST", "POSTPOST") and post_price:
            current_price = post_price
            reference_price = regular_price or regular_previous_close
            if post_change_pct is not None:
                pct_change = float(post_change_pct)
                usd_change_per_share = float(post_change_usd) if post_change_usd is not None else None
        elif regular_price:
            current_price = regular_price
            reference_price = regular_previous_close
        else:
            current_price = post_price or pre_price
            reference_price = regular_previous_close

        if current_price is None or reference_price in (None, 0):
            return None

        current_price = float(current_price)
        reference_price = float(reference_price)

        # Yahoo!ファイナンス自身の変化率が取得できなかった場合の自前計算
        # （上の説明の通り、基準値には regularMarketPrice 優先の reference_price を使う）。
        if pct_change is None:
            pct_change = (current_price / reference_price - 1) * 100
        if usd_change_per_share is None:
            usd_change_per_share = current_price - reference_price

        return {
            "ticker": ticker,
            "market_state": market_state,
            "market_state_label": MARKET_STATE_LABELS.get(market_state, "取引時間外"),
            "current_price": current_price,
            "reference_price": reference_price,
            "pct_change": float(pct_change),
            "usd_change_per_share": float(usd_change_per_share),
        }
    except Exception as e:
        print(f"[警告] {ticker} の株価取得に失敗しました: {e}", file=sys.stderr)
        return None

def build_summary_report(holdings, usdjpy_rate):
    """定時サマリー用：保有銘柄それぞれの「前日終値からの」当日変化を計算する。"""
    report = []
    for h in holdings:
        ticker = h.get("ticker")
        shares = float(h.get("shares", 0))
        snap = get_reference_snapshot(ticker)
        if snap is None:
            report.append({"ticker": ticker, "shares": shares, "error": True})
            continue

        usd_change = shares * snap["usd_change_per_share"]
        jpy_change = usd_change * usdjpy_rate if usdjpy_rate else None

        snap.update({
            "shares": shares,
            "usd_change": usd_change,
            "jpy_change": jpy_change,
            "error": False,
        })
        report.append(snap)
    return report

def build_alert_check(holdings, usdjpy_rate, last_prices=None):
    """アラート用：保有銘柄それぞれについて「前回チェックからの」変化を計算する。

    last_prices を省略した場合は data/last_check_prices.json から読み込む
    （単発実行のalertモード用）。ループ実行（portfolio_loop.py）からは、
    メモリ上に保持している辞書を直接渡すことで、毎回のファイル読み込みを省略できる。

    戻り値: (triggered, current_prices)
      triggered …… ±3%以上動いた銘柄の情報リスト（前回価格が無い＝初回チェックの
                    銘柄は、比較対象が無いためアラート対象にはしない）
      current_prices … 今回取得できた「銘柄→現在値」の辞書。呼び出し側で
                        次回チェック用に保存する。
    """
    if last_prices is None:
        last_prices = load_last_check_prices()
    current_prices = {}
    triggered = []

    for h in holdings:
        ticker = h.get("ticker")
        shares = float(h.get("shares", 0))
        snap = get_reference_snapshot(ticker)
        if snap is None:
            # 取得に失敗した銘柄は、前回価格をそのまま維持する（誤って基準を
            # リセットしないため）。
            if ticker in last_prices:
                current_prices[ticker] = last_prices[ticker]
            continue

        current_price = snap["current_price"]
        current_prices[ticker] = current_price

        last_price = last_prices.get(ticker)
        if last_price is None:
            # この銘柄について保存済みの「前回価格」がまだ無い（今回が初回チェック、
            # またはファイルが壊れていた等）。比較対象が無いため、今回はアラート
            # 判定をせず、価格の記録だけ行う。
            print(f"[情報] {ticker}: 前回チェック価格が未記録のため、今回は基準値として記録するのみ。")
            continue

        pct_change = (current_price / last_price - 1) * 100 if last_price else 0.0
        usd_change = shares * (current_price - last_price)
        jpy_change = usd_change * usdjpy_rate if usdjpy_rate else None

        if abs(pct_change) >= ALERT_THRESHOLD_PCT:
            triggered.append({
                "ticker": ticker,
                "market_state_label": snap["market_state_label"],
                "current_price": current_price,
                "last_price": last_price,
                "pct_change": pct_change,
                "jpy_change": jpy_change,
            })

    return triggered, current_prices

# =====================================================
# メール本文づくり
# =====================================================

def now_jst_str():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

def format_summary_email(report, usdjpy_rate):
    """定時サマリーメールの件名・本文を作る（前日終値基準）。"""
    total_jpy_change = sum(r["jpy_change"] for r in report if not r["error"] and r["jpy_change"] is not None)

    lines = []
    lines.append("🕒 ポートフォリオ 定時サマリー（時間外・プレマーケット状況）")
    lines.append(f"日時: {now_jst_str()}")
    if usdjpy_rate:
        lines.append(f"参考ドル円レート: {usdjpy_rate:,.2f} 円/ドル")
    lines.append("")
    lines.append("―― 銘柄別（前日終値比） ――")

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
    lines.append("―― 合計 ――")
    lines.append(f"保有銘柄の当日の円換算損益 合計: {total_jpy_change:+,.0f} 円")
    lines.append("")
    lines.append("※ このメールは定期的に届くレポートで、前日終値からの変化を基準にしています。")
    lines.append("　 アラートは「前回チェック時からの変化」が基準のため、数値の意味が異なります。")
    lines.append("")
    lines.append("⚠️ このメールは価格変動の事実をお知らせするものであり、投資助言ではありません。")
    lines.append("　 個別銘柄の売買判断はご自身の責任で行ってください。")

    subject = f"【定時サマリー】ポートフォリオ時間外レポート {datetime.now(JST).strftime('%m/%d %H:%M')}"
    return subject, "\n".join(lines)

def format_alert_email(triggered):
    """前回チェック比で±3%以上動いた銘柄がある時だけ送る「アラート」メール。"""
    lines = []
    lines.append(f"🚨 ±{ALERT_THRESHOLD_PCT:.0f}%以上の値動きを検知しました")
    lines.append(f"日時: {now_jst_str()}")
    lines.append("（前回チェック時の価格との比較です）")
    lines.append("")

    ticker_labels = []
    for r in triggered:
        arrow = "🔺" if r["pct_change"] >= 0 else "🔻"
        jpy_text = f"{r['jpy_change']:+,.0f} 円" if r["jpy_change"] is not None else "円換算不可"
        lines.append(
            f"・{r['ticker']}（{r['market_state_label']}）: "
            f"{r['last_price']:.2f}ドル → {r['current_price']:.2f}ドル"
            f"（前回チェック比 {arrow}{r['pct_change']:+.2f}%）"
            f" ｜ 円換算損益（この間の分）: {jpy_text}"
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
# メイン処理（単発実行モード：alert / summary）
# =====================================================

def run_alert_mode():
    """アラート判定のみ行う（単発実行、data/last_check_prices.json をファイル経由で利用）。"""
    holdings, _cash_jpy = load_portfolio()
    if not holdings:
        print("portfolio.json に保有銘柄がないため、処理を終了します。")
        return

    usdjpy_rate = get_usdjpy_rate()

    triggered, current_prices = build_alert_check(holdings, usdjpy_rate)
    if triggered:
        subject, body = format_alert_email(triggered)
        send_email(subject, body)
    else:
        print(f"前回チェック比でしきい値(±{ALERT_THRESHOLD_PCT:.0f}%)を超えた銘柄はありませんでした。アラートメールは送信しません。")

    if current_prices:
        save_last_check_prices(current_prices)
        print(f"[記録] 次回チェック用に価格を保存しました: {current_prices}")

def run_summary_mode():
    """定時サマリーのみ行う（単発実行）。実行されるたびに必ず定時サマリーメールを送る。"""
    holdings, _cash_jpy = load_portfolio()
    if not holdings:
        print("portfolio.json に保有銘柄がないため、処理を終了します。")
        return

    usdjpy_rate = get_usdjpy_rate()

    report = build_summary_report(holdings, usdjpy_rate)
    subject, body = format_summary_email(report, usdjpy_rate)
    send_email(subject, body)

def main():
    # 第1引数で "alert" か "summary" かを切り替える。省略時は後方互換のため "alert"。
    mode = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "alert"

    if mode == "summary":
        run_summary_mode()
    elif mode == "alert":
        run_alert_mode()
    else:
        print(f"[エラー] 不明なモードです: {mode!r}（alert または summary を指定してください）", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
