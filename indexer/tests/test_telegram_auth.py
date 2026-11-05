"""Tests for QR-based Telegram login helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram_auth import print_qr_login_url


def test_print_qr_login_url_renders_without_error(capsys):
    print_qr_login_url("tg://login?token=abc")
    output = capsys.readouterr().out
    assert "tg://login?token=abc" in output
