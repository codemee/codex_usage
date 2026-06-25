import unittest
import tkinter as tk

from codex_usage_widget import CARD_HEIGHT, CARD_WIDTH


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


if __name__ == "__main__":
    unittest.main()
