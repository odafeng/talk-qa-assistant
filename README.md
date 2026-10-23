# talk-qa-assistant

線上報告的即時 Q&A 助手。Q&A 時間開著它，它會聽整段會議，把聽眾的問題挑出來，幾秒內給你一份可以直接唸的口語稿，附上論文出處和該翻到第幾頁投影片。

```
Meet 分頁音訊（聽眾） ─┐                          ┌─ 不用回答／還沒講完 → 略過
                      ├─> Gemini Live 逐字稿 ─> Jev ┤
你的麥克風（我） ─────┘   （有標說話的人）   判斷  └─ 新問題或沒把握 ─> Claude Sonnet 5.5 ─> 口語稿
                                          0.2 秒                    （論文 + 你的筆記）
```

- **聽 Meet 分頁本身的聲音**，不經過麥克風。只有別人的聲音會被當成提問；戴耳機也收得到。
- **自己判斷哪些是問題**：寒暄、主持人串場會略過；問題講到一半會等；同一題盡量不重答（第一場重播：10 個問題答了 11 次）；聽眾補充問題時會給更新版。
- 完整回答大約 5–8 秒（隨 Claude API 的回應速度變動），第一個字通常 1–2 秒就出現。
- 全程逐字稿和問答紀錄可以下載。

## 需要的東西

- macOS 或 Linux、Python 3.12+、[uv](https://docs.astral.sh/uv/)
- `pdftotext`（poppler）：macOS 用 `brew install poppler`
- Chrome（用來擷取 Meet 分頁的音訊）
- Claude API key（Max / Team 方案每月附 API 額度，見 [官方說明](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)）
- Gemini API key（即時轉錄用 `gemini-3.5-transcribe-live`）
- 選配：TypeSafe API key（Jev 先判斷要不要回答，Claude 的呼叫大約少四成；沒有也能用）

## 設定

```bash
uv sync
cp .env.example .env                       # 填入 Claude、Gemini 的 key（Jev 選配）
uv run python qa.py init ~/path/to/project # 在專案裡建立 qa_context/ 和啟動檔
```

把這場演講的資料丟進 `qa_context/`，執行中加檔案也會在 10 秒內讀進來：

| 檔案 | 當成 |
|---|---|
| `*.pdf`、`*.paper.txt` | 論文（可以好幾篇；轉成逐頁文字，引用會標頁碼） |
| `*.md`、`*.txt` | 講稿、大綱、Q&A 筆記 |
| `*.png`、`*.jpg`、`*.webp`、`*.gif` | 圖表、截圖（檔名當說明；只有問到圖時才送，見 [ADR 0005](docs/adr/0005-context-folder-and-images.md)） |
| `*.tsx` | open-slide 投影片，用來回答「翻到第幾頁」 |
| `talk.toml` | 選配：題目、講者、場合、專有名詞 |

`qa_context/` 含有你的講稿和個人資料，所以放在你自己的專案裡，不要放進這個 repo。

## 使用

雙擊 `qa_context/QA_Assistant.command`（或手動執行 `QA_CONTEXT=~/path/to/project/qa_context uv run uvicorn app:app --port 8777`）。

1. 用 Chrome 打開 http://localhost:8777 ，按「開始聆聽」。
2. 選**會議的分頁**（Meet、Zoom 網頁版、Teams 網頁版都可以），並打開**「同時分享分頁音訊」**。
3. 聽眾提問後，舞台上會出現口語稿：問題、可以直接唸的回答、翻到哪一頁、依據和注意事項。
4. 漏抓時可以按「現在回答」，或在下方輸入框手動打問題。

在會議裡分享投影片分頁就好，這個頁面不會被分享出去。Q&A 開始前就先按「開始聆聽」，按之前講的話它聽不到。

## 開發

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pre-commit install        # commit 前自動跑上面這些
```

測試用 `tests/conftest.py` 裡的迷你假專案（含一張測試圖片），不需要真實的論文或 API key。

## 設計紀錄

- [0001 用 Meet 分頁音訊加 Gemini Live 做即時逐字稿](docs/adr/0001-meet-tab-audio-and-gemini-live.md)
- [0002 用 Claude Sonnet 5.5（不思考）加論文逐頁文字來回答](docs/adr/0002-claude-sonnet-with-page-text-context.md)
- [0003 演講資料放在 repo 外，用 talk.toml 指過去](docs/adr/0003-talk-materials-outside-the-repo.md)
- [0004 先讓 Jev 判斷要不要回答，有把握才照它的判斷](docs/adr/0004-jev-gate-before-claude.md)
- [0005 每個專案一個 qa_context 資料夾，圖片只在問到圖時才送](docs/adr/0005-context-folder-and-images.md)

## 隱私

- 逐字稿和問答紀錄寫在 `qa_context/sessions/`，`init` 會建一個 `.gitignore` 把它排除。
- 會議音訊會送到 Google（Gemini），逐字稿和你的筆記會送到 Anthropic（Claude）；有設定 Jev 的話，逐字稿也會送到 TypeSafe。錄別人的聲音之前，先確認會議允許。
- 本機服務只接受來自 `localhost:8777` 頁面的連線，其他網站連不進來。
