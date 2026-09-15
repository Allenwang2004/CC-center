# cc-center — Claude Code 工作台

把散在好幾台機器上的 Claude Code session 收回來: 現在誰在跑、誰停下來在等你,
以及今天到底做了什麼。資料來源是每台機器的 `~/.claude/projects/*/*.jsonl`
transcript, 掃描不連任何雲端服務, 也不用 `pip install` 任何東西。

兩種用法, 同一套邏輯: `bin/cc-center-app` 是常駐的介面 (平常用這個),
`bin/cc-center` / `bin/cc-center-all` 是 CLI (寫進腳本或 cron 用)。

分界從頭到尾只有一條: **transcript 掃一遍就重建得回來的東西一律用算的,
要留下來的字才進資料庫** —— 你打的 note, 還有每天早上 Claude 替你寫好的 journal。
那份資料庫在 **Supabase**, 跟著你的帳號走, 不跟著機器; 本機留一份快取, 離線也看得到。
所以介面要先用 email 登入 (寄六位數驗證碼), 沒登入什麼都不給看。

## 專案結構

```
bin/                    三個入口, 直接執行, 不用安裝
├── cc-center           報告 CLI
├── cc-center-app       介面 + 常駐監看
└── cc-center-all       本機 + 遠端一起收 (bash)

src/cccenter/           Python 套件 (只用標準函式庫)
├── scanner.py          讀 transcript 與 git log → 事實。**刻意維持單一檔案**
├── analysis.py         那些事實加起來是什麼 (一天、一個專案改了什麼)
├── render/             給人看的 Markdown (以及一份給 claude 看的)
│   ├── daily.py        以天為單位
│   ├── projects.py     以專案為單位, 一題一題
│   └── journal.py      一天一個專案的完整紀錄, journal 就是從這份寫出來的
├── entries.py          note 投影到專案資料夾的那份 markdown (journal 以前也在, 現在只讀不寫)
├── cloud.py            Supabase 的 client (登入、refresh、entries 那張表), 只用 urllib
├── sync.py             雲端是真相: 寫的時候雲端先、快取後; pull 把雲端整份收下來
├── store.py            SQLite —— 雲端在本機的快取, 讀都走這裡
├── env.py              讀 repo 根目錄的 .env (個人設定不進程式碼)
├── cli.py              報告命令列
└── app/                本機介面
    ├── paths.py        程式碼、頁面、本機狀態各在哪
    ├── settings.py     設定與機器清單
    ├── util.py         now / parse_iso / shell_quote
    ├── attention.py    「這個 session 在等你嗎」的判斷 (純邏輯)
    ├── desktop.py      系統通知、開瀏覽器
    ├── bus.py          推給每個分頁的事件通道 (SSE)
    ├── writing.py      存 journal/note (Supabase → 快取 → note 投影到專案資料夾)
    ├── journal.py      每天早上把昨天的 journal 寫好: 收、排、問 claude、存
    ├── usage.py        方案用量 (5 小時 / 7 天): Claude Code statusline 的水槽
    ├── monitor.py      監看迴圈: 掃描、比對、推播
    ├── server.py       HTTP: 一頁、幾個 JSON route、一條事件流
    ├── daemon.py       背景程序的啟動/停止/狀態
    ├── service.py      開機自動起來 (launchd / systemd)
    └── main.py         把上面接成命令列

web/                    前端 (TypeScript, 英文介面)
├── index.html
├── style.css
├── src/
│   ├── main.ts         進入點: 分頁、快捷鍵、接線
│   ├── core/           不碰 DOM 的東西: api / store / types / format
│   ├── ui/             dom / sidebar / editor (editor + markdown + syntax)
│   └── views/          signin / agents / projects / sessions / activity / spend / detail / settings
├── dist/               編出來的 ES module —— **有進版控**, 所以拿到就能跑
└── tests/              smoke (jsdom 跑整個介面, 含登入) + 純函式單元測試

supabase/schema.sql     Supabase 那張表 + RLS, 在後台 SQL Editor 跑一次
tests/                  Python 測試 (unittest, 全部跑在暫存目錄裡, Supabase 是本機假的)
```

### 相依方向

```
scanner → analysis → render ─┐
   └────→ entries → store ───┼──→ app/* → web/
          cloud ──→ sync ────┤
                    cli ─────┘
```

