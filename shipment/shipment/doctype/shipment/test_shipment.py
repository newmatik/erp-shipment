# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and Contributors
# See license.txt
from __future__ import unicode_literals

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from frappe import ValidationError, _dict

from shipment.api.let_me_ship import (
	_get_letmeship_user_error,
	_get_pickup_interval,
	_normalize_goods_value,
	_parse_json_list,
	_raise_letmeship_rejection,
)
from shipment.api import sendcloud
from shipment.api.utils import format_tracking_url
from shipment.shipment.doctype.shipment.shipment import (
	Shipment,
	_fit_data_value,
	_update_tracking,
	_format_contact_display,
	_get_delivery_note_names,
	cancel_dhl_shipment,
	calculate_shipping_cost,
	cancel_letmeship_shipment_after_confirmation,
	confirm_dhl_portal_checked,
	create_shipment,
	fetch_shipping_rates,
	get_holidays,
	make_shipment,
	make_shipment_from_delivery_note,
	update_delivery_note,
	refresh_dhl_tracking_status,
	update_tracking,
	update_tracking_info,
)


class OverflowingGoodsValue:
	"""Raise the conversion overflow handled by the LetMeShip boundary."""

	def __float__(self):
		raise OverflowError


class FakeShipment(_dict):
	"""Provide the child-row behavior needed by mapper unit tests."""

	def append(self, fieldname, value):
		"""Append one dictionary child row."""
		row = _dict(value)
		self.setdefault(fieldname, []).append(row)
		return row

	def set(self, fieldname, value):
		"""Set one document field for controller tests."""
		self[fieldname] = value


