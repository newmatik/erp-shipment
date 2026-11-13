"""The carrier interface: registry, per-provider rules, and the controller's use of it."""

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from frappe import _dict

from shipment.api import carriers
from shipment.api.carriers import (
	BEFORE_BOOKING,
	BEFORE_CANCEL,
	BEFORE_CUSTOMS_REFRESH,
	NO_CARRIER,
	Carrier,
	carrier_of,
	get_carrier,
)

CONTROLLER = Path(__file__).resolve().parents[1] / "shipment/doctype/shipment/shipment.py"
PROVIDER_NAMES = {carrier.name for carrier in carriers.carriers()}


class TestCarrierRegistry(unittest.TestCase):
	def test_registry_holds_every_provider_under_its_stored_name(self):
		# `service_provider` values on existing Shipments and the Shipment Service Provider record names.
		self.assertEqual(
			[(carrier.name, carrier.settings_name) for carrier in carriers.carriers()],
			[("LetMeShip", "Let Me Ship"), ("Packlink", "PackLink"), ("SendCloud", "SendCloud"), ("DHL", "DHL")],
		)
		for carrier in carriers.carriers():
			self.assertIs(get_carrier(carrier.name), carrier)

	def test_unknown_or_missing_provider_has_no_carrier_and_no_capabilities(self):
		for name in (None, "", "letmeship", "Unknown"):
			self.assertIsNone(get_carrier(name))
			doc = _dict(service_provider=name, shipment_id="X1")
			carrier = carrier_of(doc)
			self.assertIs(carrier, NO_CARRIER)
			self.assertFalse(carrier.cancel_needs_proof or carrier.deletes_labels_on_cancel)
			self.assertFalse(carrier.confirms_booking_later or carrier.polls_status_in_background)
			self.assertFalse(carrier.can_reconcile_cost(doc))
			# No rule blocks the ERP cancellation, and nothing is left to do at a provider.
			carrier.before_cancel(doc)
			carrier.after_cancel(doc)

	def test_every_provider_implements_the_whole_interface(self):
		for carrier in carriers.carriers():
			for method in ("rates", "book", "label", "tracking"):
				self.assertIsNot(
					getattr(type(carrier), method), getattr(Carrier, method), f"{carrier.name}.{method}"
				)

	def test_capabilities(self):
		flags = ("booked_status", "confirms_booking_later", "cancel_needs_proof",
			"deletes_labels_on_cancel", "polls_status_in_background")
		self.assertEqual(
			{carrier.name: tuple(getattr(carrier, flag) for flag in flags) for carrier in carriers.carriers()},
			{
				"LetMeShip": ("Booking Pending", True, True, False, False),
				"Packlink": ("Booked", False, False, False, False),
				"SendCloud": ("Booked", False, False, False, False),
				"DHL": ("Booked", False, False, True, True),
			},
		)


