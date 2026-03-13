import unittest
from unittest.mock import patch, MagicMock

from notifier import send_telegram


class TestSendTelegram(unittest.TestCase):
    @patch("notifier.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_telegram("123:ABC", "-100", "test message")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("123:ABC", call_kwargs[0][0])
        self.assertEqual(call_kwargs[1]["json"]["text"], "test message")

    @patch("notifier.requests.post")
    def test_failure_does_not_raise(self, mock_post):
        mock_post.side_effect = Exception("network error")
        # Should not raise — fire and forget
        send_telegram("123:ABC", "-100", "test message")

    @patch("notifier.requests.post")
    def test_sends_correct_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_telegram("token", "chat", "hello")

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["chat_id"], "chat")
        self.assertEqual(payload["text"], "hello")


if __name__ == "__main__":
    unittest.main()
