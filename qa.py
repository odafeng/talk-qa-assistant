"""Set up a project for the assistant:  uv run python qa.py init /path/to/project

Creates /path/to/project/qa_context/ with a talk.toml to fill in and a double-click launcher
(QA_Assistant.command) that starts this copy of the assistant on that folder.
"""

import argparse
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent

TALK_TOML = """# 這場演講的基本資料（都可以省略）。其他檔案直接丟進這個資料夾就會被讀取：
#   *.pdf 論文、*.md / *.txt 講稿與筆記、*.png / *.jpg 圖表與截圖、*.tsx open-slide 投影片
title = "{title}"
speaker = "講者"
venue = "研討會"
# body_pages = 30          # 投影片第幾頁之後算 Backup
# slides = "../deck/slides/my-talk/index.tsx"   # 投影片不在這個資料夾時，指過去
vocabulary = []           # 論文名、資料集、指標等專有名詞，幫 Gemini 轉錄得更準
"""

LAUNCHER = """#!/bin/zsh
# 雙擊啟動 Q&A 助手。參考資料就是這個資料夾裡的檔案，放進來就會自動讀取（執行中也會）。
CONTEXT="$(cd "$(dirname "$0")" && pwd)"
APP="{app}"
PORT=8777
URL="http://localhost:$PORT/"

cd "$APP" || {{ echo "找不到 Q&A 助手：$APP"; read; exit 1; }}
if curl -s -o /dev/null "http://localhost:$PORT/health"; then
  echo "已經有一個 Q&A 助手在跑（可能是別的專案的），先關掉那個視窗再試一次。"
  open "$URL"; read; exit 0
fi
# A stale exported ANTHROPIC_API_KEY must not override the key in the assistant's .env.
QA_CONTEXT="$CONTEXT" env -u ANTHROPIC_API_KEY uv run uvicorn app:app --port $PORT --log-level warning &
SERVER=$!
until curl -s -o /dev/null "http://localhost:$PORT/health"; do sleep 0.5; done
open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
echo "Q&A 助手：$URL"
echo "參考資料夾：$CONTEXT"
echo "用完關掉這個視窗即可。逐字稿和問答紀錄存在 $CONTEXT/sessions/。"
wait $SERVER
"""

GITIGNORE = "# 逐字稿和問答紀錄含有別人的發言，不進版控\nsessions/\n"


def init(project: Path) -> Path:
    folder = project.expanduser().resolve() / "qa_context"
    (folder / "sessions").mkdir(parents=True, exist_ok=True)
    toml = folder / "talk.toml"
    if not toml.exists():
        toml.write_text(TALK_TOML.format(title=folder.parent.name), encoding="utf-8")
    (folder / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    launcher = folder / "QA_Assistant.command"
    launcher.write_text(LAUNCHER.format(app=HERE), encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="在專案裡建立 qa_context/ 和啟動檔")
    p.add_argument("project", type=Path)
    args = parser.parse_args()
    folder = init(args.project)
    print(f"已建立 {folder}\n把論文、筆記、圖片放進去，雙擊 {folder / 'QA_Assistant.command'} 啟動。")


if __name__ == "__main__":
    main()
