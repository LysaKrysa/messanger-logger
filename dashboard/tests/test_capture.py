"""Transient screenshot targets cannot poison later read-only captures."""
import unittest
from unittest.mock import Mock

from browser.capture import FrameCapture


class FrameCaptureTests(unittest.TestCase):
    def test_reuses_a_working_session_and_replaces_it_after_a_redirect_error(self):
        broken, recovered = Mock(), Mock()
        broken.send.side_effect = [{"data": "FIRST_FRAME"}, RuntimeError("Synthetic detached target")]
        recovered.send.return_value = {"data": "NEXT_FRAME"}
        context, page = Mock(), object()
        context.new_cdp_session.side_effect = [broken, recovered]
        capture = FrameCapture(context)
        self.assertEqual(capture.jpeg(page), "FIRST_FRAME")
        with self.assertRaisesRegex(RuntimeError, "Synthetic detached target"):
            capture.jpeg(page)
        self.assertIsNone(capture.session)
        broken.detach.assert_called_once()
        self.assertEqual(capture.jpeg(page), "NEXT_FRAME")
        self.assertEqual(context.new_cdp_session.call_count, 2)

    def test_switching_pages_detaches_the_previous_target(self):
        first, second = Mock(), Mock()
        first.send.return_value = {"data": "FIRST_FRAME"}
        second.send.return_value = {"data": "SECOND_FRAME"}
        context = Mock()
        context.new_cdp_session.side_effect = [first, second]
        capture = FrameCapture(context)
        capture.jpeg(object())
        self.assertEqual(capture.jpeg(object()), "SECOND_FRAME")
        first.detach.assert_called_once()

    def test_failed_attachment_and_detach_can_be_retried_without_replaying_input(self):
        session = Mock()
        session.send.return_value = {"data": "RECOVERED_FRAME"}
        session.detach.side_effect = RuntimeError("Synthetic already closed")
        context, page = Mock(), object()
        context.new_cdp_session.side_effect = [RuntimeError("Synthetic navigation"), session]
        capture = FrameCapture(context)
        with self.assertRaises(RuntimeError):
            capture.jpeg(page)
        self.assertEqual(capture.jpeg(page), "RECOVERED_FRAME")
        capture.reset()
        self.assertIsNone(capture.session)
        self.assertEqual(session.send.call_args.args[0], "Page.captureScreenshot")

    def test_invalid_capture_result_drops_the_session(self):
        context, page = Mock(), object()
        context.new_cdp_session.return_value.send.return_value = {"data": ""}
        capture = FrameCapture(context)
        with self.assertRaises(ValueError):
            capture.jpeg(page)
        self.assertIsNone(capture.session)


if __name__ == "__main__":
    unittest.main()
