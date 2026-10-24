"""Test the DHL Parcel DE Shipping client with every HTTP call mocked."""

import base64
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from frappe import ValidationError, _dict
from frappe.utils import getdate

from shipment.api import dhl

MODULE = "shipment.api.dhl"
# The real limiter; setUp replaces it with an always-free slot.
ACQUIRE = dhl._acquire_tracking_slot


class FakeProvider(_dict):
	"""A DHL Shipment Service Provider with Password fields."""

	def get_password(self, fieldname, raise_exception=True):
		return self.get("_passwords", {}).get(fieldname)


def make_provider(**overrides):
	provider = FakeProvider(
		name="DHL", shipment_provider_name="DHL", enabled=1, api_key="app-key",
		dhl_sandbox=1, dhl_ekp="3333333333", dhl_profile="STANDARD_GRUPPENPROFIL",
		dhl_print_format="910-300-600", dhl_gkp_username="user-valid", dhl_gkp_password="*****",
		dhl_send_recipient_contact=0, dhl_poll_tracking=0,
		_passwords={"dhl_gkp_password": "gkp-secret", "dhl_api_secret": "app-secret"},
		dhl_products=[
			_dict(idx=1, product="V01PAK", participation="01", destination_country=None, max_weight=0, net_price=5.2),
			_dict(idx=2, product="V01PAK", participation="02", destination_country=None, max_weight=5, net_price=4.1),
			_dict(idx=3, product="V62KP", participation="01", destination_country=None, max_weight=0, net_price=3.0),
			_dict(idx=4, product="V53WPAK", participation="01", destination_country=None, max_weight=0, net_price=14.0),
			_dict(idx=5, product="V53WPAK", participation="01", destination_country="Austria", max_weight=0, net_price=11.0),
		],
	)
	provider.update(overrides)
	return provider


def make_address(country_code="DE", country="Germany", pincode="53113", **overrides):
	address = _dict(
		name="ADDR-" + country_code, address_title="Kunde GmbH", address_line1="Hauptstrasse 1",
		address_line2="", city="Bonn", pincode=pincode, country=country, country_code=country_code,
	)
	address.update(overrides)
	return address


def make_contact(**overrides):
	contact = _dict(
		first_name="Erika", last_name="Muster", email="erika@example.com",
		phone_prefix="+43", phone="6641234567",
	)
	contact.update(overrides)
	return contact


def response(status, body=None, content=b""):
	return Mock(status_code=status, text=json.dumps(body) if body is not None else "", content=content)


LABEL_B64 = base64.b64encode(b"%PDF-1.4 label").decode()


def created_item(number, warnings=()):
	return {
		"shipmentNo": number,
		"sstatus": {"title": "OK", "status": 200},
		"label": {"b64": LABEL_B64, "fileFormat": "PDF"},
		"validationMessages": [
			{"property": "consignee", "validationMessage": text, "validationState": "Warning"} for text in warnings
		],
	}


