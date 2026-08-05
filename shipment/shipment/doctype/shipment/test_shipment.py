# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and Contributors
# See license.txt
from __future__ import unicode_literals

# import frappe
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from frappe import _dict

from shipment.api.let_me_ship import (
	_get_letmeship_user_error,
	_get_pickup_interval,
	_normalize_goods_value,
	_parse_json_list,
)
from shipment.shipment.doctype.shipment.shipment import (
	Shipment,
	_get_delivery_note_names,
	make_shipment_from_delivery_note,
	update_delivery_note,
	update_tracking_info,
)


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

	def test_parses_notification_email_request_arguments(self):
		"""Decode notification recipients serialized by frappe.call."""
		self.assertEqual(
			_parse_json_list('["shipping@example.com", "tracking@example.com"]'),
			["shipping@example.com", "tracking@example.com"],
		)
		self.assertEqual(_parse_json_list("[]"), [])

	def test_moves_expired_pickup_window_to_tomorrow(self):
		"""Avoid sending a pickup interval whose start is after its end."""
		interval = _get_pickup_interval(
			"2026-08-05",
			"09:00",
			"15:30",
			True,
			now=datetime(2026, 8, 5, 15, 29),
		)

		self.assertEqual(
			interval,
			{"date": "2026-08-06", "timeFrom": "09:00:00", "timeTo": "15:30:00"},
		)

	def test_rounds_active_pickup_window_forward(self):
		"""Use the next half-hour while today's pickup window remains open."""
		interval = _get_pickup_interval(
			"2026-08-05",
			"09:00",
			"15:30",
			True,
			now=datetime(2026, 8, 5, 10, 5),
		)

		self.assertEqual(
			interval,
			{"date": "2026-08-05", "timeFrom": "10:30:00", "timeTo": "15:30:00"},
		)

	@patch("shipment.api.let_me_ship.now_datetime", return_value=datetime(2026, 8, 5, 10, 45))
	def test_uses_frappe_system_timezone_for_pickup_window(self, system_now):
		"""Calculate the pickup window from Frappe's configured local time."""
		interval = _get_pickup_interval("2026-08-05", "09:00", "17:00", True)

		self.assertEqual(
			interval,
			{"date": "2026-08-05", "timeFrom": "11:00:00", "timeTo": "17:00:00"},
		)
		system_now.assert_called_once_with()

	def test_parses_all_delivery_note_argument_shapes(self):
		"""Return every linked Delivery Note without duplicates."""
		rows = [
			_dict(delivery_note="DN-1"),
			{"delivery_note": "DN-2"},
			_dict(delivery_note="DN-1"),
		]
		self.assertEqual(_get_delivery_note_names(rows), ["DN-1", "DN-2"])
		self.assertEqual(_get_delivery_note_names('["DN-1", "DN-2"]'), ["DN-1", "DN-2"])

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_doc")
	def test_updates_every_linked_delivery_note(self, get_doc):
		"""Write booking and tracking details to every linked Delivery Note."""
		delivery_notes = {"DN-1": Mock(), "DN-2": Mock()}
		get_doc.side_effect = lambda doctype, name: delivery_notes[name]

		update_delivery_note(
			'["DN-1", "DN-2"]',
			shipment_info={
				"carrier": "UPS",
				"carrier_service": "Standard Paket National",
				"awb_number": "TRACK-123",
			},
			tracking_info={
				"awb_number": "TRACK-123",
				"tracking_url": "https://tracking.example/TRACK-123",
				"tracking_status": "In Progress",
				"tracking_status_info": "IN_TRANSIT",
			},
		)

		for name, delivery_note in delivery_notes.items():
			get_doc.assert_any_call("Delivery Note", name)
			delivery_note.db_set.assert_any_call("delivery_type", "Parcel Service")
			delivery_note.db_set.assert_any_call("parcel_service", "UPS")
			delivery_note.db_set.assert_any_call(
				"parcel_service_type", "Standard Paket National"
			)
			delivery_note.db_set.assert_any_call("tracking_number", "TRACK-123")
			delivery_note.db_set.assert_any_call(
				"tracking_url", "https://tracking.example/TRACK-123"
			)
			delivery_note.db_set.assert_any_call("tracking_status", "In Progress")
			delivery_note.db_set.assert_any_call("tracking_status_info", "IN_TRANSIT")

	@patch(
		"shipment.shipment.doctype.shipment.shipment.get_address",
		return_value=_dict(address_line1="Short street"),
	)
	def test_submit_and_cancel_persist_status(self, get_address):
		"""Persist status changes made after the document database update."""
		shipment = Mock(
			shipment_parcel=[Mock()],
			value_of_goods=100,
			pickup_address_name="Pickup",
			delivery_address_name="Delivery",
		)

		Shipment.on_submit(shipment)
		Shipment.on_cancel(shipment)

		shipment.db_set.assert_any_call("status", "Submitted")
		shipment.db_set.assert_any_call("status", "Cancelled")
		self.assertEqual(get_address.call_count, 2)

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_all", return_value=[])
	def test_scheduler_retries_bookings_without_initial_tracking_data(self, get_all):
		"""Include booked Shipments whose AWB or tracking status is still empty."""
		update_tracking_info()

		filters = get_all.call_args.kwargs["filters"]
		self.assertNotIn("awb_number", filters)
		self.assertNotIn("tracking_status", filters)
		self.assertEqual(filters["status"], "Booked")
