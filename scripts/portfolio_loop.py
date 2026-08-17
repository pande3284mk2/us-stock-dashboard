# -*- coding: utf-8 -*-
"""
ポートフォリオ 自己ループ式 メール通知スクリプト
=====================================================

【これは何？】
  GitHub Actions の1回のジョブ実行の中で、Pythonのループとして長時間
  （最大5時間30分）動き続け、5分おきに±3%アラート判定、30分おきに定時サマリー
  送信を行う。5時間30分が経過したら、GitHub REST API（ワークフロー手動起動API）
  を使って「自分自身（このワークフロー）」を再度起動してから終了する。

【なぜこんな仕組みが必要か】
  通常のcronスケジュール（5分おき・30分おき）で細かく実行しようとしても、
  GitHub側の都合で実際には数時間に1回程度しか実行されないことが分かった
  （過去の検証結果。ワークフローを分離しても改善しなかった）。
  一方、1回のジョブ実行時間の上限は6時間ある。この中でPython自身が
  time.sleep()でループを回せば、GitHub側のスケジューラーに実行タイミングを
  委ねることなく、正確な間隔で処理を続けられる。6時間の上限に達する前に
  ジョブが自分自身を再起動することで、実質的に「エンドレスに動き続けるが、
  5時間30分ごとにバトンタッチする」形になる。

【注意：GitHubの利用規約について】
  これはGitHub Actionsの本来の使い方（CI/CDジョブの実行）から外れた使い方で、
  GitHubの利用規約上グレーゾーンとなる可能性がある。明確に禁止されているわけ
  ではないが、「常時起動し続けるサーバーの代わりとしてActionsを使う」ような
  使い方は、GitHubの不正利用検知に引っかかるリスクがある。強制停止や警告が
  来た場合はこの方式を諦め、他の方式（数時間おきの実行で妥協する、または
  外部の無料cronサービスを使う）に切り替えること。

【2026-08-18 追記：休場中（CLOSED）はチェックを止めるようにした】
  このシステムはプレマーケット・アフターマーケットの価格しか意味のある形で
  取得できない設計（通常のザラ場中は別のダッシュボード側で見る想定）だったが、
  それとは別に「本当に取引が完全に止まっている時間（CLOSED＝土日や、
  アフターマーケット終了〜翌日のプレマーケット開始前などの空白帯）」まで
  5分おきにチェックし続けるのは無駄が多く、GitHub Actionsを24時間365日
  動かし続ける形に近づいてしまい、利用規約上のグレーゾーン度合いも高まる。
  そこで、代表銘柄（SPY）のmarketStateを見て、CLOSEDのときだけチェックを
  止める（ジョブを終了する）ようにした。PRE・REGULAR・POSTの間は、
  これまで通り5分おきのチェックを続ける。CLOSEDで止まった後は、
  .github/workflows/portfolio_loop.yml の毎時0分のウォッチドッグが
  様子を見て、CLOSED以外に変わっていれば自動的にループを再開する。
  固定の時刻（日本時間◯時〜◯時、など）で判定しないのは、米国の
  サマータイム切り替え（年2回）で時差がズレるのを避けるため。

【保険（ウォッチドッグ）】
  .github/workflows/portfolio_loop.yml には、万一このループが何らかの理由で
  停止してしまった場合に備えて、毎時0分に様子を見て再起動を試みる schedule
  トリガーも設定されている（concurrencyの設定により、ループが実際に動いている
  間はウォッチドッグは待機するだけで、重複して起動することはない）。CLOSED中に
  ループを止めた後、次にPRE/REGULAR/POSTへ変わったことを検知して再開させる役割も
  このウォッチドッグが担う。
"""

import json as json_module
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import yfinance as yf

import portfolio_email_alert as pea

# ループ全体の上限（秒）。GitHub Actionsの1ジョブ実行上限は6時間なので、
# 余裕を持って5時間30分（19800秒）で自発的に終了し、自分自身を再起動する。
MAX_LOOP_SECONDS = 5.5 * 60 * 60

# アラート判定のチェック間隔（秒）＝5分。
CHECK_INTERVAL_SECONDS = 5 * 60