往下相依, 不往上。四條規矩:

1. **`scanner.py` 不 import 這個套件裡的任何東西。** 它是唯一要在「什麼都沒裝」的
   機器上跑的檔案 —— 用 stdin 餵給對面的 `python3`, 所以它不能有兄弟模組。
   其他模組都可以 import 它, 反過來不行。這是部署條件決定的模組邊界, 不是偷懶。
2. **算出來的東西一律不寫進你的檔案。** `entries.py` 只寫你打的字。
3. **介面跟 CLI 讀同一份 `analysis.day_changes`**, 所以兩邊不可能對不上。
4. **瀏覽器永遠只打 127.0.0.1。** Supabase 是本機 server 代打的 (`cloud.py`, 只用 urllib),
   頁面上沒有任何 key, 也不載任何 CDN。

### 跑起來

```bash
bin/cc-center-app          # 背景啟動 + 開瀏覽器 (已經在跑就只開分頁)
bin/cc-center --days 7     # 報告
```

不用安裝、沒有相依套件。想裝成一般指令的話 `pip install -e .` 也可以,
`cc-center` 跟 `cc-center-app` 會進 PATH。

個人設定 (機器清單、時區、瀏覽器、Supabase) 放在 repo 根目錄的 `.env`, 不進版控:

```bash
cp .env.example .env     # 然後改成你的
```

三個入口讀的都是同一份, 已經設好的環境變數永遠優先。全部的變數:
`CC_HOSTS`、`CC_TZ`、`CC_BROWSER`、`CC_SSH_TIMEOUT`、`CC_CENTER_PORT` (預設 8787)、
`CC_CENTER_STATE` (預設 `~/.cc-center`)、`CC_CENTER_WEB` (前端放在別的地方時用)、
`CLAUDE_CONFIG_DIR`、`SUPABASE_URL`、`SUPABASE_ANON_KEY`。CLI 什麼都不設也能跑:
只看本機、用本機時區。介面則一定要有 Supabase, 因為 note 跟 journal 存在那裡。

### Supabase

第一次要做三件事, 之後就不用再碰:

1. 在 Supabase 後台開一個專案, SQL Editor 貼上 `supabase/schema.sql` 跑一次 ——
   一張 `entries` 表, row level security 讓每個帳號只看得到自己的列。
2. Authentication → Email Templates → **Magic Link** 的樣板加上 `{{ .Token }}`
   (預設只有連結, 沒有驗證碼)。介面登入是「輸入 email → 收信 → 把六位數驗證碼貼回來」,
   不用設 redirect URL, port 換了也沒差。
3. Project Settings → API 的 Project URL 跟 anon key 填進 `.env`:

   ```bash
   SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
   SUPABASE_ANON_KEY=...
   ```

登入態 (refresh token) 存在 `~/.cc-center/auth.json` (mode 0600), 開機不用重登;
Settings → Account 可以登出。Supabase 內建的寄信服務一小時只寄幾封, 驗證碼別連按。

### 開發

前端是 TypeScript 編成原生 ES module, 沒有打包器、沒有框架、執行期零相依。
`web/dist/` 有進版控, 所以拿到 repo 直接跑就有介面, 不用先 `npm install`。

```bash
npm install          # 只有要改介面才需要
npm run build        # web/src -> web/dist
npm run watch        # 邊改邊編
npm run check        # 只做型別檢查
npm test             # markdown 與編輯器文字操作的單元測試 (22 項)
npm run smoke        # 用 jsdom 把整個介面跑一遍, 確認沒有 render 爆掉
npm run verify       # 上面三個一起

python3 -m unittest discover -s tests -t .    # Python 測試 (104 項)
pyright                                        # 型別檢查 (設定在 pyproject.toml)
```

Python 測試全部跑在暫存的 `CC_CENTER_STATE` 與暫存的 `~/.claude` 底下, Supabase 是
`tests/fake_supabase.py` 在本機 port 上假的一個 (真的 urllib、真的 header、真的分頁),
碰不到你真正的資料庫、你的帳號, 也碰不到你寫過的任何一個字。

## 介面是怎麼設計的

一句話: **機器講 mono, 你講 sans。**