class TestShipment(unittest.TestCase):
	"""Verify Shipment compatibility behavior."""

	def test_exposes_standard_menu_for_permitted_delete_action(self):
		"""Keep the ordinary Delete menu available without bypassing link checks."""
		schema = json.loads(Path(__file__).with_name("shipment.json").read_text(encoding="utf-8"))
		self.assertFalse(schema.get("hide_toolbar"))

	def test_does_not_copy_previous_carrier_booking_when_duplicating(self):
		"""Require a fresh carrier booking for duplicated Shipments."""
		schema = json.loads(Path(__file__).with_name("shipment.json").read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in schema["fields"]}
		for fieldname in (
			"service_provider", "carrier", "carrier_service", "shipment_id", "awb_number", "status",
			"tracking_status", "tracking_status_info", "tracking_url", "base_price", "net_price",
			"total_vat", "shipment_amount",
		):
			with self.subTest(fieldname=fieldname):
				self.assertEqual(fields[fieldname].get("no_copy"), 1)
		for fieldname in ("shipment_delivery_notes", "shipment_parcel", "pickup_address_name"):
			self.assertFalse(fields[fieldname].get("no_copy"))

	def test_clears_previous_booking_on_amendment_without_changing_order_data(self):
		"""Make an amendment bookable while retaining its parcels and Delivery Notes."""
		shipment = FakeShipment(
			amended_from="SHIPMENT-05340", shipment_id="34115532", service_provider="LetMeShip",
			carrier="UPS", carrier_service="Standard", awb_number="previous-label",
			tracking_status="In Progress", tracking_status_info="INFO", tracking_url="previous-url",
			base_price=10, net_price=11, total_vat=2, shipment_amount=13,
			shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			shipment_parcel=[_dict(weight=2)], pickup_address_name="Sender", value_of_goods=100,
		)

		Shipment.before_insert(shipment)

		for fieldname in (
			"service_provider", "carrier", "carrier_service", "shipment_id", "awb_number",
			"tracking_status", "tracking_status_info", "tracking_url", "base_price", "net_price",
			"total_vat", "shipment_amount",
		):
			self.assertIsNone(shipment[fieldname])
		self.assertEqual(shipment.amended_from, "SHIPMENT-05340")
		self.assertEqual(shipment.shipment_delivery_notes[0].delivery_note, "DN-1")
		self.assertEqual(shipment.shipment_parcel[0].weight, 2)
		self.assertEqual(shipment.pickup_address_name, "Sender")
		self.assertEqual(shipment.value_of_goods, 100)

	def test_preserves_booking_values_for_non_amended_insert(self):
		"""Leave non-amended insert behavior unchanged."""
		shipment = FakeShipment(amended_from=None, shipment_id="imported-booking")
		Shipment.before_insert(shipment)
		self.assertEqual(shipment.shipment_id, "imported-booking")

	def test_html_display_fields_use_text_editor_controls(self):
		"""Render persisted address, contact, and tracking markup as HTML."""
		schema_path = Path(__file__).with_name("shipment.json")
		schema = json.loads(schema_path.read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in schema["fields"]}

		for fieldname in (
			"pickup_address",
			"pickup_contact",
			"delivery_address",
			"delivery_contact",
			"tracking_url",
		):
			with self.subTest(fieldname=fieldname):
				self.assertIn(fieldname, fields)
				self.assertEqual(fields[fieldname]["fieldtype"], "Text Editor")
				self.assertEqual(fields[fieldname]["read_only"], 1)

	def test_escapes_contact_values_before_rendering_markup(self):
		"""Prevent stored contact data from injecting markup."""
		value = _format_contact_display(
			'<img src=x onerror="alert(1)">',
			"service&tracking@example.com",
		)

		self.assertNotIn("<img", value)
		self.assertIn("&lt;img", value)
		self.assertIn("service&amp;tracking@example.com", value)
		self.assertEqual(value.count("<br>"), 1)

	def test_tracking_links_allow_only_escaped_http_urls(self):
		"""Reject unsafe schemes and escape provider-controlled URLs."""
		anchor = format_tracking_url(
			'https://tracking.example/parcel?value="><script>alert(1)</script>',
		)

		self.assertIn('href="https://tracking.example/', anchor)
		self.assertNotIn("<script>", anchor)
		self.assertIn("&lt;script&gt;", anchor)
		self.assertEqual(format_tracking_url("javascript:alert(1)"), "")

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

	def test_rejects_non_finite_and_overflowing_goods_values(self):
		"""Reject values that cannot be represented by LetMeShip's integer field."""
		invalid_values = (
			float("nan"),
			float("inf"),
			float("-inf"),
			OverflowingGoodsValue(),
		)

		for invalid_value in invalid_values:
			with self.subTest(value=invalid_value):
				with self.assertRaises(ValidationError):
					_normalize_goods_value(invalid_value)

	def test_returns_safe_message_for_letmeship_parse_errors(self):
		"""Hide provider implementation details from the user-facing error."""
		error = _get_letmeship_user_error(
			{"errorMessage": "Cannot deserialize value of type java.lang.Integer"}
		)

		self.assertIn("LetMeShip could not process the shipment data", error)
		self.assertNotIn("deserialize", error)

	def test_returns_top_level_letmeship_message(self):
		"""Show the reason a LetMeShip 4xx gives as a top-level message."""
		self.assertEqual(
			_get_letmeship_user_error({"message": "Invalid phone number"}),
			"Invalid phone number",
		)
		self.assertEqual(
			_get_letmeship_user_error({"message": ["<b>zip</b> invalid", "phone missing"]}),
			"&lt;b&gt;zip&lt;/b&gt; invalid<br>phone missing",
		)
		self.assertEqual(
			_get_letmeship_user_error({"status": "error", "message": "Invalid phone number"}),
			"Invalid phone number",
		)

	@patch("shipment.api.let_me_ship.frappe.throw", side_effect=ValidationError)
	@patch("shipment.api.let_me_ship.frappe.log_error")
	def test_logs_letmeship_rejection_reason(self, log_error, throw):
		"""Show a rejected booking's reason but log none of its echoed data (ERPNEXT-1C)."""
		body = json.dumps({
			"message": "Invalid phone number +49 621 555 0100 for Musterstrasse 12",
			"deliveryInfo": {"email": "buyer@example.com", "street": "Musterstrasse 12"},
		})
		response = Mock(status_code=400, text=body, reason="", headers={})

		with self.assertRaises(ValidationError):
			_raise_letmeship_rejection(response)

		self.assertIn("Invalid phone number", throw.call_args.args[0])
		logged = log_error.call_args.kwargs["message"]
		self.assertIn("body_keys=['deliveryInfo', 'message']", logged)
		for echoed in ("buyer@example.com", "555 0100", "Musterstrasse"):
			self.assertNotIn(echoed, logged)

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

	def test_parses_single_delivery_note_dict_as_one_row(self):
		"""Treat one dictionary argument as one Delivery Note child row."""
		self.assertEqual(_get_delivery_note_names({"delivery_note": "DN-1"}), ["DN-1"])

	def _book_shipment(self, shipment_doc, provider_result=None, permission_error=False):
		"""Call create_shipment with a mocked Shipment and LetMeShip; return the call log.

		The log is also kept on self.booking_calls (and the mocked frappe.db on
		self.booking_db) so callers can inspect it after an expected exception.
		"""
		calls = self.booking_calls = []
		module = "shipment.shipment.doctype.shipment.shipment"

		def get_doc(*args, **kwargs):
			calls.append(("get_doc", args, kwargs))
			return shipment_doc

		def check_permission(ptype):
			calls.append(("check_permission", ptype))
			if permission_error:
				raise frappe.PermissionError

		def provider(**kwargs):
			calls.append(("provider", kwargs["shipment"]))
			return provider_result

		shipment_doc.check_permission.side_effect = check_permission
		db = self.booking_db = Mock()
		with (
			patch(f"{module}.frappe.get_doc", side_effect=get_doc),
			patch(f"{module}.create_letmeship_shipment", side_effect=provider),
			patch(f"{module}.frappe.throw", side_effect=ValidationError),
			patch(f"{module}.frappe.db", db),
			patch(f"{module}.frappe.msgprint"),
		):
			create_shipment(
				shipment="SHIPMENT-1",
				pickup_from_type="Company",
				delivery_to_type="Customer",
				pickup_address_name="ADDR-1",
				delivery_address_name="ADDR-2",
				shipment_parcel="[]",
				description_of_content="Parts",
				pickup_date="2026-08-05",
				value_of_goods=100,
				service_data=json.dumps({"service_provider": "LetMeShip"}),
				shipment_notific_email=0,
				tracking_notific_email=0,
			)
		return calls

	def test_locks_and_checks_shipment_before_booking_with_carrier(self):
		"""Lock the row and check write permission before any carrier side effect."""
		shipment_doc = Mock(docstatus=1, shipment_id=None, shipment_delivery_notes=[])
		calls = self._book_shipment(
			shipment_doc, provider_result={"shipment_id": "LMS-1", "service_provider": "LetMeShip"}
		)

		self.assertEqual(
			calls,
			[
				("get_doc", ("Shipment", "SHIPMENT-1"), {"for_update": True}),
				("check_permission", "write"),
				("provider", "SHIPMENT-1"),
			],
		)
		self.booking_db.set_value.assert_called_once()
		self.assertEqual(self.booking_db.set_value.call_args.args[2]["status"], "Booked")

	def test_refuses_booking_before_any_carrier_side_effect(self):
		"""Stop draft, already-booked and unauthorized bookings before the carrier call.

		The error type is asserted exactly: ValidationError is not a PermissionError
		subclass, so moving check_permission after the guards fails the no-write cases.
		"""
		cases = (
			("draft", Mock(docstatus=0, shipment_id=None, shipment_delivery_notes=[]), False, ValidationError),
			("booked", Mock(docstatus=1, shipment_id="LMS-1", shipment_delivery_notes=[]), False, ValidationError),
			("no write", Mock(docstatus=1, shipment_id=None, shipment_delivery_notes=[]), True, frappe.PermissionError),
			# The permission check runs before the state guards, so a user without
			# write access learns nothing about whether a Shipment is draft or booked.
			("no write, draft", Mock(docstatus=0, shipment_id=None, shipment_delivery_notes=[]), True, frappe.PermissionError),
			("no write, booked", Mock(docstatus=1, shipment_id="LMS-1", shipment_delivery_notes=[]), True, frappe.PermissionError),
		)
		for label, shipment_doc, permission_error, error in cases:
			with self.subTest(label):
				with self.assertRaises(error):
					self._book_shipment(shipment_doc, permission_error=permission_error)
				self.assertNotIn("provider", [call[0] for call in self.booking_calls])
				self.booking_db.set_value.assert_not_called()

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

	def test_submit_and_cancel_persist_status(self):
		"""Persist status changes made after the document database update."""
		shipment = Mock(
			shipment_parcel=[Mock()],
			value_of_goods=100,
		)

		Shipment.on_submit(shipment)
		Shipment.on_cancel(shipment)

		shipment.db_set.assert_any_call("status", "Submitted")
		shipment.db_set.assert_any_call("status", "Cancelled")

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.throw", side_effect=ValidationError)
	def test_booked_letmeship_cannot_be_cancelled_without_confirmation(self, throw):
		"""Prevent an ERP cancellation that leaves a carrier booking active."""
		shipment = FakeShipment(
			name="SHIPMENT-1", service_provider="LetMeShip", shipment_id="34117364", flags=_dict()
		)
		with self.assertRaises(ValidationError):
			Shipment.before_cancel(shipment)
		self.assertIn("LetMeShip booking 34117364", throw.call_args.args[0])

		shipment.flags.carrier_cancellation_confirmed = True
		Shipment.before_cancel(shipment)

	def test_unbooked_and_other_provider_cancellation_remain_available(self):
		"""Keep ordinary cancellation for Shipments with no LetMeShip booking."""
		for provider, booking in (("LetMeShip", None), ("Packlink", "PK-1"), ("SendCloud", "SC-1")):
			with self.subTest(provider=provider, booking=booking):
				shipment = FakeShipment(service_provider=provider, shipment_id=booking, flags=_dict())
				Shipment.before_cancel(shipment)

	def test_confirmation_override_is_post_only(self):
		"""Keep the state-changing support route unavailable to HTTP GET requests."""
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[cancel_letmeship_shipment_after_confirmation],
			["POST"],
		)

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_doc")
	@patch("shipment.shipment.doctype.shipment.shipment.frappe.only_for")
	@patch("shipment.shipment.doctype.shipment.shipment.frappe.throw", side_effect=ValidationError)
	def test_confirmation_requires_evidence_attached_to_same_shipment(self, throw, only_for, get_doc):
		"""Reject an unrelated file as carrier cancellation evidence."""
		shipment = Mock(name="SHIPMENT-1", docstatus=1, service_provider="LetMeShip", shipment_id="34117364")
		shipment.name = "SHIPMENT-1"
		evidence = Mock(attached_to_doctype="Shipment", attached_to_name="SHIPMENT-OTHER", file_url="/private/files/proof.pdf")
		get_doc.side_effect = [shipment, evidence]
		with self.assertRaises(ValidationError):
			cancel_letmeship_shipment_after_confirmation("SHIPMENT-1", "FILE-1")
		only_for.assert_called_once_with("System Manager")
		self.assertIn("Attach the LetMeShip cancellation confirmation", throw.call_args.args[0])
		shipment.cancel.assert_not_called()

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_doc")
	@patch("shipment.shipment.doctype.shipment.shipment.frappe.only_for", side_effect=frappe.PermissionError)
	def test_non_manager_cannot_use_confirmation_override(self, only_for, get_doc):
		"""Stop before reading a Shipment when the caller lacks System Manager."""
		with self.assertRaises(frappe.PermissionError):
			cancel_letmeship_shipment_after_confirmation("SHIPMENT-1", "FILE-1")
		only_for.assert_called_once_with("System Manager")
		get_doc.assert_not_called()

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_doc")
	@patch("shipment.shipment.doctype.shipment.shipment.frappe.only_for")
	def test_manager_confirmation_cancels_and_audits_booking(self, only_for, get_doc):
		"""Cancel only in the evidence-backed manager path and record its actor."""
		shipment = Mock(docstatus=1, service_provider="LetMeShip", shipment_id="34117364", status="Cancelled")
		shipment.name = "SHIPMENT-1"
		shipment.flags = _dict()
		evidence = Mock(attached_to_doctype="Shipment", attached_to_name="SHIPMENT-1", file_url="/private/files/proof.pdf")
		evidence.name = "FILE-1"
		get_doc.side_effect = [shipment, evidence]
		with patch.dict("frappe.__dict__", {"session": _dict(user="support@example.test")}):
			result = cancel_letmeship_shipment_after_confirmation("SHIPMENT-1", "FILE-1")

		only_for.assert_called_once_with("System Manager")
		shipment.check_permission.assert_called_once_with("cancel")
		self.assertTrue(shipment.flags.carrier_cancellation_confirmed)
		shipment.cancel.assert_called_once_with()
		self.assertIn("34117364", shipment.add_comment.call_args.args[1])
		self.assertIn("FILE-1", shipment.add_comment.call_args.args[1])
		self.assertEqual(result, {"shipment": "SHIPMENT-1", "status": "Cancelled"})

	@patch(
		"shipment.shipment.doctype.shipment.shipment.get_address",
		create=True,
		return_value=_dict(address_line1="A" * 36),
	)
	def test_submit_defers_address_limits_to_service_provider(self, get_address):
		"""Let the selected service provider normalize long address lines."""
		shipment = Mock(shipment_parcel=[Mock()], value_of_goods=100)

		Shipment.on_submit(shipment)

		shipment.db_set.assert_called_once_with("status", "Submitted")
		get_address.assert_not_called()

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_all", return_value=[])
	def test_scheduler_retries_bookings_without_initial_tracking_data(self, get_all):
		"""Include booked Shipments whose AWB or tracking status is still empty."""
		update_tracking_info()

		filters = get_all.call_args.kwargs["filters"]
		self.assertNotIn("awb_number", filters)
		self.assertNotIn("tracking_status", filters)
		self.assertEqual(filters["status"], "Booked")

	def test_holidays_accept_boolean_and_json_weekend_flags(self):
		"""Decode only form-encoded flags; Python callers may rely on the bool default."""
		sql = Mock(return_value=[])
		module = "shipment.shipment.doctype.shipment.shipment.frappe"
		with (
			patch(f"{module}.db", Mock(sql=sql)),
			patch(f"{module}.get_cached_value", Mock(return_value="HL")),
		):
			get_holidays()
			self.assertEqual(sql.call_args.args[1]["exclude_weekend"], 1)

			get_holidays(exclude_weekend="false")
			self.assertEqual(sql.call_args.args[1]["exclude_weekend"], 0)

	def test_make_shipment_accepts_mobile_only_contacts(self):
		"""Accept a mobile number wherever the message allows Phone/Mobile."""
		get_value = Mock(side_effect=[
			_dict(first_name="Ada", last_name="Lovelace", email_id="ada@example.com", phone=None, mobile_no="+49 1"),
			_dict(full_name="Packer", email="packer@example.com", phone=None, mobile_no="+49 2"),
		])
		new_doc = Mock(side_effect=RuntimeError("stop"))
		module = "shipment.shipment.doctype.shipment.shipment.frappe"
		with (
			patch(f"{module}.db", Mock(get_value=get_value)),
			patch(f"{module}.session", _dict(user="packer@example.com")),
			patch(f"{module}.new_doc", new_doc),
			self.assertRaisesRegex(RuntimeError, "stop"),
		):
			make_shipment("Co", "Cust", "Addr", "", "Contact", "PAddr", "", "DN-1", 10, "false")
		new_doc.assert_called_once_with("Shipment")

	@patch("shipment.api.sendcloud.frappe.db", Mock(get_value=Mock(return_value=("key", "secret"))))
	@patch("shipment.api.sendcloud.requests.get")
	def test_sendcloud_tracking_maps_status_to_select_values(self, get):
		"""Write a valid Select value and keep the carrier wording in the info field."""
		def parcel(status_id, message):
			return Mock(text=json.dumps({"parcel": {
				"tracking_url": "https://tracking.example/1",
				"tracking_number": "T{}".format(status_id),
				"status": {"id": status_id, "message": message},
			}}))

		get.side_effect = [parcel(11, "Delivered"), parcel(3, "En route to sorting center")]
		data = sendcloud.get_sendcloud_tracking_data("1, 2")
		self.assertEqual(data["tracking_status"], "In Progress")
		self.assertEqual(data["tracking_status_info"], "Delivered, En route to sorting center")

		get.side_effect = [parcel(11, "Delivered")]
		self.assertEqual(sendcloud.get_sendcloud_tracking_data("1")["tracking_status"], "Delivered")

		get.side_effect = [parcel(11, "Delivered"), parcel(93, "Shipment collected by customer")]
		self.assertEqual(sendcloud.get_sendcloud_tracking_data("1, 2")["tracking_status"], "Delivered")

	@patch("shipment.api.sendcloud.frappe.msgprint")
	@patch("shipment.api.sendcloud.get_company_contact", return_value=_dict(first_name="A", last_name="B", phone="1", email="a@example.com"))
	@patch("shipment.api.sendcloud.get_address", return_value=_dict(country_code="DE", address_title="Co", address_line1="Street 1", address_line2="", city="City", pincode="1"))
	@patch("shipment.api.sendcloud.frappe.db", Mock(get_value=Mock(return_value=("key", "secret"))))
	@patch("shipment.api.sendcloud.requests.post")
	def test_sendcloud_keeps_created_parcels_of_a_partially_failed_batch(self, post, get_address, get_contact, msgprint):
		"""Record parcels SendCloud created even when others in the batch failed."""
		service = {"service_id": 8, "carrier": "dhl", "service_name": "DHL", "total_price": 12}
		parcels = json.dumps([{"count": 1, "weight": 1}, {"count": 2, "weight": 2}])

		post.return_value = Mock(text=json.dumps({
			"parcels": [{"id": 1, "tracking_number": "T1", "order_number": "SHIP-1-2"}],
			"failed_parcels": [{"errors": {"weight": ["too heavy"]}}],
		}))
		result = sendcloud.create_sendcloud_shipment("SHIP-1", "Company", "Addr", None, service, parcels, "Goods", 10)
		self.assertEqual(result["shipment_id"], "1")
		# Row 2 (2 of 3 units) was created, so only its share of the price is booked.
		self.assertEqual(result["shipment_amount"], 8)
		msgprint.assert_called_once()
		self.assertEqual(msgprint.call_args.kwargs.get("indicator"), "red")
		self.assertNotIn("alert", msgprint.call_args.kwargs)

		# Without echoed order numbers the amount falls back to the parcel ratio.
		post.return_value = Mock(text=json.dumps({
			"parcels": [{"id": 1, "tracking_number": "T1"}],
			"failed_parcels": [{"errors": "bad"}],
		}))
		result = sendcloud.create_sendcloud_shipment("SHIP-1", "Company", "Addr", None, service, parcels, "Goods", 10)
		self.assertEqual(result["shipment_amount"], 6)

		# A fully successful batch keeps the quoted price.
		post.return_value = Mock(text=json.dumps({"parcels": [
			{"id": 1, "tracking_number": "T1", "order_number": "SHIP-1-1"},
			{"id": 2, "tracking_number": "T2", "order_number": "SHIP-1-2"},
		]}))
		result = sendcloud.create_sendcloud_shipment("SHIP-1", "Company", "Addr", None, service, parcels, "Goods", 10)
		self.assertEqual(result["shipment_amount"], 12)

		post.return_value = Mock(text=json.dumps({"parcels": [], "failed_parcels": [{"errors": "bad"}]}))
		self.assertEqual(
			sendcloud.create_sendcloud_shipment("SHIP-1", "Company", "Addr", None, service, parcels, "Goods", 10), {}
		)

	@patch("shipment.api.sendcloud.frappe.msgprint")
	@patch("shipment.api.sendcloud.frappe.db", Mock(get_value=Mock(side_effect=Exception("offline"))))
	def test_sendcloud_rates_return_a_list_on_failure(self, msgprint):
		"""Keep fetch_shipping_rates able to concatenate provider results."""
		self.assertEqual(sendcloud.get_sendcloud_available_services("Addr", "[]"), [])

	def test_shipment_form_contact_rules_use_real_fields(self):
		"""Guard the refresh contact rules and notification cleanup against phantom fields."""
		script = Path(__file__).with_name("shipment.js").read_text(encoding="utf-8")
		self.assertNotIn("delivery_from_type", script)
		self.assertNotIn("frm.doc.pickup_email", script)
		self.assertNotIn("frm.doc.delivery_email", script)
		self.assertNotIn("contact_display += '<br>'", script)
		self.assertIn("frappe.utils.escape_html(String(value))", script)
		self.assertNotIn("add_days(frappe.datetime.get_today()))", script)
		self.assertIn("next_working_day(frm.doc.pickup_date)", script)


