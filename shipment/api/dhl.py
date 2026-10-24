# -*- coding: utf-8 -*-
# Copyright (c) 2026, Newmatik and contributors
# For license information, please see license.txt
"""DHL Parcel DE Shipping API v2 (Post & Parcel Germany) client.

Books DHL Geschaeftskunden labels directly with DHL. Supported products are
V01PAK (DHL Paket) and V62KP (DHL Kleinpaket) within Germany and V53WPAK
(DHL Paket International) and V66WPI (Warenpost International) to the other
EU member states. Destinations that need customs data are refused.

API reference: https://developer.dhl.com/api-reference/parcel-de-shipping-post-parcel-germany-v2
"""

import base64
import hashlib
import json
import re
import time
from urllib.parse import quote, urlparse

import frappe
import requests
from frappe import _
from frappe.utils import cint, escape_html, flt, getdate
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
from shipment.api.utils import format_tracking_url, get_address, get_company_contact, get_contact

# Name of the Shipment Service Provider record and the service_provider value
# written to booked Shipments.
DHL_PROVIDER = "DHL"
DHL_CARRIER = "DHL"

DHL_PRODUCTION_HOST = "https://api-eu.dhl.com"
DHL_SANDBOX_HOST = "https://api-sandbox.dhl.com"
DHL_SHIPPING_PATH = "/parcel/de/shipping/v2"
DHL_TOKEN_PATH = "/parcel/de/account/auth/ropc/v1/token"
DHL_TRACKING_API_URL = "https://api-eu.dhl.com/track/shipments"
DHL_TRACKING_LINK = "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={0}"
DHL_DEFAULT_PROFILE = "STANDARD_GRUPPENPROFIL"
DHL_TIMEOUT = 30
# POST/GET/DELETE /orders accept at most 30 shipments per call.
DHL_MAX_SHIPMENTS = 30
# The Unified Tracking API's default service level allows 1 call every 5 s.
DHL_TRACKING_INTERVAL = 5
DHL_TOKEN_CACHE_KEY = "shipment:dhl:access_token:{0}"
DHL_TRACKING_SLOT_KEY = "shipment:dhl:tracking_slot:{0}"
DHL_SANDBOX = "Sandbox"
DHL_PRODUCTION = "Production"

# Product limits from the API documentation ("Requirements for shipping"):
# maximum weight in kg and maximum length/width/height in cm.
DHL_PRODUCTS = {
	"V01PAK": frappe._dict(procedure="01", label="DHL Paket", scope="domestic", max_weight=31.5, max_dims=(120, 60, 60)),
	"V62KP": frappe._dict(procedure="62", label="DHL Kleinpaket", scope="domestic", max_weight=1.0, max_dims=(35, 25, 8)),
	"V53WPAK": frappe._dict(procedure="53", label="DHL Paket International", scope="eu", max_weight=31.5, max_dims=(120, 60, 60)),
	"V66WPI": frappe._dict(procedure="66", label="Warenpost International", scope="eu", max_weight=1.0, max_dims=(35.3, 25, 10)),
}

# EU member states other than Germany (ISO 3166-1 alpha-2 -> alpha-3). The API
# requires alpha-3 country codes; ERPNext's Country only stores alpha-2.
DHL_EU_COUNTRIES = {
	"AT": "AUT", "BE": "BEL", "BG": "BGR", "CY": "CYP", "CZ": "CZE", "DK": "DNK",
	"EE": "EST", "ES": "ESP", "FI": "FIN", "FR": "FRA", "GR": "GRC", "HR": "HRV",
	"HU": "HUN", "IE": "IRL", "IT": "ITA", "LT": "LTU", "LU": "LUX", "LV": "LVA",
	"MT": "MLT", "NL": "NLD", "PL": "POL", "PT": "PRT", "RO": "ROU", "SE": "SWE",
	"SI": "SVN", "SK": "SVK",
}

# Territories outside the EU customs or VAT area, where postal shipments need
# customs data: Helgoland and Buesingen (Germany), Canary Islands, Ceuta,
# Melilla, Livigno, Campione d'Italia, Aland and Mount Athos (postal code prefixes).
DHL_CUSTOMS_TERRITORIES = {
	"DE": ("27498", "78266"),
	"ES": ("35", "38", "51", "52"),
	"IT": ("23041", "22061"),
	"FI": ("22",),
	"GR": ("63086", "63087"),
}

def get_dhl_provider():
	"""Return the DHL Shipment Service Provider record."""
	return frappe.get_doc("Shipment Service Provider", DHL_PROVIDER)


