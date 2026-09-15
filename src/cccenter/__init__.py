"""
cc-center — what Claude Code did today, and what you have to say about it.

    scanner   read the transcripts and the git log; facts only, no opinions
    analysis  what those facts add up to for a project on a day
    render    Markdown for a person to read
    entries   the journal/note Markdown that lands in the project folder
    store     SQLite: the truth for the parts you wrote yourself
    cli       the command line
    app       the local web interface and the watcher behind it

Standard library only, all the way down. Nothing here reaches the network except
`app`, which talks to your own machines over ssh, and to Claude on your PATH.
"""

__version__ = "2.0.0"