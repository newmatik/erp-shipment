"""Test the Shipment summary that Moth shows outside the ERP."""

import unittest
from unittest.mock import patch

from frappe import _dict

from shipment.api.moth_shipping import _summary


def _shipment(name):
	return _dict(
		name=name,
		docstatus=1,
		status="Booked",
		shipment_id="LMS-1",
		awb_number="1Z999",
		service_provider="LetMeShip",
		carrier="UPS",
		carrier_service="Standard",
		pickup_date="2026-11-10",
		shipment_delivery_notes=[],
		carrier_booking_data=None,
	)


class TestSummaryErpUrl(unittest.TestCase):
	"""The ERP link must work where Moth shows it, outside the ERP."""

	def test_erp_url_is_an_absolute_desk_link(self):
		"""Return the site's absolute v16 desk URL, not a relative /app/ path."""
		with patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://erp.example.com" + uri):
			url = _summary(_shipment("SHIP-00042"))["erp_url"]
		self.assertEqual(url, "https://erp.example.com/desk/shipment/SHIP-00042")

	def test_erp_url_quotes_the_document_name(self):
		"""Percent-encode characters the URL cannot carry, such as spaces."""
		with patch("frappe.utils.data.get_url", side_effect=lambda uri: "https://erp.example.com" + uri):
			url = _summary(_shipment("SHIP 1"))["erp_url"]
		self.assertEqual(url, "https://erp.example.com/desk/shipment/SHIP%201")