# 市場全体が「休場中（CLOSED）」かどうかを判定するための代表銘柄。
# 保有銘柄は売買で入れ替わることがあるため、それに影響されないよう、
# 常に活発に取引されている銘柄（S&P500連動ETFのSPY）を固定で使う。
REFERENCE_MARKET_TICKER = "SPY"


def get_market_state():
    """米国市場全体が今どの状態か（PRE/REGULAR/POST/CLOSEDなど）を取得する。

    【なぜ「CLOSED以外は全部チェックする」という判定にしているか】
    プレマーケット(PRE)・通常取引(REGULAR)・アフターマーケット(POST)の
    どの時間帯でも株価は動いており、意味のあるアラート判定ができる。
    意味のあるデータが取れないのは、完全に取引が止まっている
    CLOSED（土日や、取引時間の完全な空白帯）のときだけ。そのため、
    「PRE/POSTのときだけ動かす」というホワイトリスト方式ではなく、
    「CLOSEDのときだけ止める」というブラックリスト方式にしている。

    取得に失敗した場合は、チェックを止めて本物のアラートを見逃すよりは
    安全なので、動く側（CLOSEDではない）とみなして処理を続ける。
    """
    try:
        info = yf.Ticker(REFERENCE_MARKET_TICKER).info
        return info.get("marketState", "REGULAR")
    except Exception as e:
        print(f"[警告] 市場状態の取得に失敗しました（安全のためチェックは継続します）: {e}", file=sys.stderr)
        return "REGULAR"


def commit_last_check_prices():
    """data/last_check_prices.json の変更をgitでコミット・pushする。

    ワークフロー側の permissions: contents: write により、
    actions/checkout@v4 が用意した認証情報でpushできる。
    """
    repo_root = pea.REPO_ROOT
    rel_path = os.path.relpath(pea.LAST_CHECK_PRICES_PATH, repo_root)
    status = subprocess.run(
        ["git", "status", "--porcelain", rel_path],
        cwd=repo_root, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        print("[記録] 前回チェック価格に変更なし。コミットは行いません。")
        return
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "add", rel_path], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "Update last-check prices (loop)"], cwd=repo_root, check=True)
    push = subprocess.run(["git", "push"], cwd=repo_root, capture_output=True, text=True)
    if push.returncode != 0:
        print(f"[警告] git push に失敗しました: {push.stderr}", file=sys.stderr)
    else:
        print("[記録] data/last_check_prices.json をコミット・pushしました。")


