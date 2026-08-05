"""Guard Shipment's fast document-search declaration."""

import unittest

from shipment import hooks


class TestSearchHooks(unittest.TestCase):
	"""Verify that the Shipment prefix remains discoverable."""

	def test_declares_shipment_prefix(self):
		"""Keep the Shipment series in the distributed search-rule hook."""
		self.assertEqual(
			hooks.awesome_bar_search_rules,
			[{"doctype": "Shipment", "prefixes": ["SHIPMENT-"]}],
		)


if __name__ == "__main__":
	unittest.main()