class TestShipmentDHL(unittest.TestCase):
	"""Verify how the Shipment controller routes and guards DHL bookings."""

	module = "shipment.shipment.doctype.shipment.shipment"

	def setUp(self):
		"""Timestamps need a site; fix them for the recorded booking data."""
		timestamp = patch("shipment.api.dhl.now", return_value="2026-10-24 10:00:00")
		timestamp.start()
		self.addCleanup(timestamp.stop)

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.throw", side_effect=ValidationError)
	def test_booked_dhl_shipment_is_cancelled_through_dhl(self, throw):
		"""Block the plain Cancel while DHL labels exist; allow it after DHL deleted them."""
		shipment = FakeShipment(name="SHIPMENT-1", service_provider="DHL", shipment_id="A1, B2", flags=_dict())
		with self.assertRaises(ValidationError):
			Shipment.before_cancel(shipment)
		self.assertIn("Cancel DHL Booking", throw.call_args.args[0])

		shipment.flags.carrier_cancellation_confirmed = True
		Shipment.before_cancel(shipment)

	def test_dhl_cancellation_is_post_only(self):
		"""Keep the state-changing DHL cancellation unavailable to HTTP GET requests."""
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[cancel_dhl_shipment], ["POST"])

	def _dhl_shipment(self, **values):
		"""A booked DHL Shipment whose db_set writes through to the fake document."""
		shipment = FakeShipment(
			docstatus=1, service_provider="DHL", shipment_id="A1, B2", status="Booked", flags=_dict(),
			carrier_booking_data=json.dumps({"environment": "Sandbox", "parcels": {"A1": 4.1, "B2": 4.1}}),
			name="SHIPMENT-1", cancel=Mock(), add_comment=Mock(), check_permission=Mock(),
		)
		shipment.update(values)
		shipment.db_set = Mock(side_effect=lambda field, value=None: shipment.update(
			field if isinstance(field, dict) else {field: value}))
		return shipment

	def test_cancels_shipment_after_dhl_deleted_every_label(self):
		"""Record and commit the DHL deletion, then cancel and audit."""
		shipment = self._dhl_shipment()
		booking = shipment.carrier_booking_data
		events = []
		shipment.cancel.side_effect = lambda: events.append("cancel")
		db = Mock(commit=Mock(side_effect=lambda: events.append("commit")))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment) as get_doc,
			patch(f"{self.module}.cancel_dhl_booking", return_value=(["A1", "B2"], [])) as cancel_booking,
			patch(f"{self.module}.frappe.db", db),
			patch.dict("frappe.__dict__", {"session": _dict(user="packer@example.test")}),
		):
			result = cancel_dhl_shipment("SHIPMENT-1")

		get_doc.assert_called_once_with("Shipment", "SHIPMENT-1", for_update=True)
		shipment.check_permission.assert_called_once_with("cancel")
		cancel_booking.assert_called_once_with("A1, B2", booking)
		self.assertEqual(events, ["commit", "cancel"])
		self.assertEqual(json.loads(shipment.carrier_booking_data)["deleted_at_dhl"], ["A1", "B2"])
		self.assertTrue(shipment.flags.carrier_cancellation_confirmed)
		self.assertIn("A1, B2", shipment.add_comment.call_args.args[1])
		self.assertEqual(result["deleted"], ["A1", "B2"])

	def test_second_click_cancels_without_calling_dhl_after_a_failed_cancellation(self):
		"""Keep the recorded DHL deletion when the ERP cancellation fails, and finish it later."""
		shipment = self._dhl_shipment()
		shipment.cancel.side_effect = frappe.LinkExistsError("linked")
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.cancel_dhl_booking", return_value=(["A1", "B2"], [])),
			patch(f"{self.module}.frappe.db", Mock()) as db,
			patch.dict("frappe.__dict__", {"session": _dict(user="packer@example.test")}),
			self.assertRaises(frappe.LinkExistsError),
		):
			cancel_dhl_shipment("SHIPMENT-1")
		db.commit.assert_called_once_with()
		self.assertEqual(json.loads(shipment.carrier_booking_data)["deleted_at_dhl"], ["A1", "B2"])

		shipment.cancel.reset_mock(side_effect=True)
		shipment.flags = _dict()
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.cancel_dhl_booking") as cancel_booking,
			patch(f"{self.module}.frappe.db", Mock()) as db,
			patch.dict("frappe.__dict__", {"session": _dict(user="packer@example.test")}),
		):
			result = cancel_dhl_shipment("SHIPMENT-1")
		cancel_booking.assert_not_called()
		db.commit.assert_not_called()
		shipment.cancel.assert_called_once_with()
		self.assertEqual(result["deleted"], ["A1", "B2"])

		# The plain Cancel is no longer blocked once DHL confirmed every deletion.
		shipment.flags = _dict()
		Shipment.before_cancel(shipment)

	@patch("shipment.api.dhl.frappe.throw", side_effect=ValidationError)
	def test_uncertain_dhl_booking_blocks_rebooking_and_cancel_until_confirmed(self, throw):
		"""Refuse a second booking or a cancellation until the DHL portal was checked."""
		record = json.dumps({"environment": "Production", "uncertain_booking": {"at": "2026-10-24 10:00:00", "reference": "SHIPMENT-1"}})
		shipment_doc = FakeShipment(docstatus=1, shipment_id=None, shipment_delivery_notes=[],
			carrier_booking_data=record, check_permission=Mock(), flags=_dict(), name="SHIPMENT-1")
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment_doc),
			patch(f"{self.module}.create_dhl_shipment") as create_dhl,
			patch(f"{self.module}.create_letmeship_shipment") as create_letmeship,
			self.assertRaises(ValidationError),
		):
			create_shipment(
				shipment="SHIPMENT-1", pickup_from_type="Company", delivery_to_type="Customer",
				pickup_address_name="ADDR-1", delivery_address_name="ADDR-2", shipment_parcel="[]",
				description_of_content="Parts", pickup_date="2026-10-26", value_of_goods=100,
				service_data=json.dumps({"service_provider": "LetMeShip"}),
				shipment_notific_email=0, tracking_notific_email=0,
			)
		create_dhl.assert_not_called()
		create_letmeship.assert_not_called()
		self.assertIn("Geschäftskundenportal", throw.call_args.args[0])
		self.assertIn("SHIPMENT-1", throw.call_args.args[0])
		with self.assertRaises(ValidationError):
			Shipment.before_cancel(shipment_doc)

	def test_confirming_the_portal_check_releases_the_shipment(self):
		"""Clear the uncertain-booking record only through an explicit, audited confirmation."""
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[confirm_dhl_portal_checked], ["POST"])
		record = json.dumps({"uncertain_booking": {"at": "2026-10-24 10:00:00", "reference": "SHIPMENT-1"}})
		shipment = self._dhl_shipment(shipment_id=None, carrier_booking_data=record)
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch.dict("frappe.__dict__", {"session": _dict(user="packer@example.test")}),
		):
			confirm_dhl_portal_checked("SHIPMENT-1")
		shipment.check_permission.assert_called_once_with("write")
		self.assertIsNone(shipment.carrier_booking_data)
		self.assertIn("packer@example.test", shipment.add_comment.call_args.args[1])

		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.frappe.throw", side_effect=ValidationError),
			self.assertRaises(ValidationError),
		):
			confirm_dhl_portal_checked("SHIPMENT-1")

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.get_all")
	def test_tracking_job_commits_each_shipment_and_rolls_back_failures(self, get_all):
		"""Release each Shipment's row lock before the next carrier call; discard a failed one."""
		get_all.return_value = [_dict(name="SHIPMENT-1"), _dict(name="SHIPMENT-2")]
		docs = {
			"SHIPMENT-1": FakeShipment(service_provider="DHL", shipment_id="A1", tracking_status="In Progress",
				shipment_delivery_notes=[], db_set=Mock()),
			"SHIPMENT-2": FakeShipment(service_provider="DHL", shipment_id="B2", tracking_status="In Progress",
				shipment_delivery_notes=[], db_set=Mock()),
		}
		events = []
		db = Mock(commit=Mock(side_effect=lambda: events.append("commit")),
			rollback=Mock(side_effect=lambda: events.append("rollback")))

		def track(provider, shipment_id, name, delivery_notes, **kwargs):
			events.append("track " + name)
			if name == "SHIPMENT-2":
				raise RuntimeError("carrier down")
			return {"awb_number": shipment_id}

		with (
			patch(f"{self.module}.frappe.get_doc", side_effect=lambda doctype, name: docs[name]),
			patch(f"{self.module}._update_tracking", side_effect=track),
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.frappe.log_error", side_effect=lambda **kwargs: events.append("log")),
			patch(f"{self.module}.frappe.get_traceback", return_value=""),
		):
			update_tracking_info()
		self.assertEqual(events, ["track SHIPMENT-1", "commit", "track SHIPMENT-2", "rollback", "log", "commit"])

	def test_keeps_shipment_booked_when_dhl_refuses_a_deletion(self):
		"""Record what DHL deleted, keep the rest booked and show DHL's reason."""
		booking = json.dumps({"environment": "Sandbox", "parcels": {"A1": 4.1, "B2": 5.2}})
		shipment = FakeShipment(
			docstatus=1, service_provider="DHL", shipment_id="A1, B2", status="Booked", flags=_dict(),
			carrier_booking_data=booking, shipment_amount=9.3, name="SHIPMENT-1",
			shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			cancel=Mock(), db_set=Mock(), add_comment=Mock(), check_permission=Mock(),
		)
		db = Mock()
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.cancel_dhl_booking", return_value=(["A1"], ["Parcel 2: <already manifested>"])),
			patch(f"{self.module}.frappe.msgprint") as msgprint,
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.update_delivery_note") as update_dn,
			patch.dict("frappe.__dict__", {"session": _dict(user="packer@example.test")}),
		):
			cancel_dhl_shipment("SHIPMENT-1")

		shipment.cancel.assert_not_called()
		values = shipment.db_set.call_args.args[0]
		self.assertEqual((values["shipment_id"], values["awb_number"]), ("B2", "B2"))
		# The remaining label's own price, not the price of both labels.
		self.assertEqual((values["base_price"], values["net_price"], values["shipment_amount"]), (5.2, 5.2, 5.2))
		self.assertEqual(json.loads(values["carrier_booking_data"]), {"environment": "Sandbox", "parcels": {"B2": 5.2}})
		# Links and Delivery Notes follow the remaining label only.
		self.assertIn("piececode=B2", values["tracking_url"])
		self.assertNotIn("piececode=A1", values["tracking_url"])
		update_dn.assert_called_once()
		self.assertEqual(update_dn.call_args.kwargs["delivery_notes"], ["DN-1"])
		self.assertEqual(update_dn.call_args.kwargs["tracking_info"]["awb_number"], "B2")
		db.savepoint.assert_called_once_with("dhl_partial_cancel_delivery_notes")
		self.assertIn("&lt;already manifested&gt;", msgprint.call_args.args[0])
		self.assertEqual(msgprint.call_args.kwargs["indicator"], "red")

	@patch("shipment.shipment.doctype.shipment.shipment.frappe.throw", side_effect=ValidationError)
	def test_dhl_cancellation_requires_a_booked_dhl_shipment(self, throw):
		"""Refuse other providers before any DHL call."""
		shipment = Mock(docstatus=1, service_provider="LetMeShip", shipment_id="1")
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.cancel_dhl_booking") as cancel_booking,
			self.assertRaises(ValidationError),
		):
			cancel_dhl_shipment("SHIPMENT-1")
		cancel_booking.assert_not_called()

	def test_routes_dhl_booking_and_marks_shipment_booked(self):
		"""Book DHL through create_dhl_shipment after the shared state guards."""
		shipment_doc = Mock(docstatus=1, shipment_id=None, shipment_delivery_notes=[])
		booking = {"service_provider": "DHL", "shipment_id": "A1", "awb_number": "A1", "carrier": "DHL",
			"carrier_service": "DHL Paket", "shipment_amount": 4.1}
		db = Mock()
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment_doc),
			patch(f"{self.module}.create_dhl_shipment", return_value=booking) as create_dhl,
			patch(f"{self.module}.create_letmeship_shipment") as create_letmeship,
			patch(f"{self.module}.frappe.db", db),
		):
			result = create_shipment(
				shipment="SHIPMENT-1", pickup_from_type="Company", delivery_to_type="Customer",
				pickup_address_name="ADDR-1", delivery_address_name="ADDR-2", shipment_parcel="[]",
				description_of_content="Parts", pickup_date="2026-10-26", value_of_goods=100,
				service_data=json.dumps({"service_provider": "DHL", "product": "V01PAK"}),
				shipment_notific_email=0, tracking_notific_email=0, delivery_contact_name="C-1",
			)

		self.assertEqual(result, booking)
		create_letmeship.assert_not_called()
		self.assertEqual(create_dhl.call_args.kwargs["service_info"]["product"], "V01PAK")
		self.assertEqual(create_dhl.call_args.kwargs["delivery_contact_name"], "C-1")
		values = db.set_value.call_args.args[2]
		self.assertEqual((values["service_provider"], values["shipment_id"], values["status"]), ("DHL", "A1", "Booked"))

	def test_refuses_dhl_booking_while_provider_is_disabled(self):
		"""Create no live DHL label from a rate dialog opened before DHL was disabled."""
		shipment_doc = Mock(docstatus=1, shipment_id=None, shipment_delivery_notes=[])
		db = Mock(get_value=Mock(return_value=0))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment_doc),
			patch(f"{self.module}.create_dhl_shipment") as create_dhl,
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.frappe.throw", side_effect=ValidationError),
			self.assertRaises(ValidationError),
		):
			create_shipment(
				shipment="SHIPMENT-1", pickup_from_type="Company", delivery_to_type="Customer",
				pickup_address_name="ADDR-1", delivery_address_name="ADDR-2", shipment_parcel="[]",
				description_of_content="Parts", pickup_date="2026-10-26", value_of_goods=100,
				service_data=json.dumps({"service_provider": "DHL", "product": "V01PAK"}),
				shipment_notific_email=0, tracking_notific_email=0,
			)
		create_dhl.assert_not_called()
		db.get_value.assert_called_once_with("Shipment Service Provider", "DHL", "enabled")

	def test_keeps_the_booking_when_delivery_notes_cannot_be_updated(self):
		"""Persist a carrier booking even if a linked Delivery Note write fails."""
		shipment_doc = Mock(docstatus=1, shipment_id=None, shipment_delivery_notes=[_dict(delivery_note="DN-1")])
		booking = {"service_provider": "DHL", "shipment_id": "A1", "awb_number": "A1", "carrier": "DHL"}
		db = Mock()
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment_doc),
			patch(f"{self.module}.create_dhl_shipment", return_value=booking),
			patch(f"{self.module}.update_delivery_note", side_effect=Exception("Data too long")),
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.frappe.log_error"),
			patch(f"{self.module}.frappe.get_traceback", return_value=""),
			patch(f"{self.module}.frappe.msgprint") as msgprint,
		):
			result = create_shipment(
				shipment="SHIPMENT-1", pickup_from_type="Company", delivery_to_type="Customer",
				pickup_address_name="ADDR-1", delivery_address_name="ADDR-2", shipment_parcel="[]",
				description_of_content="Parts", pickup_date="2026-10-26", value_of_goods=100,
				service_data=json.dumps({"service_provider": "DHL", "product": "V01PAK"}),
				shipment_notific_email=0, tracking_notific_email=0,
			)
		self.assertEqual(result, booking)
		self.assertEqual(db.set_value.call_args.args[2]["shipment_id"], "A1")
		db.rollback.assert_called_once_with(save_point="shipment_delivery_note_update")
		self.assertEqual(msgprint.call_args.kwargs["indicator"], "orange")

	def test_booking_numbers_fit_their_fields(self):
		"""Store up to 30 DHL numbers on the Shipment; shorten them for Delivery Note Data fields."""
		schema = json.loads(Path(__file__).with_name("shipment.json").read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in schema["fields"]}
		for fieldname in ("shipment_id", "awb_number"):
			with self.subTest(fieldname=fieldname):
				self.assertIn(fields[fieldname]["fieldtype"], ("Small Text", "Text", "Long Text"))
				self.assertEqual(fields[fieldname]["read_only"], 1)

		numbers = ", ".join("00340434161094{0:06d}".format(i) for i in range(30))
		self.assertGreater(len(numbers), 140)
		fitted = _fit_data_value(numbers)
		self.assertLessEqual(len(fitted), 140)
		self.assertTrue(fitted.startswith("00340434161094000000, "))
		self.assertRegex(fitted, r"\(\+\d+\)$")
		self.assertEqual(_fit_data_value("A1, B2"), "A1, B2")
		self.assertEqual(_fit_data_value(None), None)

		delivery_note = Mock()
		with patch(f"{self.module}.frappe.get_doc", return_value=delivery_note):
			update_delivery_note(["DN-1"], shipment_info={"carrier": "DHL", "carrier_service": "DHL Paket", "awb_number": numbers},
				tracking_info={"awb_number": numbers, "tracking_url": "<a>" * 100})
		for call in delivery_note.db_set.call_args_list:
			fieldname, value = call.args
			if fieldname != "tracking_url":
				self.assertLessEqual(len(value), 140, fieldname)
		delivery_note.db_set.assert_any_call("tracking_url", "<a>" * 100)

	def test_rate_list_includes_dhl_and_explains_an_empty_result(self):
		"""Merge DHL rates with the others and pass DHL's reason when nothing fits."""
		enabled = {"DHL": 1}
		db = Mock(get_value=Mock(side_effect=lambda doctype, name, field: enabled.get(name)))

		def dhl_rates(errors, **kwargs):
			return [_dict(service_provider="DHL", carrier="DHL", service_name="DHL Paket", total_price=4.1)]

		with (
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.get_dhl_available_services", side_effect=dhl_rates) as get_rates,
		):
			rates = fetch_shipping_rates("Company", "Customer", "ADDR-1", "ADDR-2", "[]", "Parts", "2026-10-26", 100)
		self.assertEqual(len(rates), 1)
		self.assertEqual(
			(rates[0]["service_provider"], rates[0]["carrier_label"], rates[0]["service_class"], rates[0]["total_price"]),
			("DHL", "DHL", "Standard", 4.1),
		)
		self.assertEqual(get_rates.call_args.kwargs["shipment_parcel"], "[]")

		def no_rates(errors, **kwargs):
			errors.append("customs data needed")
			return []

		local = _dict(response={})
		with (
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.get_dhl_available_services", side_effect=no_rates),
			patch.dict("frappe.__dict__", {"local": local}),
		):
			self.assertEqual(
				fetch_shipping_rates("Company", "Customer", "ADDR-1", "ADDR-2", "[]", "Parts", "2026-10-26", 100), [])
		self.assertIn("customs data needed", local.response["error_messages"][0])

	def test_updates_dhl_tracking(self):
		"""Route manual and scheduled tracking updates for DHL Shipments."""
		shipment = FakeShipment(
			shipment_id="A1", service_provider="DHL", shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
		)
		tracking = {"awb_number": "A1", "tracking_status": "In Progress", "tracking_status_info": "",
			"tracking_url": "<a>link</a>"}
		db = Mock(get_value=Mock(return_value=_dict(shipment_id="A1", tracking_status="In Progress", docstatus=1, status="Booked")))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking) as get_tracking,
			patch(f"{self.module}.frappe.enqueue") as enqueue,
			patch(f"{self.module}.update_delivery_note") as update_dn,
			patch(f"{self.module}.frappe.db", db),
		):
			self.assertEqual(update_tracking("DHL", "A1", "SHIPMENT-1"), tracking)
		# The desk request returns links only; DHL status polling is queued as a background job.
		get_tracking.assert_called_once_with("A1", '{"environment": "Production"}', poll=False)
		self.assertEqual(enqueue.call_args.args[0], "shipment.shipment.doctype.shipment.shipment.refresh_dhl_tracking_status")
		self.assertEqual(enqueue.call_args.kwargs["shipment"], "SHIPMENT-1")
		self.assertEqual(enqueue.call_args.kwargs["job_id"], "dhl-tracking-SHIPMENT-1")
		# Passing the Shipment restores the booking fields if the update at booking failed.
		update_dn.assert_called_once_with(delivery_notes=["DN-1"], shipment_info=shipment, tracking_info=tracking)
		self.assertEqual(db.set_value.call_args.args[2]["awb_number"], "A1")
		db.get_value.assert_called_once_with(
			"Shipment", "SHIPMENT-1", ["shipment_id", "tracking_status", "docstatus", "status"], as_dict=True, for_update=True)

	def test_background_job_polls_dhl_status(self):
		"""The queued job polls DHL (poll=True) and queues nothing further."""
		shipment = FakeShipment(
			shipment_id="A1", service_provider="DHL", shipment_delivery_notes=[],
			carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
		)
		tracking = {"awb_number": "A1", "tracking_status": "Delivered", "tracking_status_info": "Zugestellt", "tracking_url": "<a>link</a>"}
		db = Mock(get_value=Mock(side_effect=[("A1", "DHL"), _dict(shipment_id="A1", tracking_status="In Progress", docstatus=1, status="Booked")]))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking) as get_tracking,
			patch(f"{self.module}.frappe.enqueue") as enqueue,
			patch(f"{self.module}.frappe.db", db),
		):
			refresh_dhl_tracking_status("SHIPMENT-1")
		get_tracking.assert_called_once_with("A1", '{"environment": "Production"}', poll=True)
		enqueue.assert_not_called()
		self.assertEqual(db.set_value.call_args.args[2]["tracking_status"], "Delivered")

	def test_tracking_keeps_confirmed_status_without_fresh_result(self):
		"""A refresh without a fresh DHL status updates links but keeps Delivered."""
		tracking = {"awb_number": "A1", "tracking_status": None, "tracking_status_info": None, "tracking_url": "<a>link</a>"}
		for previous, expected in (("Delivered", None), ("", "In Progress")):
			shipment = FakeShipment(
				shipment_id="A1", service_provider="DHL", shipment_delivery_notes=[], tracking_status=previous,
				carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
			)
			db = Mock(get_value=Mock(return_value=_dict(shipment_id="A1", tracking_status=previous, docstatus=1, status="Booked")))
			with (
				patch(f"{self.module}.frappe.get_doc", return_value=shipment),
				patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking),
			patch(f"{self.module}.frappe.enqueue") as enqueue,
				patch(f"{self.module}.frappe.db", db),
			):
				update_tracking("DHL", "A1", "SHIPMENT-1")
			values = db.set_value.call_args.args[2]
			self.assertEqual(values["tracking_url"], "<a>link</a>")
			self.assertNotIn("tracking_status_info", values)
			self.assertEqual(values.get("tracking_status"), expected)

	def test_older_poll_does_not_undo_a_confirmed_delivery(self):
		"""A stale In Progress result never overwrites Delivered on the Shipment or its Delivery Notes."""
		shipment = FakeShipment(
			shipment_id="A1", service_provider="DHL", shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
		)
		tracking = {"awb_number": "A1", "tracking_status": "In Progress", "tracking_status_info": "Unterwegs", "tracking_url": "<a>link</a>"}
		db = Mock(get_value=Mock(return_value=_dict(shipment_id="A1", tracking_status="Delivered", docstatus=1, status="Booked")))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking),
			patch(f"{self.module}.update_delivery_note") as update_dn,
			patch(f"{self.module}.frappe.db", db),
		):
			_update_tracking("DHL", "A1", "SHIPMENT-1", poll_status=True)
		values = db.set_value.call_args.args[2]
		self.assertNotIn("tracking_status", values)
		self.assertEqual(values["tracking_url"], "<a>link</a>")
		self.assertIsNone(update_dn.call_args.kwargs["tracking_info"]["tracking_status"])

	def test_tracking_drops_results_when_booking_changed_meanwhile(self):
		"""A partial DHL cancellation during the carrier call must not get stale AWB numbers back."""
		shipment = FakeShipment(
			shipment_id="A1, B2", service_provider="DHL", shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
		)
		tracking = {"awb_number": "A1, B2", "tracking_status": "In Progress", "tracking_status_info": "",
			"tracking_url": "<a>link</a>"}
		db = Mock(get_value=Mock(return_value=_dict(shipment_id="B2", tracking_status="In Progress", docstatus=1, status="Booked")))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking),
			patch(f"{self.module}.frappe.enqueue") as enqueue,
			patch(f"{self.module}.update_delivery_note") as update_dn,
			patch(f"{self.module}.frappe.db", db),
		):
			self.assertIsNone(update_tracking("DHL", "A1, B2", "SHIPMENT-1"))
		update_dn.assert_not_called()
		db.set_value.assert_not_called()

	def test_tracking_skips_cancelled_shipments(self):
		"""A fully cancelled DHL booking keeps its shipment_id; tracking must not write to it."""
		shipment = FakeShipment(
			shipment_id="A1", service_provider="DHL", shipment_delivery_notes=[_dict(delivery_note="DN-1")],
			carrier_booking_data='{"environment": "Production"}', check_permission=Mock(),
		)
		tracking = {"awb_number": "A1", "tracking_status": "In Progress", "tracking_status_info": "x", "tracking_url": "<a>link</a>"}
		db = Mock(get_value=Mock(return_value=_dict(shipment_id="A1", tracking_status="In Progress", docstatus=2, status="Cancelled")))
		with (
			patch(f"{self.module}.frappe.get_doc", return_value=shipment),
			patch(f"{self.module}.get_dhl_tracking_data", return_value=tracking),
			patch(f"{self.module}.frappe.enqueue") as enqueue,
			patch(f"{self.module}.update_delivery_note") as update_dn,
			patch(f"{self.module}.frappe.db", db),
		):
			self.assertIsNone(update_tracking("DHL", "A1", "SHIPMENT-1"))
		update_dn.assert_not_called()
		db.set_value.assert_not_called()

	def _shipping_cost(self, persisted, client=None):
		"""Run calculate_shipping_cost with a persisted Shipment and return the Shipping item rate."""
		shipment = Mock(as_dict=Mock(return_value=persisted), check_permission=Mock())
		docs = {"Shipment": shipment, "Shipment Settings": _dict(margin_cost=2)}
		db = Mock(get_value=Mock(side_effect=["DAP", 100.0]), get_list=Mock(side_effect=lambda *args, **kwargs: []))
		with (
			patch(f"{self.module}.frappe.get_doc", side_effect=lambda doctype, *args, **kwargs: docs[doctype]) as get_doc,
			patch(f"{self.module}.frappe.db", db),
			patch(f"{self.module}.update_child_qty_rate") as update_rate,
			patch("frappe.get_system_settings", return_value=None),
		):
			calculate_shipping_cost(json.dumps(client or persisted))
		shipment.check_permission.assert_called_once_with("write")
		get_doc.assert_any_call("Shipment", "SHIPMENT-1", for_update=True)
		return json.loads(update_rate.call_args.args[1])[-1]["rate"]

	def test_shipping_cost_margin_counts_only_booked_parcels(self):
		"""Charge the per-parcel margin for the labels DHL created, not for failed parcels."""
		data = {
			"name": "SHIPMENT-1", "base_price": 4.1,
			"shipment_parcel": [{"count": 2}],
			"shipment_delivery_notes": [{"delivery_note": "DN-1", "parent": "SHIPMENT-1"}],
			"carrier_booking_data": json.dumps({"environment": "Production", "parcels": {"A1": 4.1}}),
		}
		self.assertEqual(self._shipping_cost(data), 6.1)

		# Bookings without per-label data keep using the requested parcel count.
		data.pop("carrier_booking_data")
		self.assertEqual(self._shipping_cost(data), 8.1)

	def test_shipping_cost_uses_the_persisted_shipment_not_the_form_snapshot(self):
		"""After a partial DHL cancellation the form may send the old two-label snapshot."""
		persisted = {
			"name": "SHIPMENT-1", "base_price": 5.2, "shipment_parcel": [{"count": 2}],
			"shipment_delivery_notes": [{"delivery_note": "DN-1", "parent": "SHIPMENT-1"}],
			"carrier_booking_data": json.dumps({"environment": "Production", "parcels": {"B2": 5.2}}),
		}
		stale = dict(persisted, base_price=9.3, carrier_booking_data=json.dumps({"environment": "Production", "parcels": {"A1": 4.1, "B2": 5.2}}))
		self.assertEqual(self._shipping_cost(persisted, client=stale), 7.2)