class DHLTestCase(unittest.TestCase):
	"""Patch the Frappe and HTTP boundaries of the DHL module."""

	def setUp(self):
		self.provider = make_provider()
		self.pickup = make_address()
		self.delivery = make_address(pincode="10115", city="Berlin")
		self.files = []
		stack = ExitStack()
		self.addCleanup(stack.close)

		def get_doc(*args, **kwargs):
			if args and isinstance(args[0], dict):
				file_doc = Mock(file_url="/private/files/" + args[0]["file_name"])
				self.files.append((args[0], file_doc))
				return file_doc
			if args[:2] == ("Shipment Service Provider", "DHL"):
				return self.provider
			return Mock()

		def throw(message, *args, **kwargs):
			raise ValidationError(message)

		addresses = {"PICKUP": lambda: self.pickup, "DELIVERY": lambda: self.delivery}
		self.get_doc = stack.enter_context(patch(f"{MODULE}.frappe.get_doc", side_effect=get_doc))
		self.throw = stack.enter_context(patch(f"{MODULE}.frappe.throw", side_effect=throw))
		self.msgprint = stack.enter_context(patch(f"{MODULE}.frappe.msgprint"))
		self.log_error = stack.enter_context(patch(f"{MODULE}.frappe.log_error"))
		self.db = stack.enter_context(patch(f"{MODULE}.frappe.db", Mock()))
		self.db.get_value.return_value = None
		self.cache_store = {}
		stack.enter_context(patch(f"{MODULE}._cached_token", side_effect=self.cache_store.get))
		self.cache_token = stack.enter_context(patch(
			f"{MODULE}._cache_token",
			side_effect=lambda key, value, expires_in_sec: self.cache_store.__setitem__(key, value),
		))
		self.clear_cache = stack.enter_context(patch(f"{MODULE}.clear_dhl_token_cache", side_effect=self.cache_store.clear))
		stack.enter_context(patch(f"{MODULE}.get_address", side_effect=lambda name: addresses[name]()))
		stack.enter_context(patch(f"{MODULE}.get_contact", side_effect=lambda name: make_contact()))
		stack.enter_context(patch(
			f"{MODULE}.get_company_contact",
			return_value=make_contact(first_name="Pack", last_name="Er", email="service@newmatik.com"),
		))
		stack.enter_context(patch(f"{MODULE}.match_parcel_service_type_alias", side_effect=lambda name, carrier: name))
		self.token_post = stack.enter_context(patch(
			f"{MODULE}.requests.post",
			return_value=response(200, {"access_token": "token-1", "token_type": "Bearer", "expires_in": 1799}),
		))
		self.request = stack.enter_context(patch(f"{MODULE}.requests.request"))
		self.slot = stack.enter_context(patch(f"{MODULE}._acquire_tracking_slot", return_value=True))
		stack.enter_context(patch(f"{MODULE}.getdate", side_effect=lambda value=None: getdate(value or "2026-10-26")))

	def book(self, product="V01PAK", parcels=None, pickup_type="Self delivery", delivery_to_type="Customer"):
		parcels = parcels if parcels is not None else [{"length": 30, "width": 20, "height": 10, "weight": 2, "count": 1}]
		return dhl.create_dhl_shipment(
			shipment="SHIPMENT-00042",
			pickup_from_type="Company",
			delivery_to_type=delivery_to_type,
			pickup_address_name="PICKUP",
			delivery_address_name="DELIVERY",
			shipment_parcel=json.dumps(parcels),
			pickup_date="2026-10-20",
			service_info={"service_provider": "DHL", "product": product, "service_name": "<b>client</b>"},
			delivery_contact_name="CONTACT-1",
			pickup_type=pickup_type,
		)