def redispatch_self():
    """GitHub REST APIで、このワークフロー自身を再度 workflow_dispatch で起動する。

    GITHUB_TOKEN はワークフローのenvから渡される。GITHUB_REPOSITORY と
    GITHUB_REF_NAME はGitHub Actionsが全ジョブに自動で設定する環境変数。
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    workflow_file = os.environ.get("LOOP_WORKFLOW_FILE", "portfolio_loop.yml")
    ref = os.environ.get("GITHUB_REF_NAME", "main")

    if not token or not repo:
        print("[エラー] GITHUB_TOKEN または GITHUB_REPOSITORY が設定されていないため、自己再起動できません。", file=sys.stderr)
        return False

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
    body = json_module.dumps({"ref": ref}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "portfolio-loop-self-dispatch")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[再起動] 自己再起動APIを呼び出しました（HTTP {resp.status}）。")
            return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"[エラー] 自己再起動APIの呼び出しに失敗しました: HTTP {e.code} {detail}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[エラー] 自己再起動APIの呼び出し中に例外が発生しました: {e}", file=sys.stderr)
        return False


def run_one_check(last_prices, last_summary_bucket):
    """1回分のチェック（アラート判定＋必要ならサマリー送信）を行う。

    last_prices は呼び出し元が保持する辞書をそのまま書き換える（in-place更新）。
    戻り値は更新後の last_summary_bucket。
    """
    holdings, _cash_jpy = pea.load_portfolio()
    if not holdings:
        print("portfolio.json に保有銘柄がないため、このチェックはスキップします。")
        return last_summary_bucket

    usdjpy_rate = pea.get_usdjpy_rate()

    # ①アラート判定（前回チェック比、毎回必ず行う）
    triggered, current_prices = pea.build_alert_check(holdings, usdjpy_rate, last_prices=last_prices)
    if triggered:
        subject, body = pea.format_alert_email(triggered)
        pea.send_email(subject, body)
    else:
        print(f"前回チェック比でしきい値(±{pea.ALERT_THRESHOLD_PCT:.0f}%)を超えた銘柄はありませんでした。")
    last_prices.update(current_prices)

    # ②定時サマリー（30分刻みのバケツが変わったときだけ、1回送る）
    now_utc = datetime.now(timezone.utc)
    bucket_minute = (now_utc.minute // 30) * 30
    bucket = now_utc.replace(minute=bucket_minute, second=0, microsecond=0)
    if bucket != last_summary_bucket:
        report = pea.build_summary_report(holdings, usdjpy_rate)
        subject, body = pea.format_summary_email(report, usdjpy_rate)
        pea.send_email(subject, body)
        last_summary_bucket = bucket
    else:
        print("この30分枠では定時サマリーを送信済みのため、スキップします。")

    return last_summary_bucket


def main():
    start = time.monotonic()
    last_prices = pea.load_last_check_prices()
    last_summary_bucket = None
    iteration = 0

    print(f"[開始] ポートフォリオ自己ループを開始します（最大{MAX_LOOP_SECONDS / 3600:.1f}時間）。")
    print(f"[記録] 起動時点の前回チェック価格: {last_prices}")

    market_state = get_market_state()
    print(f"[市場状態] 起動時点のmarketState（{REFERENCE_MARKET_TICKER}基準）: {market_state}")
    if market_state == "CLOSED":
        print(
            "[休場] 現在は完全休場中（CLOSED）のため、チェックを行わずに終了します。"
            "土日や、取引時間外の完全な空白帯（アフターマーケット終了〜翌日の"
            "プレマーケット開始前など）がこれに該当します。次にPRE/REGULAR/POSTの"
            "いずれかに変わったタイミングは、1時間おきの保険（watchdog）実行が"
            "検知して自動的にループを再開します（今回は自己再起動を行いません）。"
        )
        return

    while True:
        iteration += 1
        now_utc = datetime.now(timezone.utc)
        elapsed = time.monotonic() - start
        print(f"--- [{iteration}回目チェック] {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}（経過 {elapsed / 60:.1f}分、marketState={market_state}） ---")

        try:
            last_summary_bucket = run_one_check(last_prices, last_summary_bucket)
        except Exception as e:
            # 1回のチェックで例外が起きても、ループ全体は継続する（次の5分後にリトライ）。
            print(f"[警告] このチェック中にエラーが発生しましたが、ループは継続します: {e}", file=sys.stderr)

        elapsed = time.monotonic() - start
        if elapsed >= MAX_LOOP_SECONDS:
            print(f"[終了] 上限（{MAX_LOOP_SECONDS / 3600:.1f}時間）に達したため、ループを終了します。")
            break

        market_state = get_market_state()
        if market_state == "CLOSED":
            print(
                "[休場] 完全休場（CLOSED）になったため、ループを終了します。"
                "次にPRE/REGULAR/POSTのいずれかに変わったタイミングは、"
                "1時間おきの保険（watchdog）実行が検知して自動的にループを"
                "再開します（今回は自己再起動を行いません）。"
            )
            pea.save_last_check_prices(last_prices)
            commit_last_check_prices()
            print("[完了] 休場のため、ここでジョブを終了します。")
            return

        sleep_for = min(CHECK_INTERVAL_SECONDS, MAX_LOOP_SECONDS - elapsed)
        if sleep_for > 0:
            print(f"[待機] 次のチェックまで{sleep_for / 60:.1f}分スリープします。")
            time.sleep(sleep_for)

    # 次回（後継ジョブ）のチェック用に、最新の価格をファイルへ保存してコミットする。
    pea.save_last_check_prices(last_prices)
    commit_last_check_prices()

    # 自分自身を再度 workflow_dispatch で起動し、実質的にループを継続する。
    redispatch_self()

    print("[完了] 今回のジョブはここで終了します（自己再起動が成功していれば、まもなく後継ジョブが始まります）。")


if __name__ == "__main__":
    main()
