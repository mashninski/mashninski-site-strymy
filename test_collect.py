"""Тесты collect.py: дневные снимки подписчиков (followers.daily), каапы (общий чат).

Запуск: python -m unittest test_collect
"""

import copy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


class FakeTwitch:
    """Ответы shared_chat/session по user_id; None — нет сессии, Exception — сбой запроса."""

    def __init__(self, sessions, users=None):
        self.sessions = sessions
        self.users = users or {}
        self.calls = []
        self.user_calls = []

    def shared_chat(self, uid):
        self.calls.append(uid)
        r = self.sessions.get(uid)
        if isinstance(r, Exception):
            raise r
        return r

    def users_by_id(self, ids):
        self.user_calls.append(list(ids))
        return {i: self.users[i] for i in ids if i in self.users}


def coop_world():
    """Трое наших: a (id 1), b (id 2), c (id 3)."""
    state = {"streamers": {
        "a": {"user_id": "1", "display_name": "A"},
        "b": {"user_id": "2", "display_name": "B"},
        "c": {"user_id": "3", "display_name": "C"},
    }}
    return {"streamers": {}}, state, ["a", "b", "c"]


def session(sid, *ids, created="2026-10-23T10:00:00.744001862Z"):
    return {"session_id": sid, "host_broadcaster_id": ids[0], "created_at": created,
            "updated_at": "2026-10-23T10:05:00Z",
            "participants": [{"broadcaster_id": i} for i in ids]}


def live(*pairs):
    return {uid: {"user_id": uid, "game_name": game} for uid, game in pairs}