class TestPlanning(DHLTestCase):
	"""Verify product eligibility, price tiers and billing numbers."""

	def test_builds_billing_number_from_ekp_procedure_and_participation(self):
		"""Compose the 14-character billing number per parcel and choose the lowest fitting tier."""
		plan = dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 2, "count": 2}, {"weight": 12, "count": 1}])

		self.assertEqual([p.billing_number for p in plan], ["33333333330102", "33333333330102", "33333333330101"])
		self.assertEqual([p.price for p in plan], [4.1, 4.1, 5.2])
		self.assertTrue(all(len(p.billing_number) == 14 for p in plan))

	def test_prefers_country_specific_price_rows(self):
		"""Use the Austria row over the generic V53WPAK row."""
		self.assertEqual(dhl.plan_dhl_parcels(self.provider, "V53WPAK", "Austria", [{"weight": 3}])[0].price, 11.0)
		self.assertEqual(dhl.plan_dhl_parcels(self.provider, "V53WPAK", "France", [{"weight": 3}])[0].price, 14.0)

	def test_refuses_parcels_outside_product_limits(self):
		"""Reject overweight, oversized, unpriced and more-than-30-parcel shipments."""
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V62KP", "Germany", [{"weight": 1.5}]))
		self.assertIsNone(dhl.plan_dhl_parcels(
			self.provider, "V62KP", "Germany", [{"weight": 0.5, "length": 40, "width": 20, "height": 5}]))
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 32}]))
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V66WPI", "France", [{"weight": 0.5}]))
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 31}]))
		self.assertEqual(len(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 30}])), 30)
		self.assertIsNone(dhl.plan_dhl_parcels(
			self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 20}, {"weight": 1, "count": 11}]))
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1, "count": -3}]))
		# An explicit 0 is not one chargeable label; only a missing count means one parcel.
		self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 0}]))
		self.assertIsNone(dhl.plan_dhl_parcels(
			self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 1}, {"weight": 2, "count": 0}]))
		self.assertEqual(len(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1}])), 1)

	def test_checks_the_parcel_total_before_creating_units(self):
		"""Refuse a huge user-entered count without building one entry per unit."""
		with patch(f"{MODULE}.frappe._dict", side_effect=AssertionError("materialised")):
			self.assertIsNone(dhl.plan_dhl_parcels(self.provider, "V01PAK", "Germany", [{"weight": 1, "count": 10**9}]))

	def test_classifies_destinations_by_customs_need(self):
		"""Book Germany and the EU; refuse customs destinations and EU territories outside the customs area."""
		cases = (
			("DE", "53113", "domestic"),
			("AT", "1010", "eu"),
			("NL", "1071 AA", "eu"),
			("CH", "3003", None),
			("GB", "SW1A1AA", None),
			("ES", "35001", None),
			("ES", "28001", "eu"),
			("FI", "22100", None),
			("DE", "27498", None),
			("DE", "78266", None),
			("DE", "78267", "domestic"),
		)
		for country_code, pincode, scope in cases:
			with self.subTest(country=country_code, pincode=pincode):
				self.assertEqual(
					dhl._destination_scope(make_address(country_code=country_code, pincode=pincode)), scope)


class TestRates(DHLTestCase):
	"""Verify the rate rows offered next to the other providers."""

	def test_offers_domestic_products_that_fit(self):
		"""Offer V01PAK and V62KP for a light domestic parcel, V01PAK only for a heavy one."""
		light = dhl.get_dhl_available_services("PICKUP", "DELIVERY", json.dumps([{"weight": 0.8, "count": 2}]))
		self.assertEqual({s.product: s.total_price for s in light}, {"V01PAK": 8.2, "V62KP": 6.0})
		self.assertTrue(all(s.service_provider == "DHL" and s.carrier == "DHL" for s in light))

		heavy = dhl.get_dhl_available_services("PICKUP", "DELIVERY", json.dumps([{"weight": 8, "count": 1}]))
		self.assertEqual([s.product for s in heavy], ["V01PAK"])

	def test_explains_customs_destinations_instead_of_offering_rates(self):
		"""Return no DHL rate for Switzerland and tell the user why."""
		self.delivery = make_address(country_code="CH", country="Switzerland", pincode="3003")
		errors = []
		self.assertEqual(dhl.get_dhl_available_services("PICKUP", "DELIVERY", "[]", errors), [])
		self.assertIn("customs data", errors[0])

	def test_requires_a_german_pickup_address(self):
		"""Parcel DE labels start in Germany."""
		self.pickup = make_address(country_code="AT", country="Austria", pincode="1010")
		errors = []
		self.assertEqual(dhl.get_dhl_available_services("PICKUP", "DELIVERY", "[]", errors), [])
		self.assertIn("Germany", errors[0])


