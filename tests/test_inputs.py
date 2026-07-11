"""Unit tests for input parsing, de-duplication, and invalid-row handling.

    python3 -m unittest discover -s tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enrichment.inputs import load_deduped  # noqa: E402


class TestInputs(unittest.TestCase):
    def _write(self, text):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        tmp.write(text)
        tmp.close()
        return tmp.name

    def test_dedup_is_case_insensitive_and_counts_occurrences(self):
        path = self._write(
            "domain\nstripe.com\nnotion.so\nstripe.com\nStripe.com\n\nnot a domain\n"
        )
        d = load_deduped(path)
        self.assertEqual(d.unique_domains, ["stripe.com", "notion.so"])
        self.assertEqual(d.occurrences["stripe.com"], 3)
        self.assertEqual(d.occurrences["notion.so"], 1)
        self.assertEqual(d.invalid_rows, ["not a domain"])
        self.assertEqual(d.total_rows, 5)

    def test_plain_list_without_header(self):
        path = self._write("figma.com\nvercel.com\n")
        d = load_deduped(path)
        self.assertEqual(d.unique_domains, ["figma.com", "vercel.com"])

    def test_empty_domain_cell_is_reported_as_invalid_input(self):
        path = self._write("domain,name\nstripe.com,Stripe\n,Acme\n")
        d = load_deduped(path)
        self.assertEqual(d.unique_domains, ["stripe.com"])
        self.assertEqual(d.invalid_rows, [""])
        self.assertEqual(d.total_rows, 2)


if __name__ == "__main__":
    unittest.main()
