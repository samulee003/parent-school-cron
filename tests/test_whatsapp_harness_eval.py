import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import whatsapp_harness


FIXTURE = Path(__file__).parent / "fixtures" / "whatsapp_harness_cases.json"
PHONE_LIKE_DIGITS = re.compile(r"\d{6,}")
EXPECTED_FIELDS = {
    "route": "expected_route",
    "intent": "expected_intent",
    "llm_purpose": "expected_llm_purpose",
    "allow_llm": "expected_allow_llm",
    "profile_patch": "expected_profile_patch",
}


class WhatsAppHarnessEvalTests(unittest.TestCase):
    def test_golden_harness_routes(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        failures = []

        for case in cases:
            self._check_privacy(case, failures)

            decision = whatsapp_harness.decide_message_route(
                case["input"],
                case.get("profile", {}),
            )
            for actual_key, expected_key in EXPECTED_FIELDS.items():
                self._check_field(case, decision, actual_key, expected_key, failures)

        self.assertEqual([], failures)

    def _check_privacy(self, case, failures):
        if case.get("privacy") != "synthetic":
            failures.append(f"{case.get('name', '<unnamed>')}: missing synthetic marker")

        serialized_case = json.dumps(case, ensure_ascii=False)
        if PHONE_LIKE_DIGITS.search(serialized_case):
            failures.append(f"{case.get('name', '<unnamed>')}: contains phone-looking digits")

    def _check_field(self, case, decision, actual_key, expected_key, failures):
        if expected_key not in case:
            failures.append(f"{case['name']}: missing {expected_key}")
            return

        if decision.get(actual_key) != case[expected_key]:
            failures.append(
                f"{case['name']}: {actual_key} "
                f"{decision.get(actual_key)} != {case[expected_key]}"
            )


if __name__ == "__main__":
    unittest.main()