class CoopsTest(unittest.TestCase):
    def test_session_starts(self):
        stats, state, logins = coop_world()
        s = session("S", "1", "2")
        tw = FakeTwitch({"1": s, "2": s})
        collect.update_coops(tw, stats, state, logins, live(("1", "Dota 2"), ("2", "Dota 2")),
                             utc("2026-10-23T10:10:00"))
        c = stats["coops"]["S"]
        self.assertEqual(c["started_at"], "2026-10-23T10:00:00Z")
        self.assertIsNone(c["ended_at"])
        self.assertEqual(c["host_id"], "1")
        self.assertEqual([p["login"] for p in c["participants"]], ["a", "b"])
        self.assertEqual(c["games"], ["Dota 2"])
        # Один запрос на сессию: b уже найден в сессии a.
        self.assertEqual(tw.calls, ["1"])
        self.assertNotIn("updated_at", c)

    def test_session_goes_on_without_changes(self):
        stats, state, logins = coop_world()
        s = session("S", "1", "2")
        tw = FakeTwitch({"1": s, "2": s})
        streams = live(("1", "Dota 2"), ("2", "Dota 2"))
        collect.update_coops(tw, stats, state, logins, streams, utc("2026-10-23T10:10:00"))
        before = copy.deepcopy(stats)
        collect.update_coops(tw, stats, state, logins, streams, utc("2026-10-23T10:20:00"))
        self.assertEqual(stats, before)

    def test_new_game_and_participant_join(self):
        stats, state, logins = coop_world()
        collect.update_coops(FakeTwitch({"1": session("S", "1")}), stats, state, logins,
                             live(("1", "Dota 2")), utc("2026-10-23T10:10:00"))
        s = session("S", "1", "2")
        collect.update_coops(FakeTwitch({"1": s, "2": s}), stats, state, logins,
                             live(("1", "Dota 2"), ("2", "Minecraft")), utc("2026-10-23T10:20:00"))
        c = stats["coops"]["S"]
        self.assertEqual([p["id"] for p in c["participants"]], ["1", "2"])
        self.assertEqual(c["games"], ["Dota 2", "Minecraft"])

    def test_session_ends_when_nobody_returns_it(self):
        stats, state, logins = coop_world()
        streams = live(("1", "Dota 2"), ("2", "Dota 2"))
        collect.update_coops(FakeTwitch({"1": session("S", "1", "2")}), stats, state, logins,
                             streams, utc("2026-10-23T10:10:00"))
        # Оба ещё в эфире, но общего чата больше нет.
        collect.update_coops(FakeTwitch({}), stats, state, logins, streams,
                             utc("2026-10-23T11:00:00"))
        self.assertEqual(stats["coops"]["S"]["ended_at"], "2026-10-23T11:00:00Z")

    def test_session_ends_when_ours_go_offline(self):
        stats, state, logins = coop_world()
        collect.update_coops(FakeTwitch({"1": session("S", "1", "2")}), stats, state, logins,
                             live(("1", "Dota 2"), ("2", "Dota 2")), utc("2026-10-23T10:10:00"))
        tw = FakeTwitch({})
        collect.update_coops(tw, stats, state, logins, {}, utc("2026-10-23T11:00:00"))
        self.assertEqual(stats["coops"]["S"]["ended_at"], "2026-10-23T11:00:00Z")
        self.assertEqual(tw.calls, [])

    def test_failed_request_keeps_session_open(self):
        stats, state, logins = coop_world()
        streams = live(("1", "Dota 2"), ("2", "Dota 2"))
        collect.update_coops(FakeTwitch({"1": session("S", "1", "2")}), stats, state, logins,
                             streams, utc("2026-10-23T10:10:00"))
        collect.update_coops(FakeTwitch({"1": RuntimeError("500"), "2": None}), stats, state,
                             logins, streams, utc("2026-10-23T10:20:00"))
        self.assertIsNone(stats["coops"]["S"]["ended_at"])

    def test_foreign_participant_one_get_users(self):
        stats, state, logins = coop_world()
        tw = FakeTwitch({"1": session("S", "1", "900", "901")}, users={
            "900": {"id": "900", "login": "Stranger", "display_name": "Stranger"},
            "901": {"id": "901", "login": "other", "display_name": "Other"},
        })
        collect.update_coops(tw, stats, state, logins, live(("1", "Dota 2")),
                             utc("2026-10-23T10:10:00"))
        parts = stats["coops"]["S"]["participants"]
        self.assertEqual([(p["id"], p["login"], p["name"]) for p in parts],
                         [("1", "a", "A"), ("900", "stranger", "Stranger"), ("901", "other", "Other")])
        self.assertEqual(tw.user_calls, [["900", "901"]])
        # Игры чужих не знаем — только наши.
        self.assertEqual(stats["coops"]["S"]["games"], ["Dota 2"])
        # Второй прогон чужих уже знает — Get Users не нужен.
        collect.update_coops(tw, stats, state, logins, live(("1", "Dota 2")),
                             utc("2026-10-23T10:20:00"))
        self.assertEqual(tw.user_calls, [["900", "901"]])

    def test_foreign_without_login_is_filled_later(self):
        stats, state, logins = coop_world()
        s = session("S", "1", "900")
        collect.update_coops(FakeTwitch({"1": s}), stats, state, logins, live(("1", "X")),
                             utc("2026-10-23T10:10:00"))
        self.assertIsNone(stats["coops"]["S"]["participants"][1]["login"])
        tw = FakeTwitch({"1": s}, users={"900": {"id": "900", "login": "z", "display_name": "Z"}})
        collect.update_coops(tw, stats, state, logins, live(("1", "X")), utc("2026-10-23T10:20:00"))
        self.assertEqual(stats["coops"]["S"]["participants"][1]["login"], "z")

    def test_prune_after_31_days(self):
        stats, state, logins = coop_world()
        stats["coops"] = {
            "old": {"host_id": "1", "started_at": "2026-09-20T10:00:00Z",
                    "ended_at": "2026-09-20T12:00:00Z", "participants": [], "games": []},
            "new": {"host_id": "1", "started_at": "2026-10-20T10:00:00Z",
                    "ended_at": "2026-10-20T12:00:00Z", "participants": [], "games": []},
        }
        collect.update_coops(FakeTwitch({}), stats, state, logins, {}, utc("2026-10-23T10:00:00"))
        self.assertEqual(list(stats["coops"]), ["new"])

    def test_archive_keeps_coops_over_old_version(self):
        stats, state, logins = coop_world()
        stats["collecting_since"] = "2026-09-23"
        stats["streamers"] = {l: collect.blank_stats() for l in logins}
        stats["coops"] = {"S2": {"host_id": "1", "started_at": "2026-10-22T10:00:00Z",
                                 "ended_at": None, "participants": [], "games": []}}
        registry = [{"login": l} for l in logins]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            collect.dump_json(d / "2026-10.json", {"month": "2026-10", "coops": {
                "S1": {"host_id": "2", "started_at": "2026-10-01T10:00:00Z",
                       "ended_at": "2026-10-01T12:00:00Z", "participants": [], "games": []}}})
            collect.update_archive(stats, state, registry, logins, utc("2026-10-23T10:00:00"), {}, d)
            october = collect.load_json(d / "2026-10.json", {})
            september = collect.load_json(d / "2026-09.json", {})
        self.assertEqual(sorted(october["coops"]), ["S1", "S2"])
        # Месяц без каапов ключа не получает.
        self.assertNotIn("coops", september)


if __name__ == "__main__":
    unittest.main()