工具算出來的東西 —— commit sha、檔案路徑、`+215 −11`、時間、session id ——
一律等寬字; 你寫的東西 —— note、journal、還有你打的那些 prompt —— 一律無襯線、
更大、行距更鬆。這個字體落差就是整個工具的主張: 兩種不同性質的真相, 不該長得一樣。

**顏色只給意義, 不給裝飾。** 整個外殼從頭到尾是灰的, 所以只有三個飽和色:
綠 = 進了 commit, 紅 = 刪掉的行, 琥珀 = 有 agent 在等你。看到顏色就是有事。
量表 (額度、context) 用同樣三個顏色講同一件事: 綠是還有空間, 60% 起轉琥珀, 85% 起轉紅。
Activity 的花費日曆是唯一的漸層 —— 一個綠色、四階、越深越貴, 兩種主題各自對著底色驗過。

**版式是帳簿。** 紀錄欄左邊一條直線, 時間住在欄外的邊界欄裡, 一天可以只靠時間掃下來。

## CLI

```bash
bin/cc-center-all                  # 今天 (本機 + 三台遠端)
bin/cc-center-all --days 7         # 最近七天
bin/cc-center-all --date 2026-09-05
bin/cc-center-all --tokens         # 每個 session 多印 token / 花費
bin/cc-center-all -o ~/daily.md    # 存成檔案

# 每個專案一題一題列: 提問 → 改動 → commit
bin/cc-center --days 7 --by-project

# 交給 Claude 寫成人話
bin/cc-center-all --json | claude -p "根據這份 JSON 幫我寫今天的中文工作日誌"

# 只看本機
bin/cc-center --tz Asia/Taipei
```

## 介面 (常駐監看)

`bin/cc-center-app` 是一個常駐在本機的小 server + 網頁介面, 只用標準函式庫。
掃描走的是同一份 `scanner.py`, 所以介面跟 CLI 的結果一定一樣。

```bash
bin/cc-center-app                 # 背景啟動 + 開瀏覽器 (已經在跑就只開分頁)
bin/cc-center-app status          # 在不在跑
bin/cc-center-app stop | restart
bin/cc-center-app open            # 再開一次分頁
bin/cc-center-app logs -f         # 看 server log
bin/cc-center-app serve           # 前景跑 (debug 用)
bin/cc-center-app install         # 裝成 launchd, 開機自動起來, 掛掉自己重開
bin/cc-center-app uninstall
bin/cc-center-app statusline-install    # 接上 Claude Code 的 statusLine, Agents 分頁才看得到方案額度
bin/cc-center-app statusline-uninstall
bin/cc-center-app journal         # 手動寫 journal (預設昨天, 所有專案、所有機器), 見下面
```

起來以後它就一直待著:

- **本機**每 12 秒看一次 transcript 的 mtime, 有動才重掃 (沒動幾乎不花 CPU)
- **遠端**每 5 分鐘 ssh 收一次 (最近有人在動的那幾台 60 秒一次), 設 0 就只有手動才收
- 瀏覽器那邊用 SSE 接更新, 不用自己按重新整理
- **每天早上**把昨天每個專案的 journal 寫好 (見「journal」那節)

### 「在等你」通知

最有用的一塊: 它會分辨 agent 是**還在做事**還是**停下來等你**, 停下來就跳 macOS 通知。

判斷用兩個訊號交叉: transcript 的尾巴 (對話停在哪) 加上**還有沒有 claude process 活著**
(還有沒有東西能繼續它)。只看 transcript 會把「已經結束的 session」誤報成「在等你」。

| 狀態 | 怎麼判斷 | 介面上 |
|---|---|---|
| 在等你回話 | process 還在, 但 assistant 講完話 (或你按了中斷) 超過 45 秒沒下文 | 橘色卡片, 跳通知 |
| 可能在等你按權限 | process 還在, Edit/Write 這類工具發出去 90 秒還沒有結果 | 橘色卡片, 跳通知 |
| 工具卡很久 | process 還在, 任何工具卡超過 15 分鐘 | 橘色卡片, 跳通知 |
| 跑到一半沒了 | process 不見了, 但 transcript 停在工具中間 = 被砍掉或掛了 | 橘色卡片, 跳通知 |
| 已經結束 | process 不見了, transcript 也正常收尾 | 不吵你 |
| 還在跑 | process 在, 最後一筆是你的提問或工具結果 | 綠色「進行中」 |

