"""API 层测试: 正常响应与各类非法输入的 422。"""

import unittest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID = {
    "initial": ["00", "00"],
    "frames": [["11", "00"], ["11", "11"], ["00", "11"]],
    "max_partial": 2,
    "full_cost": 10,
    "cell_cost": 1,
}


def post(payload):
    return client.post("/plan", json=payload)


class TestApi(unittest.TestCase):
    def test_happy_path(self):
        r = post(VALID)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("total_cost", body)
        self.assertEqual(len(body["plan"]), 3)
        for step in body["plan"]:
            self.assertIn(step["mode"], ("skip", "full", "partial"))
            if step["mode"] == "partial":
                rect = step["rect"]
                self.assertLessEqual(rect["top"], rect["bottom"])
                self.assertLessEqual(rect["left"], rect["right"])
            else:
                self.assertIsNone(step["rect"])

    def test_invalid_payloads_return_422(self):
        bad = []
        # 缺字段
        bad.append({k: v for k, v in VALID.items() if k != "initial"})
        # 空初始画面 / 超尺寸
        bad.append({**VALID, "initial": []})
        bad.append({**VALID, "initial": ["0"] * 5})
        bad.append({**VALID, "initial": ["00000"]})
        # 非法字符 / 行宽不一致
        bad.append({**VALID, "initial": ["0a"]})
        bad.append({**VALID, "initial": ["0", "00"]})
        # 帧数越界
        bad.append({**VALID, "frames": []})
        bad.append({**VALID, "frames": [["0"]] * 21})
        # 帧尺寸与 initial 不一致
        bad.append({**VALID, "frames": [["000", "000"]]})
        bad.append({**VALID, "frames": [["0"]]})
        # max_partial 越界 / 非整数
        bad.append({**VALID, "max_partial": -1})
        bad.append({**VALID, "max_partial": 4})
        bad.append({**VALID, "max_partial": 1.5})
        bad.append({**VALID, "max_partial": "2"})
        # 花费非正整数
        bad.append({**VALID, "full_cost": 0})
        bad.append({**VALID, "cell_cost": -3})
        bad.append({**VALID, "full_cost": True})
        # 帧不是 0/1 字符串网格
        bad.append({**VALID, "frames": [["02", "00"]]})
        bad.append({**VALID, "frames": ["not-a-grid"]})
        for payload in bad:
            r = post(payload)
            self.assertEqual(r.status_code, 422, payload)

    def test_malformed_json_returns_422(self):
        r = client.post("/plan", content=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
