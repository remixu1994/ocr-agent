import io
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from ocr_service.app import create_app
from ocr_service.document import MAX_TEXT_LENGTH, document_payload
from ocr_service.engine import MAX_BYTES
from ocr_service.parse import report_payload


HTML = """<table><tr><td>项目名称</td><td>项目缩写</td><td>结果</td>
<td>单位</td><td>参考区间</td></tr>
<tr><td>钾</td><td>K</td><td>4.28</td><td>mmo1/L</td><td>3.5-5.3</td></tr>
<tr><td>血压</td><td>BP</td><td>122/80</td><td>mmHg</td><td></td></tr></table>"""


class FakeEngine:
    def __init__(self):
        self.sizes = []

    def analyze(self, image):
        self.sizes.append(image.size)
        return {
            "table_res_list": [{"pred_html": HTML}],
            "overall_ocr_res": {"rec_texts": [
                "标本：血浆", "报告日期：2025-10-14", "采样时间：2025-10-13 08:12",
                "该行不应进入响应",
            ], "rec_boxes": [[1, 1, 24, 8], [0, 0, 1000, 1000]]},
        }


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.token = "fake-test-token-for-local-tests-only"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "image/png",
        }
        self.client = self.enterContext(TestClient(
            create_app(lambda: self.engine, lambda: self.token)
        ))
        buffer = io.BytesIO()
        Image.new("RGB", (100, 60)).save(buffer, "PNG")
        self.png = buffer.getvalue()

    def test_returns_only_rows_and_expected_metadata(self):
        result = self.client.post(
            "/v1/ocr", content=self.png,
            headers=self.headers | {
                "X-Ocr-Quarter-Turns": "1", "X-Ocr-Crop-Percent": "5",
            },
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(self.engine.sizes, [(54, 90)])
        payload = result.json()
        self.assertEqual(payload["strategy"], "table")
        self.assertEqual(payload["date"], "2025-10-13")
        self.assertEqual(payload["date_source"], "sample")
        self.assertEqual(payload["sample_time"], "08:12")
        self.assertEqual(payload["specimen"], "blood")
        self.assertEqual([r["result"] for r in payload["rows"]], ["4.28", "122", "80"])
        self.assertEqual(payload["rows"][0]["unit"], "mmo1/L")
        self.assertNotIn("该行", result.text)

    def test_auth_and_input_rejections_do_not_infer(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/v1/health").status_code, 401)
        self.assertEqual(self.client.get("/v1/health", headers=self.headers).status_code, 200)
        for extra, image, expected in [
            ({"Authorization": ""}, b"", 401),
            ({"Content-Type": "application/pdf"}, b"", 415),
            ({"Content-Length": str(MAX_BYTES + 1)}, b"", 413),
            ({"X-Ocr-Crop-Percent": "99"}, self.png, 400),
            ({}, b"not a valid photo", 422),
            ({"Content-Type": "image/jpeg"}, self.png, 422),
        ]:
            result = self.client.post("/v1/ocr", headers=self.headers | extra, content=image)
            self.assertEqual(result.status_code, expected)
        self.assertEqual(self.engine.sizes, [])

    def test_general_documents_return_plain_ocr_and_tables(self):
        path = "/v1/documents/recognize"
        self.assertEqual(self.client.post(path, content=self.png).status_code, 401)
        result = self.client.post(path, headers=self.headers, content=self.png)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        payload = result.json()
        self.assertEqual(payload["image"], {"width": 100, "height": 60})
        self.assertEqual(payload["text_blocks"][0]["box"], [1, 1, 24, 8])
        self.assertIsNone(payload["text_blocks"][1]["box"])
        self.assertEqual(payload["tables"][0]["rows"][1], ["钾", "K", "4.28", "mmo1/L", "3.5-5.3"])
        self.assertNotIn("pred_html", result.text)
        self.assertEqual(self.engine.sizes, [(100, 60)])

    def test_generic_response_limits_and_merged_tables(self):
        actual = document_payload({
            "overall_ocr_res": {"rec_texts": ["x" * (MAX_TEXT_LENGTH + 1)]},
            "table_res_list": [{"pred_html": '<table><tr><td colspan="2">title</td></tr></table>'}],
        }, 100, 60)
        self.assertTrue(actual["partial"])
        self.assertEqual(len(actual["text_blocks"][0]["text"]), MAX_TEXT_LENGTH)
        self.assertEqual(actual["tables"], [])

    def test_borderless_rules_do_not_guess_without_headers(self):
        words = ["项目名称", "项目缩写", "结果", "单位", "参考区间", "蛋白", "PRO",
                 "1+ 0.3", "g/L", "阴性"]
        boxes = [[200 * i, y, 200 * i + 70, y + 30] for y in (20, 80) for i in range(5)]
        actual = report_payload({"overall_ocr_res": {"rec_texts": words, "rec_boxes": boxes}})
        self.assertEqual(actual["strategy"], "spatial")
        self.assertEqual(actual["rows"][0]["result"], "1+ 0.3")
        self.assertTrue(actual["rows"][0]["requires_review"])
        self.assertEqual(report_payload({"overall_ocr_res": {
            "rec_texts": words[5:], "rec_boxes": boxes[5:],
        }})["rows"], [])


if __name__ == "__main__":
    unittest.main()