def _environment(provider):
	return DHL_SANDBOX if cint(provider.dhl_sandbox) else DHL_PRODUCTION


def _booking_data(raw):
	"""Return the Shipment's carrier_booking_data as a dict ({} when missing or invalid)."""
	if isinstance(raw, dict):
		return raw
	if not isinstance(raw, str) or not raw.strip():
		return {}
	try:
		data = json.loads(raw)
	except ValueError:
		return {}
	return data if isinstance(data, dict) else {}


def _require_booking_environment(provider, booking_data):
	"""Refuse to act on a booking from the other DHL system (sandbox vs production)."""
	booked = _booking_data(booking_data).get("environment")
	if booked and booked != _environment(provider):
		frappe.throw(_(
			"This Shipment was booked in the DHL {0} environment, but the DHL provider is now set to {1}. "
			"Switch Use DHL Sandbox back to manage this booking."
		).format(_(booked), _(_environment(provider))))


def booked_parcel_count(booking_data):
	"""Number of parcels the carrier booked, or None when the booking did not record it."""
	parcels = _booking_data(booking_data).get("parcels")
	return len(parcels) if isinstance(parcels, dict) else None


def remaining_dhl_booking(shipment_doc, remaining):
	"""Return the Shipment values for the labels that remain after a partial deletion at DHL."""
	numbers = ", ".join(remaining)
	data = _booking_data(shipment_doc.get("carrier_booking_data"))
	parcels = data.get("parcels") if isinstance(data.get("parcels"), dict) else None
	if parcels is not None and all(number in parcels for number in remaining):
		amount = round(sum(flt(parcels[number]) for number in remaining), 2)
		data["parcels"] = {number: parcels[number] for number in remaining}
	else:
		# No recorded price per label: share the amount by label count.
		booked = _split_numbers(shipment_doc.shipment_id)
		amount = round(flt(shipment_doc.shipment_amount) * len(remaining) / (len(booked) or 1), 2)
	return {
		"shipment_id": numbers,
		"awb_number": numbers,
		"carrier_booking_data": json.dumps(data) if data else shipment_doc.get("carrier_booking_data"),
		"base_price": amount,
		"net_price": amount,
		"shipment_amount": amount,
	}


def _base_url(provider):
	host = DHL_SANDBOX_HOST if cint(provider.dhl_sandbox) else DHL_PRODUCTION_HOST
	return host + DHL_SHIPPING_PATH


def _accept_language():
	return "de-DE" if (getattr(frappe.local, "lang", None) or "").startswith("de") else "en-US"


def _cached_token(cache_key):
	return frappe.cache.get_value(cache_key)


def _cache_token(cache_key, token, expires_in_sec):
	frappe.cache.set_value(cache_key, token, expires_in_sec=expires_in_sec)


def clear_dhl_token_cache():
	"""Forget cached OAuth tokens, e.g. after the credentials changed."""
	for environment in ("sandbox", "production"):
		frappe.cache.delete_value(DHL_TOKEN_CACHE_KEY.format(environment))


def _get_access_token(provider, api_key, api_secret, username, password):
	"""Return an OAuth2 access token (resource owner password grant), cached until shortly before expiry."""
	sandbox = cint(provider.dhl_sandbox)
	cache_key = DHL_TOKEN_CACHE_KEY.format("sandbox" if sandbox else "production")
	token = _cached_token(cache_key)
	if token:
		return token
	response = requests.post(
		(DHL_SANDBOX_HOST if sandbox else DHL_PRODUCTION_HOST) + DHL_TOKEN_PATH,
		data={
			"grant_type": "password",
			"username": username,
			"password": password,
			"client_id": api_key,
			"client_secret": api_secret,
		},
		headers={"Accept": "application/json"},
		timeout=DHL_TIMEOUT,
	)
	data = _parse_json(response)
	token = data.get("access_token")
	if response.status_code != 200 or not token:
		frappe.log_error(
			title="DHL authentication failed",
			message="status={0} body_keys={1}".format(response.status_code, sorted(data)),
		)
		frappe.throw(_(
			"DHL rejected the credentials. Check the API key, API secret, GKP username and "
			"GKP password on the DHL Shipment Service Provider, and whether the sandbox setting matches them."
		))
	expires_in = cint(data.get("expires_in")) or 1799
	_cache_token(cache_key, token, max(60, expires_in - 60))
	return token


