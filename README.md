# cc-center

[繁體中文](README.zh-TW.md)

A menu bar app and CLI that keeps track of your Claude Code sessions across every
machine you work on: which ones are running, which ones have stopped and are
waiting for you, and what actually got done today. Alongside that, the half a
tool cannot compute: your own notes, and a daily journal Claude writes for you
every morning from the full record of the day.

Everything is read from the transcripts Claude Code already keeps
(`~/.claude/projects/*/*.jsonl`). Nothing needs to be installed on the machines
being watched, and nothing runs in the cloud except the store for your notes and
journal entries.

## Features

- **Waiting on you.** Tells a session that is still working apart from one that
  has stopped and needs an answer, a permission, or has died mid-tool, and sends
  a desktop notification. The menu bar shows the counts (waiting, working) and
  lists the sessions; a click opens the window on that one.
- **Interrupt or end a session.** From the Agents tab: Interrupt is Ctrl+C
  (this turn stops, the conversation stays), End exits the claude process
  (resumable later). Works over ssh for sessions on other machines.
- **Every machine.** Remote machines are collected over ssh with the same
  scanner, fed to the remote `python3` over stdin. Nothing to install there.
- **What changed, question by question.** Each prompt is matched to the files it
  touched, the commands it ran, and the commit it landed in (looked up in the
  repo's `git log`, not guessed from the transcript). Uncommitted changes are
  listed too.
- **Cost and context.** Per-day spend at API list prices, a spend calendar, the
  context window each live session has used, and your plan's 5-hour and 7-day
  limits from Claude Code's status line.
- **Notes and a daily journal.** Notes are yours, one file per thought. The
  journal is written for you every morning by `claude -p` from the day's full
  record, and it explains how the day's work was built: one section per
  mechanism (a pipeline, an integration, a fix), every term explained the
  first time, the steps in the order the system runs them, each tied to the
  file or command that does it, and how to check it really works that way.
  You add your own words on top. Both are stored in Supabase under your
  account, cached locally, and the page works offline for reading.
- **Your CLAUDE.md files.** The global `~/.claude/CLAUDE.md` on every machine and
  each project's `.claude/CLAUDE.md`, edited in place where Claude Code reads
  them; a project without one gets a Create button.
- **Reports.** The same facts as Markdown or JSON, by day or by project, from
  the command line, for scripts and cron.

## Install

### Mac app

Download `cc-center_<version>_aarch64.dmg` from Releases, drag `cc-center.app`
to Applications and open it. The app lives in the menu bar: click the icon for
the window, close the window and it keeps watching.

The build is not yet signed or notarized. macOS will refuse to open it the first
time; allow it under System Settings, Privacy & Security ("Open Anyway"), or
clear the quarantine flag:

```bash
xattr -dr com.apple.quarantine /Applications/cc-center.app
```

Requires Apple Silicon and macOS 26 for now (see Roadmap). Claude Code itself
should be installed, so that the journal can be written.

### From source

The simplest way to run it on your own machine, and the way to get the Mac app
without a signed download: build it yourself. An app you built is not
quarantined, so macOS opens it without complaint.

You need Xcode Command Line Tools (`xcode-select --install`), `python3`, and
for the Mac app also [Rust](https://rustup.rs) and Node. Claude Code itself
should be installed so the journal can be written.

```bash
git clone https://github.com/Allenwang2004/cc-center.git
cd cc-center
cp .env.example .env        # already points at a working Supabase project; add machines, time zone if you like

# the page in a browser (python3 only, nothing to build)
bin/cc-center-app

# or the menu bar app
cd desktop
npm install
npm run build               # first time compiles Tauri, about five minutes
npm run install-app         # into /Applications
```

To update later: `git pull`, then `npm run build && npm run install-app` again.
`pip install -e .` puts `cc-center` and `cc-center-app` on your PATH if you
prefer; it is not required.

### Supabase

Notes and journal entries live in Supabase, under an account you sign in to
with your email. `.env.example` already points at the project this repository
uses, so nothing needs to be set up to try it: sign in, and the rows are yours
(row level security keeps every account to its own). The anon key in there is
meant to be public; the tables are protected by row level security, not by the
key. The Mac app has the project baked in at build time.

To run your own project instead, three steps once:

1. Create a project and run `supabase/schema.sql` in the SQL Editor. It creates
   one table, `entries`, and one private storage bucket, `cc-images`, for the
   pictures in a journal entry, with row level security so every account only
   sees its own rows and its own folder. Safe to run again after an update.
2. Sign-in is by email and a code. Supabase only lets you edit email templates
   with a custom SMTP server configured (Project Settings, Authentication, SMTP
   Settings), so set one up, then add `{{ .Token }}` to both the **Confirm
   signup** and **Magic Link** templates.
3. Put your project's URL and anon key in `.env` in place of the defaults.

## Using it

**Sign in.** The page asks for an email, sends a code, and remembers you
afterwards (the session is stored in `~/.cc-center/auth.json`).

**Add machines.** Each remote machine needs an alias in `~/.ssh/config` with
key-based login. Add them in the sidebar, or set `CC_HOSTS` in `.env`. The
"Remote" switch turns all ssh off, for when the lab machines are unreachable.

**Plan usage.** To see your plan's 5-hour and 7-day limits on the Agents tab,
hook Claude Code's status line once:

```bash
bin/cc-center-app statusline-install
```

Your existing status line command keeps running; the hook only copies the
limits out on the way through.

**Journal.** Every morning after 06:00 (Settings, Journal) the previous day's
entry is written for each project you asked something in, unless you already
wrote on that day. By hand:

```bash
bin/cc-center-app journal                      # yesterday, every project and machine
bin/cc-center-app journal --date 2026-09-10    # a given day
bin/cc-center-app journal --dry-run            # print, do not save
```

**Reports.**

```bash
bin/cc-center-all                    # today, this machine plus every host in the list
bin/cc-center-all --days 7 -o ~/week.md
bin/cc-center --days 7 --by-project  # one project at a time: question, changes, commit
bin/cc-center-all --json | claude -p "Write today's work log from this JSON"
```

Keyboard: `R` collects now, `/` jumps to search, `1` to `6` switch tabs; in an
editor `Cmd+Enter` saves and `Esc` reverts. Nothing is saved on a timer.

## Configuration

Personal settings live in `.env` at the repository root (never committed); a
variable already set in the environment always wins. Everything else is set on
the page's Settings tab and stored in `~/.cc-center/settings.json`.

| Variable | Meaning |
|---|---|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Where notes and journal entries are stored. Required for the page. |
| `CC_HOSTS` | Remote machines, space separated (`~/.cc-center-hosts` takes precedence once written). |
| `CC_TZ` | Time zone for splitting days, IANA name or offset. Defaults to the machine's. |
| `CC_BROWSER` | Browser to open the page in (macOS app name). |
| `CC_SSH_TIMEOUT` | Seconds before an ssh attempt is given up. Default 8. |
| `CC_CENTER_PORT` | Port for the local server. Default 8787, only ever bound to 127.0.0.1. |
| `CC_CENTER_STATE` | Where the database, settings and log live. Default `~/.cc-center`. |
| `CLAUDE_CONFIG_DIR` | Claude Code's config directory, if not `~/.claude`. |

## How it works

One rule runs through the whole thing: whatever can be rebuilt by scanning the
transcripts again is computed and never stored; only the words you write go to
a database.

```
transcripts (local + ssh) --> scanner --> analysis --> page / report     (computed)
notes, journal ---------------> Supabase --> local SQLite cache --> page   (written)
```

- The local server (`src/cccenter/app/`) is plain Python, binds `127.0.0.1`
  only, and every API call carries a token the page was served with. The
  browser never talks to Supabase; the server does, on its behalf.
- Writes go to Supabase first and reach the local cache only once the cloud has
  them. The cache is refreshed in full every few minutes and on every sign-in,
  so a note written on one machine shows up on the others.
- The Mac app (`desktop/`) is a Tauri shell around the same server, frozen with
  PyInstaller and carried as a sidecar. The page inside the window is the same
  `web/` as the browser sees; it talks to the sidecar through the shell.

## Development

```bash
npm install && npm run build       # web/src (TypeScript) -> web/dist, committed
npm run vendor                     # highlight.js, KaTeX, Excalidraw -> web/vendor, committed; only after a bump
npm run verify                     # type check, unit tests, and a jsdom smoke run of the page
python3 -m unittest discover -s tests -t .   # Python tests; Supabase is a local fake
pyright

cd desktop
npm install
npm run build                      # cc-center.app and a .dmg (needs Rust and python3)
npm run install-app                # swap it into /Applications
```

```
bin/            cc-center (report CLI), cc-center-app (server), cc-center-all (local + remote)
src/cccenter/   scanner, analysis, render, entries, cloud, sync, store, cli, app/
web/            index.html, style.css, src/ (TypeScript), dist/ (built, committed),
                vendor/ (highlight.js, KaTeX, Excalidraw, bundled by vendor.mjs, committed)
desktop/        Tauri shell (src-tauri/), sidecar and bundle scripts
supabase/       schema.sql
tests/          Python tests, fake Supabase
```

## Roadmap

- Sign and notarize the Mac app; automatic updates
- Build the sidecar against python.org's Python for older macOS and Intel Macs
- Native notifications from the app
- Move the server to Rust one module at a time; the page does not change

## Contributing

Issues and pull requests are welcome. Keep `src/cccenter/scanner.py` a single
file with no imports from the package: it is the one file that runs on remote
machines, fed over stdin, and it has to stand on its own. Run `npm run verify`
and the Python tests before opening a pull request.

## Team

- [Allenwang2004](https://github.com/Allenwang2004)

## License

[MIT](LICENSE)