這段判斷是 `app/attention.py`, 純函式、沒有 I/O, 所以測得到 —— `tests/test_attention.py`
就是上面這張表。

### 額度跟 context

Agents 分頁最上面有一塊 **Plan**: 訂閱方案的 5 小時、7 天用了幾 %、什麼時候重置。
每個在跑的 session 旁邊也有一條 `ctx 24%` —— 這個 session 的 context window 塞了多少。

context 是從 transcript 算的: 最後一次 API 呼叫送進去的 input (含 cache) 就是現在
window 裡的東西, 跟 Claude Code 自己 statusline 的算法一樣; window 大小從模型名字推
(4.6 之後的 opus / sonnet 跟 5 系列是 1M, haiku 200k), 遠端機器一樣算得出來。

額度就沒辦法了 —— 它不在任何檔案裡, 只有 Claude Code 在跑的時候會把 `rate_limits`
塞給你設定的 statusline 指令。所以要看這塊得先接一條水管:

```bash
bin/cc-center-app statusline-install
```

它把 `~/.claude/settings.json` 的 `statusLine.command` 改成
`bin/cc-center-app statusline -- <你原本的指令>`。鉤子把要的欄位寫進
`~/.cc-center/usage.json`, 再把同一份 JSON 原封不動餵給你原本的指令, 所以原本的
status line 照常。每收到一則回覆就更新一次; 沒接的話 Plan 那塊會告訴你怎麼接。
額度是帳號層級的, 哪台機器報的都一樣 —— 遠端有接的話也會帶回來, 挑最新的用。
`statusline-uninstall` 可以還原。

### 每天花多少

Activity 分頁最上面是 **Spend**: GitHub 貢獻圖那種日曆, 一格一天, 越綠越貴。
它**不看側欄的範圍** —— 有 transcript 的每一天都算, 從最早那一筆到今天。滑過去下面
那行會讀出那天的數字 (幾次呼叫、input / output / cache 各多少 token、多少錢、哪些模型、
哪幾台機器), 再下面的「Every day」表格把每一天都列出來。

錢是**照 API 牌價算的**: 每次回覆的 token 乘上那個模型的 input / output 單價, cache 讀取
算 input 的 1/10, cache 寫入 5 分鐘的 1.25 倍、1 小時的 2 倍 (transcript 的 `usage.cache_creation`
有分)。訂閱制實際上沒付這個數, 但這是唯一能跨天比較的尺。牌價表在 `scanner.py` 的 `PRICES`,
跟 Claude Code 自己算的 `cost-state` 對過, 差不到 1%。

一則回覆有幾個 content block 就寫幾筆 assistant 紀錄, usage 每筆都一樣, 所以 token 一律
按 `requestId` 只算一次 —— `/fork` 或 `--continue` 複製過去的舊紀錄也不會重複算。
遠端機器一樣算, 收集端把每台的每一天加起來。能看多遠取決於 Claude Code 自己留多久的
transcript (`cleanupPeriodDays`, 預設 30 天)。

### process 是怎麼看的

每次收集時, `scanner.py` 會在那台機器上跑 `ps` 撈出**自己這個 uid** 底下
argv[0] 是 `claude` 的 process, 連同它的 cwd (Linux 讀 `/proc/PID/cwd`, macOS 用 `lsof`)。

對應 session 用的是 **cwd**, 不是 session id —— claude 不會一直開著 transcript 的 fd,
對不回去。所以「這個 cwd 有 process」只是必要條件, 不是充分條件。另外兩條規則補起來
(在 `app/monitor.py` 的 `alive_index` / `alive_of`):

- 一個 session 不可能屬於比它**最後一筆還晚出生**的 process
- 一個資料夾有幾顆 process, 最多就只有幾個 session 算活著 —— 同一顆 process 的生命
  週期裡開過的那些 session 是同一個人在做事, 用最後活動時間決定誰是現在那個

幾個看不到的情況, 這時會顯示 `?` 並退回只看 transcript:

- 那台機器收不到 (ssh 掛了, 或遠端開關關著)
- session 是用**別的帳號**跑的 (`ps` 只看得到自己 uid 的)
- session 跑在 **容器裡** —— 容器內的 process 對 host 是另一個 uid, `/proc` 也讀不到

