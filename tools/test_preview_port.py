import unittest
from unittest.mock import MagicMock, patch

from preview_port import find_port


class PreviewPortTests(unittest.TestCase):
    def test_invalid_port(self):
        for port in (0, 1023, 65536):
            with self.assertRaises(ValueError):
                find_port(port)

    @patch("preview_port.socket.socket")
    def test_first_available_port(self, socket):
        self.assertEqual(find_port(8001), 8001)
        socket.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 8001))

    @patch("preview_port.socket.socket")
    def test_occupied_port_is_skipped(self, socket):
        occupied, free = MagicMock(), MagicMock()
        occupied.__enter__.return_value.bind.side_effect = OSError("occupied")
        socket.side_effect = [occupied, free]
        self.assertEqual(find_port(8001), 8002)

    @patch("preview_port.socket.socket")
    def test_does_not_overflow_port_range(self, socket):
        socket.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
        with self.assertRaises(RuntimeError):
            find_port(65535)
        self.assertEqual(socket.call_count, 1)


if __name__ == "__main__":
    unittest.main()
