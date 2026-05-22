import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import whatsapp_harness


class WhatsAppHarnessTests(unittest.TestCase):
    STABLE_DECISION_KEYS = {
        "route",
        "normalized_text",
        "intent",
        "allow_llm",
        "llm_purpose",
        "profile_ready",
        "profile_patch",
        "recommended_action",
    }

    def test_off_topic_fails_closed_before_recommendation_intent(self):
        decision = whatsapp_harness.decide_message_route("推薦餐廳", {})

        self.assertEqual(decision["route"], "off_topic")
        self.assertFalse(decision["allow_llm"])
        self.assertEqual(decision["recommended_action"], "local_refusal")

    def test_mixed_off_topic_with_parent_pain_still_fails_closed(self):
        for text in ["我小朋友13歲情緒壓力大，想推薦餐廳", "推薦餐廳課程"]:
            with self.subTest(text=text):
                decision = whatsapp_harness.decide_message_route(text, {})

                self.assertEqual(decision["route"], "off_topic")
                self.assertFalse(decision["allow_llm"])
                self.assertEqual(decision["recommended_action"], "local_refusal")

    def test_exact_local_command_routes_locally(self):
        decision = whatsapp_harness.decide_message_route(
            "更多",
            {"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]},
        )

        self.assertEqual(decision["route"], "local_command")
        self.assertEqual(decision["intent"], "next_page")
        self.assertFalse(decision["allow_llm"])

    def test_obvious_profile_patch_routes_locally(self):
        decision = whatsapp_harness.decide_message_route("八歲，情緒", {})

        self.assertEqual(decision["route"], "local_profile_update")
        self.assertEqual(decision["profile_patch"]["age_groups"], ["7-12歲"])
        self.assertIn("情緒壓力", decision["profile_patch"]["pain_points"])
        self.assertFalse(decision["allow_llm"])
        self.assertEqual(decision["recommended_action"], "recommend_after_profile_update")

    def test_simple_bare_age_list_stays_local(self):
        decision = whatsapp_harness.decide_message_route(
            "8 and 6",
            {"pain_points": ["親子溝通"]},
        )

        self.assertEqual(decision["route"], "local_profile_update")
        self.assertEqual(decision["profile_patch"]["age_groups"], ["3-6歲", "7-12歲"])
        self.assertFalse(decision["allow_llm"])
        self.assertEqual(decision["recommended_action"], "recommend_after_profile_update")

    def test_short_ambiguous_in_domain_text_uses_llm_profile_extraction(self):
        decision = whatsapp_harness.decide_message_route(
            "大仔讀緊高小，細仔仲好細",
            {"pain_points": ["親子溝通"]},
        )

        self.assertEqual(decision["route"], "llm_profile_extraction")
        self.assertTrue(decision["allow_llm"])
        self.assertEqual(decision["llm_purpose"], "profile_extraction")

    def test_standalone_child_characters_do_not_trigger_profile_extraction(self):
        for text in ["我想買女裝", "牛仔褲邊度買", "女裝8折", "我想買女裝8號"]:
            with self.subTest(text=text):
                decision = whatsapp_harness.decide_message_route(text, {})

                self.assertEqual(decision["route"], "unknown")
                self.assertFalse(decision["allow_llm"])
                self.assertEqual(decision["recommended_action"], "ask_for_supported_query")

    def test_ready_profile_recommendation_command_uses_bounded_recommendation(self):
        decision = whatsapp_harness.decide_message_route(
            "幫我揀",
            {"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]},
        )

        self.assertEqual(decision["route"], "recommend_courses")
        self.assertTrue(decision["allow_llm"])
        self.assertEqual(decision["llm_purpose"], "bounded_recommendation")
        self.assertTrue(decision["profile_ready"])

    def test_unknown_unrelated_text_does_not_use_llm(self):
        decision = whatsapp_harness.decide_message_route("你好嗎", {})

        self.assertEqual(decision["route"], "unknown")
        self.assertFalse(decision["allow_llm"])
        self.assertEqual(decision["recommended_action"], "ask_for_supported_query")

    def test_profile_ready_requires_age_and_concern_signal(self):
        incomplete_age_only = whatsapp_harness.decide_message_route(
            "幫我揀",
            {"age_groups": ["13-18歲"]},
        )
        ready_with_topic = whatsapp_harness.decide_message_route(
            "幫我揀",
            {"age_groups": ["13-18歲"], "topic": "身心健康"},
        )
        ready_with_target = whatsapp_harness.decide_message_route(
            "幫我揀",
            {"age_groups": ["13-18歲"], "target": "家長"},
        )

        self.assertFalse(incomplete_age_only["profile_ready"])
        self.assertFalse(incomplete_age_only["allow_llm"])
        self.assertEqual(incomplete_age_only["recommended_action"], "ask_for_profile_completion")
        self.assertTrue(ready_with_topic["profile_ready"])
        self.assertEqual(ready_with_topic["route"], "recommend_courses")
        self.assertTrue(ready_with_target["profile_ready"])
        self.assertEqual(ready_with_target["route"], "recommend_courses")

    def test_local_profile_update_recommends_next_action_for_missing_fields(self):
        age_only = whatsapp_harness.decide_message_route("8", {})
        concern_only = whatsapp_harness.decide_message_route("情緒", {})

        self.assertEqual(age_only["route"], "local_profile_update")
        self.assertEqual(age_only["recommended_action"], "ask_missing_concern")
        self.assertEqual(concern_only["route"], "local_profile_update")
        self.assertEqual(concern_only["recommended_action"], "ask_missing_age")

    def test_decision_schema_is_stable_across_representative_routes(self):
        cases = [
            ("推薦餐廳", {}),
            ("更多", {}),
            ("八歲，情緒", {}),
            ("大仔讀緊高小，細仔仲好細", {"pain_points": ["親子溝通"]}),
            ("幫我揀", {"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]}),
            ("你好嗎", {}),
        ]

        for text, profile in cases:
            with self.subTest(text=text):
                decision = whatsapp_harness.decide_message_route(text, profile)

                self.assertEqual(set(decision), self.STABLE_DECISION_KEYS)
                self.assertIsInstance(decision["intent"], str)
                self.assertIsInstance(decision["allow_llm"], bool)
                self.assertIsInstance(decision["llm_purpose"], str)
                self.assertIsInstance(decision["profile_ready"], bool)
                self.assertIsInstance(decision["profile_patch"], dict)
                self.assertTrue(decision["recommended_action"])


if __name__ == "__main__":
    unittest.main()
