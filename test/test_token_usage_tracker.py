import unittest

from backend.token_usage_tracker import TokenUsageTracker, estimate_text_tokens, extract_provider_usage


class FakeMessage:
    def __init__(self, usage_metadata=None, response_metadata=None):
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata


class TokenUsageTrackerTests(unittest.TestCase):
    def test_estimates_generated_chinese_text(self):
        tracker = TokenUsageTracker()

        tracker.add_generated_text("清蒸鲈鱼需要大火蒸8分钟。")

        snapshot = tracker.snapshot()
        self.assertGreater(snapshot["completion_tokens_estimated"], 0)
        self.assertEqual(snapshot["completion_chars"], len("清蒸鲈鱼需要大火蒸8分钟。"))
        self.assertEqual(snapshot["source"], "estimated")

    def test_normalizes_langchain_usage_metadata(self):
        usage = extract_provider_usage(
            FakeMessage(
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 24,
                    "total_tokens": 124,
                }
            )
        )

        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 24)
        self.assertEqual(usage["total_tokens"], 124)

    def test_normalizes_openai_response_metadata(self):
        usage = extract_provider_usage(
            FakeMessage(
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 90,
                        "completion_tokens": 10,
                        "total_tokens": 100,
                    }
                }
            )
        )

        self.assertEqual(usage["input_tokens"], 90)
        self.assertEqual(usage["output_tokens"], 10)
        self.assertEqual(usage["total_tokens"], 100)

    def test_accumulates_provider_usage_and_estimated_output(self):
        tracker = TokenUsageTracker()

        tracker.add_model_usage(FakeMessage(usage_metadata={"input_tokens": 10, "output_tokens": 5}))
        tracker.add_model_usage(FakeMessage(response_metadata={"usage": {"prompt_tokens": 7, "completion_tokens": 3}}))
        tracker.add_generated_text("番茄炒蛋")

        snapshot = tracker.snapshot(final=True)
        self.assertEqual(snapshot["input_tokens"], 17)
        self.assertEqual(snapshot["output_tokens"], 8)
        self.assertEqual(snapshot["total_tokens"], 25)
        self.assertEqual(snapshot["source"], "mixed")
        self.assertGreaterEqual(snapshot["completion_tokens_estimated"], estimate_text_tokens("番茄炒蛋"))
        self.assertTrue(snapshot["final"])


if __name__ == "__main__":
    unittest.main()
