import io
import json
import time
from typing import Dict, Any, List, Optional

from flask import Flask, request, render_template_string, send_file
import requests

app = Flask(__name__)
API_BASE = "https://api.x.com/2"

HTML = """
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>Tweets.js Fetcher (互換フォーマット保証)</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif; margin: 24px; color: #222; }
    form { display: grid; gap: 12px; max-width: 860px; }
    label { font-weight: 600; }
    input, select { width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .btns { display: flex; gap: 8px; flex-wrap: wrap; }
    button { padding: 10px 14px; border: none; border-radius: 8px; cursor: pointer; font-weight: 700; background: #6b5cff; color: #fff; }
    .msg { padding: 10px 12px; border-radius: 8px; background: #f8f8f8; white-space: pre-wrap; }
    .muted { color: #666; font-size: 12px; }
  </style>
</head>
<body>
  <h1>Tweets.js Fetcher (互換フォーマット保証)</h1>
  <p class="muted">削除ツールが読む <code>window.YTD.tweets.part0 = [...]</code> 形式にピッタリ合わせて出力するよ。</p>
  {% if message %}<div class="msg">{{ message }}</div>{% endif %}

  <form action="/" method="post">
    <label>Bearer Token</label>
    <input type="password" name="bearer" required placeholder="AAAAAAAA...（Bearer）" value="{{ bearer or '' }}">

    <div class="row">
      <div>
        <label>ユーザー名（@なし）</label>
        <input type="text" name="username" required placeholder="example" value="{{ username or '' }}">
      </div>
      <div>
        <label>取得件数（合計）</label>
        <input type="number" name="total_count" min="1" max="3200" step="1" value="{{ total_count or 200 }}">
        <div class="muted">1回100件。ページングで指定数まで集めるよ。</div>
      </div>
    </div>

    <div class="row">
      <div>
        <label>リツイートを含める</label>
        <select name="include_rts">
          <option value="true" {% if include_rts %}selected{% endif %}>含める</option>
          <option value="false" {% if not include_rts %}selected{% endif %}>含めない</option>
        </select>
      </div>
      <div>
        <label>リプライを除外</label>
        <select name="exclude_replies">
          <option value="false" {% if not exclude_replies %}selected{% endif %}>除外しない</option>
          <option value="true" {% if exclude_replies %}selected{% endif %}>除外する</option>
        </select>
      </div>
    </div>

    <div class="btns">
      <button type="submit" name="action" value="check">接続確認</button>
      <button type="submit" name="action" value="fetch">取得してダウンロード</button>
    </div>
  </form>
</body>
</html>
"""

# ---- helpers ----
def auth_headers(bearer: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}

def sleep_for_reset(r: requests.Response):
    # レートヘッダに従って自動待機
    try:
        rem = int(r.headers.get("x-rate-limit-remaining", "1"))
        reset = int(r.headers.get("x-rate-limit-reset", "0"))
    except ValueError:
        rem, reset = 1, 0
    if r.status_code == 429 or rem <= 0:
        wait = max(0, reset - int(time.time())) + 2
        time.sleep(wait)

# ---- API ----
def get_user_by_username(bearer: str, username: str) -> Dict[str, Any]:
    url = f"{API_BASE}/users/by/username/{username}"
    r = requests.get(url, headers=auth_headers(bearer), timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"users/by/username {r.status_code}: {r.text[:300]}")
    return r.json()["data"]

def fetch_user_tweets_v2(
    bearer: str, user_id: str, total_count: int,
    include_rts: bool, exclude_replies: bool
) -> List[Dict[str, Any]]:
    url = f"{API_BASE}/users/{user_id}/tweets"
    items: List[Dict[str, Any]] = []
    next_token: Optional[str] = None

    excludes = []
    if not include_rts: excludes.append("retweets")
    if exclude_replies: excludes.append("replies")
    exclude_param = ",".join(excludes) if excludes else None

    while len(items) < total_count:
        params = {
            "max_results": 100,
            "tweet.fields": "created_at,lang,public_metrics,entities,source",
        }
        if exclude_param: params["exclude"] = exclude_param
        if next_token: params["pagination_token"] = next_token

        r = requests.get(url, headers=auth_headers(bearer), params=params, timeout=30)
        if r.status_code in (429, 503):
            sleep_for_reset(r); continue
        if r.status_code != 200:
            raise RuntimeError(f"/tweets {r.status_code}: {r.text[:300]}")

        data = r.json()
        batch = data.get("data", [])
        if not batch: break
        items.extend(batch)

        next_token = data.get("meta", {}).get("next_token")
        time.sleep(1.0); sleep_for_reset(r)
        if not next_token: break

    return items[:total_count]