def _get_auth(provider):
	"""Return (headers, basic_auth) for a Parcel DE Shipping call."""
	api_key = (provider.api_key or "").strip()
	username = (provider.dhl_gkp_username or "").strip()
	password = provider.get_password("dhl_gkp_password", raise_exception=False)
	if not (api_key and username and password):
		frappe.throw(_(
			"The DHL Shipment Service Provider needs an API Key, a GKP Username and a GKP Password."
		))
	headers = {"Accept": "application/json", "Accept-Language": _accept_language()}
	api_secret = provider.get_password("dhl_api_secret", raise_exception=False)
	if api_secret:
		headers["Authorization"] = "Bearer " + _get_access_token(provider, api_key, api_secret, username, password)
		return headers, None
	# Deprecated by DHL, still accepted by v2: API key header plus GKP Basic Auth.
	headers["dhl-api-key"] = api_key
	return headers, (username, password)


def _dhl_request(provider, method, path, params=None, payload=None):
	"""Send one Parcel DE Shipping request; renew an expired OAuth token once."""
	url = _base_url(provider) + path
	for attempt in range(2):
		headers, auth = _get_auth(provider)
		if payload is not None:
			headers["Content-Type"] = "application/json"
		response = requests.request(
			method, url, params=params, json=payload, headers=headers, auth=auth, timeout=DHL_TIMEOUT
		)
		if response.status_code == 401 and auth is None and attempt == 0:
			clear_dhl_token_cache()
			continue
		return response
	return response


def _parse_json(response):
	try:
		data = json.loads(getattr(response, "text", "") or "{}")
	except ValueError:
		return {}
	return data if isinstance(data, dict) else {}


def _status_code(status):
	if not isinstance(status, dict):
		return 0
	return cint(status.get("status") or status.get("statusCode"))


def _item_messages(item, states=None):
	"""Return 'property: message' lines of a response item, optionally filtered by validationState."""
	lines = []
	for message in item.get("validationMessages") or []:
		if not isinstance(message, dict):
			continue
		state = message.get("validationState") or ""
		if states is not None and state not in states:
			continue
		text = str(message.get("validationMessage") or state or "").strip()
		if message.get("property"):
			text = "{0}: {1}".format(message["property"], text)
		if text:
			lines.append(text)
	return lines


def _item_failed(item):
	return _status_code(item.get("sstatus")) >= 400 or bool(_item_messages(item, {"Error"}))


def _response_messages(data):
	"""Return every error line of a DHL response, including the overall status."""
	lines = []
	for index, item in enumerate(data.get("items") or [], start=1):
		if not isinstance(item, dict) or not _item_failed(item):
			continue
		item_lines = _item_messages(item, {"Error"}) or _item_messages(item)
		if not item_lines:
			status = item.get("sstatus") or {}
			item_lines = [" ".join(str(v) for v in (status.get("title"), status.get("detail")) if v)]
		lines.extend(_("Parcel {0}: {1}").format(index, line) for line in item_lines if line)
	status = data.get("status") if isinstance(data.get("status"), dict) else data
	detail = " ".join(str(v) for v in (status.get("title"), status.get("detail")) if v)
	if detail and not lines:
		lines.append(detail)
	return lines


def _format_lines(lines):
	return "<br>".join(escape_html(str(line)) for line in lines)


def _raise_dhl_rejection(response, data, action):
	"""Log a rejected call without its body (it echoes addresses), then show DHL's reasons."""
	frappe.log_error(
		title="DHL {0} failed".format(action),
		message="status={0} body_keys={1}".format(getattr(response, "status_code", "n/a"), sorted(data)),
	)
	lines = _response_messages(data) or [_("HTTP {0}").format(getattr(response, "status_code", "n/a"))]
	frappe.throw(
		_("DHL rejected the request:") + "<br>" + _format_lines(lines),
		title=_("DHL error"),
	)


def _clean(value):
	return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_numbers(shipment_id):
	return [number.strip() for number in str(shipment_id or "").split(",") if number.strip()]


def _destination_scope(address):
	"""Return 'domestic', 'eu', or None when the destination needs customs data."""
	country_code = (address.country_code or "").upper()
	if country_code != "DE" and country_code not in DHL_EU_COUNTRIES:
		return None
	postal_code = (address.pincode or "").replace(" ", "")
	if postal_code.startswith(DHL_CUSTOMS_TERRITORIES.get(country_code, ())):
		return None
	return "domestic" if country_code == "DE" else "eu"


def _customs_message(address):
	return _(
		"DHL: {0} {1} needs customs data (export documents), which the DHL integration does not "
		"send. Book this Shipment with another provider."
	).format(escape_html(address.country or address.country_code or ""), escape_html(address.pincode or ""))


def _alpha3(country_code):
	country_code = (country_code or "").upper()
	return "DEU" if country_code == "DE" else DHL_EU_COUNTRIES.get(country_code)


