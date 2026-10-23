# 0001. 用 Meet 分頁音訊加 Gemini Live 做即時逐字稿

## Status

Accepted（2026-10-23）

## Context

線上報告的 Q&A 時間，講者需要即時看到聽眾的問題。聽眾的聲音只存在 Google Meet 分頁裡。電腦麥克風收不到戴耳機時的對方聲音；沒戴耳機時又會把喇叭的聲音收進來，和講者自己的聲音混在一起。

講者原本的 lecture_live_notes 專案已經用 Gemini Live（`gemini-3.5-transcribe-live`）做過即時轉錄，連線輪替、斷線重試都處理過，但那個專案只收麥克風。

## Decision

- 瀏覽器用 `getDisplayMedia` 擷取 Meet 分頁的音訊，標成「聽眾」。講者的麥克風另外用 `getUserMedia` 擷取，標成「我」，可以關掉。
- 兩路音訊各開一個 Gemini Live 連線。轉錄的程式移植自 lecture_live_notes：每 570 秒輪替一次 session，連續失敗 5 次就放棄，逐字稿用 OpenCC `s2twp` 轉成台灣用語。
- 每段音訊的第一個 byte 標明是哪一路，伺服器據此分流。

## Consequences

- 好處：不需要 BlackHole 之類的虛擬音訊驅動，也不用改系統音訊設定。戴不戴耳機都收得到對方的聲音，而且「聽眾」和「我」天生分開，不需要做語者辨識。
- 代價：每次開始都要在瀏覽器選 Meet 分頁，還要記得勾「同時分享分頁音訊」，漏勾就收不到聲音（頁面會提醒）。
- 代價：Gemini 不分辨不同的聽眾，所有聽眾的話都標成同一個「聽眾」。
- 代價：兩個 Live 連線的費用和網路流量都是兩倍。
- 代價：Chrome 的 headless 模式不支援分頁擷取，自動化測試要開有視窗的 Chrome 才測得到。
