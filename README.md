# Tweets.js ツールセット

**Twitter (X)** のツイートを取得し、アーカイブ形式（`tweets.js`）に変換するツールと、そのアーカイブを用いてツイートを削除するツールのセットです。

## 内容

- `fetch_tweets_app.py`  
  Twitter API v2 からツイートを取得し、Twitter アーカイブ互換形式（`window.YTD.tweets.part0 = [...]`）の `tweets.js` を生成します。

- `app.py`  
  生成した `tweets.js` を読み込み、Twitter API v1.1 を使ってツイートを削除する GUI アプリです。進行状況やログをリアルタイムで確認できます。

- `requirements.txt`  
  必要な Python ライブラリ一覧。

---

## インストール

```bash
git clone https://github.com/あなたのアカウント/tweets-tools.git
cd tweets-tools
pip install -r requirements.txt
```

### キーの取得方法（ざっくり）

1. X Developer Portal https://developer.x.com/en/portal/dashboard にログイン  
2. プロジェクト & アプリを作成  
3. 「Keys and tokens」から以下をコピーして保存しておく  

- **Bearer Token（v2 用）** → `fetch_tweets_app.py` で使用  
- **API Key / API Key Secret / Access Token / Access Token Secret（v1.1 用）** → `app.py` で使用  

> ⚠️ これらのキーは **絶対に公開リポジトリに書かないこと！**

---

## 使い方

### 1. ツイートを取得（`fetch_tweets_app.py`）

#### 手順：

1. Bearer Token を用意する  
2. サーバーを起動

```bash
python fetch_tweets_app.py
```

3. ブラウザで `http://localhost:5000/` を開く  
4. Bearer Token とユーザー名を入力 → 「取得してダウンロード」ボタンを押す  
5. `tweets.js` がダウンロードされる！

#### オプション設定：

- リツイートを含める / 含めない  
- リプライを除外する / しない  
- 取得件数（最大 **3200件**まで）

> ⚠️ API 経由では 3200件が上限です。  
> それ以上必要な場合は、公式のアーカイブダウンロード機能を使って `tweets.js` を入手してください。

---

### 2. ツイートを削除（`app.py`）

#### 手順：

1. API Key / Secret / Access Token / Secret を用意  
2. サーバーを起動  

```bash
python app.py
```

3. ブラウザで `http://localhost:5000/` を開く  
4. APIキーを入力し、`tweets.js` をアップロード  
5. 「実行（削除）」で削除を開始！

#### GUI上でできること：

- 一時停止 / 再開 / キャンセル  
- ログは `logs/` フォルダに保存される

---

## レート制限について

- ツイート取得は **Twitter API v2** のレート制限に従います。  
  → 参考：[X API レート制限]https://developer.x.com/ja/docs/x-api/rate-limits

- ツイート削除は **API v1.1** を利用し、**20秒間隔**で実行されます。

---

## ⚠️ 注意点
### 🔒 このツールの利用範囲について
本ツールは、**個人がローカル環境で自己責任のもと使用すること**を想定しています。  
インターネット上の**公開サーバーでの運用**や、**第三者へのサービス提供**などは、セキュリティ上のリスクを伴うため**推奨されません**。
- ✅ ローカルPCで動かす  
- ❌ クラウド/VPS上で公開運用する  
---
- 自己責任で使用してください。削除は取り消せません。  
- API の仕様変更により動作しなくなる可能性があります。  
- 本ツールは **Twitter 社や X Corp とは無関係の非公式ツール**です。
---

## 免責事項

> 本ツールの使用によって生じたいかなる損害や不利益について、作者は責任を負いません。  
> 利用者自身の責任で使用してください。



