# 0002. 用 Claude Sonnet 5.5（不思考）加論文逐頁文字來回答

## Status

Accepted（2026-10-23）

## Context

Q&A 時，聽眾問完到講者看到可以唸的口語稿，等越久越尷尬。2026-10-23 用同一個問題、快取已經熱好的情況實測：

| 設定 | 第一個字 | 全部出完 |
|---|---|---|
| Opus 5.5、PDF | 7.4 s | 13.6 s |
| Opus 5.5 fast mode、PDF | 5.2 s | 7.5 s |
| Sonnet 5.5（思考）、PDF | 6.6–12.2 s | 9.7–15.5 s |
| Sonnet 5.5（不思考）、逐頁文字 | 0.8–2.1 s | 4.2–5.9 s |

PDF 會把每一頁的圖也送進去，context 從 8.8 萬變成 17.3 萬 tokens，處理特別慢。

## Decision

- 模型用 `claude-sonnet-5-5`，effort `low`，`thinking: {type: "between_tools"}` 關掉思考。這是講者看過比較表之後自己選的。
- 論文用 `pdftotext -layout` 轉成逐頁文字，放進 content document，開 citations，用 block index 對回頁碼。
- 論文和講者筆記放在快取前綴，TTL 1 小時，啟動時先預熱；閒置 50 分鐘就重新預熱。
- 每次判斷帶最近 40 行逐字稿和已經回答過的問題。模型只會回 SKIP、WAIT 或格式化的口語稿。
- 開啟 server-side fallback（`fallbacks: "default"`）。

## Consequences

- 好處：完整回答約 5 秒，SKIP 約 3 秒，WAIT 約 2 秒；每次判斷的費用也比 PDF 版低一半。
- 好處：拿 Table 2、3、6 的數字問了三題，答案的數字和標準差都對。
- 代價：純文字看不到圖。問到只畫在圖裡、正文沒寫的內容，模型答不出來。
- 代價：表格被攤平成文字，複雜表格有讀錯欄位的風險，目前只驗證過三張。
- 代價：Sonnet 不思考時，遇到需要推理的追問可能不如 Opus。要換回 Opus，改 `answerer.py` 的 `MODEL` 和 `THINKING` 就好，但 Opus 5.5 不能關思考。
- 代價：需要系統裝 poppler（`pdftotext`）。