def _parse_parcels(shipment_parcel):
	if isinstance(shipment_parcel, str):
		shipment_parcel = json.loads(shipment_parcel or "[]")
	return [parcel for parcel in shipment_parcel or [] if parcel]


def _fits_dimensions(parcel, max_dims):
	dims = sorted((flt(parcel.get(key)) for key in ("length", "width", "height")), reverse=True)
	if not all(dims):
		# Without complete dimensions DHL checks the parcel itself.
		return True
	return all(dim <= limit for dim, limit in zip(dims, max_dims))


def _select_rate(provider, product_code, country, weight):
	"""Return the price row for one parcel: country-specific rows first, then the lowest fitting weight tier."""
	limit = DHL_PRODUCTS[product_code].max_weight
	candidates = [
		row for row in provider.get("dhl_products") or []
		if row.product == product_code
		and (not row.destination_country or row.destination_country == country)
		and weight <= (flt(row.max_weight) or limit)
	]
	if not candidates:
		return None
	return min(
		candidates,
		key=lambda row: (0 if row.destination_country else 1, flt(row.max_weight) or limit, flt(row.net_price)),
	)


def plan_dhl_parcels(provider, product_code, country, shipment_parcel):
	"""Return one entry per physical parcel (DHL has no multi-piece shipments), or None if the product cannot carry them."""
	product = DHL_PRODUCTS.get(product_code)
	if not product:
		return None
	parcels = _parse_parcels(shipment_parcel)
	# A missing count means one parcel; an explicit 0 or a negative count is refused.
	counts = [1 if parcel.get("count") in (None, "") else cint(parcel.get("count")) for parcel in parcels]
	# count is user input: check the total before creating one entry per unit.
	if not parcels or min(counts) < 1 or sum(counts) > DHL_MAX_SHIPMENTS:
		return None
	plan = []
	for parcel, count in zip(parcels, counts):
		weight = flt(parcel.get("weight"))
		if weight <= 0 or weight > product.max_weight or not _fits_dimensions(parcel, product.max_dims):
			return None
		rate = _select_rate(provider, product_code, country, weight)
		if not rate:
			return None
		for _unit in range(count):
			plan.append(frappe._dict(
				weight=weight,
				length=cint(parcel.get("length")),
				width=cint(parcel.get("width")),
				height=cint(parcel.get("height")),
				billing_number="{0}{1}{2}".format(provider.dhl_ekp or "", product.procedure, rate.participation or ""),
				price=flt(rate.net_price),
			))
	return plan


def _service_name(product_code):
	return match_parcel_service_type_alias(DHL_PRODUCTS[product_code].label, DHL_CARRIER)


def get_dhl_available_services(pickup_address_name, delivery_address_name, shipment_parcel, errors=None):
	"""Return a rate row per configured DHL product that can carry the parcels.

	DHL's shipping API has no rate endpoint, so the prices are the contract net
	prices configured on the provider. Reasons for an empty list are appended
	to `errors` so fetch_shipping_rates can show them.
	"""
	errors = errors if errors is not None else []
	try:
		provider = get_dhl_provider()
		pickup_address = get_address(pickup_address_name)
		delivery_address = get_address(delivery_address_name)
		if pickup_address.country_code != "DE":
			errors.append(_("DHL: Parcel DE labels can only be created for pickup addresses in Germany."))
			return []
		scope = _destination_scope(delivery_address)
		if not scope:
			errors.append(_customs_message(delivery_address))
			return []
		available_services = []
		for product_code, product in DHL_PRODUCTS.items():
			if product.scope != scope:
				continue
			plan = plan_dhl_parcels(provider, product_code, delivery_address.country, shipment_parcel)
			if not plan:
				continue
			total = round(sum(parcel.price for parcel in plan), 2)
			available_service = frappe._dict()
			available_service.service_provider = DHL_PROVIDER
			available_service.carrier = DHL_CARRIER
			available_service.product = product_code
			available_service.service_name = _service_name(product_code)
			available_service.is_preferred = frappe.db.get_value(
				'Parcel Service Type', available_service.service_name, 'show_in_preferred_services_list')
			available_service.base_price = total
			available_service.net_price = total
			available_service.total_vat = 0
			available_service.total_price = total
			available_services.append(available_service)
		if not available_services:
			errors.append(_(
				"DHL: no configured DHL product fits these parcels (weight, size, more than {0} parcels, "
				"or no price row for the destination)."
			).format(DHL_MAX_SHIPMENTS))
		return available_services
	except frappe.ValidationError as exc:
		errors.append(_("DHL: {0}").format(str(exc)))
		return []
	except Exception:
		frappe.log_error(title="DHL rate lookup failed", message=frappe.get_traceback())
		return []


