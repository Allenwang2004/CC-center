"""
Markdown for people to read --- and one page of facts for Claude.

Three shapes of the same record:

    daily     — one heading per day, then each session under it
    projects  — one heading per project, then each day, question by question
    journal   — one day of one project, complete, for `claude -p` to write from

The renderers only read what `scanner` and `analysis` computed. Nothing here
touches the filesystem or the network, so a report is reproducible: same
sessions in, same words out.
"""

from __future__ import annotations

from .daily import render
from .journal import render_journal_facts
from .projects import render_projects, turn_lines

__all__ = ["render", "render_journal_facts", "render_projects", "turn_lines"]
