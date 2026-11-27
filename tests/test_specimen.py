import unittest

from ocr_service.parse import report_payload


class SpecimenTests(unittest.TestCase):
    def test_only_an_explicit_unambiguous_report_specimen_is_returned(self):
        for words, expected in [
            (["标本(Type)：血浆"], ("blood", "血浆")),
            (["标 本（Ｔｙｐｅ）：", " 血 浆 ", "诊断：合成测试"], ("blood", "血浆")),
            (["标本类型：血清"], ("blood", "血清")),
            (["样本(Specimen Type):", "全血"], ("blood", "全血")),
            (["样本(Type)：尿液"], ("urine", "尿液")),
            (["标本：", "尿酸"], ("unknown", None)),
            (["标本：", "送检说明：血浆"], ("unknown", None)),
            (["标本：血浆", "标本：尿液"], ("unknown", None)),
            (["血浆", "尿酸"], ("unknown", None)),
        ]:
            with self.subTest(words=words):
                actual = report_payload({"overall_ocr_res": {"rec_texts": words}})
                self.assertEqual((actual["specimen"], actual.get("specimen_detail")), expected)