def _fit(value, limit, label, address_name):
	value = _clean(value)
	if len(value) > limit:
		frappe.throw(_(
			"DHL allows at most {0} characters for {1} (currently {2}). Please shorten Address {3}."
		).format(limit, label, len(value), escape_html(address_name or "")))
	return value


def _build_party(address, contact, email=None, phone=None):
	"""Return a Parcel DE shipper or consignee address."""
	name1 = _clean(address.address_title)[:50]
	party = {
		"name1": name1,
		"addressStreet": _fit(address.address_line1, 50, _("Address Line 1"), address.name),
		"postalCode": _fit(address.pincode, 10, _("Postal Code"), address.name),
		"city": _fit(address.city, 40, _("City"), address.name),
		"country": _alpha3(address.country_code),
	}
	person = _clean(" ".join(p for p in (contact.first_name, contact.last_name) if p))[:50]
	if person and person != name1:
		party["name2"] = person
	line2 = _fit(address.address_line2, 50, _("Address Line 2"), address.name)
	if line2:
		# Printed as name3 because additionalAddressInformation is not printed on domestic labels.
		party["name3"] = line2
	if email:
		party["email"] = _clean(email)[:80]
	if phone:
		party["phone"] = phone
	return party


def _recipient_phone(contact):
	phone = re.sub(r"[^\d+ ]", "", "{0}{1}".format(contact.get("phone_prefix") or "", contact.get("phone") or "")).strip()
	return phone[:20] or None


def _label_file_name(kind, shipment_no):
	return "DHL-{0}-{1}.pdf".format(kind, re.sub(r"[^A-Za-z0-9]", "", str(shipment_no)))


def _is_dhl_document_url(url):
	parsed = urlparse(str(url or ""))
	return parsed.scheme == "https" and parsed.netloc in {"api-eu.dhl.com", "api-sandbox.dhl.com"}


def _attach_document(shipment, shipment_no, document, kind):
	"""Store a PDF document from a DHL response as a private File on the Shipment; return its URL."""
	if not isinstance(document, dict):
		return None
	content = None
	if document.get("b64"):
		content = base64.b64decode(document["b64"])
	elif _is_dhl_document_url(document.get("url")):
		response = requests.get(document["url"], timeout=DHL_TIMEOUT)
		if response.status_code == 200:
			content = response.content
	if not content:
		return None
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": _label_file_name(kind, shipment_no),
		"attached_to_doctype": "Shipment",
		"attached_to_name": shipment,
		"is_private": 1,
		"content": content,
	})
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


def _attach_documents(shipment, item):
	"""Store the label and any customs document of a response item; return {kind: file URL}."""
	urls = {}
	for key, kind in (("label", "label"), ("customsDoc", "customs")):
		try:
			url = _attach_document(shipment, item.get("shipmentNo"), item.get(key), kind)
		except Exception:
			frappe.log_error(title="DHL label attachment failed", message=frappe.get_traceback())
			frappe.msgprint(
				_("DHL created shipment {0}, but its {1} could not be saved. Use Print Shipping Label to fetch it again.").format(
					escape_html(item.get("shipmentNo") or ""), kind),
				indicator="orange",
			)
			url = None
		if url:
			urls[kind] = url
	return urls


def _label_params(provider):
	params = {"docFormat": "PDF", "includeDocs": "include"}
	if provider.dhl_print_format:
		params["printFormat"] = provider.dhl_print_format
	return params


