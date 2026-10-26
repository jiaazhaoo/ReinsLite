"""reins report push writes to Cloudflare KV only when the production data changed (its timestamp aside), or when the
last push is over an hour old -- every KV write counts against the 1,000/day free tier and the timer runs every 5 min."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reins import production, report


class PushSkipsUnchanged(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = {"kv_id": "kv1", "url": "https://x"}
        self.view = {"at": "2026-10-26T09:00:00", "packages": [{"scope": "a-wp1", "end": "2026-10-26T09:00:00", "n": 1}]}
        self.writes: list[str] = []
        self.now = 1_000_000.0
        patches = [
            mock.patch.object(report, "_cf", return_value={"hide": [], "lang": "en"}),
            mock.patch.object(report, "_state", side_effect=lambda: dict(self.state)),
            mock.patch.object(report, "_save", side_effect=lambda st: self.state.update(st)),
            mock.patch.object(report, "_dir", return_value=self.tmp),
            mock.patch.object(report, "build", return_value={}),
            mock.patch.object(report, "connect", return_value=mock.MagicMock()),
            mock.patch.object(report, "_wrangler", side_effect=lambda *a, **k: self.writes.append(a[3]) or ""),
            mock.patch.object(production, "view", side_effect=lambda con: self.view),
            mock.patch("time.time", side_effect=lambda: self.now),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tick(self, seconds: float, at: str):
        self.now += seconds
        self.view = {**self.view, "at": at,
                     "packages": [{**p, "end": at} for p in self.view["packages"]]}

    def test_first_push_writes_both_keys(self):
        r = report.push()
        self.assertNotIn("skipped", r)
        self.assertEqual(self.writes, ["production", "xlsx"])

    def test_only_timestamp_changed_is_skipped(self):
        report.push()
        self.tick(300, "2026-10-26T09:05:00")
        r = report.push()
        self.assertEqual(r.get("skipped"), "unchanged")
        self.assertEqual(len(self.writes), 2)

    def test_data_change_is_pushed(self):
        report.push()
        self.tick(report.PUSH_MIN_SECONDS, "2026-10-26T09:10:00")
        self.view["packages"][0]["n"] = 2
        r = report.push()
        self.assertNotIn("skipped", r)
        self.assertEqual(len(self.writes), 4)

    def test_data_change_within_10_minutes_waits(self):
        report.push()
        self.tick(300, "2026-10-26T09:05:00")
        self.view["packages"][0]["n"] = 2
        self.assertEqual(report.push().get("skipped"), "too soon")
        self.tick(300, "2026-10-26T09:10:00")
        self.assertNotIn("skipped", report.push())
        self.assertEqual(len(self.writes), 4)

    def test_a_busy_day_data_changing_every_tick_stays_under_30_percent(self):
        for i in range(288):
            self.tick(300, f"2026-10-26T{(i * 5) // 60 % 24:02d}:{(i * 5) % 60:02d}:00")
            self.view["packages"][0]["n"] = i
            report.push()
        self.assertLessEqual(len(self.writes), 290)  # was 576 (57% of the 1,000/day free tier)

    def test_unchanged_still_refreshed_hourly(self):
        report.push()
        self.tick(report.PUSH_REFRESH_SECONDS + 1, "2026-10-26T10:00:01")
        self.assertNotIn("skipped", report.push())
        self.assertEqual(len(self.writes), 4)

    def test_force_pushes_unchanged(self):
        report.push()
        self.tick(300, "2026-10-26T09:05:00")
        self.assertNotIn("skipped", report.push(force=True))
        self.assertEqual(len(self.writes), 4)

    def test_a_day_of_5_minute_ticks_stays_far_below_the_free_tier(self):
        for i in range(288):
            self.tick(300, f"2026-10-26T{(i * 5) // 60 % 24:02d}:{(i * 5) % 60:02d}:00")
            report.push()
        self.assertLessEqual(len(self.writes), 2 * 25)  # hourly refreshes only, vs 576 before


if __name__ == "__main__":
    unittest.main()
