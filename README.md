# E-Paper Refresh Planner（离线电子纸刷新规划 API）

给定初始画面与 1~20 帧目标画面（均为同尺寸 0/1 字符串行网格，高、宽各 1~4），在局刷次数上限 `max_partial`（0~3）约束下，求总花费最低的逐帧刷新方案。

## 规则

- 各格局刷次数初始为 0。
- **无变化帧只能跳过**（`skip`），花费 0，局刷次数不变。
- **变化帧**二选一：
  - **全刷 `full`**：花费 `full_cost`，所有格局刷次数清零；
  - **局刷 `partial`**：刷新变化格的**最小轴对齐包围矩形**，花费 `面积 × cell_cost`；矩形内**每格**（含未变格）局刷次数加一，且任何格次数不得超过 `max_partial`（`max_partial=0` 时局刷不可用）。
- 目标：总花费最低；**同价时按模式序列裁决，`full` 优先于 `partial`**（`skip` 位置由输入唯一确定）。

## 一键启动

```bash
docker compose up --build
```

（等价地：`docker build -t epaper-planner . && docker run -p 8000:8000 epaper-planner`）

- 访问地址：**http://localhost:8000**
- 端口：**8000**
- 交互式文档（Swagger UI）：http://localhost:8000/docs

## HTTP 示例

```bash
curl -X POST http://localhost:8000/plan \
  -H "Content-Type: application/json" \
  -d '{
    "initial": ["00", "00"],
    "frames": [["11", "00"], ["11", "11"], ["00", "11"]],
    "max_partial": 2,
    "full_cost": 10,
    "cell_cost": 1
  }'
```

响应（`200 OK`）：

```json
{
  "total_cost": 6,
  "plan": [
    { "mode": "partial", "rect": { "top": 0, "left": 0, "bottom": 0, "right": 1 } },
    { "mode": "partial", "rect": { "top": 1, "left": 0, "bottom": 1, "right": 1 } },
    { "mode": "partial", "rect": { "top": 0, "left": 0, "bottom": 0, "right": 1 } }
  ]
}
```

## 接口说明

### `POST /plan`

请求体：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `initial` | `string[]` | 初始画面，1~4 行，每行 1~4 个 `0`/`1` 字符，等宽 |
| `frames` | `string[][]` | 1~20 帧目标画面，每帧尺寸与 `initial` 相同 |
| `max_partial` | `int` | 0~3，每格局刷次数上限 |
| `full_cost` | `int` | 正整数，全刷花费 |
| `cell_cost` | `int` | 正整数，局刷单格花费 |

响应体：

| 字段 | 说明 |
| --- | --- |
| `total_cost` | 最优方案总花费 |
| `plan[i].mode` | 第 i 帧模式：`skip` / `full` / `partial` |
| `plan[i].rect` | 局刷矩形 `(top, left, bottom, right)`，**零基闭区间**；仅 `partial` 时给出，其余为 `null` |

非法输入（尺寸越界、非法字符、帧数越界、尺寸不一致、参数越界或非正整数、JSON  malformed 等）一律返回 **`422 Unprocessable Entity`**。

## 设备上报会话（在线核验）

设备逐帧上报**已执行**的刷新指令，服务端按会话核验并维护当前画面、各格局刷次数与累计花费。会话创建后 `version` 与各格计数均从 0 开始；仅**成功提交**才推进画面、计数、累计花费与版本（每次 +1）。

规则与 `/plan` 完全一致：

- 画面无变化只能 `skip`（费用 0，计数不变）；画面有变化只能 `full` / `partial`；
- `partial` 必须携带变化格的**最小轴对齐包围矩形** `rect`（零基闭区间），矩形内**每格（含未变格）**计数 +1 且不得超过 `max_partial`，费用 `面积 × cell_cost`；
- `full` 清零全部计数，费用 `full_cost`。

状态码：成功 `200`/`201`；会话不存在 **`404`**；版本不符 **`409`**（同版本并发提交至多成功一次）；指令非法 **`422`**（模式与画面变化矛盾、矩形不是最小包围盒/越界、超局刷上限、尺寸不符等）；任何失败都不改变状态。

### 可执行调用示例

