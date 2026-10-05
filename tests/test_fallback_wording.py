"""A turn that cannot finish tells the player what to do next without blaming them."""
from __future__ import annotations

import unittest

from app.services import turn_fallback


class FallbackWordingTests(unittest.TestCase):
    def test_no_action_does_not_put_the_blame_on_the_player(self):
        text = turn_fallback.guidance("executor_no_action")
        self.assertIn("劇本裡沒有足夠的內容", text)
        self.assertIn("沒有替它編造", text)
        self.assertIn("補上對象與方式", text)  # a vague action is still told how to retry
        self.assertNotIn("找到可以執行的處理", text)


if __name__ == "__main__":
    unittest.main()
