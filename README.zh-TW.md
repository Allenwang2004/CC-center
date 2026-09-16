# cc-center

[English](README.md)

一個常駐在狀態列的 Mac app (也有 CLI), 把你散在好幾台機器上的 Claude Code session
收回來: 現在誰在跑、誰停下來在等你、今天到底做了什麼。再加上工具算不出來的那一半:
你自己的 note, 以及每天早上 Claude 從整天的紀錄替你寫好的 journal。

所有東西都是從 Claude Code 本來就會留下的 transcript (`~/.claude/projects/*/*.jsonl`)
讀出來的。被監看的機器不用裝任何東西; 除了存 note 和 journal 的資料庫之外, 沒有任何
東西跑在雲端。

## 功能

- **在等你。** 分得出「還在做事」跟「停下來等你回話、等你按權限、跑到一半掛了」,
  停下來就跳桌面通知。狀態列顯示「在等你 · 在跑」兩個數字, 選單直接列出那些 session,
  點一則就開視窗跳到它。
- **中斷或結束 session。** 在 Agents 分頁: Interrupt 等於 Ctrl+C (打斷這一輪, 對話還在),
  End 結束 claude 程式 (之後還能 resume)。遠端機器上的 session 用 ssh 一樣能停。
- **每一台機器。** 遠端機器用 ssh 收, 同一份 scanner 用 stdin 餵給對面的 `python3`,
  遠端什麼都不用裝。
- **一題一題對到 commit。** 每則提問對上它改了哪些檔、跑了哪些指令、進了哪個 commit
  (從 repo 的 `git log` 撈回來對上的, 不是從 transcript 猜的); 還沒進 commit 的改動也列出來。
- **花費與 context。** 每天照 API 牌價算的花費、花費日曆、每個活著的 session 用了多少
  context window, 以及從 Claude Code status line 接來的方案 5 小時 / 7 天額度。
- **note 與每日 journal。** note 是你的, 一則一個檔。journal 每天早上由 `claude -p` 從
  那天的完整紀錄寫出來 (為什麼、怎麼做、哪個 commit), 你再補自己的話。兩者都存在
  你帳號底下的 Supabase, 本機有快取, 離線也看得到。
- **你的 CLAUDE.md。** 每台機器的全域 `~/.claude/CLAUDE.md` 跟每個專案的
  `.claude/CLAUDE.md`, 直接在 Claude Code 讀的位置編輯; 專案沒有的話一鍵建立。
- **報告。** 同一份事實輸出成 Markdown 或 JSON, 以天或以專案, 從命令列輸出,
  可以寫進腳本跟 cron。

## 安裝

### Mac app

從 Releases 下載 `cc-center_<版本>_aarch64.dmg`, 把 `cc-center.app` 拖進「應用程式」
打開。它住在狀態列: 點圖示開視窗, 關掉視窗照樣監看。

目前的 build 還沒簽章、沒有 notarize, 第一次打開 macOS 會擋。到 系統設定 → 隱私與安全性
按「強制打開」, 或清掉隔離旗標:

```bash
xattr -dr com.apple.quarantine /Applications/cc-center.app
```

目前需要 Apple Silicon 跟 macOS 26 (見 Roadmap)。機器上要裝好 Claude Code, journal 才寫得出來。

### 從原始碼

在自己機器上跑最簡單的路, 也是不用簽章就能拿到 Mac app 的路: 自己 build。自己 build
出來的 app 沒有隔離旗標, macOS 直接就開。

