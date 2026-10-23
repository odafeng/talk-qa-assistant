# 0003. 演講資料放在 repo 外，用 talk.toml 指過去

## Status

Accepted（2026-10-23）

## Context

這個工具一開始寫在單一場研討會的 repo 裡，論文 PDF、講稿、講者姓名都直接寫死在程式裡。論文有版權，講稿和講者資料是個人資訊，都不該進公開或共用的 repo；而且下一場演講要能直接沿用。

## Decision

- 每場演講寫一份 `talk.toml`，內容包括題目、講者、場合、論文、筆記、投影片 deck、自訂詞彙、紀錄存放位置。這份檔案跟著演講資料放，不進這個 repo（`.gitignore` 也擋了 `talk.toml`）。
- 用環境變數 `TALK_CONFIG` 指定 `talk.toml` 的位置。相對路徑以 `talk.toml` 所在的資料夾為準。
- System prompt 用模板，講者、場合、題目從 `talk.toml` 填進去。
- 逐字稿和問答紀錄寫進 `talk.toml` 指定的 `sessions` 資料夾，跟著演講資料走。
- 測試用一份迷你假演講（`tests/conftest.py`），CI 不需要任何真實資料。

## Consequences

- 好處：repo 裡沒有論文、講稿或個人資料，可以公開，也可以給別的演講用。
- 好處：每場演講的問答紀錄留在那場演講的資料夾裡。
- 代價：多了一份設定檔要維護，啟動時一定要指定 `TALK_CONFIG`，不然找不到 `talk.toml` 就無法啟動。
- 代價：投影片頁碼對照只支援 open-slide 的 deck 格式；其他格式的投影片目前不會產生「翻到第幾頁」。