預設**只有遠端機器**會跳通知 —— 本機那個通常就在你眼前, 不用再吵一次;
要連本機也叫就在側邊欄改成「全部」。同一個 session 狀態沒變不會重複叫,
超過 4 小時沒動的也不再提醒。門檻秒數都可以在側邊欄調。

Bash 跑很久是正常的, 所以不會被當成「等你按權限」, 只有真的卡超過 15 分鐘才講一聲。
反過來說, Bash 在等權限這種情況會晚一點才被抓到 —— transcript 分不出「跑很慢」跟
「在等你按 yes」, 只能用工具種類和時間猜。

裝了 `terminal-notifier` (`brew install terminal-notifier`) 的話, 通知點下去會直接開介面。

### 五個分頁

| 分頁 | 內容 |
|---|---|
| Agents | 最上面是「在等你」的 session, 接著是還在動的, 以及每台機器的連線狀態 |
| Projects | **留下來的那一半**: 每個專案的 note 與 journal (存在 Supabase) |
| Sessions | **算出來的那一半**: 這天改了什麼, 以及一題一題的紀錄 (提問 → 改動 → commit) |
| Activity | session/時間/提問/檔案/commit/花費, 加上每天、每專案、每個工具的長條圖 |
| Report | 就是 CLI 那份 Markdown (可以切 by project / by day), 可以複製、存檔、看原始碼 |

Projects 跟 Sessions 的分工就是這個工具的分界線: **Projects 是要留下來的字
(你的 note、寫好給你改的 journal), Sessions 只有工具算的東西。** 兩邊不重複。

每個 session 都可以一鍵複製接續指令 (`claude --resume <id>`, 遠端的會包成
`ssh -t`), 複製 transcript 路徑, 本機的還可以直接在 Finder 顯示。

右上角的**「遠端: 開/關」**是總開關。下班後連不到 lab 的機器就按一下關掉,
它就完全不會去 ssh —— 不會每輪卡 8 秒逾時, 也不會一直噴連不上的錯誤,
本機照樣繼續監看。回到公司再按開, 會立刻收一次。

側邊欄和主畫面中間那條線可以左右拖來調寬度 (雙擊還原成預設), 寬度會記起來。
機器那一欄可以抓左邊的 `⠿` 拖著換順序 (也可以 focus 在上面用 ↑ ↓), 順序直接寫回
`~/.cc-center-hosts`, 所以 `bin/cc-center-all` 收集的順序也跟著變。

側欄只放每天會動的東西: 看哪幾天、問哪幾台。其他設定 (收集頻率、通知、報告、帳號、server)
都在 **Settings** 分頁, 存在 `~/.cc-center/settings.json`, 改了下一輪就生效。
**Account** 那塊顯示登入的是誰、上次跟 Supabase 對過是什麼時候、有沒有還只在這台的字,
可以按 Sync now 或登出。
Projects 分頁預設只列這段時間有改到東西的專案, 勾掉「Only what changed something」就全列。
分頁標題會顯示在等你的數量 (`(2) cc-center`)。

快捷鍵: `R` 重新收集、`/` 跳到 Projects 搜尋、`1`~`6` 切分頁。
編輯框裡: `⌘↵` 存、`Esc` 還原。

只綁 `127.0.0.1`, 而且每個 API 都要帶頁面發下來的 token, 別的網站打不進來。
token 後面還有一道門: 本機 server 沒登入 Supabase 的話, 頁面就只有登入表單, 其他 route
一律 401。預設 port 8787 (被占用會自動往後找)。

## Sessions 分頁: 一題一題對到 commit

一題 = 一則提問, 加上你問完之後 agent 做的所有事, 直到你下一次開口。每一題底下會有:

- **改了** 哪些檔案, 各自 +/- 幾行 (從 transcript 的 structuredPatch 算)
- **跑了** 每一筆指令, 一筆一列, 點開就是原文 (heredoc / inline script 幾行就幾行),
  超過六筆收在「+N more」底下。介面列的是這一題跑過的前 20 筆;
  Markdown 報告那邊仍然只挑值得記的幾筆 (`analysis.dedupe_commands`)
- **commit** —— 短 sha、訊息、動了幾個檔、+/- 幾行, 點 sha 複製完整 sha