def create_dhl_shipment(
	shipment,
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	shipment_parcel,
	pickup_date,
	service_info,
	pickup_contact_name=None,
	delivery_contact_name=None,
	pickup_type=None,
):
	"""Validate and create one DHL shipment per parcel, attach the labels and return the booking."""
	provider = get_dhl_provider()
	if not cint(provider.enabled):
		frappe.throw(_("The DHL service provider is disabled."))
	product_code = service_info.get("product")
	if product_code not in DHL_PRODUCTS:
		frappe.throw(_("Unsupported DHL product."))
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	if pickup_address.country_code != "DE":
		frappe.throw(_("DHL: Parcel DE labels can only be created for pickup addresses in Germany."))
	scope = _destination_scope(delivery_address)
	if not scope:
		frappe.throw(_customs_message(delivery_address))
	if DHL_PRODUCTS[product_code].scope != scope:
		frappe.throw(_("{0} is not available for this destination.").format(DHL_PRODUCTS[product_code].label))
	# Re-plan on the server: the client only chose the product.
	plan = plan_dhl_parcels(provider, product_code, delivery_address.country, shipment_parcel)
	if not plan:
		frappe.throw(_(
			"{0} cannot carry these parcels: check weight and size, at most {1} parcels, and that a price "
			"row exists for the destination."
		).format(DHL_PRODUCTS[product_code].label, DHL_MAX_SHIPMENTS))

	if pickup_from_type != 'Company':
		pickup_contact = get_contact(pickup_contact_name)
	else:
		pickup_contact = get_company_contact()
	if delivery_to_type != 'Company':
		delivery_contact = get_contact(delivery_contact_name)
	else:
		delivery_contact = get_company_contact()

	# Our own company contact may appear on the label; a customer's or supplier's
	# email and phone only go to DHL when the provider is set to share them.
	shipper = _build_party(
		pickup_address, pickup_contact, email=pickup_contact.email if pickup_from_type == 'Company' else None)
	recipient_email = recipient_phone = None
	if cint(provider.dhl_send_recipient_contact):
		recipient_email = delivery_contact.email
		# The API does not accept a recipient phone number for German addresses.
		if scope != "domestic":
			recipient_phone = _recipient_phone(delivery_contact)
	consignee = _build_party(delivery_address, delivery_contact, email=recipient_email, phone=recipient_phone)

	ship_date = max(getdate(pickup_date), getdate()).isoformat()
	shipments = []
	for index, parcel in enumerate(plan, start=1):
		dhl_shipment = {
			"product": product_code,
			"billingNumber": parcel.billing_number,
			"shipDate": ship_date,
			"shipper": shipper,
			"consignee": consignee,
			"details": {"weight": {"uom": "kg", "value": round(parcel.weight, 3)}},
		}
		if len(shipment or "") >= 8:
			dhl_shipment["refNo"] = shipment[:35]
		if len(plan) > 1:
			dhl_shipment["costCenter"] = "{0}/{1}".format(index, len(plan))
		if parcel.length and parcel.width and parcel.height:
			dhl_shipment["details"]["dim"] = {
				"uom": "cm", "length": parcel.length, "width": parcel.width, "height": parcel.height}
		shipments.append(dhl_shipment)
	payload = {"profile": provider.dhl_profile or DHL_DEFAULT_PROFILE, "shipments": shipments}

	try:
		# Dry run first: DHL states that a validated request will also be created.
		response = _dhl_request(provider, "POST", "/orders", params={"validate": "true"}, payload=payload)
		data = _parse_json(response)
		if response.status_code >= 400 or any(_item_failed(item) for item in data.get("items") or []):
			_raise_dhl_rejection(response, data, "validation")
		response = _dhl_request(provider, "POST", "/orders", params=_label_params(provider), payload=payload)
	except requests.RequestException:
		frappe.log_error(title="DHL shipment creation failed", message=frappe.get_traceback())
		frappe.throw(_(
			"DHL could not be reached. Check in the DHL business customer portal whether a label was "
			"created before you try again."
		))
	data = _parse_json(response)
	items = [item for item in data.get("items") or [] if isinstance(item, dict)]
	created = [
		(index, item) for index, item in enumerate(items)
		if index < len(plan) and item.get("shipmentNo") and not _item_failed(item)
	]
	if not created:
		_raise_dhl_rejection(response, data, "shipment creation")

	warnings = []
	for _index, item in created:
		_attach_documents(shipment, item)
		warnings.extend(_item_messages(item, {"Warning"}))
	if len(created) < len(plan):
		# Persistent, not a toast: the packer must see which parcels have no label.
		message = _("DHL created {0} of {1} parcels; the others have no label:").format(len(created), len(plan))
		message += "<br>" + _format_lines(_response_messages(data))
		message += "<br>" + _("This Shipment is booked for the created parcels only. Ship the others with a new Shipment.")
		frappe.msgprint(message, title=_("DHL parcels failed"), indicator="red")
	if warnings:
		# DHL documents that labels returned with warnings are valid.
		frappe.msgprint(
			_("DHL created the labels with these notes:") + "<br>" + _format_lines(warnings),
			title=_("DHL notes"), indicator="orange")
	if pickup_type == "Pickup":
		frappe.msgprint(
			_("The DHL integration creates labels only and does not order a pickup. Hand the parcels to DHL "
			  "or use your regular DHL pickup."),
			indicator="orange", alert=True)

	shipment_numbers = ", ".join(str(item["shipmentNo"]) for _index, item in created)
	amount = round(sum(plan[index].price for index, _item in created), 2)
	booking_data = {
		"environment": _environment(provider),
		# Price per booked label: partial bookings and deletions bill only these.
		"parcels": {str(item["shipmentNo"]): plan[index].price for index, item in created},
	}
	return {
		'service_provider': DHL_PROVIDER,
		'shipment_id': shipment_numbers,
		'carrier': DHL_CARRIER,
		'carrier_service': _service_name(product_code),
		'base_price': amount,
		'net_price': amount,
		'total_vat': 0,
		'shipment_amount': amount,
		'awb_number': shipment_numbers,
		'carrier_booking_data': json.dumps(booking_data),
	}


