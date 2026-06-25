import unittest
import tkinter as tk
from unittest.mock import patch

from codex_usage_widget import (
    CARD_HEIGHT,
    CARD_WIDTH,
    DEFAULT_OPACITY_PERCENT,
    RateLimitSnapshot,
    UsageWidget,
    create_tray_icon_image,
    detect_system_language,
    format_tray_tooltip,
    main_remaining_percent,
    read_macos_user_language,
)


class LayoutConstantTests(unittest.TestCase):
    def test_card_size_fits_percent_and_long_reset_text(self):
        root = tk.Tk()
        root.withdraw()
        try:
            card = tk.Frame(root, width=CARD_WIDTH, height=CARD_HEIGHT, padx=10, pady=9)
            card.grid_propagate(False)
            tk.Label(card, text="7 day", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
            tk.Label(card, text="100%", font=("Segoe UI", 26, "bold")).grid(row=1, column=0, sticky="w")
            tk.Label(card, text="reset 06-30 05:00", font=("Segoe UI", 8)).grid(row=2, column=0, sticky="w")
            card.update_idletasks()

            required_width = max(child.winfo_reqwidth() for child in card.winfo_children()) + 20
            required_height = sum(child.winfo_reqheight() for child in card.winfo_children()) + 18
        finally:
            root.destroy()

        self.assertLessEqual(required_width, CARD_WIDTH)
        self.assertLessEqual(required_height, CARD_HEIGHT)

    def test_default_opacity_matches_product_requirement(self):
        self.assertEqual(DEFAULT_OPACITY_PERCENT, 75)

    def test_detects_supported_system_languages(self):
        with (
            patch("codex_usage_widget.read_windows_user_locale", return_value=None),
            patch("codex_usage_widget.read_macos_user_language", return_value=None),
            patch("codex_usage_widget.locale.getlocale", return_value=("zh_TW", "UTF-8")),
        ):
            self.assertEqual(detect_system_language(), "zh")

        with (
            patch("codex_usage_widget.read_windows_user_locale", return_value=None),
            patch("codex_usage_widget.read_macos_user_language", return_value=None),
            patch("codex_usage_widget.locale.getlocale", return_value=("en_US", "UTF-8")),
        ):
            self.assertEqual(detect_system_language(), "en")

        with (
            patch("codex_usage_widget.read_windows_user_locale", return_value=None),
            patch("codex_usage_widget.read_macos_user_language", return_value=None),
            patch("codex_usage_widget.locale.getlocale", return_value=("ja_JP", "UTF-8")),
        ):
            self.assertEqual(detect_system_language(), "en")

    def test_windows_user_locale_takes_priority(self):
        with (
            patch("codex_usage_widget.read_windows_user_locale", return_value="zh-TW"),
            patch("codex_usage_widget.read_macos_user_language", return_value=None),
            patch("codex_usage_widget.locale.getlocale", return_value=("en_US", "UTF-8")),
        ):
            self.assertEqual(detect_system_language(), "zh")

    def test_macos_user_language_is_used_before_locale(self):
        with (
            patch("codex_usage_widget.read_windows_user_locale", return_value=None),
            patch("codex_usage_widget.read_macos_user_language", return_value="zh-Hant-TW"),
            patch("codex_usage_widget.locale.getlocale", return_value=("en_US", "UTF-8")),
        ):
            self.assertEqual(detect_system_language(), "zh")

    def test_reads_first_macos_user_language(self):
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": '(\n    "zh-Hant-TW",\n    "en-US"\n)\n'},
        )()
        with patch("codex_usage_widget.sys.platform", "darwin"), patch("codex_usage_widget.subprocess.run", return_value=completed):
            self.assertEqual(read_macos_user_language(), "zh-Hant-TW")

    def test_widget_starts_with_prd_controls(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch("codex_usage_widget.detect_system_language", return_value="zh"),
                patch("codex_usage_widget.detect_system_theme", return_value="dark"),
            ):
                widget = UsageWidget(root, enable_tray=False)

            self.assertEqual(widget.language_mode, "system")
            self.assertEqual(widget.theme_mode, "system")
            self.assertEqual(widget.language, "zh")
            self.assertEqual(widget.theme_name, "dark")
            self.assertEqual(widget.opacity_percent, DEFAULT_OPACITY_PERCENT)
            self.assertEqual(widget.language_button.cget("text"), "◎")
            self.assertEqual(widget.theme_button.cget("text"), "◐")
            self.assertEqual(widget.language_button.cget("bg"), "#3e3d32")
            self.assertEqual(widget.language_button.cget("fg"), "#f8f8f2")
        finally:
            root.destroy()

    def test_toolbar_width_tracks_usage_cards(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch("codex_usage_widget.detect_system_language", return_value="en"),
                patch("codex_usage_widget.detect_system_theme", return_value="light"),
            ):
                widget = UsageWidget(root, enable_tray=False)

            snapshots = [
                RateLimitSnapshot("codex", "primary", None, 53, 47, 300, 1, "20:14", "5 hr", 0, None),
                RateLimitSnapshot("codex", "secondary", None, 90, 10, 10080, 1, "06-28 00:16", "7 day", 0, None),
            ]
            widget._show_snapshots(snapshots)

            self.assertGreaterEqual(widget.frame.columnconfigure(0)["minsize"], CARD_WIDTH * 2 + 8)
        finally:
            root.destroy()

    def test_tray_tooltip_uses_primary_remaining_usage(self):
        snapshots = [
            RateLimitSnapshot("codex", "primary", None, 25, 75, 300, 1, "10:00", "5 hr", None, None),
            RateLimitSnapshot("codex_week", "primary", None, 40, 60, 10080, 1, "06-30 10:00", "7 day", None, None),
        ]

        remaining = main_remaining_percent(snapshots)

        self.assertEqual(remaining, 75)
        self.assertEqual(format_tray_tooltip("Codex Usage", remaining), "Codex Usage: 75%")

    def test_tray_icon_image_is_generated(self):
        image = create_tray_icon_image(75, "dark")

        self.assertEqual(image.size, (64, 64))
        self.assertEqual(image.mode, "RGBA")


if __name__ == "__main__":
    unittest.main()
