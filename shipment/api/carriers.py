"""One interface for every shipping provider the Shipment controller books with.

The controller (`shipment/shipment/doctype/shipment/shipment.py`) never compares
`service_provider` against a provider name. It looks the provider up here and
calls the interface; what one provider does differently (which rates it offers,
how it books, tracks and cancels) lives in that provider's class.

Adding a provider: write its HTTP client under `shipment/api/`, subclass
`Carrier` here and add the class to `_CARRIERS`.
"""

import json

import frappe
from frappe import _
from frappe.utils import escape_html

from shipment.api.dhl import (
	DHL_PROVIDER,
	create_dhl_shipment,
	dhl_booking_deleted,
	ensure_no_uncertain_dhl_booking,
	get_dhl_available_services,
	get_dhl_label,
	get_dhl_tracking_data,
)
from shipment.api.let_me_ship import (
	create_letmeship_shipment,
	get_letmeship_available_services,
	get_letmeship_label,
	get_letmeship_tracking_data,
)
from shipment.api.letmeship_billing import ensure_no_uncertain_booking as ensure_no_uncertain_letmeship_booking
from shipment.api.packlink import (
	create_packlink_shipment,
	get_packlink_available_services,
	get_packlink_label,
	get_packlink_tracking_data,
	packlink_draft_url,
)
from shipment.api.sendcloud import (
	create_sendcloud_shipment,
	get_sendcloud_available_services,
	get_sendcloud_label,
	get_sendcloud_tracking_data,
)
from shipment.api.sendcloud_v3 import (
	ensure_no_uncertain_booking as ensure_no_uncertain_sendcloud_booking,
	recover_shipment as recover_sendcloud_shipment,
	release_absent_booking as release_absent_sendcloud_booking,
	uncertain_booking as uncertain_sendcloud_booking,
)

# The moments at which the controller asks every provider whether a booking
# request whose answer was lost still stands in the way.
BEFORE_BOOKING = "booking"
BEFORE_CANCEL = "cancel"
BEFORE_CUSTOMS_REFRESH = "customs refresh"


class Carrier:
	"""A shipping provider. The defaults describe a provider with no special rules."""

	# `service_provider` as stored on the Shipment and sent by the rate dialog.
	name = None
	# Name of the provider's Shipment Service Provider record.
	settings_name = None
	# Shipment status right after a successful booking.
	booked_status = "Booked"
	# The booking is confirmed by the provider only later: it is polled every few
	# minutes and its tracking answer carries the booking state.
	confirms_booking_later = False
	# The ERP cancellation needs the provider's cancellation proof (System Manager).
	cancel_needs_proof = False
	# The labels are deleted at the provider through its own cancel endpoint.
	deletes_labels_on_cancel = False
	# The tracking status is polled in a background job, never in a desk request.
	polls_status_in_background = False

	def enabled(self):
		return frappe.db.get_value("Shipment Service Provider", self.settings_name, "enabled")

	# Rates

	def offers_rates(self, pickup_from_type, recipient_only):
		"""Whether this provider is asked for rates. `recipient_only`: the freight goes on the customer's account."""
		return not recipient_only

	def rates(self, request):
		"""Return (rates, errors, notice) for the rate request (keyword arguments of `fetch_shipping_rates`).

		`errors` are shown when no provider returned a rate; `notice` is shown when others did.
		"""
		raise NotImplementedError

	def bills_recipient_account(self, service_info):
		"""Whether the selected service can be booked on the recipient's carrier account."""
		return False

	# Booking

	def ensure_bookable(self):
		"""Refuse a booking the rate dialog offered before the provider was switched off."""

	def book(self, request):
		"""Book the shipment (keyword arguments of `create_shipment`) and return the booking values."""
		raise NotImplementedError

	def ensure_no_uncertain_booking(self, shipment_doc, before):
		"""Throw while a booking request of this provider whose answer was lost is unresolved."""

	def has_recoverable_booking(self, shipment_doc):
		"""Whether a lost booking answer of this provider must be recovered instead of booking anew."""
		return False

	def recover_booking(self, shipment_doc, selected):
		"""Return the booking values of the lost booking. `selected` is the provider the user chose now."""
		raise NotImplementedError

	# Label and tracking

	def label(self, shipment_id):
		raise NotImplementedError

	def tracking(self, shipment_doc, shipment_name, poll_status=True):
		"""Return the provider's tracking answer for the booked Shipment `shipment_name`."""
		raise NotImplementedError

	def tracking_values(self, shipment_doc, tracking_data):
		"""Shipment values beyond the tracking fields that a tracking answer updates."""
		return {}

	# Cancellation

	def before_cancel(self, shipment_doc):
		"""Throw when the booked Shipment must not be cancelled in the ERP yet."""

	def after_cancel(self, shipment_doc):
		"""Tell the user what is left to do at the provider after the ERP cancellation."""

	# Provider-specific actions

	def can_reconcile_cost(self, shipment_doc):
		"""Whether the booked cost can be reconciled with the provider's quotation."""
		return False