要有 Xcode Command Line Tools (`xcode-select --install`)、`python3`; 要 Mac app 的話
再加 [Rust](https://rustup.rs) 跟 Node。Claude Code 本身要裝好, journal 才寫得出來。

```bash
git clone https://github.com/Allenwang2004/cc-center.git
cd cc-center
cp .env.example .env        # 已經指到一個能用的 Supabase 專案; 機器清單、時區想改再改

# 用瀏覽器開頁面 (只要 python3, 什麼都不用 build)
bin/cc-center-app

# 或者 build 成狀態列 app
cd desktop
npm install
npm run build               # 第一次要編 Tauri, 大約五分鐘
npm run install-app         # 換進 /Applications
```

之後更新: `git pull`, 再 `npm run build && npm run install-app` 一次。
想裝成一般指令的話 `pip install -e .`, `cc-center` 跟 `cc-center-app` 會進 PATH; 不是必要的。

### Supabase

note 跟 journal 存在 Supabase, 用 email 登入的那個帳號底下。`.env.example` 已經指到這個
repo 用的專案, 所以什麼都不用設就能試: 登入, 那些列就是你的 (row level security 讓每個
帳號只看得到自己的)。裡面那把 anon key 本來就是設計成公開的, 保護資料的是 RLS 不是 key。
Mac app 在建置時就把專案埋進去了。

想改用自己的專案, 第一次做三件事:

1. 開一個專案, 在 SQL Editor 跑一次 `supabase/schema.sql`。它建一張 `entries` 表,
   row level security 讓每個帳號只看得到自己的列。
2. 登入是 email + 驗證碼。Supabase 要接了自己的 SMTP (Project Settings → Authentication →
   SMTP Settings) 才准改 email 樣板, 所以先設一個, 然後在 **Confirm signup** 跟
   **Magic Link** 兩個樣板都加上 `{{ .Token }}`。
3. 把你專案的 URL 跟 anon key 填進 `.env`, 換掉預設的那兩行。

## 怎麼用

**登入。** 頁面要一個 email, 寄驗證碼, 之後就記得你 (登入態存在 `~/.cc-center/auth.json`)。

**加機器。** 每台遠端機器要在 `~/.ssh/config` 有別名、能用金鑰免密碼登入。在側欄加,
或在 `.env` 設 `CC_HOSTS`。「Remote」開關可以把所有 ssh 關掉, 下班連不到 lab 的機器時用。

**方案額度。** Agents 分頁要看 5 小時 / 7 天額度, 先把 Claude Code 的 status line 接上一次:

```bash
bin/cc-center-app statusline-install
```

你原本的 status line 指令照常跑, 鉤子只是在中間把額度抄一份出來。

**Journal。** 每天早上 06:00 之後 (Settings → Journal 可改) 自動寫昨天的, 每個那天有
提問的專案各一份; 你已經寫過的那天不會被蓋。手動:

```bash
bin/cc-center-app journal                      # 昨天, 所有專案、所有機器
bin/cc-center-app journal --date 2026-09-10    # 指定那一天
bin/cc-center-app journal --dry-run            # 印出來, 不存
```

**報告。**

```bash
bin/cc-center-all                    # 今天, 本機加清單上的每一台
bin/cc-center-all --days 7 -o ~/week.md
bin/cc-center --days 7 --by-project  # 一個專案一題一題: 提問、改動、commit
bin/cc-center-all --json | claude -p "根據這份 JSON 幫我寫今天的工作日誌"
```

快捷鍵: `R` 立刻收集、`/` 跳到搜尋、`1`~`6` 切分頁; 編輯框裡 `Cmd+Enter` 存、`Esc` 還原。
不會自動存。

## 設定

個人設定放在 repo 根目錄的 `.env` (不進版控); 已經設好的環境變數永遠優先。其他都在
頁面的 Settings 分頁改, 存在 `~/.cc-center/settings.json`。

| 變數 | 意思 |
|---|---|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | note 跟 journal 存哪裡。頁面一定要有。 |
| `CC_HOSTS` | 遠端機器, 空白分隔 (`~/.cc-center-hosts` 一旦寫過就以它為準)。 |
| `CC_TZ` | 分日用的時區, IANA 名稱或固定位移。預設用機器的。 |
| `CC_BROWSER` | 用哪個瀏覽器開頁面 (macOS 的 app 名稱)。 |
| `CC_SSH_TIMEOUT` | ssh 連不上幾秒放棄。預設 8。 |
| `CC_CENTER_PORT` | 本機 server 的 port。預設 8787, 只綁 127.0.0.1。 |
| `CC_CENTER_STATE` | 資料庫、設定、log 放哪。預設 `~/.cc-center`。 |
| `CLAUDE_CONFIG_DIR` | Claude Code 的設定資料夾, 不是 `~/.claude` 時才設。 |

## 它是怎麼運作的

一條規矩貫穿全部: 重新掃 transcript 就重建得回來的東西一律用算的、不落地; 只有你寫的字才進資料庫。

```
transcript (本機 + ssh) --> scanner --> analysis --> 頁面 / 報告      (算的)
note, journal ------------> Supabase --> 本機 SQLite 快取 --> 頁面   (寫的)
```

- 本機 server (`src/cccenter/app/`) 是純 Python, 只綁 `127.0.0.1`, 每個 API 呼叫都要帶
  頁面發下來的 token。瀏覽器從不碰 Supabase, 是 server 代打的。
- 寫的時候 Supabase 先, 雲端收下了才進本機快取。快取每幾分鐘、以及每次登入時整份重拉,
  所以一台機器寫的 note 會出現在其他台。
- Mac app (`desktop/`) 是一個 Tauri 殼包著同一個 server —— 用 PyInstaller 凍起來當
  sidecar 帶著。視窗裡的頁面跟瀏覽器看到的是同一份 `web/`, 只是透過殼跟 sidecar 講話。

## 開發

```bash
npm install && npm run build       # web/src (TypeScript) -> web/dist, 有進版控
npm run verify                     # 型別檢查、單元測試、用 jsdom 把頁面跑一遍
python3 -m unittest discover -s tests -t .   # Python 測試; Supabase 是本機假的
pyright

cd desktop
npm install
npm run build                      # cc-center.app 跟 .dmg (要有 Rust 跟 python3)
npm run install-app                # 換進 /Applications
```

```
bin/            cc-center (報告 CLI), cc-center-app (server), cc-center-all (本機 + 遠端)
src/cccenter/   scanner, analysis, render, entries, cloud, sync, store, cli, app/
web/            index.html, style.css, src/ (TypeScript), dist/ (編好的, 有進版控)
desktop/        Tauri 殼 (src-tauri/), sidecar 與打包腳本
supabase/       schema.sql
tests/          Python 測試, 假的 Supabase
```

## Roadmap

- Mac app 簽章與 notarize; 自動更新
- sidecar 改用 python.org 的 Python 建, 支援舊版 macOS 跟 Intel Mac
- app 自己的原生通知
- server 一個模組一個模組搬到 Rust, 頁面不用動

## 貢獻

歡迎開 issue 跟 pull request。`src/cccenter/scanner.py` 要維持單一檔案、不 import 套件裡的
任何東西: 它是唯一會跑在遠端機器上的檔, 用 stdin 餵過去, 必須自己站得住。送 PR 前跑一次
`npm run verify` 跟 Python 測試。

## 團隊

- [Allenwang2004](https://github.com/Allenwang2004)

## 授權

[MIT](LICENSE)