class TestBooking(DHLTestCase):
	"""Verify validation, creation, labels and the recipient-contact opt-in."""

	def test_validates_then_creates_and_attaches_the_label(self):
		"""Dry-run first, then create; store the PDF on the Shipment and return the booking."""
		self.request.side_effect = [
			response(200, {"items": [{"sstatus": {"status": 200}}]}),
			response(200, {"items": [created_item("00340434161094042557")]}),
		]

		result = self.book()

		validate_call, create_call = self.request.call_args_list
		self.assertEqual(validate_call.args[:2], ("POST", "https://api-sandbox.dhl.com/parcel/de/shipping/v2/orders"))
		self.assertEqual(validate_call.kwargs["params"], {"validate": "true"})
		self.assertEqual(
			create_call.kwargs["params"],
			{"docFormat": "PDF", "includeDocs": "include", "printFormat": "910-300-600"},
		)
		self.assertEqual(validate_call.kwargs["json"], create_call.kwargs["json"])
		self.assertEqual(create_call.kwargs["headers"]["Authorization"], "Bearer token-1")
		self.assertIsNone(create_call.kwargs["auth"])
		self.assertEqual(self.token_post.call_count, 1)

		payload = create_call.kwargs["json"]
		self.assertEqual(payload["profile"], "STANDARD_GRUPPENPROFIL")
		shipment = payload["shipments"][0]
		self.assertEqual(shipment["product"], "V01PAK")
		self.assertEqual(shipment["billingNumber"], "33333333330102")
		self.assertEqual(shipment["refNo"], "SHIPMENT-00042")
		self.assertEqual(shipment["shipDate"], "2026-10-26")
		self.assertEqual(shipment["details"], {
			"weight": {"uom": "kg", "value": 2.0},
			"dim": {"uom": "cm", "length": 30, "width": 20, "height": 10},
		})
		self.assertEqual(shipment["consignee"]["country"], "DEU")
		self.assertEqual(shipment["consignee"]["name2"], "Erika Muster")
		self.assertEqual(shipment["shipper"]["email"], "service@newmatik.com")

		file_data, file_doc = self.files[0]
		self.assertEqual(file_data["file_name"], "DHL-label-00340434161094042557.pdf")
		self.assertEqual(file_data["attached_to_name"], "SHIPMENT-00042")
		self.assertEqual(file_data["is_private"], 1)
		self.assertEqual(file_data["content"], b"%PDF-1.4 label")
		file_doc.insert.assert_called_once_with(ignore_permissions=True)

		self.assertEqual(result["service_provider"], "DHL")
		self.assertEqual(result["shipment_id"], "00340434161094042557")
		self.assertEqual(result["awb_number"], "00340434161094042557")
		self.assertEqual(result["carrier_service"], "DHL Paket")
		self.assertEqual(result["shipment_amount"], 4.1)
		self.assertEqual(
			json.loads(result["carrier_booking_data"]),
			{"environment": "Sandbox", "parcels": {"00340434161094042557": 4.1}},
		)

	def test_keeps_recipient_email_and_phone_from_dhl_by_default(self):
		"""Send recipient contact data only after the provider opts in; never a German phone number."""
		cases = (
			(0, "DE", {}),
			(1, "DE", {"email": "erika@example.com"}),
			(0, "AT", {}),
			(1, "AT", {"email": "erika@example.com", "phone": "+436641234567"}),
		)
		for opt_in, country_code, expected in cases:
			with self.subTest(opt_in=opt_in, country=country_code):
				self.provider.dhl_send_recipient_contact = opt_in
				self.delivery = make_address(country_code=country_code, country={"DE": "Germany", "AT": "Austria"}[country_code], pincode="1010")
				self.request.reset_mock()
				self.request.side_effect = [response(200, {"items": [{}]}), response(200, {"items": [created_item("1")]})]

				self.book(product="V01PAK" if country_code == "DE" else "V53WPAK")

				consignee = self.request.call_args.kwargs["json"]["shipments"][0]["consignee"]
				self.assertEqual({key: consignee[key] for key in ("email", "phone") if key in consignee}, expected)

	def test_surfaces_validation_messages_and_creates_nothing(self):
		"""Show DHL's per-field reasons (escaped) and skip the create call."""
		self.request.side_effect = [response(400, {
			"status": {"title": "Bad Request", "status": 400},
			"items": [{
				"sstatus": {"title": "Bad Request", "status": 400},
				"validationMessages": [{
					"property": "consignee.postalCode",
					"validationMessage": "The postal code <10115> is invalid.",
					"validationState": "Error",
				}],
			}],
		})]

		with self.assertRaises(ValidationError) as raised:
			self.book()

		self.assertEqual(self.request.call_count, 1)
		message = str(raised.exception)
		self.assertIn("consignee.postalCode: The postal code &lt;10115&gt; is invalid.", message)
		self.assertNotIn("10115", self.log_error.call_args.kwargs["message"])
		self.assertEqual(self.files, [])

	def test_books_created_parcels_of_a_partial_batch(self):
		"""Record the labels DHL created, warn about the rest and bill only the created parcels."""
		self.request.side_effect = [
			response(200, {"items": [{}, {}]}),
			response(207, {"status": {"title": "Multi-Status", "status": 207}, "items": [
				created_item("A1", warnings=["Address could not be encoded."]),
				{"sstatus": {"title": "Bad Request", "status": 400}, "validationMessages": [
					{"property": "details.weight", "validationMessage": "Too heavy", "validationState": "Error"}]},
			]}),
		]

		result = self.book(parcels=[{"weight": 2, "count": 2}])

		self.assertEqual(result["shipment_id"], "A1")
		self.assertEqual(result["shipment_amount"], 4.1)
		self.assertEqual(dhl.booked_parcel_count(result["carrier_booking_data"]), 1)
		shipments = self.request.call_args.kwargs["json"]["shipments"]
		self.assertEqual([s["costCenter"] for s in shipments], ["1/2", "2/2"])
		indicators = [call.kwargs.get("indicator") for call in self.msgprint.call_args_list]
		self.assertIn("red", indicators)
		self.assertIn("orange", indicators)
		self.assertIn("Too heavy", self.msgprint.call_args_list[0].args[0])
		self.assertIn("Address could not be encoded.", self.msgprint.call_args_list[1].args[0])

	def test_refuses_booking_with_a_disabled_provider(self):
		"""Create no label after DHL was disabled."""
		self.provider.enabled = 0
		with self.assertRaises(ValidationError):
			self.book()
		self.request.assert_not_called()

	def test_refuses_customs_destinations_and_wrong_products(self):
		"""Never send a booking that needs export documents or a product the destination cannot use."""
		self.delivery = make_address(country_code="CH", country="Switzerland", pincode="3003")
		with self.assertRaises(ValidationError):
			self.book(product="V53WPAK")
		self.delivery = make_address()
		with self.assertRaises(ValidationError):
			self.book(product="V53WPAK")
		with self.assertRaises(ValidationError):
			self.book(product="V99XXX")
		self.request.assert_not_called()

	def test_renews_an_expired_token_once(self):
		"""Retry a 401 with a fresh token instead of failing the booking."""
		self.cache_store["shipment:dhl:access_token:sandbox"] = "stale-token"
		self.request.side_effect = [
			response(401, {"title": "Unauthorized", "status": 401}),
			response(200, {"items": [{}]}),
			response(200, {"items": [created_item("1")]}),
		]

		self.book()

		headers = [call.kwargs["headers"]["Authorization"] for call in self.request.call_args_list]
		self.assertEqual(headers, ["Bearer stale-token", "Bearer token-1", "Bearer token-1"])
		self.clear_cache.assert_called_once_with()
		self.cache_token.assert_called_with("shipment:dhl:access_token:sandbox", "token-1", 1739)

	def test_falls_back_to_api_key_and_basic_auth_without_secret(self):
		"""Use DHL's deprecated key + GKP Basic Auth when no API secret is configured."""
		self.provider._passwords = {"dhl_gkp_password": "gkp-secret"}
		self.request.side_effect = [response(200, {"items": [{}]}), response(200, {"items": [created_item("1")]})]

		self.book()

		call = self.request.call_args
		self.assertEqual(call.kwargs["headers"]["dhl-api-key"], "app-key")
		self.assertNotIn("Authorization", call.kwargs["headers"])
		self.assertEqual(call.kwargs["auth"], ("user-valid", "gkp-secret"))
		self.token_post.assert_not_called()

	def test_production_uses_the_production_host(self):
		"""Switch hosts with the sandbox toggle."""
		self.provider.dhl_sandbox = 0
		self.request.side_effect = [response(200, {"items": [{}]}), response(200, {"items": [created_item("1")]})]
		self.book()
		self.assertTrue(self.request.call_args.args[1].startswith("https://api-eu.dhl.com/parcel/de/shipping/v2/"))
		self.assertTrue(self.token_post.call_args.args[0].startswith("https://api-eu.dhl.com/parcel/de/account/auth/ropc/v1/"))

	def test_reports_rejected_credentials(self):
		"""Explain an authentication failure without logging the response body."""
		self.token_post.return_value = response(401, {"error": "invalid_grant"})
		with self.assertRaises(ValidationError) as raised:
			self.book()
		self.assertIn("rejected the credentials", str(raised.exception))
		self.request.assert_not_called()

	def test_warns_that_pickups_are_not_ordered(self):
		"""Tell the user that a label-only booking does not order a DHL pickup."""
		self.request.side_effect = [response(200, {"items": [{}]}), response(200, {"items": [created_item("1")]})]
		self.book(pickup_type="Pickup")
		self.assertIn("does not order a pickup", self.msgprint.call_args.args[0])


