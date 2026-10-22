"""Тесты collect.py: дневные снимки подписчиков (followers.daily).

Запуск: python -m unittest test_collect
"""

import unittest
from datetime import datetime, timezone

import collect


def stats_with(*logins):
    return {"streamers": {login: collect.blank_stats() for login in logins}}


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class DailyFollowersTest(unittest.TestCase):
    def test_writes_snapshot_of_the_day(self):
        stats = stats_with("a")
        collect.update_followers(stats, ["a"], {"a": 100}, utc("2026-10-22T10:00:00"))
        self.assertEqual(stats["streamers"]["a"]["followers"]["daily"], {"2026-10-22": 100})

    def test_same_day_is_not_overwritten(self):
        stats = stats_with("a")
        collect.update_followers(stats, ["a"], {"a": 100}, utc("2026-10-22T10:00:00"))
        collect.update_followers(stats, ["a"], {"a": 105}, utc("2026-10-22T18:00:00"))
        f = stats["streamers"]["a"]["followers"]
        self.assertEqual(f["daily"], {"2026-10-22": 100})
        self.assertEqual(f["count"], 105)

    def test_day_changes_at_minsk_midnight(self):
        stats = stats_with("a")
        # 20:59 UTC — 23:59 по Мінску, 21:00 UTC — уже следующие сутки.
        collect.update_followers(stats, ["a"], {"a": 100}, utc("2026-10-22T20:59:00"))
        collect.update_followers(stats, ["a"], {"a": 101}, utc("2026-10-22T21:00:00"))
        self.assertEqual(
            stats["streamers"]["a"]["followers"]["daily"],
            {"2026-10-22": 100, "2026-10-23": 101},
        )

    def test_failed_request_writes_no_day(self):
        stats = stats_with("a", "b")
        collect.update_followers(stats, ["a", "b"], None, utc("2026-10-22T10:00:00"))
        # Twitch не отдал b — и его день не пишется.
        collect.update_followers(stats, ["a", "b"], {"a": 100}, utc("2026-10-22T11:00:00"))
        self.assertEqual(stats["streamers"]["a"]["followers"]["daily"], {"2026-10-22": 100})
        self.assertEqual(stats["streamers"]["b"]["followers"]["daily"], {})

    def test_record_without_daily_gets_it(self):
        stats = stats_with("a")
        del stats["streamers"]["a"]["followers"]["daily"]
        ts = utc("2026-10-22T10:00:00")
        collect.update_followers(stats, ["a"], {"a": 100}, ts)
        collect.prune_stats(stats["streamers"]["a"], ts)
        self.assertEqual(stats["streamers"]["a"]["followers"]["daily"], {"2026-10-22": 100})

    def test_prune_keeps_31_days(self):
        ss = collect.blank_stats()
        ss["followers"]["daily"] = {
            "2026-09-20": 1,
            "2026-09-21": 2,  # 31 день назад — удаляется
            "2026-09-22": 3,  # 30 дней назад — остаётся: это база прироста
            "2026-10-21": 4,
            "2026-10-22": 5,
        }
        # 23:30 по Мінску 22.10 — сутки ещё 22-го.
        collect.prune_stats(ss, utc("2026-10-22T20:30:00"))
        self.assertEqual(
            ss["followers"]["daily"], {"2026-09-22": 3, "2026-10-21": 4, "2026-10-22": 5}
        )

    def test_monthly_snapshots_untouched(self):
        stats = stats_with("a")
        f = stats["streamers"]["a"]["followers"]
        f["snapshots"] = {"2026-10-01": 90, "2026-09-01": 80}
        ts = utc("2026-10-22T10:00:00")
        collect.update_followers(stats, ["a"], {"a": 100}, ts)
        collect.prune_stats(stats["streamers"]["a"], ts)
        self.assertEqual(f["snapshots"], {"2026-10-01": 90, "2026-09-01": 80})


if __name__ == "__main__":
    unittest.main()