class LetMeShip(Carrier):
	name = "LetMeShip"
	settings_name = "Let Me Ship"
	booked_status = "Booking Pending"
	confirms_booking_later = True
	cancel_needs_proof = True

	def offers_rates(self, pickup_from_type, recipient_only):
		return True

	def rates(self, request):
		errors = []
		try:
			prices = get_letmeship_available_services(
				pickup_from_type=request["pickup_from_type"],
				delivery_to_type=request["delivery_to_type"],
				pickup_address_name=request["pickup_address_name"],
				delivery_address_name=request["delivery_address_name"],
				shipment_parcel=request["shipment_parcel"],
				description_of_content=request["description_of_content"],
				pickup_date=request["pickup_date"],
				value_of_goods=request["value_of_goods"],
				pickup_contact_name=request["pickup_contact_name"],
				delivery_contact_name=request["delivery_contact_name"],
				pickup_type=request["pickup_type"],
				shipment=request["shipment"],
			)
			if not prices:
				# Check if there's an error message from LetMeShip API
				if hasattr(frappe.local, "response") and "letmeship_error" in frappe.local.response:
					errors.append(frappe.local.response.get("letmeship_error"))
					del frappe.local.response["letmeship_error"]  # Clean up
				else:
					frappe.log_error(
						f"LetMeShip returned 0 results for {request['delivery_address_name']}",
						"fetch_shipping_rates - LetMeShip Empty Results",
					)
			return prices, errors, None
		except frappe.ValidationError as exc:
			# Missing contact data is for the user to fix, not an error to log.
			# frappe.throw queued the same text; the controller shows it once.
			frappe.clear_last_message()
			return [], [str(exc)], str(exc)
		except Exception as exc:
			frappe.log_error(f"fetch_shipping_rates - LetMeShip error: {str(exc)}")
			return [], [], None

	def bills_recipient_account(self, service_info):
		return service_info.get("carrier") == "UPS"

	def book(self, request):
		return create_letmeship_shipment(
			pickup_from_type=request["pickup_from_type"],
			delivery_to_type=request["delivery_to_type"],
			pickup_address_name=request["pickup_address_name"],
			delivery_address_name=request["delivery_address_name"],
			shipment_parcel=request["shipment_parcel"],
			description_of_content=request["description_of_content"],
			pickup_date=request["pickup_date"],
			value_of_goods=request["value_of_goods"],
			pickup_contact_name=request["pickup_contact_name"],
			delivery_contact_name=request["delivery_contact_name"],
			service_info=request["service_info"],
			pickup_type=request["pickup_type"],
			shipment_notific_email=request["shipment_notific_email"],
			tracking_notific_email=request["tracking_notific_email"],
			shipment=request["shipment"],
			label_email=request["label_email"],
		)

	def ensure_no_uncertain_booking(self, shipment_doc, before):
		if before != BEFORE_CUSTOMS_REFRESH:
			ensure_no_uncertain_letmeship_booking(shipment_doc)

	def label(self, shipment_id):
		return get_letmeship_label(shipment_id)

	def tracking(self, shipment_doc, shipment_name, poll_status=True):
		return get_letmeship_tracking_data(shipment_doc.shipment_id, shipment_name)

	def tracking_values(self, shipment_doc, tracking_data):
		values = {}
		if tracking_data.get("status"):
			values["status"] = tracking_data["status"]
		if tracking_data.get("carrier_booking_data"):
			try:
				previous = json.loads(shipment_doc.get("carrier_booking_data") or "{}").get("letmeship", {})
			except (ValueError, TypeError, AttributeError):
				previous = {}
			if not isinstance(previous, dict):
				previous = {}
			fresh = json.loads(tracking_data["carrier_booking_data"])
			for key in ("requested_settings", "acknowledged_settings", "recipient_billing", "attempt_state", "request_id"):
				if key in previous:
					fresh["letmeship"][key] = previous[key]
			values["carrier_booking_data"] = json.dumps(fresh)
		return values

	def before_cancel(self, shipment_doc):
		frappe.throw(
			_(
				"Shipment {0} is still linked to LetMeShip booking {1}. "
				"Cancel the booking with LetMeShip first, then contact Support "
				"with the cancellation confirmation before cancelling this ERP shipment."
			).format(shipment_doc.name, shipment_doc.shipment_id)
		)