sha 是**從那個 repo 的 `git log` 撈回來再對上去的**, 不是從 transcript 猜的 ——
transcript 裡只有 `git commit -m "..."` 那行指令, 沒有 sha, 而且你自己在終端機
commit 的根本不會出現在 transcript 裡。對應分兩輪 (`scanner.link_commits`):

1. 先用 commit 訊息對 (最準)
2. 剩下的看時間: commit 落在「這一題開始」到「這一題最後一個動作 + 4 分鐘」之間,
   而且上界不會吃到下一題

兩輪都對不上的, 會列在最後的**「沒有對應到任何提問的 commit」** —— 那就是你
自己手動做的, 或是 agent 以外的改動。

> 用 heredoc / inline script 寫檔 (`python3 - <<EOF`, `cat > file`) 的改動,
> transcript 裡看不出動到哪個檔, 所以「改了」那行會漏。這種情況以 commit 的
> numstat 為準 —— 這也是把 sha 撈回來的另一個理由。

「還沒進 commit 的改動」是這裡最有用的一塊, 而且只有工具算得出來: agent 這天碰過的
檔案, 減掉這天所有 commit 涵蓋的檔案, 再拿 `git status` 對一次, 就知道哪些改動還懸著、
哪些後來被收掉了。

## Projects 分頁: note 與 journal

note 是你想到什麼記什麼; journal 是每天一份的工作日誌 —— 機器先寫一遍, 你再補。

這些東西存在 **Supabase** (`supabase/schema.sql` 那張 `entries` 表) —— 那是唯一真相,
跟著帳號走, 哪台機器登入都看得到同一份。本機的 SQLite (`~/.cc-center/cc-center.db`)
是**快取**: 讀一律走快取所以頁面快、離線也看得到; 寫一律雲端先, Supabase 收下了才進快取,
連不上就整個失敗、什麼都不留, 不會出現「頁面上有、雲端沒有」的字。

快取跟雲端對的時機: 開機、登入、按 Collect 或 Settings → Sync now, 以及每 5 分鐘一次
(`sync.pull`)。每次都是整份收下來 —— 幾百列你自己的字, 一次讀完比增量同步加刪除標記
好信得多。雲端刪掉的, 這裡也跟著掉; 只有這台有的 (下面說的收進來的舊檔) 推上去,
雲端已經有的不蓋。換帳號登入會把快取整份清掉, 上一個人的字不會留給下一個人。

專案資料夾裡的 markdown 是 note 的**投影**: 存檔時順手寫一份出去, 讓它跟著 git 走、
離開這個工具也看得到。journal **不再**寫進專案資料夾 —— 它是機器每天早上產的,
屬於帳號不屬於 repo, 放進去只會把專案弄亂。

第一次看到某個專案時會把它資料夾裡既有的 `journal/*.md`、`note/*.md` 收進快取
(重跑安全, 已經有的不動), 下一次 sync 推上 Supabase, 之後就只認雲端。以前投影出去的
journal 檔就是這樣搬上去的, 檔案本身留著不動。快取掉了也救得回來 —— 刪掉重開,
下一次 sync 從雲端整份拿回來。這條路徑有測試看著 (`tests/test_cloud.py`、`tests/test_writing.py`)。

| 投影出去的檔 | 單位 | 內容 |
|---|---|---|
| `note/YYYY-MM-DD-HHMM.md` | **一則一個檔**, 不綁日期 | 想到什麼就記一則 —— 卡在哪、下一步、要記得的事 |
| (journal 不投影) | 一個專案一天一份, 只在 Supabase | 這天做了什麼: 一題一題, 為什麼、怎麼做、哪個 commit。Claude 寫, 你補 |

專案清單直接來自每台機器的 `~/.claude/projects` —— **所有專案都會列**, 這個時間範圍
裡沒動過的也在, 只是預設收起來。專案資料夾被你刪掉了也還看得到, 會標「資料夾不在了」。

> 目錄名是編碼過的路徑 (`-Users-you-my-project`), 底線跟斜線都變成 `-`, 還原不回來 ——
> 所以 cwd 是去讀那個目錄裡最新幾份 transcript 的 `cwd` 欄位撈出來的。

### note: 想到就記一則