```bash
BASE=http://localhost:8000

# 1) 创建会话（参数范围与 /plan 相同；计数与版本从零开始）
SID=$(curl -s -X POST "$BASE/sessions" \
  -H "Content-Type: application/json" \
  -d '{"initial":["00","00"],"max_partial":1,"full_cost":10,"cell_cost":2}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')
echo "session_id=$SID"

# 2) 查询当前状态
curl -s "$BASE/sessions/$SID"

# 3) 上报局刷：变化格为 (0,1) 与 (1,0)，最小包围矩形覆盖整个 2x2
#    -> 4 格计数均为 1（含 2 个未变格），花费 4*2=8，version 变 1
curl -s -X POST "$BASE/sessions/$SID/submit" -H "Content-Type: application/json" \
  -d '{"version":0,"frame":["01","10"],"mode":"partial","rect":{"top":0,"left":0,"bottom":1,"right":1}}'

# 4) 用旧版本 0 重放 -> 409 Conflict，状态不变
curl -i -X POST "$BASE/sessions/$SID/submit" \
  -H "Content-Type: application/json" \
  -d '{"version":0,"frame":["00","00"],"mode":"full"}'

# 5) 上报全刷（version=1）-> 计数清零，花费 10，累计 18，version 变 2
curl -s -X POST "$BASE/sessions/$SID/submit" \
  -H "Content-Type: application/json" \
  -d '{"version":1,"frame":["00","00"],"mode":"full"}'

# 6) 画面无变化只能 skip（带 rect 或填 full/partial 均为 422）
curl -s -X POST "$BASE/sessions/$SID/submit" \
  -H "Content-Type: application/json" \
  -d '{"version":2,"frame":["00","00"],"mode":"skip"}'

# 7) 查询不存在的会话 -> 404
curl -i "$BASE/sessions/deadbeef"
```

成功提交响应体（查询接口返回相同结构）：

```json
{
  "session_id": "ca6d22481f06452f92d8d66abd8d977e",
  "version": 1,
  "current_frame": ["01", "10"],
  "counts": [[1, 1], [1, 1]],
  "total_cost": 8,
  "max_partial": 1,
  "full_cost": 10,
  "cell_cost": 2
}
```

### 接口说明

| 方法与路径 | 说明 |
| --- | --- |
| `POST /sessions` | 创建会话；请求体为 `initial` / `max_partial` / `full_cost` / `cell_cost`（约束同 `/plan`，无 `frames`），返回 `201` 与初始状态 |
| `GET /sessions/{id}` | 查询当前画面、计数、累计花费、版本及配置；不存在返回 `404` |
| `POST /sessions/{id}/submit` | 提交一帧已执行的刷新指令，见下 |

`POST /sessions/{id}/submit` 请求体：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `version` | `int` | 客户端持有的会话版本，非负整数；与服务端不一致返回 `409` |
| `frame` | `string[]` | 目标画面，0/1 行网格，尺寸须与创建时的 `initial` 一致 |
| `mode` | `string` | `skip` / `full` / `partial` |
| `rect` | `object` | 仅 `partial` **必须**提供：`{top,left,bottom,right}`，零基闭区间，须恰好等于变化格的最小包围矩形且不越界 |

> 会话保存在服务进程内存中，重启服务后失效；当前部署为单 uvicorn worker（见 `docker-compose.yml` / `Dockerfile`），同版本并发提交经每会话锁串行化，至多成功一次。

## 本地开发与测试

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000   # 启动
python -m unittest discover -s tests              # 测试（含与暴力枚举对照的随机正确性测试）
```

## 实现要点

- 动态规划：状态为各格局刷次数向量（每格 0~`max_partial`），逐帧转移；
  每状态保留 `(累计花费, 模式序列)` 最小者——元组字典序天然实现
  “先比总花费，同价时 `full` 优先于 `partial`” 的裁决。
- 项目结构：
  - `app/solver.py` — 纯求解逻辑（不依赖 Web 框架）
  - `app/main.py` — FastAPI 入口：`/plan` 离线规划 + `/sessions` 在线上报会话（内存存储、乐观版本、每会话锁）
  - `tests/` — 单元测试与 API 测试（含会话同版本并发测试）