class TestLabelsCancellationTracking(DHLTestCase):
	"""Verify label reprints, deletion at DHL and tracking."""

	def test_returns_stored_labels_and_fetches_missing_ones(self):
		"""Reuse attached PDFs and download only the labels that are not stored yet."""
		self.db.get_value.side_effect = ["SHIPMENT-00042", "/private/files/DHL-label-A1.pdf", None]
		self.request.return_value = response(200, {"items": [created_item("B2")]})

		urls = dhl.get_dhl_label("A1, B2")

		self.assertEqual(urls, ["/private/files/DHL-label-A1.pdf", "/private/files/DHL-label-B2.pdf"])
		call = self.request.call_args
		self.assertEqual(call.args[0], "GET")
		self.assertEqual(call.kwargs["params"]["shipment"], ["B2"])

	def test_reports_every_label_dhl_could_not_return(self):
		"""Name the unresolved parcels instead of silently opening only the stored labels."""
		self.db.get_value.side_effect = ["SHIPMENT-00042", "/private/files/DHL-label-A1.pdf", None, None]
		self.request.return_value = response(207, {"items": [
			created_item("B2"),
			{"shipmentNo": "C3", "sstatus": {"title": "Bad Request", "status": 400, "detail": "Shipment is closed"}},
		]})

		urls = dhl.get_dhl_label("A1, B2, C3")

		self.assertEqual(urls, ["/private/files/DHL-label-A1.pdf", "/private/files/DHL-label-B2.pdf"])
		self.assertEqual(self.request.call_args.kwargs["params"]["shipment"], ["B2", "C3"])
		message = self.msgprint.call_args.args[0]
		self.assertIn("C3", message)
		self.assertIn("Shipment is closed", message)
		self.assertEqual(self.msgprint.call_args.kwargs["indicator"], "red")

		self.db.get_value.side_effect = ["SHIPMENT-00042", None]
		self.request.return_value = response(400, {"title": "Bad Request", "status": 400, "detail": "Shipment is closed"})
		with self.assertRaises(ValidationError) as raised:
			dhl.get_dhl_label("C3")
		self.assertIn("C3", str(raised.exception))

	def test_deletes_every_shipment_number_with_the_profile(self):
		"""Send one DELETE with the user group profile and all shipment numbers."""
		self.request.return_value = response(207, {"items": [
			{"shipmentNo": "A1", "sstatus": {"status": 200}},
			{"shipmentNo": "B2", "sstatus": {"title": "Bad Request", "status": 400, "detail": "already manifested"}},
		]})

		deleted, failures = dhl.cancel_dhl_booking("A1, B2")

		call = self.request.call_args
		self.assertEqual(call.args[0], "DELETE")
		self.assertEqual(call.kwargs["params"], {"profile": "STANDARD_GRUPPENPROFIL", "shipment": ["A1", "B2"]})
		self.assertEqual(deleted, ["A1"])
		self.assertIn("already manifested", failures[0])

	def test_tracking_link_only_unless_polling_is_enabled(self):
		"""Return the dhl.de link per parcel and make no tracking call by default."""
		with patch(f"{MODULE}.requests.get") as get:
			data = dhl.get_dhl_tracking_data("A1, B2")
		get.assert_not_called()
		self.assertEqual(data["awb_number"], "A1, B2")
		# No fresh status: None, so the Shipment keeps its last confirmed status.
		self.assertIsNone(data["tracking_status"])
		self.assertIn("verfolgen.html?piececode=A1", data["tracking_url"])
		self.assertIn("piececode=B2", data["tracking_url"])

	@patch(f"{MODULE}.time.sleep")
	def test_polls_unified_tracking_when_enabled(self, sleep):
		"""Mark the Shipment delivered only when every parcel is delivered; stop on a rate limit."""
		self.provider.update(dhl_poll_tracking=1, dhl_sandbox=0)

		def tracking(code, text):
			return response(200, {"shipments": [{"status": {"statusCode": code, "status": text}}]})

		with patch(f"{MODULE}.requests.get", side_effect=[tracking("delivered", "Zugestellt"), tracking("delivered", "Zugestellt")]) as get:
			data = dhl.get_dhl_tracking_data("A1, B2")
		self.assertEqual(data["tracking_status"], "Delivered")
		self.assertEqual(get.call_args.kwargs["headers"]["DHL-API-Key"], "app-key")
		self.assertEqual(get.call_args.kwargs["params"], {"trackingNumber": "B2", "service": "parcel-de"})

		with patch(f"{MODULE}.requests.get", side_effect=[tracking("delivered", "Zugestellt"), response(429, {})]):
			data = dhl.get_dhl_tracking_data("A1, B2")
		self.assertIsNone(data["tracking_status"])
		self.assertIsNone(data["tracking_status_info"])

		with patch(f"{MODULE}.requests.get", side_effect=[tracking("delivered", "Zugestellt"), tracking("transit", "Unterwegs")]):
			data = dhl.get_dhl_tracking_data("A1, B2")
		self.assertEqual(data["tracking_status"], "In Progress")
		self.assertEqual(data["tracking_status_info"], "Zugestellt, Unterwegs")

		self.provider.dhl_sandbox = 1
		with patch(f"{MODULE}.requests.get") as get:
			dhl.get_dhl_tracking_data("A1")
		get.assert_not_called()