class Packlink(Carrier):
	name = "Packlink"
	settings_name = "PackLink"

	def rates(self, request):
		try:
			return get_packlink_available_services(
				pickup_address_name=request["pickup_address_name"],
				delivery_address_name=request["delivery_address_name"],
				shipment_parcel=request["shipment_parcel"],
				pickup_date=request["pickup_date"],
			), [], None
		except Exception as exc:
			frappe.log_error(f"fetch_shipping_rates - PackLink error: {str(exc)}")
			return [], [], None

	def book(self, request):
		return create_packlink_shipment(
			pickup_from_type=request["pickup_from_type"],
			delivery_to_type=request["delivery_to_type"],
			pickup_address_name=request["pickup_address_name"],
			delivery_address_name=request["delivery_address_name"],
			shipment_parcel=request["shipment_parcel"],
			description_of_content=request["description_of_content"],
			pickup_date=request["pickup_date"],
			value_of_goods=request["value_of_goods"],
			pickup_contact_name=request["pickup_contact_name"],
			delivery_contact_name=request["delivery_contact_name"],
			service_info=request["service_info"],
			shipment=request["shipment"],
		)

	def label(self, shipment_id):
		return get_packlink_label(shipment_id)

	def tracking(self, shipment_doc, shipment_name, poll_status=True):
		return get_packlink_tracking_data(shipment_doc.shipment_id)

	def after_cancel(self, shipment_doc):
		# Packlink supports no API for orders, so a draft is deleted (or a paid shipment cancelled) in Packlink PRO.
		url = escape_html(packlink_draft_url(shipment_doc.shipment_id))
		frappe.msgprint(
			_(
				"Shipment {0} is cancelled in the ERP only. Packlink still holds {1}: delete the draft, or cancel "
				"the shipment if it was paid, in Packlink PRO so nobody pays or ships it by mistake: "
				'<a href="{2}" target="_blank" rel="noopener noreferrer">{2}</a>'
			).format(escape_html(shipment_doc.name), escape_html(shipment_doc.shipment_id), url),
			title=_("Remove it in Packlink PRO"),
			indicator="orange",
		)


class SendCloud(Carrier):
	name = "SendCloud"
	settings_name = "SendCloud"

	def offers_rates(self, pickup_from_type, recipient_only):
		return pickup_from_type == "Company" and not recipient_only

	def rates(self, request):
		try:
			return get_sendcloud_available_services(
				delivery_address_name=request["delivery_address_name"],
				shipment_parcel=request["shipment_parcel"],
				pickup_address_name=request["pickup_address_name"],
			), [], None
		except Exception as exc:
			frappe.log_error(f"fetch_shipping_rates - SendCloud error: {str(exc)}")
			return [], [], None

	def book(self, request):
		return create_sendcloud_shipment(
			shipment=request["shipment"],
			delivery_to_type=request["delivery_to_type"],
			delivery_address_name=request["delivery_address_name"],
			delivery_contact_name=request["delivery_contact_name"],
			service_info=request["service_info"],
			shipment_parcel=request["shipment_parcel"],
			description_of_content=request["description_of_content"],
			value_of_goods=request["value_of_goods"],
			pickup_address_name=request["pickup_address_name"],
		)

	def ensure_no_uncertain_booking(self, shipment_doc, before):
		if before == BEFORE_BOOKING:
			# A lost announcement is recovered by the booking itself (`recover_booking`).
			return
		# Before a cancellation, an announcement Sendcloud confirms it never created no longer blocks.
		if before == BEFORE_CANCEL and release_absent_sendcloud_booking(shipment_doc):
			return
		ensure_no_uncertain_sendcloud_booking(shipment_doc)

	def has_recoverable_booking(self, shipment_doc):
		# A failed announcement that Sendcloud confirms it never created no longer blocks a new booking.
		return bool(uncertain_sendcloud_booking(shipment_doc)) and not release_absent_sendcloud_booking(shipment_doc)

	def recover_booking(self, shipment_doc, selected):
		if selected is not self:
			ensure_no_uncertain_sendcloud_booking(shipment_doc)
		return recover_sendcloud_shipment(shipment_doc)

	def label(self, shipment_id):
		return get_sendcloud_label(shipment_id)

	def tracking(self, shipment_doc, shipment_name, poll_status=True):
		return get_sendcloud_tracking_data(shipment_doc.shipment_id)

	def can_reconcile_cost(self, shipment_doc):
		return str(shipment_doc.shipment_id or "").startswith("v3:")


