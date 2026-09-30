"""离线电子纸刷新规划 HTTP API (FastAPI)。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.solver import _changed_box, solve_plan

app = FastAPI(title="E-Paper Refresh Planner", version="1.0.0")


def _validate_grid(grid: List[str], name: str) -> List[str]:
    if not 1 <= len(grid) <= 4:
        raise ValueError(f"{name}: 网格高度须为 1~4 行")
    width = len(grid[0])
    if not 1 <= width <= 4:
        raise ValueError(f"{name}: 网格宽度须为 1~4 列")
    for row in grid:
        if len(row) != width:
            raise ValueError(f"{name}: 各行宽度必须一致")
        if any(ch not in "01" for ch in row):
            raise ValueError(f"{name}: 网格只能包含字符 '0' 和 '1'")
    return grid


class PlanRequest(BaseModel):
    initial: List[str] = Field(description="初始画面, 0/1 字符串行网格")
    frames: List[List[str]] = Field(description="1~20 帧目标画面")
    max_partial: int = Field(strict=True, ge=0, le=3, description="每格局刷次数上限 0~3")
    full_cost: int = Field(strict=True, gt=0, description="全刷花费, 正整数")
    cell_cost: int = Field(strict=True, gt=0, description="局刷单格花费, 正整数")

    @field_validator("initial")
    @classmethod
    def _check_initial(cls, grid: List[str]) -> List[str]:
        return _validate_grid(grid, "initial")

    @field_validator("frames")
    @classmethod
    def _check_frames(cls, frames: List[List[str]]) -> List[List[str]]:
        if not 1 <= len(frames) <= 20:
            raise ValueError("frames: 目标帧数须为 1~20")
        for i, grid in enumerate(frames):
            _validate_grid(grid, f"frames[{i}]")
        return frames

    @model_validator(mode="after")
    def _check_same_dimensions(self) -> "PlanRequest":
        height, width = len(self.initial), len(self.initial[0])
        for i, grid in enumerate(self.frames):
            if len(grid) != height or any(len(row) != width for row in grid):
                raise ValueError(f"frames[{i}]: 所有画面尺寸必须与 initial 一致 ({height}x{width})")
        return self


class Rect(BaseModel):
    top: int
    left: int
    bottom: int
    right: int


class Step(BaseModel):
    mode: str = Field(description="skip / full / partial")
    rect: Optional[Rect] = Field(default=None, description="局刷矩形 (top,left,bottom,right) 零基闭区间, 仅 partial 时给出")


class PlanResponse(BaseModel):
    total_cost: int
    plan: List[Step]


@app.get("/")
def root() -> dict:
    return {
        "service": "e-paper-refresh-planner",
        "plan_endpoint": "POST /plan",
        "session_endpoints": ["POST /sessions", "GET /sessions/{id}", "POST /sessions/{id}/submit"],
        "docs": "/docs",
    }


@app.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> dict:
    return solve_plan(
        initial=req.initial,
        frames=req.frames,
        max_partial=req.max_partial,
        full_cost=req.full_cost,
        cell_cost=req.cell_cost,
    )


# ---------------------------------------------------------------------------
# 设备逐帧上报会话 (有状态)
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    initial: List[str] = Field(description="初始画面, 0/1 字符串行网格")
    max_partial: int = Field(strict=True, ge=0, le=3, description="每格局刷次数上限 0~3")
    full_cost: int = Field(strict=True, gt=0, description="全刷花费, 正整数")
    cell_cost: int = Field(strict=True, gt=0, description="局刷单格花费, 正整数")

    @field_validator("initial")
    @classmethod
    def _check_initial(cls, grid: List[str]) -> List[str]:
        return _validate_grid(grid, "initial")


class RectIn(BaseModel):
    top: int = Field(strict=True, ge=0)
    left: int = Field(strict=True, ge=0)
    bottom: int = Field(strict=True, ge=0)
    right: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> "RectIn":
        if self.top > self.bottom or self.left > self.right:
            raise ValueError("rect: top 不得大于 bottom, left 不得大于 right")
        return self


class CommandRequest(BaseModel):
    version: int = Field(strict=True, ge=0, description="客户端持有的会话版本")
    frame: List[str] = Field(description="目标画面, 0/1 字符串行网格")
    mode: Literal["skip", "full", "partial"] = Field(description="上报的刷新指令")
    rect: Optional[RectIn] = Field(default=None, description="局刷矩形, 仅 partial 时必须给出")

    @field_validator("frame")
    @classmethod
    def _check_frame(cls, grid: List[str]) -> List[str]:
        return _validate_grid(grid, "frame")

    @model_validator(mode="after")
    def _check_rect_by_mode(self) -> "CommandRequest":
        if self.mode == "partial" and self.rect is None:
            raise ValueError("partial 模式必须提供 rect")
        if self.mode != "partial" and self.rect is not None:
            raise ValueError("仅 partial 模式可以携带 rect")
        return self


class SessionDetail(BaseModel):
    session_id: str
    version: int = Field(description="会话版本, 初始为 0, 每次成功提交加一")
    current_frame: List[str] = Field(description="当前画面")
    counts: List[List[int]] = Field(description="各格局刷次数, 与画面同尺寸")
    total_cost: int = Field(description="累计花费")
    max_partial: int
    full_cost: int
    cell_cost: int


class _Session:
    """会话的可变状态; 所有读写都须持有自身的锁。"""

    def __init__(self, req: SessionCreateRequest) -> None:
        self.lock = asyncio.Lock()
        self.height = len(req.initial)
        self.width = len(req.initial[0])
        self.frame = list(req.initial)
        self.counts = [[0] * self.width for _ in range(self.height)]
        self.version = 0
        self.total_cost = 0
        self.max_partial = req.max_partial
        self.full_cost = req.full_cost
        self.cell_cost = req.cell_cost

    def snapshot(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "version": self.version,
            "current_frame": self.frame,
            "counts": self.counts,
            "total_cost": self.total_cost,
            "max_partial": self.max_partial,
            "full_cost": self.full_cost,
            "cell_cost": self.cell_cost,
        }


_SESSIONS: Dict[str, _Session] = {}


def _get_session(session_id: str) -> _Session:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.post("/sessions", response_model=SessionDetail, status_code=201)
async def create_session(req: SessionCreateRequest) -> dict:
    session_id = uuid.uuid4().hex
    session = _Session(req)
    _SESSIONS[session_id] = session
    return session.snapshot(session_id)


@app.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> dict:
    session = _get_session(session_id)
    async with session.lock:
        return session.snapshot(session_id)


@app.post("/sessions/{session_id}/submit", response_model=SessionDetail)
async def submit_command(session_id: str, cmd: CommandRequest) -> dict:
    session = _get_session(session_id)
    async with session.lock:
        # 乐观并发: 同版本并发提交至多成功一次
        if cmd.version != session.version:
            raise HTTPException(
                status_code=409,
                detail=f"version conflict: expected {session.version}, got {cmd.version}",
            )
        # 尺寸须与创建会话时的画面一致
        if len(cmd.frame) != session.height or any(len(row) != session.width for row in cmd.frame):
            raise HTTPException(
                status_code=422,
                detail=f"frame: 尺寸须与 initial 一致 ({session.height}x{session.width})",
            )

        box = _changed_box(session.frame, cmd.frame, session.height, session.width)
        changed = box is not None

        # 无变化只能 skip; 变化只能 full / partial
        if not changed and cmd.mode != "skip":
            raise HTTPException(status_code=422, detail="画面无变化时只能 skip")
        if changed and cmd.mode == "skip":
            raise HTTPException(status_code=422, detail="画面发生变化时必须 full 或 partial")

        if cmd.mode == "skip":
            new_counts = session.counts
            added_cost = 0
        elif cmd.mode == "full":
            # 全刷清零全部计数
            new_counts = [[0] * session.width for _ in range(session.height)]
            added_cost = session.full_cost
        else:
            # mode == "partial": 此处 box 必非 None 且 rect 由模型校验保证存在
            top, left, bottom, right = box  # type: ignore[misc]
            rect = cmd.rect
            assert rect is not None
            # 矩形不得越界
            if rect.bottom >= session.height or rect.right >= session.width:
                raise HTTPException(status_code=422, detail="rect 超出画面范围")
            # 矩形必须恰好是变化格的最小包围矩形
            if (rect.top, rect.left, rect.bottom, rect.right) != box:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "rect 必须为变化格的最小包围矩形 "
                        f"(top={top}, left={left}, bottom={bottom}, right={right})"
                    ),
                )
            rect_cells = [
                (r, c)
                for r in range(rect.top, rect.bottom + 1)
                for c in range(rect.left, rect.right + 1)
            ]
            # 矩形内每格 (含未变格) 计数加一, 不得超过上限
            if any(session.counts[r][c] >= session.max_partial for r, c in rect_cells):
                raise HTTPException(
                    status_code=422,
                    detail=f"矩形内存在已达局刷上限 {session.max_partial} 的格",
                )
            new_counts = [row[:] for row in session.counts]
            for r, c in rect_cells:
                new_counts[r][c] += 1
            area = (rect.bottom - rect.top + 1) * (rect.right - rect.left + 1)
            added_cost = area * session.cell_cost

        # 仅在全部校验通过后才更新画面、计数、累计花费与版本
        session.counts = new_counts
        session.total_cost += added_cost
        session.frame = list(cmd.frame)
        session.version += 1
        return session.snapshot(session_id)