class TestProviderValidation(DHLTestCase):
	"""Verify the configuration checks on the Shipment Service Provider."""

	def test_accepts_a_complete_configuration(self):
		"""Normalize participations and accept the sandbox-style setup."""
		self.provider.dhl_products[0].participation = " 0a "
		dhl.validate_dhl_provider(self.provider)
		self.assertEqual(self.provider.dhl_products[0].participation, "0A")

	def test_rejects_invalid_configuration(self):
		"""Reject malformed EKPs and participations and incomplete enabled setups."""
		cases = (
			("ekp", {"dhl_ekp": "12345"}),
			("participation", {"dhl_products": [_dict(idx=1, product="V01PAK", participation="1", net_price=1)]}),
			("weight", {"dhl_products": [_dict(idx=1, product="V62KP", participation="01", max_weight=2, net_price=1)]}),
			("no products", {"dhl_products": []}),
			("no password", {"dhl_gkp_password": None}),
			("no ekp", {"dhl_ekp": ""}),
		)
		for label, overrides in cases:
			with self.subTest(label):
				with self.assertRaises(ValidationError):
					dhl.validate_dhl_provider(make_provider(**overrides))
		dhl.validate_dhl_provider(make_provider(enabled=0, dhl_ekp="", dhl_products=[]))


