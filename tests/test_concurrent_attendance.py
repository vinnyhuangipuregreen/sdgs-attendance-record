import copy
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from api.storage import AttendanceStore, ConcurrentUpdate


class ConcurrentAttendanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DATABASE_PATH": str(Path(self.temp.name) / "test.db"),
            "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": "", "VERCEL": ""})
        self.env.start()
        self.store = AttendanceStore()
        self.seed = {"admins": [], "students": [{"id": i, "name": f"Student {i}"} for i in range(1, 51)],
                     "courses": [{"id": 1, "title": "Test"}], "attendances": {}}
        self.store.initialize(self.seed)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_fifty_same_snapshot_checkins_and_leaves_all_persist(self):
        original = self.store.load()[1]
        barrier = Barrier(50)

        def submit(i):
            data = copy.deepcopy(original)
            data["attendances"][f"{i}_1"] = {"status": "SIGNED_IN" if i % 2 else "ON_LEAVE", "reason": str(i)}
            barrier.wait()
            self.store.save_records(original, data)

        with ThreadPoolExecutor(max_workers=50) as pool:
            list(pool.map(submit, range(1, 51)))
        saved = self.store.load()[1]
        self.assertEqual(len(saved["attendances"]), 50)
        for i in range(1, 51):
            self.assertEqual(saved["attendances"][f"{i}_1"]["status"], "SIGNED_IN" if i % 2 else "ON_LEAVE")

    def test_same_record_race_and_stale_admin_cannot_overwrite(self):
        version, original = self.store.load()
        first, second = copy.deepcopy(original), copy.deepcopy(original)
        first["attendances"]["1_1"] = {"status": "SIGNED_IN", "signed_at": "original time"}
        second["attendances"]["1_1"] = {"status": "ON_LEAVE", "reason": "conflict"}
        self.store.save_records(original, first)
        with self.assertRaises(ConcurrentUpdate):
            self.store.save_records(original, second)
        with self.assertRaises(ConcurrentUpdate):
            self.store.save_records(original, first)
        with self.assertRaises(ConcurrentUpdate):
            self.store.save(version, original)
        self.assertEqual(self.store.load()[1]["attendances"]["1_1"], first["attendances"]["1_1"])

    def test_course_change_and_student_removal_reject_stale_action(self):
        for collection in ("courses", "students"):
            with self.subTest(collection=collection):
                version, original = self.store.load()
                changed, action = copy.deepcopy(original), copy.deepcopy(original)
                changed[collection] = []
                action["attendances"]["1_1"] = {"status": "SIGNED_IN"}
                self.store.save(version, changed)
                with self.assertRaises(ConcurrentUpdate):
                    self.store.save_records(original, action)
                self.assertEqual(self.store.load()[1], changed)
                self.store.save(self.store.load()[0], original)

    def profile(self, i):
        return {"id": i, "name": f"Name {i}", "email": f"student{i}@test.invalid", "account_type": "student"}

    def test_new_student_links_allocate_unique_ids_and_preserve_records(self):
        barrier = Barrier(20)
        def link(i):
            barrier.wait()
            self.store.sync_student(self.profile(i))
        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(link, range(1, 21)))
        data = self.store.load()[1]
        self.assertEqual(len(data["students"]), 70)
        self.assertEqual(len({s["id"] for s in data["students"]}), 70)
        self.assertEqual(len({s["platform_key"] for s in data["students"] if "platform_key" in s}), 20)
        self.store.sync_student(self.profile(1))
        self.assertEqual(len(self.store.load()[1]["students"]), 70)

    def test_legacy_link_reuses_id_and_preserves_old_attendance(self):
        version, data = self.store.load()
        data["students"][0]["email"] = "STUDENT9@test.invalid"
        data["attendances"]["1_1"] = {"status": "SIGNED_IN"}
        self.store.save(version, data)
        _, linked = self.store.sync_student(self.profile(9))
        self.assertEqual(linked["students"][0]["platform_key"], "student:9")
        self.assertEqual(linked["students"][0]["id"], 1)
        self.assertEqual(linked["attendances"], data["attendances"])

    def test_year_isolation_and_unrelated_profile_update(self):
        self.store.initialize_year(2)
        _, original = self.store.load()
        self.store.sync_student(self.profile(100))
        action = copy.deepcopy(original)
        action["attendances"]["1_1"] = {"status": "ON_LEAVE", "reason": "test"}
        self.store.save_records(original, action)
        self.assertEqual(self.store.load(2)[1]["attendances"], {})
        self.store.sync_student(self.profile(100), 2)
        self.assertEqual(len(self.store.load(2)[1]["students"]), 1)


if __name__ == "__main__":
    unittest.main()
