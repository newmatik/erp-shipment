# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and Contributors
# See license.txt
from __future__ import unicode_literals

# import frappe
import unittest
from unittest.mock import patch

from frappe import _dict

from shipment.api.let_me_ship import _get_letmeship_user_error, _normalize_goods_value
from shipment.shipment.doctype.shipment.shipment import make_shipment_from_delivery_note


class FakeShipment(_dict):
	"""Provide the child-row behavior needed by mapper unit tests."""

	def append(self, fieldname, value):
		"""Append one dictionary child row."""
		row = _dict(value)
		self.setdefault(fieldname, []).append(row)
		return row


class TestShipment(unittest.TestCase):
	"""Verify Shipment compatibility behavior."""

	@patch(
		"shipment.shipment.doctype.shipment.shipment._get_delivery_note_grand_total",
		return_value=3285.97,
	)
	@patch("erpnext.stock.doctype.delivery_note.delivery_note.make_shipment")
	def test_maps_delivery_note_to_legacy_child_table(self, core_mapper, get_value):
		"""Populate the custom Delivery Note table after the ERPNext mapper runs."""
		shipment = FakeShipment(shipment_delivery_notes=[])
		core_mapper.return_value = shipment

		result = make_shipment_from_delivery_note("DN-DE-26-00806")

		self.assertIs(result, shipment)
		self.assertEqual(len(result.shipment_delivery_notes), 1)
		self.assertEqual(result.shipment_delivery_notes[0].delivery_note, "DN-DE-26-00806")
		self.assertEqual(result.shipment_delivery_notes[0].grand_total, 3285.97)
		core_mapper.assert_called_once_with("DN-DE-26-00806", None)
		get_value.assert_called_once_with("DN-DE-26-00806")

	@patch("shipment.shipment.doctype.shipment.shipment._get_delivery_note_grand_total")
	@patch("erpnext.stock.doctype.delivery_note.delivery_note.make_shipment")
	def test_does_not_duplicate_existing_delivery_note(self, core_mapper, get_value):
		"""Keep an existing custom Delivery Note row unchanged."""
		shipment = FakeShipment(
			shipment_delivery_notes=[_dict(delivery_note="DN-DE-26-00806", grand_total=3285.97)]
		)
		core_mapper.return_value = shipment

		result = make_shipment_from_delivery_note("DN-DE-26-00806", "target")

		self.assertEqual(len(result.shipment_delivery_notes), 1)
		core_mapper.assert_called_once_with("DN-DE-26-00806", "target")
		get_value.assert_not_called()

	def test_normalizes_goods_value_for_letmeship(self):
		"""Round the outbound goods value up to LetMeShip's required integer."""
		self.assertEqual(_normalize_goods_value(3285.97), 3286)
		self.assertEqual(_normalize_goods_value("3285"), 3285)

	def test_returns_safe_message_for_letmeship_parse_errors(self):
		"""Hide provider implementation details from the user-facing error."""
		error = _get_letmeship_user_error(
			{"errorMessage": "Cannot deserialize value of type java.lang.Integer"}
		)

		self.assertIn("LetMeShip could not process the shipment data", error)
		self.assertNotIn("deserialize", error)