class TestCarrierRules(unittest.TestCase):
	def test_rate_offers(self):
		offers = {
			carrier.name: [
				carrier.offers_rates(pickup_from_type=pickup, recipient_only=recipient_only)
				for pickup, recipient_only in (("Company", False), ("Customer", False), ("Company", True))
			]
			for carrier in carriers.carriers()
		}
		self.assertEqual(offers, {
			# Freight on the customer's account is booked through LetMeShip only.
			"LetMeShip": [True, True, True],
			"Packlink": [True, True, False],
			# Sendcloud collects from the company's own address only.
			"SendCloud": [True, False, False],
			"DHL": [True, True, False],
		})

	def test_only_letmeship_ups_bills_the_recipient_account(self):
		billed = {
			(carrier.name, service): carrier.bills_recipient_account({"carrier": service})
			for carrier in carriers.carriers() for service in ("UPS", "DHL")
		}
		self.assertEqual([key for key, value in billed.items() if value], [("LetMeShip", "UPS")])

	def test_dhl_booking_is_refused_once_the_provider_is_disabled(self):
		dhl = get_carrier("DHL")
		with patch.object(carriers.frappe, "db", Mock(get_value=Mock(return_value=0))), \
				patch.object(carriers.frappe, "throw", side_effect=ValueError) as throw:
			with self.assertRaises(ValueError):
				dhl.ensure_bookable()
		self.assertIn("disabled", throw.call_args.args[0])
		with patch.object(carriers.frappe, "db", Mock(get_value=Mock(return_value=1))):
			dhl.ensure_bookable()
		# The aggregators are checked when their rates are fetched, as before.
		with patch.object(carriers.frappe, "db", Mock(get_value=Mock(side_effect=AssertionError))):
			for name in ("LetMeShip", "Packlink", "SendCloud"):
				get_carrier(name).ensure_bookable()

	def test_uncertain_booking_checks_per_moment(self):
		doc = _dict()
		with patch.object(carriers, "ensure_no_uncertain_letmeship_booking") as letmeship, \
				patch.object(carriers, "ensure_no_uncertain_dhl_booking") as dhl, \
				patch.object(carriers, "ensure_no_uncertain_sendcloud_booking") as sendcloud, \
				patch.object(carriers, "release_absent_sendcloud_booking", return_value=False) as release:
			calls = {}
			for before in (BEFORE_BOOKING, BEFORE_CANCEL, BEFORE_CUSTOMS_REFRESH):
				for mock in (letmeship, dhl, sendcloud, release):
					mock.reset_mock()
				for carrier in carriers.carriers():
					carrier.ensure_no_uncertain_booking(doc, before)
				calls[before] = (letmeship.call_count, dhl.call_count, sendcloud.call_count, release.call_count)
		self.assertEqual(calls, {
			# A lost Sendcloud announcement is recovered by the booking, not refused up front.
			BEFORE_BOOKING: (1, 1, 0, 0),
			BEFORE_CANCEL: (1, 1, 1, 1),
			# Saving customs items never touched a LetMeShip attempt, and releases nothing.
			BEFORE_CUSTOMS_REFRESH: (0, 1, 1, 0),
		})

	def test_released_sendcloud_announcement_does_not_block_a_cancellation(self):
		with patch.object(carriers, "ensure_no_uncertain_sendcloud_booking") as ensure, \
				patch.object(carriers, "release_absent_sendcloud_booking", return_value=True):
			get_carrier("SendCloud").ensure_no_uncertain_booking(_dict(), BEFORE_CANCEL)
		ensure.assert_not_called()

	def test_sendcloud_recovery_refuses_another_provider(self):
		sendcloud = get_carrier("SendCloud")
		doc = _dict()
		with patch.object(carriers, "ensure_no_uncertain_sendcloud_booking") as ensure, \
				patch.object(carriers, "recover_sendcloud_shipment", return_value={"shipment_id": "v3:1"}) as recover:
			self.assertEqual(sendcloud.recover_booking(doc, selected=sendcloud), {"shipment_id": "v3:1"})
			ensure.assert_not_called()
			sendcloud.recover_booking(doc, selected=get_carrier("Packlink"))
			ensure.assert_called_once_with(doc)
		self.assertEqual(recover.call_count, 2)

	def test_only_sendcloud_v3_bookings_reconcile_their_cost(self):
		result = {
			(name, shipment_id): get_carrier(name).can_reconcile_cost(_dict(shipment_id=shipment_id))
			for name in sorted(PROVIDER_NAMES) for shipment_id in ("v3:abc", "12345", None)
		}
		self.assertEqual([key for key, value in result.items() if value], [("SendCloud", "v3:abc")])

	def test_cancel_rules(self):
		doc = _dict(name="SHIP-1", shipment_id="A1")
		with patch.object(carriers.frappe, "throw", side_effect=ValueError) as throw, \
				patch.object(carriers.frappe, "msgprint") as msgprint, \
				patch.object(carriers, "dhl_booking_deleted", return_value=False):
			blocked = []
			for carrier in carriers.carriers():
				try:
					carrier.before_cancel(doc)
				except ValueError:
					blocked.append(carrier.name)
			self.assertEqual(blocked, ["LetMeShip", "DHL"])
			self.assertIn("Cancel DHL Booking", throw.call_args.args[0])
			for carrier in carriers.carriers():
				carrier.after_cancel(doc)
			# Only Packlink leaves something to remove by hand.
			msgprint.assert_called_once()
			self.assertIn("Packlink PRO", msgprint.call_args.args[0])
		with patch.object(carriers, "dhl_booking_deleted", return_value=True):
			get_carrier("DHL").before_cancel(doc)

	def test_letmeship_tracking_keeps_the_booking_attempt_record(self):
		import json

		letmeship = get_carrier("LetMeShip")
		doc = _dict(carrier_booking_data=json.dumps({"letmeship": {"request_id": "r1", "attempt_state": "sent", "other": 1}}))
		values = letmeship.tracking_values(doc, {
			"status": "Booked", "carrier_booking_data": json.dumps({"letmeship": {"state": "CONFIRMED"}})})
		self.assertEqual(values["status"], "Booked")
		self.assertEqual(
			json.loads(values["carrier_booking_data"]),
			{"letmeship": {"state": "CONFIRMED", "request_id": "r1", "attempt_state": "sent"}},
		)
		self.assertEqual(letmeship.tracking_values(doc, {"tracking_status": "In Transit"}), {})
		for name in ("Packlink", "SendCloud", "DHL"):
			self.assertEqual(get_carrier(name).tracking_values(doc, {"status": "Booked"}), {})


class TestControllerUsesTheInterface(unittest.TestCase):
	"""The Shipment controller must not decide by provider name (#235)."""

	def setUp(self):
		self.tree = ast.parse(CONTROLLER.read_text())

	def test_no_comparison_against_a_provider_name(self):
		found = []
		for node in ast.walk(self.tree):
			if not isinstance(node, ast.Compare):
				continue
			for operand in [node.left, *node.comparators]:
				for part in ast.walk(operand):
					if isinstance(part, ast.Constant) and part.value in PROVIDER_NAMES:
						found.append(f"line {node.lineno}: {ast.unparse(node)}")
					if isinstance(part, ast.Name) and part.id.endswith("_PROVIDER"):
						found.append(f"line {node.lineno}: {ast.unparse(node)}")
		self.assertEqual(found, [], "Move the rule into the provider's class in shipment/api/carriers.py")

	def test_no_provider_client_call_for_rates_booking_label_or_tracking(self):
		imported = {
			alias.asname or alias.name
			for node in ast.walk(self.tree) if isinstance(node, ast.ImportFrom)
			and (node.module or "").startswith("shipment.api.")
			for alias in node.names
		}
		dispatching = sorted(
			name for name in imported
			if name.startswith(("create_", "get_")) and name.endswith(("_shipment", "_services", "_label", "_tracking_data"))
		)
		self.assertEqual(dispatching, [])