def get_dhl_label(shipment_id):
	"""Return the file URLs of the stored DHL labels, fetching missing ones from DHL."""
	shipment = frappe.db.get_value('Shipment', {'service_provider': DHL_PROVIDER, 'shipment_id': shipment_id}, 'name')
	if not shipment:
		frappe.throw(_("No DHL Shipment found for {0}.").format(escape_html(str(shipment_id))))
	shipment_doc = frappe.get_doc('Shipment', shipment)
	shipment_doc.check_permission('read')

	numbers = _split_numbers(shipment_id)
	labels = {}
	missing = []
	for shipment_no in numbers:
		url = frappe.db.get_value('File', {
			'attached_to_doctype': 'Shipment',
			'attached_to_name': shipment,
			'file_name': _label_file_name('label', shipment_no),
		}, 'file_url')
		if url:
			labels[shipment_no] = url
		else:
			missing.append(shipment_no)
	if not missing:
		return [labels[number] for number in numbers]

	provider = get_dhl_provider()
	_require_booking_environment(provider, shipment_doc.get('carrier_booking_data'))
	params = _label_params(provider)
	params['shipment'] = missing
	response = _dhl_request(provider, 'GET', '/orders', params=params)
	data = _parse_json(response)
	for item in data.get('items') or []:
		if isinstance(item, dict) and str(item.get('shipmentNo')) in missing and not _item_failed(item):
			url = _attach_documents(shipment, item).get('label')
			if url:
				labels[str(item['shipmentNo'])] = url
	unresolved = [number for number in numbers if number not in labels]
	if unresolved:
		# DHL only returns labels until a few days after the end-of-day closing.
		frappe.log_error(
			title="DHL label download failed",
			message="status={0} unresolved={1} requested={2}".format(response.status_code, len(unresolved), len(numbers)),
		)
		message = _("DHL returned no label for shipment(s) {0}.").format(escape_html(", ".join(unresolved)))
		reasons = _response_messages(data)
		if reasons:
			message += "<br>" + _format_lines(reasons)
		if not labels:
			frappe.throw(message, title=_("DHL error"))
		message += "<br>" + _("Only the labels of {0} are opened.").format(
			escape_html(", ".join(number for number in numbers if number in labels)))
		frappe.msgprint(message, title=_("DHL labels missing"), indicator="red")
	return [labels[number] for number in numbers if number in labels]


def cancel_dhl_booking(shipment_id, booking_data=None):
	"""Delete DHL shipments before the end-of-day closing; return (deleted, failure lines)."""
	provider = get_dhl_provider()
	_require_booking_environment(provider, booking_data)
	numbers = _split_numbers(shipment_id)
	if not numbers:
		return [], []
	response = _dhl_request(provider, 'DELETE', '/orders', params={
		'profile': provider.dhl_profile or DHL_DEFAULT_PROFILE,
		'shipment': numbers,
	})
	data = _parse_json(response)
	items = [item for item in data.get('items') or [] if isinstance(item, dict)]
	deleted = [str(item['shipmentNo']) for item in items if item.get('shipmentNo') and not _item_failed(item)]
	failures = _response_messages(data) if len(deleted) < len(numbers) else []
	if len(deleted) < len(numbers) and not failures:
		failures = [_("HTTP {0}").format(response.status_code)]
	if failures:
		frappe.log_error(
			title="DHL shipment deletion failed",
			message="status={0} deleted={1} requested={2}".format(response.status_code, len(deleted), len(numbers)),
		)
	return [number for number in numbers if number in deleted], failures


def _acquire_tracking_slot(api_key, attempts=4):
	"""Claim the API key's next tracking call slot, shared by all workers through Redis.

	SET NX with a 5 s expiry is atomic, so at most one call per key starts every
	DHL_TRACKING_INTERVAL seconds across web and scheduler workers. Returns
	False when no slot became free within the attempts.
	"""
	key = frappe.cache.make_key(DHL_TRACKING_SLOT_KEY.format(hashlib.sha256(api_key.encode()).hexdigest()[:16]))
	for _attempt in range(attempts):
		if frappe.cache.set(key, 1, nx=True, px=DHL_TRACKING_INTERVAL * 1000):
			return True
		wait_ms = frappe.cache.pttl(key)
		time.sleep(max(cint(wait_ms), 100) / 1000)
	return False


