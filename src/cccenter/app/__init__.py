"""
The local interface: a page on 127.0.0.1, and a watcher that keeps it true.

    paths      where the code, the page and this machine's state live
    settings   what you chose, and which machines to ask
    util       now / parse_iso / shell_quote
    attention  is this session waiting for you?
    desktop    notifications and opening a browser
    bus        the push channel to every open tab
    writing    saving journal entries and notes (and drafting one with Claude)
    usage      the plan's 5h / 7d limits, caught from Claude Code's status line
    monitor    the watch loop: scan, compare, push
    server     HTTP: one page, a few JSON routes, one event stream
    daemon     start / stop / status of the background process
    service    start on login (launchd, systemd)
    main       the command line that ties those together

Standard library only. Nothing listens outside 127.0.0.1; remote machines are
reached outwards over ssh, never inwards.
"""