左邊是這個專案所有 note 的索引 (新的在上), 右邊是編輯器 —— 就是筆記軟體的樣子。
沒有標題欄, 一則 note 的名字就是它的第一行。每寫一則就多一個檔, 同一分鐘內再寫會
自動加號碼 (`-2`, `-3`)。點開舊的可以改、可以刪。

### journal: 機器先寫一遍, 你再補

工具知道「agent 做了什麼」; 「為什麼要這樣做」多半在 agent 回你的那些話裡,
而不在指令裡。所以 journal 是從這份紀錄寫出來的: 這天你在這個專案的每一則提問原文、
agent 每一段回覆、改了哪些檔、進了哪個 commit, 以及值得記的那幾筆指令 —— 排成一份
(`render/journal.py`, `--facts` 印得出來), 餵給 `claude -p`, 請它寫成:

```
1. **03:12** 把 draft 的 transcript 藏起來
   - 為什麼: 工具把自己算成你的工作
   - 怎麼做: claude -p --session-id 固定 id, 結束後 rename 到 drafts/
   - commit `a1b2c3d`

2. **04:05** 修 Draft 按鈕錯誤訊息
   - 怎麼做: 改 projects.ts 的 catch
   - (還沒 commit)
```

固定繁體中文, 檔名指令照原文; 連續幾題做同一件事會合成一題, 「繼續」「好」這種略過;
「為什麼」找不到就省略, 不硬編; sha 只能用紀錄裡有的。判斷跟語氣是它的, 事實是工具的。

**什麼時候寫**: 每天早上 (預設 06:00 之後, Settings → Journal 可改) 自動寫**昨天**的,
每個那天有提問的專案各一份, 本機跟遠端都算。不是「到那一刻跑」而是「時間過了、還沒寫齊
就跑」—— 筆電那時多半在睡覺, 醒來下一輪補, 往回補到三天前。遠端連不上或 claude 出錯的,
半小時後再試 (最多八次)。

**不會蓋掉你的字**: 已經有內容的那天不會再自動寫。你在產出後進編輯器補自己的說明, 存了
就是你的。要重寫得自己下 `--force`, 它會先問。

餵多少是量過的, 不是猜的: 指令佔一天九成以上的字, 拿掉之後寫出來的日誌一樣;
agent 說的話只佔一成, 拿掉之後 22 題只寫得出 2 題。所以指令只留報告那套眼光挑出來的
幾筆 (git / npm / python 這類, 只看看的不記, 內嵌腳本只留呼叫本身), 但寫檔的一律留 ——
`cat > x <<EOF` 改了 x, 這種改動只有指令裡看得到檔名。一天大約 5~10 萬 token,
haiku 一份 $0.1 上下 (Settings 可改模型)。超過 20 萬字會先縮 agent 說的話的中段,
提問跟 commit 永遠完整。指示走 `--system-prompt`、紀錄走 stdin, 不給它任何工具 ——
它只負責寫。

手動寫任何一天:

```bash
bin/cc-center-app journal                       # 昨天, 所有專案、所有機器
bin/cc-center-app journal --date 2026-09-10     # 那一天
bin/cc-center-app journal --days 3              # 從昨天往回三天, 沒寫的補上
bin/cc-center-app journal --project ~/cc_report --host lab2
bin/cc-center-app journal --dry-run             # 印出來, 不存
bin/cc-center-app journal --facts               # 只印餵給 claude 的紀錄, 不問它
bin/cc-center-app journal --force               # 已經有的也重寫 (會蓋掉你補的字, 會先問)
```

不跑常駐的話 `0 6 * * * cd ~/cc_report && bin/cc-center-app journal` 一樣。

> 那次 `claude -p` 自己也會留一份 transcript。它會被搬到 `~/.cc-center/drafts/`,
> 免得工具把自己算成你的工作。

### 存檔

**不自動存。** 打字的時候左下角寫「Unsaved — ⌘↩ or Save now」, 按 `⌘↵` 或右邊的
Save now 才寫出去, `Esc` 還原。有沒存的字的時候關掉分頁會先問你。

