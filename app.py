import io
import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Dict, Any, Optional

from flask import Flask, request, render_template_string, redirect, url_for, jsonify, send_from_directory
import requests
from requests_oauthlib import OAuth1
from werkzeug.utils import secure_filename
import email.utils as eut  # for RFC 2822 'created_at' parsing

# ====== 基本設定 ======
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# レート関連（1件ずつ方式）
INTERVAL_SEC = 20  # 1件ごと20秒

# タイムゾーン（JST固定）
JST = timezone(timedelta(hours=9))

# ====== 状態と制御フラグ ======
pause_event = threading.Event()   # set中はポーズ状態
cancel_event = threading.Event()  # setでキャンセル
state_lock = threading.Lock()

run_state: Dict[str, Any] = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "ng": 0,
    "current_id": None,
    "current_text": "",
    "phase": "idle",  # idle / processing / waiting / paused / canceled / finished / error
    "wait_until": 0.0,  # 待機終了予定時刻（epoch秒）
    "started_at": None,
    "log_filename": None,
    "message": "",
}

# ====== HTML（シングルファイルUI） ======
HTML = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>Tweet Deleter GUI</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root { --accent:#6b5cff; --danger:#e63b3b; --muted:#666; }
    body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif; margin: 24px; color: #222; }
    h1 { font-size: 20px; margin-bottom: 12px; }
    form { display: grid; gap: 12px; max-width: 900px; }
    label { font-weight: 600; }
    input[type=text], input[type=password] { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    input[type=file] { padding: 6px 0; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .btns { display: flex; flex-wrap: wrap; gap: 8px; }
    button { padding: 10px 14px; border: none; border-radius: 8px; cursor: pointer; font-weight: 700; }
    .check { background: #eef6ff; color: #114488; }
    .run { background: #ffefef; color: #882222; }
    .ctrl { background: var(--accent); color: #fff; }
    .ctrl.stop { background: var(--danger); }
    .msg { padding: 10px 12px; border-radius: 8px; background: #f8f8f8; white-space: pre-wrap; }
    .muted { color: var(--muted); font-size: 12px; }
    .result-ok { color: #0a7f39; }
    .result-ng { color: #a11212; }
    details { background: #fafafa; border-radius: 8px; padding: 8px 12px; }
    summary { cursor: pointer; font-weight: 600; }

    .panel { margin-top: 20px; padding: 12px; border: 1px solid #eee; border-radius: 8px; }
    .status-line { margin: 6px 0; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .progress-wrap { margin: 10px 0; }
    progress { width: 100%; height: 16px; }
    .countdown { font-weight: 700; }
    .log-link { margin-top: 8px; }
    .kbd { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: #f1f1f1; padding: 2px 6px; border-radius: 6px; }
    .flex { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  </style>
</head>
<body>
  <h1>Tweet Deleter GUI</h1>
  <p class="muted">APIキー類は送信ごとに使い捨てで処理し、サーバー側に保存しないよ。</p>
  {% if message %}
    <div class="msg">{{ message }}</div>
  {% endif %}
  <form id="main-form" action="{{ url_for('handle') }}" method="post" enctype="multipart/form-data">
    <div class="row">
      <div>
        <label>API_KEY</label>
        <input type="password" name="api_key" required placeholder="YOUR_API_KEY" value="{{ api_key or '' }}">
      </div>
      <div>
        <label>API_SECRET_KEY</label>
        <input type="password" name="api_secret" required placeholder="YOUR_API_SECRET_KEY" value="{{ api_secret or '' }}">
      </div>
    </div>
    <div class="row">
      <div>
        <label>ACCESS_TOKEN</label>
        <input type="password" name="access_token" required placeholder="YOUR_ACCESS_TOKEN" value="{{ access_token or '' }}">
      </div>
      <div>
        <label>ACCESS_TOKEN_SECRET</label>
        <input type="password" name="access_token_secret" required placeholder="YOUR_ACCESS_TOKEN_SECRET" value="{{ access_token_secret or '' }}">
      </div>
    </div>

    <label>tweet.js（Twitterアーカイブ内のファイル）</label>
    <input type="file" name="tweet_js" accept=".js,application/json">

    <div class="btns">
      <button class="check" type="submit" name="action" value="check">接続確認</button>
      <button class="run" type="submit" name="action" value="run">実行（削除）</button>
    </div>
  </form>

  <div class="panel">
    <div class="flex">
      <button class="ctrl" id="btn-pause">一時停止</button>
      <button class="ctrl" id="btn-resume">再開</button>
      <button class="ctrl stop" id="btn-cancel">キャンセル</button>
    </div>

    <div id="status-view">
      <div class="status-line">状態: <b id="st-phase">idle</b> / 実行中: <b id="st-running">false</b></div>
      <div class="status-line">進捗: <span id="st-done">0</span> / <span id="st-total">0</span>（OK: <span id="st-ok">0</span> / NG: <span id="st-ng">0</span>）</div>
      <div class="progress-wrap">
        <progress id="st-progress" value="0" max="100"></progress>
      </div>
      <div class="status-line">進捗率: <b id="st-pct">0%</b></div>
      <div class="status-line">残り時間(推定): <b id="st-eta">-</b></div>
      <div class="status-line mono">現在: ID <span id="st-id">-</span> / <span id="st-text">-</span></div>
      <div class="status-line">待機: <span class="countdown" id="st-wait">-</span></div>
      <div class="status-line">ログ: <span id="st-log">-</span> <span id="st-loglink" class="log-link"></span></div>
      <div class="status-line muted">注: 1件ごとに {{ interval_sec }} 秒待機するよ。tweet.js は <span class="kbd">window.YTD.tweets.part0 = [...];</span> を想定。</div>
    </div>
  </div>

<script>
const $ = (sel) => document.querySelector(sel);

function fmtText(s, n=80) {
  if (!s) return "-";
  s = String(s).replaceAll("\\n", " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

async function postControl(cmd) {
  try {
    const res = await fetch("{{ url_for('control') }}", {
      method: "POST",
      headers: {"Content-Type":"application/x-www-form-urlencoded"},
      body: "cmd=" + encodeURIComponent(cmd)
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
  } catch(e) { console.error(e); }
}

$("#btn-pause").addEventListener("click", () => postControl("pause"));
$("#btn-resume").addEventListener("click", () => postControl("resume"));
$("#btn-cancel").addEventListener("click", () => postControl("cancel"));

async function poll() {
  try {
    const res = await fetch("{{ url_for('status') }}?_=" + Date.now());
    if (!res.ok) throw new Error("HTTP " + res.status);
    const s = await res.json();

    $("#st-phase").textContent = s.phase;
    $("#st-running").textContent = s.running;
    $("#st-done").textContent = s.done;
    $("#st-total").textContent = s.total;
    $("#st-ok").textContent = s.ok;
    $("#st-ng").textContent = s.ng;
    $("#st-id").textContent = s.current_id || "-";
    $("#st-text").textContent = fmtText(s.current_text || "-", 120);

    // ％とバー
    $("#st-pct").textContent = (s.pct ?? 0) + "%";
    $("#st-progress").value = s.pct ?? 0;
    $("#st-progress").max = 100;

    // 残り時間（推定）
    $("#st-eta").textContent = s.eta_hms || "-";

    // 待機残り
    if (s.phase === "waiting" && s.wait_remaining >= 0) {
      $("#st-wait").textContent = s.wait_remaining + " 秒";
    } else if (s.phase === "paused") {
      $("#st-wait").textContent = "一時停止中";
    } else {
      $("#st-wait").textContent = "-";
    }

    // ログリンク
    $("#st-log").textContent = s.log_filename || "-";
    if (s.log_filename) {
      $("#st-loglink").innerHTML = ' - <a href="{{ url_for("download_log", filename="__F__") }}".replace("__F__", encodeURIComponent(s.log_filename)) target="_blank">ダウンロード</a>';
    } else {
      $("#st-loglink").textContent = "";
    }
  } catch(e) {
    console.error(e);
  } finally {
    setTimeout(poll, 1200);
  }
}
poll();
</script>
</body>
</html>
"""

# ====== ユーティリティ ======
def make_auth(api_key: str, api_secret: str, access_token: str, access_secret: str) -> OAuth1:
    return OAuth1(api_key, api_secret, access_token, access_secret)

def verify_credentials(auth: OAuth1) -> Tuple[bool, int, str, dict]:
    url = "https://api.twitter.com/1.1/account/verify_credentials.json?skip_status=true&include_email=false"
    resp = requests.get(url, auth=auth, timeout=20)
    if resp.status_code == 200:
        data = resp.json()
        return True, resp.status_code, "", data
    return False, resp.status_code, resp.text, {}

def parse_twitter_created_at_to_jst(s: str) -> Optional[str]:
    """
    Twitterの created_at (例: 'Mon Apr 06 22:19:45 +0000 2009') を
    JST(+09:00) の ISO 文字列に変換する。失敗したら None。
    """
    try:
        dt = eut.parsedate_to_datetime(s)   # tz-aware
        return dt.astimezone(JST).isoformat(timespec="seconds")
    except Exception:
        return None

def parse_tweet_js(file_bytes: bytes) -> List[Dict[str, str]]:
    """
    tweet.js（window.YTD.tweets.part0 = ...）から
    [{"id":"...", "text":"...", "posted_at":"..."}] の配列を返す。
    削除順は「古い方から」にしたいので、この後でID昇順にソートする（Twitter Snowflakeは時間順）。
    """
    text = file_bytes.decode("utf-8", errors="replace").strip()
    prefix = "window.YTD.tweets.part0 = "
    if text.startswith(prefix):
        text = text[len(prefix):]
    while text and text[0] not in "[{":
        text = text[1:]
    data = json.loads(text)

    items = []
    for item in data:
        if isinstance(item, dict) and "tweet" in item:
            tw = item["tweet"]
            tid = tw.get("id_str") or tw.get("id")
            if not tid:
                continue
            ttext = tw.get("full_text") or tw.get("text") or ""
            posted_at_iso = parse_twitter_created_at_to_jst(tw.get("created_at", ""))
            items.append({"id": str(tid), "text": ttext, "posted_at": posted_at_iso})
    # ★ 古い順（ID昇順）で並べ替え
    items.sort(key=lambda x: int(x["id"]))
    return items

# ====== ログ ======
def open_log() -> str:
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    filename = f"deleted_ids_{ts}.log"
    path = os.path.join(LOG_DIR, filename)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"# Tweet Deleter Log {ts}\n")
        f.write("# timezone: JST(+09:00)\n")
        f.write("# format: <response_at_iso>\t<tweet_id>\t<status>\t<posted_at_iso>\t<text(head)>\n")
    return filename

def append_log(filename: str, tid: str, status: str, text_head: str,
               response_at: Optional[datetime] = None,
               posted_at_iso: Optional[str] = None):
    path = os.path.join(LOG_DIR, filename)
    head = (text_head or "").replace("\n", " ").strip()[:120]
    now_iso = (response_at or datetime.now(JST)).isoformat(timespec="seconds")
    posted = posted_at_iso or ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{now_iso}\t{tid}\t{status}\t{posted}\t{head}\n")

# ====== 補助：ETA表示用 ======
def seconds_to_hms(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}時間{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"

# ====== 実処理（1件ずつ＋待機中カウントダウン＋一時停止/キャンセル） ======
def delete_tweets_incremental(auth: OAuth1, tweets: List[Dict[str, str]]):
    with state_lock:
        run_state.update({
            "running": True, "phase": "processing", "message": "",
            "total": len(tweets), "done": 0, "ok": 0, "ng": 0,
            "current_id": None, "current_text": "",
            "started_at": time.time(),
            "wait_until": 0.0,
            "log_filename": open_log(),
        })
        log_name = run_state["log_filename"]

    try:
        for item in tweets:
            # キャンセル？
            if cancel_event.is_set():
                with state_lock:
                    run_state["phase"] = "canceled"
                break

            # ポーズ
            while pause_event.is_set() and not cancel_event.is_set():
                with state_lock:
                    run_state["phase"] = "paused"
                time.sleep(0.5)

            if cancel_event.is_set():
                with state_lock:
                    run_state["phase"] = "canceled"
                break

            tid = item["id"]
            ttext = item.get("text", "")
            posted_at_iso = item.get("posted_at")  # 追加: 投稿時刻（JST）

            # 表示用に更新
            with state_lock:
                run_state["phase"] = "processing"
                run_state["current_id"] = tid
                run_state["current_text"] = ttext

            # 削除リクエスト
            url = f"https://api.twitter.com/1.1/statuses/destroy/{tid}.json"
            resp = requests.post(url, auth=auth, timeout=20)
            response_at = datetime.now(JST)  # 追加: レスポンス返却時刻（JST）

            if resp.status_code == 200:
                with state_lock:
                    run_state["ok"] += 1
                append_log(log_name, tid, "OK", ttext, response_at, posted_at_iso)
            else:
                with state_lock:
                    run_state["ng"] += 1
                append_log(log_name, tid, f"NG({resp.status_code})", ttext, response_at, posted_at_iso)

            with state_lock:
                run_state["done"] += 1

            # ここから待機（リアルタイム表示）
            wait_until = time.time() + INTERVAL_SEC
            with state_lock:
                run_state["phase"] = "waiting"
                run_state["wait_until"] = wait_until

            while True:
                if cancel_event.is_set():
                    with state_lock:
                        run_state["phase"] = "canceled"
                    break
                if pause_event.is_set():
                    with state_lock:
                        run_state["phase"] = "paused"
                    time.sleep(0.5)
                    continue
                if time.time() >= wait_until:
                    break
                time.sleep(0.5)

            if cancel_event.is_set():
                break

        # 終了
        with state_lock:
            if run_state["phase"] not in ("canceled", "error"):
                run_state["phase"] = "finished"
    except Exception as e:
        with state_lock:
            run_state["phase"] = "error"
            run_state["message"] = f"{type(e).__name__}: {e}"
    finally:
        with state_lock:
            run_state["running"] = False
        pause_event.clear()
        cancel_event.clear()

# ====== ルーティング ======
@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML, message=None, interval_sec=INTERVAL_SEC)

@app.route("/handle", methods=["POST"])
def handle():
    api_key = request.form.get("api_key", "").strip()
    api_secret = request.form.get("api_secret", "").strip()
    access_token = request.form.get("access_token", "").strip()
    access_token_secret = request.form.get("access_token_secret", "").strip()
    action = request.form.get("action")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        return render_template_string(HTML, message="キーが不足してるよ！全部入れてね。", interval_sec=INTERVAL_SEC)

    auth = make_auth(api_key, api_secret, access_token, access_token_secret)

    if action == "check":
        ok, status, text, data = verify_credentials(auth)
        if ok:
            msg = f"OK！ 認証できたよ。@{data.get('screen_name','')}（{data.get('name','')}）"
        else:
            msg = f"認証エラー：{status} / {text[:500]}"
        return render_template_string(
            HTML,
            message=msg,
            api_key=api_key, api_secret=api_secret, access_token=access_token, access_token_secret=access_token_secret,
            interval_sec=INTERVAL_SEC
        )

    if action == "run":
        f = request.files.get("tweet_js")
        if not f or f.filename == "":
            return render_template_string(HTML, message="tweet.js を選んでね。", interval_sec=INTERVAL_SEC)

        _ = secure_filename(f.filename)
        file_bytes = f.read()

        try:
            tweets = parse_tweet_js(file_bytes)  # [{"id","text","posted_at"}...], ★この中で古い順に並べ替え済み
        except Exception as e:
            return render_template_string(HTML, message=f"tweet.js の解析でエラー: {e}", interval_sec=INTERVAL_SEC)

        if not tweets:
            return render_template_string(HTML, message="tweet.js からツイートIDを見つけられなかったよ…。", interval_sec=INTERVAL_SEC)

        # 実行開始（バックグラウンド）
        with state_lock:
            run_state.update({"running": True, "phase": "processing", "message": ""})
        t = threading.Thread(target=delete_tweets_incremental, args=(auth, tweets), daemon=True)
        t.start()

        return render_template_string(HTML, message="削除を開始したよ！パネルで進捗を見てね。", interval_sec=INTERVAL_SEC)

    return redirect(url_for("index"))

@app.route("/control", methods=["POST"])
def control():
    cmd = request.form.get("cmd")
    if cmd == "pause":
        pause_event.set()
    elif cmd == "resume":
        pause_event.clear()
    elif cmd == "cancel":
        cancel_event.set()
    return "ok"

@app.route("/status")
def status():
    with state_lock:
        s = dict(run_state)  # shallow copy

    # 待機残り秒
    wait_remaining = -1
    if s.get("phase") == "waiting":
        wait_remaining = max(0, int(s.get("wait_until", 0) - time.time()))
    if s.get("phase") == "paused":
        wait_remaining = -1

    total = int(s.get("total") or 0)
    done = int(s.get("done") or 0)
    started_at = s.get("started_at")
    now = time.time()

    # 進捗％
    pct = int((done / total) * 100) if total > 0 else 0

    # ETA（実測ペースで自己補正、最低でもINTERVAL_SEC/件）
    eta_seconds = 0
    if total > 0 and done < total and started_at:
        elapsed = max(0.0, now - float(started_at))
        avg = max(INTERVAL_SEC, elapsed / done) if done > 0 else INTERVAL_SEC
        items_left = total - done
        eta_seconds = int(items_left * avg)
        if wait_remaining and wait_remaining > 0:
            eta_seconds += wait_remaining

    def seconds_to_hms(sec: int) -> str:
        h = sec // 3600
        m = (sec % 3600) // 60
        s2 = sec % 60
        if h > 0:
            return f"{h}時間{m}分{s2}秒"
        if m > 0:
            return f"{m}分{s2}秒"
        return f"{s2}秒"

    return jsonify({
        "running": s.get("running"),
        "total": total,
        "done": done,
        "ok": s.get("ok"),
        "ng": s.get("ng"),
        "current_id": s.get("current_id"),
        "current_text": s.get("current_text"),
        "phase": s.get("phase"),
        "wait_remaining": wait_remaining,
        "log_filename": s.get("log_filename"),
        "message": s.get("message"),
        "pct": pct,
        "eta_seconds": eta_seconds,
        "eta_hms": seconds_to_hms(eta_seconds),
    })

@app.route("/logs/<path:filename>")
def download_log(filename):
    return send_from_directory(LOG_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