class DHL(Carrier):
	name = DHL_PROVIDER
	settings_name = DHL_PROVIDER
	deletes_labels_on_cancel = True
	polls_status_in_background = True

	def rates(self, request):
		errors = []
		prices = get_dhl_available_services(
			pickup_address_name=request["pickup_address_name"],
			delivery_address_name=request["delivery_address_name"],
			shipment_parcel=request["shipment_parcel"],
			errors=errors,
		)
		return prices, ([] if prices else errors), None

	def ensure_bookable(self):
		# The rate dialog may predate disabling DHL: never create live labels then.
		if not self.enabled():
			frappe.throw(_("The DHL service provider is disabled."))

	def book(self, request):
		return create_dhl_shipment(
			shipment=request["shipment"],
			pickup_from_type=request["pickup_from_type"],
			delivery_to_type=request["delivery_to_type"],
			pickup_address_name=request["pickup_address_name"],
			delivery_address_name=request["delivery_address_name"],
			shipment_parcel=request["shipment_parcel"],
			pickup_date=request["pickup_date"],
			service_info=request["service_info"],
			pickup_contact_name=request["pickup_contact_name"],
			delivery_contact_name=request["delivery_contact_name"],
			pickup_type=request["pickup_type"],
		)

	def ensure_no_uncertain_booking(self, shipment_doc, before):
		# Labels from a DHL call whose response was lost may exist.
		ensure_no_uncertain_dhl_booking(shipment_doc)

	def label(self, shipment_id):
		return get_dhl_label(shipment_id)

	def tracking(self, shipment_doc, shipment_name, poll_status=True):
		tracking_data = get_dhl_tracking_data(
			shipment_doc.shipment_id, shipment_doc.get("carrier_booking_data"), poll=poll_status)
		if not poll_status:
			# Polling waits 5 s per parcel; never inside a desk request.
			frappe.enqueue(
				"shipment.shipment.doctype.shipment.shipment.refresh_dhl_tracking_status",
				queue="short", job_id=f"dhl-tracking-{shipment_name}", deduplicate=True,
				enqueue_after_commit=True, shipment=shipment_name,
			)
		return tracking_data

	def before_cancel(self, shipment_doc):
		if dhl_booking_deleted(shipment_doc):
			# DHL already confirmed every deletion; only the ERP cancellation is left.
			return
		frappe.throw(
			_(
				"Shipment {0} is still linked to DHL shipment(s) {1}. "
				"Use Cancel DHL Booking to delete the label(s) at DHL and cancel this Shipment."
			).format(shipment_doc.name, shipment_doc.shipment_id)
		)


# Rate order of the comparison dialog before sorting; also the order of every check.
_CARRIERS = (LetMeShip(), Packlink(), SendCloud(), DHL())
_BY_NAME = {carrier.name: carrier for carrier in _CARRIERS}
# Stands for a Shipment that is not booked or names a provider this app does not know.
NO_CARRIER = Carrier()


def carriers():
	"""Every provider, in rate order."""
	return _CARRIERS


def get_carrier(name):
	"""The provider stored as `service_provider`, or None."""
	return _BY_NAME.get(name)


def carrier_of(shipment_doc):
	"""The provider a Shipment is booked with; `NO_CARRIER` (no rules, no capabilities) when there is none."""
	return _BY_NAME.get(getattr(shipment_doc, "service_provider", None)) or NO_CARRIER
