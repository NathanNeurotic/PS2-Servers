"""The SESSIONS tab reads the SESSION lines every server prints (#249 item 5).

SessionLog is pure Python and is what most of this exercises. The Tk half
skips itself where there is no display, like the other GUI tests.

Run:  python -m unittest tests.test_session_view -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from launcher import session_stats, session_view  # noqa: E402

LINE = ("SESSION udpfs client=192.168.1.20 seconds=184 connections=1 list=2 open=4 "
        "read=913 bytes=14925824 write=0 close=3 errors=0 ended=idle")


class SessionLogTests(unittest.TestCase):
    def test_session_lines_become_rows_and_the_rest_is_ignored(self):
        log = session_view.SessionLog(clock=lambda: 1000.0)
        added = log.add("udpfs", "[udpfs] listening on 0.0.0.0:18194\n" + LINE + "\n"
                        "some other line\n")
        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertEqual(row["service"], "udpfs")
        self.assertEqual(row["client"], "192.168.1.20")
        self.assertEqual(row["read"], 913)
        self.assertEqual(row["bytes"], 14925824)
        self.assertEqual(row["ended"], "idle")
        self.assertEqual(row["source"], "udpfs")
        self.assertEqual(row["at"], 1000.0)
        self.assertEqual(log.rows(), added)

    def test_rows_from_every_service_share_one_table_newest_first(self):
        log = session_view.SessionLog()
        stats = session_stats.SessionStats("smbv3", emit=lambda line: log.add("smbv3", line))
        stats.record("10.0.0.5", "read", 3)
        stats.stop()
        log.add("udpbd", "SESSION udpbd client=10.0.0.6 seconds=1 connections=1 list=0 "
                         "open=0 read=5 bytes=2560 write=0 close=0 errors=0 ended=stopped")
        services = [row["service"] for row in log.rows()]
        self.assertEqual(services, ["udpbd", "smbv3"])

    def test_the_table_is_bounded(self):
        log = session_view.SessionLog(max_rows=3)
        for i in range(5):
            log.add("http", LINE.replace("seconds=184", "seconds=%d" % i))
        self.assertEqual([row["seconds"] for row in log.rows()], [4, 3, 2])
        log.clear()
        self.assertEqual(len(log), 0)

    def test_a_chunk_with_several_lines_keeps_their_order(self):
        log = session_view.SessionLog()
        log.add("http", LINE.replace("seconds=184", "seconds=1") + "\n"
                + LINE.replace("seconds=184", "seconds=2") + "\n")
        self.assertEqual([row["seconds"] for row in log.rows()], [2, 1])

    def test_bytes_are_readable(self):
        self.assertEqual(session_view.format_bytes(512), "512")
        self.assertEqual(session_view.format_bytes(14925824), "14.2 MB")
        self.assertEqual(session_view.format_bytes(41943040), "40.0 MB")
        self.assertEqual(session_view.format_bytes("x"), "x")

    def test_row_values_follow_the_columns(self):
        log = session_view.SessionLog(clock=lambda: 0.0)
        row = log.add("udpfs", LINE)[0]
        values = session_view.row_values(row)
        keys = [key for key, _h, _w in session_view.COLUMNS]
        self.assertEqual(len(values), len(keys))
        self.assertEqual(values[keys.index("client")], "192.168.1.20")
        self.assertEqual(values[keys.index("bytes")], "14.2 MB")
        self.assertEqual(values[keys.index("errors")], "0")


class SessionPanelTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:
            raise unittest.SkipTest("no tkinter")
        try:
            self.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_feed_fills_the_table_and_clear_empties_it(self):
        panel = session_view.SessionPanel(self.root)
        self.assertEqual(panel.hint.get(), session_view.EMPTY_HINT)
        panel.feed("udpfs", "noise\n" + LINE + "\n")
        panel.feed("udpfs", "more noise\n")
        items = panel.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertIn("192.168.1.20", panel.tree.item(items[0], "values"))
        self.assertIn("1 session", panel.hint.get())
        panel.clear()
        self.assertEqual(panel.tree.get_children(), ())
        self.assertEqual(panel.hint.get(), session_view.EMPTY_HINT)

    def test_the_widget_is_bounded_like_the_log(self):
        panel = session_view.SessionPanel(self.root, log=session_view.SessionLog(max_rows=2))
        for i in range(4):
            panel.feed("http", LINE.replace("seconds=184", "seconds=%d" % i))
        values = [panel.tree.item(i, "values")[3] for i in panel.tree.get_children()]
        self.assertEqual(values, ["3", "2"])


if __name__ == "__main__":
    unittest.main()