編輯器是完整的 markdown: 工具列直接標成語法本身 (`#` `##` `**` `` ` `` `-` `1.`
`- [ ]` `>` `[]()` ` ``` ` `|` `---`), 支援 Write / Split / Preview 三種檢視,
Enter 會接續清單、Tab 縮排, 中文輸入法選字的那個 Enter 不會被當成換行。
Markdown 是自己寫的 renderer (`web/src/ui/editor/markdown.ts`), 不連 CDN,
貼進去的東西一律當文字, 不會變成頁面上的 HTML。

其他:

- **專案在哪台機器, note 的 markdown 就投影到哪台**: 遠端的用 ssh 寫過去
- 投影失敗 (資料夾不見了、遠端連不上) 不影響已經存好的內容, 會回報一聲
- 讀都走本機快取, 所以遠端專案的 note 不用 ssh 讀回來
- 沒登入或 Supabase 連不上: 快取照看, 存檔會直接告訴你失敗, 字還在編輯框裡
- 早上自動寫 journal 的那條, 沒登入就跳過並在 log 講一聲 (寫出來也存不進去, 不白花錢),
  登入後下一輪補; `bin/cc-center-app journal` 一樣要先在介面登入過

## 機器清單

預設沒有 —— 什麼都不設就只收本機。要收遠端, 機器要在 `~/.ssh/config` 有別名、
而且能免密碼登入, 然後三選一:

```bash
echo 'CC_HOSTS="a b c"' >> .env           # repo 根目錄的 .env (見 .env.example)
CC_HOSTS="a b c" bin/cc-center-all        # 環境變數, 優先於 .env
echo "new-host" >> ~/.cc-center-hosts     # 設定檔, 一行一台 (介面也讀同一個檔, 優先於前兩者)
```

介面的 Settings 也能改, 改了就存進 `~/.cc-center-hosts`。

遠端**不用裝任何東西** —— `src/cccenter/scanner.py` 是用 stdin 餵給對面的 `python3`
執行的, 時區也在本機換算成固定位移再送過去, 所以遠端沒有 tzdata 也沒差。
這也是 `scanner.py` 必須維持單一檔案的原因。

## 報告裡有什麼

每天一個區塊, 底下按「專案 × 機器」分組, 再列出每個 session:

- **標題** 取自 Claude Code 自己產的 `ai-title`, 沒有的話退回第一則提問
- **你問了幾次** + 前幾則問題原文 (skill 注入、IDE 開檔提示這類雜訊會濾掉)
- **改了哪些檔案** 以及 +/- 行數 (從 transcript 裡的 structuredPatch 算)
- **跑過的指令**、git commit 訊息、用到的 slash command / subagent / 工具
- **實際使用時間** —— 相鄰兩筆紀錄間隔超過 5 分鐘就當作離開座位, 不是單純看頭尾相減
- 當天所有 **commit** 與產出的 **artifact** 連結彙整在最後

VS Code extension 開的 session 會標 `vscode`, 終端機開的標 `cli`。
跨午夜的 session 會依實際活動時間拆到各天, 不會整段算在開始那天。
`claude -p` / SDK 那種一次性執行預設不算一段工作 (`--oneshot` 可以打開)。

## 常用旗標 (bin/cc-center)

| 旗標 | 作用 |
|---|---|
| `--days N` / `--date YYYY-MM-DD` | 時間範圍 |
| `--since` / `--until` | UTC ISO 視窗 (給遠端用, 免得要有 tzdata) |
| `--tz Asia/Taipei` 或 `--tz +08:00` | 用哪個時區分日 |
| `--json` | 輸出結構化 JSON (可再 `--merge`) |
| `--merge FILE...` | 合併多台的 JSON |
| `--entrypoint claude-vscode` | 只看某個入口, 可重複 |
| `--sidechains` | 連 subagent 的獨立 transcript 也算 |
| `--oneshot` | 連 `claude -p` / SDK 的一次性執行也算 |
| `--prompts N` | 每個 session 最多列幾則提問 (預設 5) |
| `--tokens` | 多印 token 用量與花費 |
| `--by-project` | 按專案排, 一題一題列出 提問 → 改動 → commit |
| `--no-git` | 不去讀 repo 的 git log (commit 就只剩訊息, 沒有 sha) |
| `-o FILE` | 寫到檔案 |

## 每天自動跑

```bash
# crontab -e, 每天 23:30 存一份
30 23 * * * cd ~/cc_report && bin/cc-center-all -o ~/Documents/cc-center/$(date +\%F).md
```
