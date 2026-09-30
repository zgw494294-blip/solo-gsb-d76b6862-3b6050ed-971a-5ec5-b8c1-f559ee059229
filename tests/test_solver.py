"""求解器单元测试: 与指数级暴力枚举对照, 验证 DP 最优性与裁决规则。"""

import itertools
import random
import unittest

from app.solver import solve_plan


def brute_force(initial, frames, max_partial, full_cost, cell_cost):
    """直接枚举所有合法模式序列, 返回 (最小花费, 最优模式序列)。"""
    h, w = len(initial), len(initial[0])
    grids = [initial] + frames
    best = None  # (cost, modes)

    def rec(i, counts, cost, modes):
        nonlocal best
        if i == len(grids):
            cand = (cost, tuple(modes))
            if best is None or cand < best:
                best = cand
            return
        prev, cur = grids[i - 1], grids[i]
        diff = [(r, c) for r in range(h) for c in range(w) if prev[r][c] != cur[r][c]]
        if not diff:
            rec(i + 1, counts, cost, modes + ["skip"])
            return
        # full
        rec(i + 1, (0,) * (h * w), cost + full_cost, modes + ["full"])
        # partial
        top = min(r for r, _ in diff)
        bottom = max(r for r, _ in diff)
        left = min(c for _, c in diff)
        right = max(c for _, c in diff)
        cells = [r * w + c for r in range(top, bottom + 1) for c in range(left, right + 1)]
        if all(counts[k] < max_partial for k in cells):
            nc = list(counts)
            for k in cells:
                nc[k] += 1
            area = (bottom - top + 1) * (right - left + 1)
            rec(i + 1, tuple(nc), cost + area * cell_cost, modes + ["partial"])

    rec(1, (0,) * (h * w), 0, [])
    return best


def rand_grid(h, w, rng):
    return ["".join(rng.choice("01") for _ in range(w)) for _ in range(h)]


class TestSolver(unittest.TestCase):
    def test_skip_only(self):
        res = solve_plan(["01", "10"], [["01", "10"], ["01", "10"]], 2, 5, 1)
        self.assertEqual(res["total_cost"], 0)
        self.assertEqual([s["mode"] for s in res["plan"]], ["skip", "skip"])
        self.assertTrue(all(s["rect"] is None for s in res["plan"]))

    def test_tie_prefers_full(self):
        # 1x1 单帧: full 花费 1 == partial 面积 1 * 1, 同价应选 full
        res = solve_plan(["0"], [["1"]], 3, 1, 1)
        self.assertEqual(res["total_cost"], 1)
        self.assertEqual(res["plan"][0]["mode"], "full")
        self.assertIsNone(res["plan"][0]["rect"])

    def test_partial_rect_closed_interval(self):
        # 变化格 (0,1) 与 (2,0) -> 包围矩形 (0,0)-(2,1)
        res = solve_plan(
            ["000", "000", "000"],
            [["010", "000", "100"]],
            3,
            100,
            2,
        )
        step = res["plan"][0]
        self.assertEqual(step["mode"], "partial")
        self.assertEqual(step["rect"], {"top": 0, "left": 0, "bottom": 2, "right": 1})
        self.assertEqual(res["total_cost"], 6 * 2)

    def test_max_partial_zero_forces_full(self):
        res = solve_plan(["0"], [["1"], ["0"]], 0, 7, 1)
        self.assertEqual([s["mode"] for s in res["plan"]], ["full", "full"])
        self.assertEqual(res["total_cost"], 14)

    def test_partial_cap_blocks_third_partial(self):
        # max_partial=1: 连续两帧同一格变化, 不能两次 partial;
        # (partial, full) 与 (full, partial) 同为 101, 同价按模式序列裁决 full 优先
        res = solve_plan(["0"], [["1"], ["0"]], 1, 100, 1)
        self.assertEqual([s["mode"] for s in res["plan"]], ["full", "partial"])
        self.assertEqual(res["total_cost"], 101)

    def test_unchanged_cells_in_rect_count_too(self):
        # 2x1: 帧1 仅 (0,0) 变 -> 矩形 1x1; 帧2 仅 (1,0) 变 -> 矩形 1x1;
        # 帧3 (0,0)(1,0) 都变 -> 矩形 2x1 含两格, max_partial=1 时 (0,0) 已达上限只能 full
        res = solve_plan(
            ["00"],
            [["10"], ["11"], ["00"]],
            1,
            100,
            1,
        )
        self.assertEqual([s["mode"] for s in res["plan"]], ["partial", "partial", "full"])

    def test_against_brute_force_random(self):
        rng = random.Random(20260929)
        for _ in range(300):
            h = rng.randint(1, 3)
            w = rng.randint(1, 3)
            n = rng.randint(1, 6)
            initial = rand_grid(h, w, rng)
            frames = [rand_grid(h, w, rng) for _ in range(n)]
            max_partial = rng.randint(0, 3)
            full_cost = rng.randint(1, 9)
            cell_cost = rng.randint(1, 9)
            res = solve_plan(initial, frames, max_partial, full_cost, cell_cost)
            exp_cost, exp_modes = brute_force(initial, frames, max_partial, full_cost, cell_cost)
            self.assertEqual(res["total_cost"], exp_cost, (initial, frames, max_partial, full_cost, cell_cost))
            self.assertEqual(tuple(s["mode"] for s in res["plan"]), exp_modes)
            # 校验返回的矩形确实等于变化格包围盒
            prev = initial
            for cur, step in zip(frames, res["plan"]):
                if step["mode"] == "partial":
                    diff = [(r, c) for r in range(h) for c in range(w) if prev[r][c] != cur[r][c]]
                    self.assertEqual(
                        step["rect"],
                        {
                            "top": min(r for r, _ in diff),
                            "left": min(c for _, c in diff),
                            "bottom": max(r for r, _ in diff),
                            "right": max(c for _, c in diff),
                        },
                    )
                else:
                    self.assertIsNone(step["rect"])
                prev = cur


if __name__ == "__main__":
    unittest.main()
