"""离线电子纸刷新规划求解器。

规则:
- 各格局刷次数初始为 0。
- 无变化帧只能跳过 (skip), 花费 0, 局刷次数不变。
- 变化帧可以:
  - 全刷 (full): 花费 full_cost, 所有格局刷次数清零。
  - 局刷 (partial): 刷变化格的最小轴对齐包围矩形, 花费 面积 * cell_cost;
    矩形内每格 (含未变格) 局刷次数加一, 且任何格次数不得超过 max_partial。
- 目标: 总花费最低; 同价时按模式序列字典序裁决, full 优先于 partial
  (skip 位置由输入唯一确定, 不参与选择)。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

Grid = Sequence[Sequence[str]]
# 某帧之后的各格局刷次数状态, 按行展开为一维元组
State = Tuple[int, ...]
# DP 记录: (累计花费, 模式序列); 元组比较即实现 "先比花费, 同价比模式序列"
# ('full' < 'partial' < 'skip' 按字典序天然成立)
Entry = Tuple[int, Tuple[str, ...]]


def _changed_box(prev: Grid, cur: Grid, height: int, width: int) -> Optional[Tuple[int, int, int, int]]:
    """返回变化格的最小轴对齐包围矩形 (top, left, bottom, right), 零基闭区间; 无变化返回 None。"""
    rows = [r for r in range(height) for c in range(width) if prev[r][c] != cur[r][c]]
    if not rows:
        return None
    cols = [c for r in range(height) for c in range(width) if prev[r][c] != cur[r][c]]
    return min(rows), min(cols), max(rows), max(cols)


def solve_plan(
    initial: Grid,
    frames: Sequence[Grid],
    max_partial: int,
    full_cost: int,
    cell_cost: int,
) -> dict:
    height = len(initial)
    width = len(initial[0])
    grids: List[Grid] = [initial, *frames]

    zero_state: State = (0,) * (height * width)
    dp: Dict[State, Entry] = {zero_state: (0, ())}

    prev = grids[0]
    for cur in grids[1:]:
        box = _changed_box(prev, cur, height, width)
        next_dp: Dict[State, Entry] = {}
        if box is None:
            # 无变化帧只能跳过
            for state, (cost, modes) in dp.items():
                cand: Entry = (cost, modes + ("skip",))
                if state not in next_dp or cand < next_dp[state]:
                    next_dp[state] = cand
        else:
            top, left, bottom, right = box
            area = (bottom - top + 1) * (right - left + 1)
            partial_cost = area * cell_cost
            rect_cells = [r * width + c for r in range(top, bottom + 1) for c in range(left, right + 1)]
            for state, (cost, modes) in dp.items():
                # 全刷: 花费 full_cost, 各格局刷次数清零
                cand = (cost + full_cost, modes + ("full",))
                if zero_state not in next_dp or cand < next_dp[zero_state]:
                    next_dp[zero_state] = cand
                # 局刷: 矩形内每格次数加一, 不得超过 max_partial
                if all(state[i] < max_partial for i in rect_cells):
                    new_state = list(state)
                    for i in rect_cells:
                        new_state[i] += 1
                    new_state_t = tuple(new_state)
                    cand = (cost + partial_cost, modes + ("partial",))
                    if new_state_t not in next_dp or cand < next_dp[new_state_t]:
                        next_dp[new_state_t] = cand
        dp = next_dp
        prev = cur

    best_cost, best_modes = min(dp.values())

    # 依据模式序列重建每帧方案 (局刷矩形由相邻帧唯一确定)
    plan = []
    prev = grids[0]
    for cur, mode in zip(grids[1:], best_modes):
        rect = None
        if mode == "partial":
            top, left, bottom, right = _changed_box(prev, cur, height, width)  # type: ignore[misc]
            rect = {"top": top, "left": left, "bottom": bottom, "right": right}
        plan.append({"mode": mode, "rect": rect})
        prev = cur

    return {"total_cost": best_cost, "plan": plan}
