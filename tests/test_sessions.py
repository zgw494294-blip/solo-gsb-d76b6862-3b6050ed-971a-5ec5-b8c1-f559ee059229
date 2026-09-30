"""设备上报会话 API 测试: 创建/查询/提交, 计数与花费, 错误码及同版本并发。"""

import threading
import unittest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


CREATE = {
    "initial": ["00", "00"],
    "max_partial": 1,
    "full_cost": 10,
    "cell_cost": 2,
}


class TestSessions(unittest.TestCase):
    def setUp(self):
        r = client.post("/sessions", json=CREATE)
        self.assertEqual(r.status_code, 201, r.text)
        self.sid = r.json()["session_id"]

    def _submit(self, payload, sid=None):
        return client.post(f"/sessions/{sid or self.sid}/submit", json=payload)

    def _get(self, sid=None):
        return client.get(f"/sessions/{sid or self.sid}")

    # ---- 创建与查询 --------------------------------------------------------

    def test_create_initial_state(self):
        body = client.post("/sessions", json=CREATE).json()
        self.assertEqual(body["version"], 0)
        self.assertEqual(body["current_frame"], ["00", "00"])
        self.assertEqual(body["counts"], [[0, 0], [0, 0]])
        self.assertEqual(body["total_cost"], 0)
        self.assertEqual(body["max_partial"], 1)
        self.assertEqual(body["full_cost"], 10)
        self.assertEqual(body["cell_cost"], 2)
        self.assertTrue(body["session_id"])

    def test_create_validation_422(self):
        bad = [
            {**CREATE, "initial": []},
            {**CREATE, "initial": ["0"] * 5},
            {**CREATE, "initial": ["0a"]},
            {**CREATE, "max_partial": 4},
            {**CREATE, "max_partial": -1},
            {**CREATE, "max_partial": 1.5},
            {**CREATE, "full_cost": 0},
            {**CREATE, "cell_cost": True},
        ]
        for payload in bad:
            r = client.post("/sessions", json=payload)
            self.assertEqual(r.status_code, 422, payload)

    def test_get_unknown_session_404(self):
        self.assertEqual(self._get("deadbeef").status_code, 404)

    # ---- skip --------------------------------------------------------------

    def test_skip_unchanged(self):
        r = self._submit({"version": 0, "frame": ["00", "00"], "mode": "skip"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["counts"], [[0, 0], [0, 0]])
        self.assertEqual(body["total_cost"], 0)
        self.assertEqual(body["current_frame"], ["00", "00"])

    def test_skip_on_change_422(self):
        r = self._submit({"version": 0, "frame": ["10", "00"], "mode": "skip"})
        self.assertEqual(r.status_code, 422)

    def test_skip_with_rect_422(self):
        payload = {
            "version": 0,
            "frame": ["00", "00"],
            "mode": "skip",
            "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0},
        }
        self.assertEqual(self._submit(payload).status_code, 422)

    def test_full_on_unchanged_422(self):
        r = self._submit({"version": 0, "frame": ["00", "00"], "mode": "full"})
        self.assertEqual(r.status_code, 422)

    # ---- partial -----------------------------------------------------------

    def test_partial_counts_and_cost(self):
        # 变化格 (0,1) 与 (1,0) -> 最小包围矩形覆盖全部 4 格 (含 2 个未变格)
        r = self._submit({"version": 0, "frame": ["01", "10"], "mode": "partial",
                          "rect": {"top": 0, "left": 0, "bottom": 1, "right": 1}})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["current_frame"], ["01", "10"])
        self.assertEqual(body["counts"], [[1, 1], [1, 1]])  # 未变格同样计数
        self.assertEqual(body["total_cost"], 4 * 2)         # 面积 4 * cell_cost 2

    def test_partial_requires_rect(self):
        r = self._submit({"version": 0, "frame": ["01", "10"], "mode": "partial"})
        self.assertEqual(r.status_code, 422)

    def test_partial_rect_must_be_minimal_bbox_422(self):
        # 实际只有 (0,0) 变化, 最小矩形 1x1; 提交 2x2 矩形非法
        r = self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                          "rect": {"top": 0, "left": 0, "bottom": 1, "right": 1}})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self._get().json()["version"], 0)

    def test_partial_rect_smaller_than_bbox_422(self):
        # 变化格 (0,1) 与 (1,0), 只框住一格同样非法
        r = self._submit({"version": 0, "frame": ["01", "10"], "mode": "partial",
                          "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(r.status_code, 422)

    def test_partial_rect_out_of_bounds_422(self):
        r = self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                          "rect": {"top": 0, "left": 0, "bottom": 2, "right": 0}})
        self.assertEqual(r.status_code, 422)

    def test_partial_rect_inverted_422(self):
        r = self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                          "rect": {"top": 1, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(r.status_code, 422)

    def test_partial_cap_blocks_and_full_resets(self):
        # max_partial=1: 第一次局刷后矩形内格全部达上限
        r1 = self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                           "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["total_cost"], 2)
        # 再次局刷同一格 -> 超上限 422, 状态不变
        r2 = self._submit({"version": 1, "frame": ["00", "00"], "mode": "partial",
                           "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(r2.status_code, 422)
        self.assertEqual(self._get().json()["total_cost"], 2)
        # 全刷: 清零计数, 花费 full_cost
        r3 = self._submit({"version": 1, "frame": ["00", "00"], "mode": "full"})
        self.assertEqual(r3.status_code, 200, r3.text)
        body = r3.json()
        self.assertEqual(body["counts"], [[0, 0], [0, 0]])
        self.assertEqual(body["total_cost"], 12)  # 2 + 10
        self.assertEqual(body["version"], 2)

    def test_max_partial_zero_makes_partial_illegal(self):
        sid = client.post("/sessions", json={**CREATE, "max_partial": 0}).json()["session_id"]
        r = client.post(f"/sessions/{sid}/submit",
                        json={"version": 0, "frame": ["10", "00"], "mode": "partial",
                              "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(r.status_code, 422)

    # ---- full --------------------------------------------------------------

    def test_full_clears_counts_and_charges(self):
        # 先局刷产生计数
        self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                      "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        r = self._submit({"version": 1, "frame": ["00", "00"], "mode": "full"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["counts"], [[0, 0], [0, 0]])
        self.assertEqual(body["total_cost"], 2 + 10)
        self.assertEqual(body["current_frame"], ["00", "00"])

    # ---- 版本与并发 ---------------------------------------------------------

    def test_version_conflict_409_and_state_unchanged(self):
        # 先用版本 0 成功一次 -> 版本变 1
        ok = self._submit({"version": 0, "frame": ["10", "00"], "mode": "partial",
                           "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}})
        self.assertEqual(ok.status_code, 200)
        # 再拿旧版本 0 提交 -> 409
        r = self._submit({"version": 0, "frame": ["00", "00"], "mode": "full"})
        self.assertEqual(r.status_code, 409)
        body = self._get().json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["current_frame"], ["10", "00"])
        self.assertEqual(body["total_cost"], 2)

    def test_submit_unknown_session_404(self):
        r = client.post("/sessions/nope/submit",
                        json={"version": 0, "frame": ["00", "00"], "mode": "skip"})
        self.assertEqual(r.status_code, 404)

    def test_concurrent_same_version_at_most_one_success(self):
        payload = {"version": 0, "frame": ["10", "00"], "mode": "partial",
                   "rect": {"top": 0, "left": 0, "bottom": 0, "right": 0}}
        results = []
        barrier = threading.Barrier(5)

        def fire():
            barrier.wait()
            results.append(self._submit(payload))

        threads = [threading.Thread(target=fire) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        statuses = sorted(r.status_code for r in results)
        self.assertEqual(statuses.count(200), 1, statuses)
        self.assertEqual(statuses.count(409), 4, statuses)
        body = self._get().json()
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["total_cost"], 2)
        self.assertEqual(body["counts"], [[1, 0], [0, 0]])

    # ---- 其余非法指令 ------------------------------------------------------

    def test_frame_dimension_mismatch_422(self):
        r = self._submit({"version": 0, "frame": ["000", "000"], "mode": "full"})
        self.assertEqual(r.status_code, 422)

    def test_bad_frame_structure_422(self):
        for frame in ([], ["0"] * 5, ["00000"], ["0a", "00"], ["0", "00"]):
            r = self._submit({"version": 0, "frame": frame, "mode": "full"})
            self.assertEqual(r.status_code, 422, frame)

    def test_bad_mode_and_version_422(self):
        self.assertEqual(
            self._submit({"version": 0, "frame": ["10", "00"], "mode": "wipe"}).status_code, 422
        )
        for bad_version in ("0", True, 1.0, -1):
            r = self._submit({"version": bad_version, "frame": ["00", "00"], "mode": "skip"})
            self.assertEqual(r.status_code, 422, bad_version)

    def test_failed_submission_keeps_state(self):
        before = self._get().json()
        # 非法模式 + 旧版本混合的多次失败提交
        for payload in (
            {"version": 0, "frame": ["10", "00"], "mode": "skip"},
            {"version": 0, "frame": ["10", "00"], "mode": "partial",
             "rect": {"top": 0, "left": 0, "bottom": 1, "right": 1}},
            {"version": 9, "frame": ["10", "00"], "mode": "full"},
        ):
            r = self._submit(payload)
            self.assertIn(r.status_code, (409, 422))
        after = self._get().json()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
