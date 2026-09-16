"""
Stopping a session is the one thing the tool does to a session rather than
about it, so the tests are about never hitting the wrong process: the right
pid by folder and age, nothing sent when it is ambiguous, ssh for a remote
machine, and the menu-bar list agreeing with the Agents tab.
"""

from __future__ import annotations

import signal
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

from tests.support import Sandbox

from cccenter.app import control
from cccenter.app.settings import DEFAULT_SETTINGS


def iso(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def sess(host="box", cwd="/p/a", sid="s1", last=10, state="waiting", entrypoint="cli"):
    return {"host": host, "cwd": cwd, "session_id": sid, "entrypoint": entrypoint,
            "title": "t", "first_prompt": "hello", "days": {},
            "tail": {"state": state, "at": iso(last), "since": iso(last)}}


def proc(pid, cwd="/p/a", started_ago=3600):
    import time
    return {"pid": pid, "cwd": cwd, "started": time.time() - started_ago}


class Pids(Sandbox):

    def test_the_process_in_the_same_folder(self):
        self.assertEqual(control.pids_for(sess(), [proc(11), proc(12, cwd="/p/b")]), [11])

    def test_a_process_born_after_the_last_line_is_not_this_session(self):
        # session 最後一筆是 10 秒前, 一顆 process 5 秒前才起來 → 不是它
        self.assertEqual(control.pids_for(sess(last=10), [proc(11, started_ago=5),
                                                          proc(12, started_ago=600)]), [12])

    def test_unknown_machine_gives_nothing(self):
        self.assertEqual(control.pids_for(sess(), None), [])


class Signalling(Sandbox):

    def test_local_interrupt_sends_sigint_to_that_pid(self):
        sent = []
        out = control.signal_session(sess(), [proc(11)], "INT", "box",
                                     kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(11, signal.SIGINT)])
        self.assertEqual(out, {"pid": 11, "signal": "INT", "host": "box"})

    def test_end_is_sigterm(self):
        sent = []
        control.signal_session(sess(), [proc(11)], "TERM", "box",
                               kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(11, signal.SIGTERM)])

    def test_two_processes_in_one_folder_means_nothing_is_sent(self):
        sent = []
        with self.assertRaises(ValueError) as cm:
            control.signal_session(sess(), [proc(11), proc(12)], "INT", "box",
                                   kill=lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [])
        self.assertIn("kill -INT 11 12", str(cm.exception))

    def test_no_process_is_a_clear_message(self):
        with self.assertRaises(ValueError) as cm:
            control.signal_session(sess(), [], "INT", "box", kill=lambda *a: None)
        self.assertIn("ended already", str(cm.exception))

    def test_a_remote_session_is_signalled_over_ssh(self):
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        out = control.signal_session(sess(host="lab"), [proc(41)], "TERM", "box", run=run,
                                     kill=lambda *a: self.fail("must not kill locally"))
        self.assertEqual(calls[0][0], "ssh")
        self.assertIn("lab", calls[0])
        self.assertEqual(calls[0][-1], "kill -TERM 41")
        self.assertEqual(out["host"], "lab")

    def test_only_int_and_term(self):
        with self.assertRaises(ValueError):
            control.signal_session(sess(), [proc(11)], "KILL", "box", kill=lambda *a: None)


class Live(Sandbox):

    def test_waiting_and_running_are_split_the_way_the_page_does_it(self):
        st = dict(DEFAULT_SETTINGS)
        waiting = sess(sid="w", last=120, state="waiting")          # 停下來 2 分鐘, process 在
        running = sess(sid="r", last=5, state="tool")                # 剛剛還在跑工具
        finished = sess(sid="f", last=30, state="waiting")           # process 沒了
        old = sess(sid="o", last=20000, state="tool")                # 超過 attention_max, 不算了
        alive = {"w": True, "r": True, "f": False, "o": True}
        out = control.live([waiting, running, finished, old], st,
                           lambda s: alive[s["session_id"]])
        self.assertEqual([x["session_id"] for x in out["waiting"]], ["w"])
        self.assertEqual(out["waiting"][0]["label"], "Waiting for your reply")
        self.assertEqual([x["session_id"] for x in out["running"]], ["r"])
        self.assertEqual(out["running"][0]["name"], "a")


if __name__ == "__main__":
    unittest.main()