class TestReviewFollowUps(DHLTestCase):
	"""Booking environment, remaining labels and the shared tracking throttle."""

	def test_refuses_to_manage_a_booking_from_the_other_dhl_system(self):
		"""Never cancel or re-download a sandbox booking against production, or the reverse."""
		production_booking = json.dumps({"environment": "Production", "parcels": {"A1": 4.1}})
		with self.assertRaises(ValidationError) as raised:
			dhl.cancel_dhl_booking("A1", production_booking)
		self.assertIn("Production", str(raised.exception))

		shipment = Mock()
		shipment.get.return_value = production_booking
		self.get_doc.side_effect = lambda doctype, *args, **kwargs: (
			self.provider if doctype == "Shipment Service Provider" else shipment)
		self.db.get_value.side_effect = ["SHIPMENT-00042", None]
		with self.assertRaises(ValidationError):
			dhl.get_dhl_label("A1")
		self.request.assert_not_called()

		self.request.return_value = response(200, {"items": [{"shipmentNo": "A1", "sstatus": {"status": 200}}]})
		self.get_doc.side_effect = lambda *args, **kwargs: self.provider
		self.assertEqual(dhl.cancel_dhl_booking("A1", json.dumps({"environment": "Sandbox"}))[0], ["A1"])

	def test_never_polls_tracking_for_sandbox_bookings(self):
		"""Skip the tracking API for a sandbox booking even after switching to production."""
		self.provider.update(dhl_poll_tracking=1, dhl_sandbox=0)
		with patch(f"{MODULE}.requests.get") as get:
			dhl.get_dhl_tracking_data("A1", json.dumps({"environment": "Sandbox"}))
		get.assert_not_called()

	def test_remaining_labels_keep_their_own_prices(self):
		"""Recompute ids, prices and parcel count for the labels left after a partial deletion."""
		shipment = _dict(
			shipment_id="A1, B2, C3", shipment_amount=13.4,
			carrier_booking_data=json.dumps({"environment": "Production", "parcels": {"A1": 4.1, "B2": 5.2, "C3": 4.1}}),
		)
		values = dhl.remaining_dhl_booking(shipment, ["B2", "C3"])
		self.assertEqual(values["shipment_id"], "B2, C3")
		self.assertEqual(values["shipment_amount"], 9.3)
		self.assertEqual(dhl.booked_parcel_count(values["carrier_booking_data"]), 2)

		values = dhl.remaining_dhl_booking(_dict(shipment_id="A1, B2", shipment_amount=8.2, carrier_booking_data=None), ["B2"])
		self.assertEqual(values["shipment_amount"], 4.1)
		self.assertIsNone(dhl.booked_parcel_count(None))

	def test_tracking_throttle_is_shared_through_redis(self):
		"""Claim the per-key slot atomically in Redis; give up instead of exceeding DHL's rate."""
		cache = Mock(make_key=Mock(side_effect=lambda key: "site|" + key), pttl=Mock(return_value=1200))
		cache.set.side_effect = [None, True]
		with (
			patch(f"{MODULE}._acquire_tracking_slot", ACQUIRE),
			patch(f"{MODULE}.frappe.cache", cache),
			patch(f"{MODULE}.time.sleep") as sleep,
		):
			self.assertTrue(dhl._acquire_tracking_slot("app-key"))
			sleep.assert_called_once_with(1.2)
			key = cache.set.call_args.args[0]
			self.assertTrue(key.startswith("site|shipment:dhl:tracking_slot:"))
			self.assertNotIn("app-key", key)
			self.assertEqual(cache.set.call_args.kwargs, {"nx": True, "px": 5000})

			cache.set.side_effect = None
			cache.set.return_value = None
			self.assertFalse(dhl._acquire_tracking_slot("app-key"))

