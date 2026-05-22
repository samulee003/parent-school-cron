import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import whatsapp_nlu


class WhatsAppNluTests(unittest.TestCase):
    def test_normalize_parent_text_nfkc_whitespace_and_strip(self):
        self.assertEqual(
            whatsapp_nlu.normalize_parent_text("  ８歲，\n\t情緒  "),
            "8歲, 情緒",
        )

    def test_parse_chinese_number_common_child_ages(self):
        cases = {
            "八": 8,
            "十": 10,
            "十三": 13,
            "十八": 18,
            "兩": 2,
            "两": 2,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(whatsapp_nlu.parse_chinese_number(raw), expected)

    def test_age_to_group_uses_official_labels(self):
        self.assertEqual(whatsapp_nlu.age_to_group(0), "0-2歲")
        self.assertEqual(whatsapp_nlu.age_to_group(2.5), "0-2歲")
        self.assertEqual(whatsapp_nlu.age_to_group(3), "3-6歲")
        self.assertEqual(whatsapp_nlu.age_to_group(6), "3-6歲")
        self.assertEqual(whatsapp_nlu.age_to_group(7), "7-12歲")
        self.assertEqual(whatsapp_nlu.age_to_group(12), "7-12歲")
        self.assertEqual(whatsapp_nlu.age_to_group(13), "13-18歲")
        self.assertEqual(whatsapp_nlu.age_to_group(18), "13-18歲")

    def test_detect_child_age_groups_from_numerals_and_school_hints(self):
        self.assertEqual(whatsapp_nlu.detect_child_age_groups("八歲，情緒"), ["7-12歲"])
        self.assertEqual(whatsapp_nlu.detect_child_age_groups("十三歲想搵情緒課"), ["13-18歲"])
        self.assertEqual(whatsapp_nlu.detect_child_age_groups("8 and 6"), ["3-6歲", "7-12歲"])
        self.assertEqual(whatsapp_nlu.detect_child_age_groups("8"), ["7-12歲"])
        self.assertEqual(whatsapp_nlu.detect_child_age_groups("小朋友8"), ["7-12歲"])
        self.assertEqual(
            whatsapp_nlu.detect_child_age_groups("大仔中學，細仔幼稚園"),
            ["3-6歲", "13-18歲"],
        )

    def test_detect_child_age_groups_ignores_command_and_date_numbers(self):
        for text in [
            "page 2",
            "detail 1",
            "詳情1",
            "第2頁",
            "2026/06/20",
            "牛仔褲8折邊度買",
            "女裝8折",
            "我想買女裝8號",
        ]:
            with self.subTest(text=text):
                self.assertEqual(whatsapp_nlu.detect_child_age_groups(text), [])

    def test_detect_local_intent(self):
        for text in ["更多", "下一頁", "還有嗎"]:
            with self.subTest(text=text):
                self.assertEqual(whatsapp_nlu.detect_local_intent(text), "next_page")
        self.assertEqual(whatsapp_nlu.detect_local_intent("全部課程"), "all_courses")
        self.assertEqual(whatsapp_nlu.detect_local_intent("重設"), "reset")
        self.assertEqual(whatsapp_nlu.detect_local_intent("課程"), "courses")

    def test_is_hard_off_topic(self):
        self.assertTrue(whatsapp_nlu.is_hard_off_topic("推薦餐廳"))
        self.assertTrue(whatsapp_nlu.is_hard_off_topic("我小朋友13歲情緒壓力大，想推薦餐廳"))
        self.assertTrue(whatsapp_nlu.is_hard_off_topic("推薦餐廳課程"))
        self.assertTrue(whatsapp_nlu.is_hard_off_topic("幫我寫 Python code"))
        self.assertFalse(whatsapp_nlu.is_hard_off_topic("孩子最近做功課很拖拉"))
        self.assertFalse(whatsapp_nlu.is_hard_off_topic("青少年家長課"))

    def test_extract_local_profile_patch(self):
        patch = whatsapp_nlu.extract_local_profile_patch("八歲，最近情緒壓力大")
        self.assertEqual(patch["age_groups"], ["7-12歲"])
        self.assertIn("情緒壓力", patch["pain_points"])

    def test_pain_point_labels_match_existing_supported_set(self):
        detected = whatsapp_nlu.extract_local_profile_patch(
            "情緒壓力、親子溝通、學習動機、環境適應、社交人際、生活照顧、科技使用"
        )
        self.assertEqual(
            detected["pain_points"],
            ["情緒壓力", "親子溝通", "學習動機", "環境適應", "社交人際", "生活照顧", "科技使用"],
        )


if __name__ == "__main__":
    unittest.main()