def _fetch_tracking_status(api_key, shipment_no):
	"""Return {code, text} from the Unified Tracking API, or None when polling must stop."""
	if not _acquire_tracking_slot(api_key):
		return None
	response = requests.get(
		DHL_TRACKING_API_URL,
		params={'trackingNumber': shipment_no, 'service': 'parcel-de'},
		headers={'DHL-API-Key': api_key, 'Accept': 'application/json'},
		timeout=DHL_TIMEOUT,
	)
	if response.status_code == 404:
		return frappe._dict(code='unknown', text=_('Not yet in DHL tracking'))
	if response.status_code != 200:
		frappe.log_error(
			title="DHL tracking request failed",
			message="status={0}".format(response.status_code),
		)
		return None
	shipments = _parse_json(response).get('shipments') or []
	status = (shipments[0].get('status') if shipments and isinstance(shipments[0], dict) else None) or {}
	return frappe._dict(
		code=status.get('statusCode') or 'unknown',
		text=_clean(status.get('status') or status.get('description') or status.get('statusCode') or ''),
	)


def get_dhl_tracking_data(shipment_id, booking_data=None):
	"""Return tracking links for the DHL shipment numbers and, when enabled, their status.

	Status and status detail are None unless DHL answered for every parcel, so callers keep
	the last confirmed status (a Delivered shipment never falls back to In Progress).
	"""
	numbers = _split_numbers(shipment_id)
	links = [format_tracking_url(DHL_TRACKING_LINK.format(quote(number, safe=''))) for number in numbers]
	tracking = {
		'awb_number': ', '.join(numbers),
		'tracking_url': '<br>'.join(link for link in links if link),
		'tracking_status': None,
		'tracking_status_info': None,
	}
	try:
		provider = get_dhl_provider()
		# Sandbox shipment numbers do not exist in DHL's tracking.
		if (
			not cint(provider.dhl_poll_tracking)
			or cint(provider.dhl_sandbox)
			or _booking_data(booking_data).get("environment") == DHL_SANDBOX
			or not provider.api_key
		):
			return tracking
		statuses = []
		for number in numbers:
			status = _fetch_tracking_status(provider.api_key.strip(), number)
			if status is None:
				break
			statuses.append(status)
		# "unknown" (404 or no status data) is not a fresh status either.
		if numbers and len(statuses) == len(numbers) and all(s.code != 'unknown' for s in statuses):
			tracking['tracking_status_info'] = ', '.join(s.text for s in statuses if s.text)[:140]
			tracking['tracking_status'] = 'Delivered' if all(s.code == 'delivered' for s in statuses) else 'In Progress'
	except Exception:
		frappe.log_error(title="DHL tracking update failed", message=frappe.get_traceback())
	return tracking


def validate_dhl_provider(provider):
	"""Check the DHL configuration on the Shipment Service Provider."""
	ekp = (provider.dhl_ekp or "").strip()
	if ekp and not re.fullmatch(r"\d{10}", ekp):
		frappe.throw(_("The DHL EKP must have exactly 10 digits."))
	provider.dhl_ekp = ekp
	for row in provider.get("dhl_products") or []:
		product = DHL_PRODUCTS.get(row.product)
		if not product:
			frappe.throw(_("Row {0}: unsupported DHL product {1}.").format(row.idx, escape_html(row.product or "")))
		row.participation = (row.participation or "").strip().upper()
		if not re.fullmatch(r"[0-9A-Z]{2}", row.participation):
			frappe.throw(_("Row {0}: the participation must be two digits or letters, e.g. 01.").format(row.idx))
		if flt(row.net_price) < 0:
			frappe.throw(_("Row {0}: the net price cannot be negative.").format(row.idx))
		if flt(row.max_weight) < 0 or flt(row.max_weight) > product.max_weight:
			frappe.throw(_("Row {0}: {1} allows at most {2} kg.").format(row.idx, product.label, product.max_weight))
	if cint(provider.enabled):
		if not ekp:
			frappe.throw(_("Enter the DHL EKP before enabling DHL."))
		if not (provider.api_key and provider.dhl_gkp_username and provider.dhl_gkp_password):
			frappe.throw(_("Enter the API Key, GKP Username and GKP Password before enabling DHL."))
		if not provider.get("dhl_products"):
			frappe.throw(_("Add at least one DHL product with its participation before enabling DHL."))
