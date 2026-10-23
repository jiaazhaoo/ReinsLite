"""Per-provider caps (2026-10-23): one provider's own daily / weekly limit across every batch, held money included,
next to the batch, stage and pool caps. Also: thinking tokens that only show in total_tokens are billed as output."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from reins import batches, gateway, settings_edit, spend
from reins.store import ReinsError, connect, now, tx


class ProviderCaps(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("REINS_HOME", "REINS_SESSION")}
        self.tmp = Path(tempfile.mkdtemp(prefix="reins-provcap-"))
        os.environ["REINS_HOME"] = str(self.tmp)
        os.environ["REINS_SESSION"] = "owner"
        (self.tmp / "config.toml").write_text("[pool]\ndaily_cap = 500.0\nweekly_cap = 2000.0\n\n"
                                              "[providers.gemini]\ndaily_cap = 5.0\nweekly_cap = 8.0\n")
        self.con = connect(self.tmp)
        (self.tmp / "cases.txt").write_text("101\n")
        self.n = 0

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def running(self, cap=100.0):
        self.n += 1
        b = batches.open_(self.con, project="p", scope=f"x-wp{self.n}", type_="experiment", purpose="caps",
                          case_file=self.tmp / "cases.txt", stages=[batches.parse_stage_spec("s:paid")], spend_cap=cap)
        batches.stage_start(self.con, b, "s")
        return b

    def spent(self, b, provider, amount, days_ago=0):
        import datetime as dt
        at = (dt.datetime.now() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds") if days_ago else now()
        with tx(self.con):
            self.con.execute("INSERT INTO spend (at, batch_id, stage, provider, model, amount, priced) VALUES (?,?, 's', ?, 'm', ?, 'table')",
                             (at, b, provider, amount))

    def test_daily_cap_counts_every_batch_and_held_money(self):
        a, b = self.running(), self.running()
        self.spent(a, "gemini", 3.0)
        r = spend.reserve(self.con, b, "s", 1.5, provider="gemini")        # 3 spent + 1.5 held = 4.5 of 5
        with self.assertRaises(spend.ProviderCapReached) as e:
            spend.reserve(self.con, a, "s", 1.0, provider="gemini")         # 4.5 + 1 > 5, though each batch is far from its cap
        self.assertIn("provider gemini", str(e.exception))
        self.assertIn("reins config set providers.gemini.daily_cap", str(e.exception))
        spend.reserve(self.con, a, "s", 1.0, provider="openrouter")        # another provider is not limited by gemini's cap
        spend.release(self.con, r)
        spend.reserve(self.con, a, "s", 1.0, provider="gemini")            # the hold given back: 3 + 1 fits

    def test_weekly_cap_spans_earlier_days(self):
        a = self.running()
        self.spent(a, "gemini", 4.0, days_ago=3)
        self.spent(a, "gemini", 3.5)
        with self.assertRaises(spend.ProviderCapReached) as e:              # today 3.5 + 1 fits 5; week 7.5 + 1 > 8
            spend.reserve(self.con, a, "s", 1.0, provider="gemini")
        self.assertIn("weekly cap", str(e.exception))
        self.spent(a, "gemini", 50.0, days_ago=9)                          # outside the window
        spend.reserve(self.con, a, "s", 0.4, provider="gemini")

    def test_config_set_adds_a_provider_cap_and_refuses_nonsense(self):
        settings_edit.set_(self.con, "providers.openai.daily_cap", "2", "try openai on two dollars a day")
        self.assertEqual(spend.provider_limits("openai")[1], {"daily_cap": 2.0})
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "providers.nosuch.daily_cap", "2", "typo")
        with self.assertRaises(ReinsError):
            settings_edit.set_(self.con, "providers.openai.weekly_cap", "lots", "not a number")
        a = self.running()
        self.spent(a, "openai", 1.8)
        with self.assertRaises(spend.ProviderCapReached):
            spend.reserve(self.con, a, "s", 0.5, provider="openai")
        card = next(c for c in spend.cost_view(self.con) if c["provider"] == "gemini")
        self.assertEqual(card["limits"], {"daily_cap": 5.0, "weekly_cap": 8.0})

    def test_gemini_preset_and_cap_message(self):
        from reins import providers
        g = providers.get("gemini")
        self.assertEqual((g["kind"], g["env"]), ("chat", "GEMINI"))
        self.assertEqual(gateway.route("/p/gemini/v1/chat/completions")[1],
                         "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        msg = gateway.cap_message("B", spend.ProviderCapReached("provider gemini: ..."))
        self.assertIn("reins ctl resume B", msg)
        self.assertNotIn("batch approve", msg)
        self.assertIn("batch approve", gateway.cap_message("B", spend.CapReached("B: batch cap")))

    def test_thinking_tokens_are_billed_as_output(self):
        spend.set_price(self.con, "gemini-2.5-flash", 0.30, 2.50, "test")
        u = {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 413}   # Gemini: 396 thinking tokens only in total
        self.assertEqual(gateway.tokens_out(u), 399)
        amt, src = gateway.cost_of(self.con, "gemini-2.5-flash", u)
        self.assertAlmostEqual(amt, 14 * 0.30 / 1e6 + 399 * 2.50 / 1e6)
        self.assertEqual(src, "table")
        self.assertEqual(gateway.tokens_out({"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}), 7)
        self.assertEqual(gateway.tokens_out({"completion_tokens": 7}), 7)


if __name__ == "__main__":
    unittest.main()