# ---- mapping to EXACT format the deleter expects ----
def to_archive_items_v2(statuses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    必須： item["tweet"]["id_str"] を必ず入れる。
    互換性UP： full_text と text の両方に同値を入れる。
    ※ 削除ツールの parse は id_str をキーに見てるよ。
    """
    out: List[Dict[str, Any]] = []
    for s in statuses:
        tid = str(s.get("id"))
        text = s.get("text") or ""
        tweet_obj = {
            "id_str": tid,          # ★必須（削除側が参照）
            "id": tid,              # 互換用（あってもOK）
            "full_text": text,      # 互換用（優先で読まれる場合あり）
            "text": text,           # 互換用（保険）
            # 以下は削除には不要だが正規っぽくなる（任意）
            "created_at": s.get("created_at"),
            "lang": s.get("lang"),
            "source": s.get("source", ""),
            "retweet_count": str((s.get("public_metrics") or {}).get("retweet_count", 0)),
            "favorite_count": str((s.get("public_metrics") or {}).get("like_count", 0)),
            "entities": s.get("entities", {}) or {},
        }
        out.append({"tweet": tweet_obj})
    return out

def to_tweets_js(part0: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    buf.write("window.YTD.tweets.part0 = ")
    buf.write(json.dumps(part0, ensure_ascii=False))
    buf.write(";\n")
    return buf.getvalue().encode("utf-8")

# ---- Flask ----
@app.route("/", methods=["GET", "POST"])
def index():
    ctx = {"message": None, "bearer": "", "username": "", "total_count": 200,
           "include_rts": True, "exclude_replies": False}
    if request.method == "POST":
        bearer = request.form.get("bearer", "").strip()
        username = request.form.get("username", "").strip()
        total_count = int(request.form.get("total_count") or 200)
        include_rts = request.form.get("include_rts", "true") == "true"
        exclude_replies = request.form.get("exclude_replies", "false") == "true"
        action = request.form.get("action")
        ctx.update({"bearer": bearer, "username": username, "total_count": total_count,
                    "include_rts": include_rts, "exclude_replies": exclude_replies})

        if not bearer or not username:
            ctx["message"] = "Bearer Token と ユーザー名は必須だよ！"
            return render_template_string(HTML, **ctx)

        try:
            user = get_user_by_username(bearer, username)
        except Exception as e:
            ctx["message"] = f"ユーザー取得エラー: {type(e).__name__}: {e}"
            return render_template_string(HTML, **ctx)

        if action == "check":
            ctx["message"] = f"OK！ @{user['username']}（{user['name']}）の取得ができるよ。"
            return render_template_string(HTML, **ctx)

        if action == "fetch":
            try:
                statuses = fetch_user_tweets_v2(
                    bearer=bearer, user_id=user["id"], total_count=total_count,
                    include_rts=include_rts, exclude_replies=exclude_replies
                )
                # 削除ツールと相性の良い「古い順（ID昇順）」に整列
                statuses.sort(key=lambda x: int(x["id"]))

                part0 = to_archive_items_v2(statuses)
                js_bytes = to_tweets_js(part0)
                return send_file(
                    io.BytesIO(js_bytes),
                    mimetype="application/javascript; charset=utf-8",
                    as_attachment=True,
                    download_name="tweets.js"
                )
            except Exception as e:
                ctx["message"] = f"取得エラー: {type(e).__name__}: {e}"
                return render_template_string(HTML, **ctx)

    return render_template_string(HTML, **ctx)

if __name__ == "__main__":
    app.run(debug=True)
