#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt


import requests
import frappe
import json
import re
from datetime import timedelta
from math import ceil, isfinite
from frappe import _
from frappe.utils import escape_html, now_datetime, today
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
from shipment.api import customs
from shipment.api.letmeship_billing import recipient_billing, net_charge, mark_attempt, ensure_no_uncertain_booking
from shipment.api.utils import (
    get_address,
    get_company_contact,
    get_contact,
    get_tracking_url,
    fit_letmeship_address,
    validate_letmeship_address,
)


def _letmeship_auto_split_enabled():
    """Read the Shipment Service Provider's auto_split_long_street toggle.

    Defaults to enabled when the field is missing (e.g. before the doctype
    migration has been applied) so upgrades never regress behaviour.
    """
    value = frappe.db.get_value(
        'Shipment Service Provider', 'Let Me Ship', 'auto_split_long_street'
    )
    if value is None:
        return True
    return bool(int(value))


def _letmeship_response_diagnostics(response):
    """Format a one-line diagnostic string for a requests.Response.

    Captures HTTP status, reason, elapsed time, request id, content type,
    and body length so transient API issues can be classified from the
    Error Log alone (5xx vs 401 vs truly empty body) without needing to
    reproduce the call.
    """
    if response is None:
        return "response=None"
    headers = getattr(response, 'headers', {}) or {}
    request_id = (
        headers.get('x-request-id')
        or headers.get('X-Request-ID')
        or headers.get('x-amzn-RequestId')
        or 'n/a'
    )
    body_text = getattr(response, 'text', '') or ''
    return (
        f"status={getattr(response, 'status_code', 'n/a')} "
        f"reason={getattr(response, 'reason', 'n/a')} "
        f"elapsed={getattr(response, 'elapsed', 'n/a')} "
        f"x-request-id={request_id} "
        f"content-type={headers.get('content-type', 'n/a')} "
        f"body_len={len(body_text)}"
    )


def _raise_letmeship_rejection(response):
	"""Log a non-2xx booking response with its body, then show its reason."""
	try:
		error_response = json.loads(response.text)
	except json.JSONDecodeError:
		error_response = {}
	if not isinstance(error_response, dict):
		error_response = {}
	error_detail = _get_letmeship_user_error(error_response) or _(
		"LetMeShip rejected the shipment request."
	)
	# Log the body's shape so a rejection can be classified (ERPNEXT-1C), but
	# none of its text: the body and the reason in it can echo the booking's
	# address, phone and email. The user sees the reason in the error below.
	frappe.log_error(
		message=f"{_letmeship_response_diagnostics(response)}\nbody_keys={sorted(error_response)}",
		title="LetMeShip shipment creation failed",
	)
	frappe.throw(_("LetMeShip could not create the shipment: {0}").format(error_detail))


def _letmeship_rate_rejection_message(response):
	"""Return the reason a rate request was rejected, logging only unexplained ones.

	A 4xx that names its reason (a missing recipient phone, an invalid zip)
	is a data problem on the Shipment: the user sees it next to the other
	providers' rates, so it is not logged. Anything else is logged with the
	response's shape but none of its text, which can echo the address, phone
	and email (ERPNEXT-1C, ERPNEXT-28, ERPNEXT-29).
	"""
	try:
		error_response = json.loads(response.text)
	except (TypeError, ValueError):
		error_response = {}
	if not isinstance(error_response, dict):
		error_response = {}
	error_detail = _get_letmeship_user_error(error_response)
	if _letmeship_reasons(error_response) and 400 <= response.status_code < 500:
		return error_detail
	frappe.log_error(
		message=f"{_letmeship_response_diagnostics(response)}\nbody_keys={sorted(error_response)}",
		title="LetMeShip rate request failed",
	)
	return error_detail or _("LetMeShip returned no rates (HTTP {0}).").format(response.status_code)


def _letmeship_no_services_message(http_response, response_data):
	"""Log a 200 rate response without a service list and return its reason.

	Like a rejection, the body can echo the address, phone and email, so
	only its shape is logged.
	"""
	if not isinstance(response_data, dict):
		response_data = {}
	frappe.log_error(
		message=f"{_letmeship_response_diagnostics(http_response)}\nbody_keys={sorted(response_data)}",
		title="LetMeShip rate request returned no services",
	)
	return _get_letmeship_user_error(response_data)


def _normalize_goods_value(value_of_goods):
	"""Return the integer goods value required by LetMeShip."""
	try:
		numeric_value = float(value_of_goods)
		if not isfinite(numeric_value):
			raise ValueError
		return ceil(numeric_value)
	except (OverflowError, TypeError, ValueError):
		return frappe.throw(_("Value of goods must be a valid number."))


def _letmeship_reasons(error_response):
	"""Return the non-blank reasons a LetMeShip error response names."""
	status = error_response.get("status")
	if not isinstance(status, dict):
		status = {}
	# A 4xx can also carry its reason as a top-level "message" (ERPNEXT-1C).
	for messages in (status.get("message"), error_response.get("message")):
		if messages is None:
			continue
		if not isinstance(messages, list):
			messages = [messages]
		# A blank entry is no reason; an all-blank list falls through.
		reasons = [str(message) for message in messages if message is not None and str(message).strip()]
		if reasons:
			return reasons
	return []


def _get_letmeship_user_error(error_response):
	"""Return a safe, useful message from a LetMeShip error response."""
	messages = _letmeship_reasons(error_response)
	if messages:
		return "<br>".join(escape_html(message) for message in messages)
	if error_response.get("errorMessage"):
		return _(
			"LetMeShip could not process the shipment data. Please check the addresses, "
			"parcel dimensions, and value of goods."
		)
	return None


def _parse_json_list(value):
	"""Return a list from a Frappe request argument or an existing list."""
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except json.JSONDecodeError:
			value = [value]
	if not isinstance(value, (list, tuple, set)):
		value = [value]
	return [item for item in value if item]


def _pickup_clock(value, default):
    """Normalize Frappe Time strings/timedeltas to the carrier's HH:MM:SS."""
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?', str(value or default))
    if not match:
        frappe.throw(_('Enter a valid pickup time.'))
    hour, minute, second = (int(part or 0) for part in match.groups())
    if hour > 23 or minute > 59 or second > 59:
        frappe.throw(_('Enter a valid pickup time.'))
    return f'{hour:02d}:{minute:02d}:{second:02d}'


def _get_pickup_interval(pickup_date, requested_from, requested_to, pickup_order, now=None):
	"""Return a valid LetMeShip pickup interval, moving expired windows to tomorrow."""
	now = now or now_datetime()
	pickup_date = str(pickup_date)
	current_date = now.strftime("%Y-%m-%d")
	if pickup_date < current_date:
		frappe.throw(_("Pickup Date cannot be in the past"))
	interval = {"date": pickup_date}
	if not pickup_order:
		return interval

	time_from = _pickup_clock(requested_from, "09:00")
	time_to = _pickup_clock(requested_to, "17:00")
	if time_from >= time_to:
		frappe.throw(_("Pickup start must be before pickup end."))
	current_time = now.strftime("%H:%M:%S")
	cutoff_time = min(time_to, "17:00:00")
	if pickup_date == current_date and current_time > time_from:
		if now.minute < 30:
			next_time = now.replace(minute=30, second=0, microsecond=0)
		else:
			next_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
		next_time_text = next_time.strftime("%H:%M:%S")
		if next_time_text >= cutoff_time:
			interval["date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
		else:
			time_from = next_time_text
	interval.update({"timeFrom": time_from, "timeTo": time_to})
	return interval


def get_letmeship_available_services(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    pickup_contact_name=None,
    delivery_contact_name=None,
    pickup_type=None,
    shipment=None,
):

    pickup_address = get_address(pickup_address_name)
    delivery_address = get_address(delivery_address_name)
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    # LetMeShip have limit of 30 characters for Company field

    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = \
            delivery_address.address_title[:30]

    auto_split = _letmeship_auto_split_enabled()
    fit_letmeship_address(pickup_address, auto_split=auto_split, role="pickup")
    fit_letmeship_address(delivery_address, auto_split=auto_split, role="delivery")
    validate_letmeship_address(pickup_address, role="pickup")
    validate_letmeship_address(delivery_address, role="delivery")

    pickupOrder = False
    if pickup_type and pickup_type == "Pickup":
        pickupOrder = True

    quote_doc = frappe.get_doc('Shipment', shipment) if shipment else None
    pickup_interval = _get_pickup_interval(
        pickup_date,
        quote_doc.pickup_from if quote_doc else "09:00",
        quote_doc.pickup_to if quote_doc else "17:00",
        pickupOrder,
    )

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)

    if not service_provider:
        return []

    url = 'https://api.letmeship.com/v1/available'
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    payload = {'pickupInfo': {
        'address': {
            'countryCode': pickup_address.country_code,
            'zip': pickup_address.pincode,
            'city': pickup_address.city,
            'street': pickup_address.address_line1,
            'addressInfo1': (pickup_address.address_line1_con if pickup_address.get('address_line1_con') else pickup_address.address_line2) or '',
            'addressInfo2': (pickup_address.address_line2 if pickup_address.get('address_line1_con') else '') or '',
            'houseNo': '',
        },
        'company': pickup_address.address_title,
        'person': {'title': pickup_contact.title,
                   'firstname': pickup_contact.first_name,
                   'lastname': pickup_contact.last_name},
        'phone': {'phoneNumber': pickup_contact.phone,
                  'phoneNumberPrefix': pickup_contact.phone_prefix.replace(" ", "")
                  if ' ' in pickup_contact.phone_prefix else pickup_contact.phone_prefix},
        'email': pickup_contact.email,
    }, 'deliveryInfo': {
        'address': {
            'countryCode': delivery_address.country_code,
            'zip': delivery_address.pincode,
            'city': delivery_address.city,
            'street': delivery_address.address_line1,
            'addressInfo1': (delivery_address.address_line1_con if delivery_address.get('address_line1_con') else delivery_address.address_line2) or '',
            'addressInfo2': (delivery_address.address_line2 if delivery_address.get('address_line1_con') else '') or '',
            'houseNo': '',
            'stateCode': delivery_address.state if delivery_address.state != '' else None
        },
        'company': delivery_address.address_title,
        'person': {'title': delivery_contact.title,
                   'firstname': delivery_contact.first_name,
                   'lastname': delivery_contact.last_name},
        'phone': {'phoneNumber': delivery_contact.phone,
                  'phoneNumberPrefix': delivery_contact.phone_prefix.replace(" ", "")
                  if ' ' in delivery_contact.phone_prefix else delivery_contact.phone_prefix},
        'email': delivery_contact.email,
    }, 'shipmentDetails': {
        'contentDescription': description_of_content,
        'shipmentType': 'PARCEL',
        'shipmentSettings': {
            'saturdayDelivery': False,
            'ddp': False,
            'insurance': False,
            'pickupOrder': pickupOrder,
            'pickupTailLift': False,
            'deliveryTailLift': False,
            'holidayDelivery': False,
        },
        'goodsValue': _normalize_goods_value(value_of_goods),
        'parcelList': parcel_list,
        'pickupInterval': pickup_interval
    }}

    billing = recipient_billing(frappe.get_doc('Shipment', shipment), payload['deliveryInfo']) if shipment else None
    if billing:
        payload['shipmentDetails']['exWorks'] = billing

    try:
        available_services = []
        response_data = requests.post(url=url,
                                      auth=(service_provider.api_key,
                                            service_provider.api_password), headers=headers,
                                      data=json.dumps(payload), timeout=30)
        
        # Check HTTP status
        if response_data.status_code != 200:
            frappe.local.response['letmeship_error'] = \
                _letmeship_rate_rejection_message(response_data)
            return []
        
        http_response = response_data
        response_data = json.loads(http_response.text)
        if isinstance(response_data, dict) and 'serviceList' in response_data:
            for response in response_data['serviceList']:
                available_service = frappe._dict()
                basic_info = response['baseServiceDetails']
                price_info = basic_info['priceInfo']
                available_service.service_provider = 'LetMeShip'
                available_service.id = basic_info['id']
                available_service.carrier = basic_info['carrier']
                available_service.service_name = \
                    match_parcel_service_type_alias(basic_info['name'],
                                                    basic_info['carrier'])
                available_service.is_preferred = \
                    frappe.db.get_value('Parcel Service Type',
                                        available_service.service_name,
                                        'show_in_preferred_services_list')
                available_service.real_weight = price_info['realWeight']
                if billing and (basic_info['carrier'] != 'UPS' or 'RECEIVER_PAYS' not in response.get('supportedExWorkType', [])):
                    continue
                available_service.total_price = net_charge(price_info)
                available_service.pickup_interval = pickup_interval
                available_service.recipient_billing = {'type': billing['type'], 'account': billing['accountNumber']} if billing else None
                available_service.price_info = price_info
                # Services that clear customs electronically (DHL Paperless Trade) need CIN data.
                available_service.cin_data_required = bool(response.get('isCinDataRequired'))
                available_service.mrn_required = bool(response.get('isMrnRequired'))
                available_services.append(available_service)
            return available_services
        else:
            # Log error but don't throw - return empty list instead
            error_detail = _letmeship_no_services_message(http_response, response_data)
            if error_detail:
                frappe.local.response['letmeship_error'] = error_detail
            return []
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        frappe.log_error(f"Error occurred while fetching LetMeShip Prices: {str(exc)}\nTraceback: {error_trace}")
    return []


# LetMeShip's reasons for export; documents need no customs invoice.
CIN_EXPORT_REASONS = {'Sale': 'COMMERCIAL_PURPOSE_OR_SALE', 'Sample': 'SAMPLE', 'Return': 'RETURN'}
# LetMeShip requires an MRN (export declaration) from this goods value on.
MRN_GOODS_VALUE = 1000


def cin_data(shipment_doc):
    """The Shipment's customs items as LetMeShip CIN data; list everything missing first."""
    parties = customs.require_customs_data(shipment_doc, 'LetMeShip')
    if shipment_doc.reason_for_export not in CIN_EXPORT_REASONS:
        frappe.throw(_('LetMeShip sends no customs invoice for documents. Choose another Reason for Export.'))
    value = customs.goods_value(shipment_doc)
    if value >= MRN_GOODS_VALUE:
        frappe.throw(_('LetMeShip needs an export declaration (MRN) for goods of {0} EUR or more, which the ERP does not '
                       'create. Book this Shipment with another provider.').format(MRN_GOODS_VALUE))
    items = []
    for row in shipment_doc.customs_items:
        quantity = max(1, int(round(float(row.qty))))
        weight = round(max(float(row.unit_weight or 0) * quantity, 0.01), 3)
        items.append({
            'itemDescription': row.description.strip()[:170],
            'commodityCode': row.customs_tariff_number.replace(' ', ''),
            'manufactureCountry': customs.country_code(row.country_of_origin),
            'quantity': quantity,
            'quantityUnitOfMeasure': 'PIECES',
            'unitPrice': round(float(row.unit_value), 2),
            'netWeight': weight,
            'grossWeight': weight,
            'order': len(items) + 1,
        })
    return {
        'eori': parties.eori,
        'invDate': today(),
        'invoiceNumber': customs.invoice_number(shipment_doc)[:35],
        'exportReason': CIN_EXPORT_REASONS[shipment_doc.reason_for_export],
        'invoiceValue': value,
        'cinDataItem': items,
    }


# A shipment ID is allocated before the asynchronous carrier booking finishes.
LETMESHIP_STATES = frozenset({
    'BOOKING_IN_PROGRESS', 'BOOKING_FINISHED', 'LOCKED',
    'CANCEL_REQUESTED', 'CANCELLED', 'NOT_FOUND',
})


def get_letmeship_booking_state(shipment_id):
    """Read the booking outcome, not the carrier's later tracking events."""
    provider = frappe.db.get_value('Shipment Service Provider', 'Let Me Ship',
                                   ['api_key', 'api_password'], as_dict=True)
    response = requests.get(
        f'https://api.letmeship.com/v1/shipments/{shipment_id}/states',
        auth=(provider.api_key, provider.api_password), timeout=20,
    )
    response.raise_for_status()
    state = response.json()
    if (not isinstance(state, dict)
            or str(state.get('shipmentId')) != str(shipment_id)
            or state.get('shipmentState') not in LETMESHIP_STATES):
        raise ValueError('Invalid LetMeShip booking state response')
    return state


def letmeship_booking_values(state, requested_settings=None):
    """Keep the full provider reason, without cancelling the ERP document."""
    state = state or {}
    code = state.get('shipmentState', 'NOT_CHECKED')
    status = 'Booking Pending'
    # Completed historical bookings can be locked against edits, with a ready label.
    if code == 'BOOKING_FINISHED' or (code == 'LOCKED' and state.get('labelState') == 'READY'):
        status = 'Booked'
    elif code in {'CANCELLED', 'NOT_FOUND'}:
        status = 'Booking Failed'
    details = {key: state.get(key) for key in
               ('shipmentState', 'labelState', 'entryDocumentsState', 'noteType', 'text')}
    details['checked_at'] = str(now_datetime())
    if requested_settings is not None:
        details['requested_settings'] = requested_settings
    return {
        'status': status,
        'carrier_booking_data': json.dumps({'letmeship': details}),
        'tracking_status_info': 'LetMeShip: ' + code,
    }


def _letmeship_booking_message(state):
    code = (state or {}).get('shipmentState', 'NOT_CHECKED')
    reason = (state or {}).get('text') or (state or {}).get('noteType') or code
    return _('LetMeShip booking {0}: {1}').format(escape_html(code), escape_html(reason))


def create_letmeship_shipment(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    service_info,
    shipment_notific_email,
    tracking_notific_email,
    pickup_contact_name=None,
    delivery_contact_name=None,
    pickup_type=None,
    shipment=None,
    label_email=True,
):

    pickup_address = get_address(pickup_address_name)
    delivery_address = get_address(delivery_address_name)
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    # LetMeShip have limit of 30 characters for Company field

    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = \
            delivery_address.address_title[:30]

    auto_split = _letmeship_auto_split_enabled()
    fit_letmeship_address(pickup_address, auto_split=auto_split, role="pickup")
    fit_letmeship_address(delivery_address, auto_split=auto_split, role="delivery")
    validate_letmeship_address(pickup_address, role="pickup")
    validate_letmeship_address(delivery_address, role="delivery")

    pickupOrder = False
    if pickup_type and pickup_type == "Pickup":
        pickupOrder = True

    # Get pickup times from the Shipment document
    shipment_doc = frappe.get_doc("Shipment", shipment)
    pickup_interval = _get_pickup_interval(
        pickup_date,
        shipment_doc.pickup_from,
        shipment_doc.pickup_to,
        pickupOrder,
    )
    if service_info.get('pickup_interval') and service_info['pickup_interval'] != pickup_interval:
        frappe.throw(_('The pickup interval changed. Request a fresh quote before booking.'))

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    if not service_provider:
        return []

    shipment_notification_emails = _parse_json_list(shipment_notific_email)
    tracking_notification_emails = _parse_json_list(tracking_notific_email)

    url = 'https://api.letmeship.com/v1/shipments'
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    payload = {
        'pickupInfo': {
            'address': {
                'countryCode': pickup_address.country_code,
                'zip': pickup_address.pincode,
                'city': pickup_address.city,
                'street': pickup_address.address_line1,
                'addressInfo1': (pickup_address.address_line1_con if pickup_address.get('address_line1_con') else pickup_address.address_line2) or '',
                'addressInfo2': (pickup_address.address_line2 if pickup_address.get('address_line1_con') else '') or '',
                'houseNo': '',
            },
            'company': pickup_address.address_title,
            'person': {'title': pickup_contact.title,
                       'firstname': pickup_contact.first_name,
                       'lastname': pickup_contact.last_name},
            'phone': {'phoneNumber': pickup_contact.phone,
                      'phoneNumberPrefix': pickup_contact.phone_prefix.strip()},
            'email': pickup_contact.email,
        },
        'deliveryInfo': {
            'address': {
                'countryCode': delivery_address.country_code,
                'zip': delivery_address.pincode,
                'city': delivery_address.city,
                'street': delivery_address.address_line1,
                'addressInfo1': (delivery_address.address_line1_con if delivery_address.get('address_line1_con') else delivery_address.address_line2) or '',
                'addressInfo2': (delivery_address.address_line2 if delivery_address.get('address_line1_con') else '') or '',
                'houseNo': '',
                'stateCode': delivery_address.state if delivery_address.state != '' else None
            },
            'company': delivery_address.address_title,
            'person': {'title': delivery_contact.title,
                       'firstname': delivery_contact.first_name,
                       'lastname': delivery_contact.last_name},
            'phone': {'phoneNumber': delivery_contact.phone,
                      'phoneNumberPrefix': delivery_contact.phone_prefix.strip()},
            'email': delivery_contact.email,
        },
        'service': {
            'baseServiceDetails': {
                'id': service_info.get('id'),
                'name': service_info.get('service_name'),
                'carrier': service_info.get('carrier'),
                'priceInfo': service_info.get('price_info', {}),
            },
            'supportedExWorkType': [],
            'messages': [''],
            'description': '',
            'serviceInfo': '',
        },
        'shipmentDetails': {
            'contentDescription': description_of_content,
            'shipmentType': 'PARCEL',
            'shipmentSettings': {
                'saturdayDelivery': False,
                'ddp': False,
                'insurance': False,
                'pickupOrder': pickupOrder,
                'pickupTailLift': False,
                'deliveryTailLift': False,
                'holidayDelivery': False,
            },
            'goodsValue': _normalize_goods_value(value_of_goods),
            'parcelList': parcel_list,
            'pickupInterval': pickup_interval,
        },
        'shipmentNotification': {'trackingNotification': {
            'deliveryNotification': True,
            'problemNotification': True,
            'emails': tracking_notification_emails,
            'notificationText': '',
        }, 'recipientNotification': {'notificationText': '',
                                     'emails': shipment_notification_emails}},
        'labelEmail': True,
    }
    billing = recipient_billing(shipment_doc, payload['deliveryInfo'])
    if billing:
        if service_info.get('carrier') != 'UPS':
            frappe.throw(_('This customer account can only pay for UPS; do not use another carrier.'))
        payload['shipmentDetails']['exWorks'] = billing
        payload['service']['supportedExWorkType'] = ['RECEIVER_PAYS']
    payload['labelEmail'] = label_email
    ensure_no_uncertain_booking(shipment_doc)
    if service_info.get('cin_data_required') and shipment:
        payload['cinData'] = cin_data(frappe.get_doc('Shipment', shipment))
    payload['shipmentDetails']['note'] = shipment
    attempt = mark_attempt(shipment, payload['shipmentDetails']['shipmentSettings'], billing)
    shipment_id = None
    
    try:
        response_data = requests.post(url=url,
                                      auth=(service_provider.api_key,
                                            service_provider.api_password), headers=headers,
                                      data=json.dumps(payload), timeout=30)
        
        # Check if response is valid before parsing JSON. Note: do not use
        # `not response_data` here -- requests.Response is falsy for any
        # non-2xx, which would silently swallow 4xx/5xx error bodies that
        # the parse-then-`message` branch below is meant to surface.
        if response_data is None or not response_data.text:
            frappe.log_error(
                message=_letmeship_response_diagnostics(response_data),
                title="Empty response from LetMeShip API",
            )
            return {'booking_uncertain': True}

        if not 200 <= response_data.status_code < 300:
            if response_data.status_code == 400:
                try:
                    rejected = response_data.json()
                    if not rejected.get('shipmentId') and str((rejected.get('status') or {}).get('code')) in {'1500', '2000'}:
                        frappe.db.set_value('Shipment', shipment, {'status': 'Booking Failed', 'carrier_booking_data': json.dumps({'letmeship': {**attempt, 'attempt_state': 'rejected'}})})
                        frappe.db.commit()
                except (ValueError, TypeError, AttributeError):
                    # Malformed rejection data leaves the committed attempt uncertain.
                    pass
            _raise_letmeship_rejection(response_data)
            
        try:
            response_data = json.loads(response_data.text)
        except Exception as json_exc:
            frappe.log_error(f"Failed to parse JSON response: {str(json_exc)}\nResponse: {response_data.text}")
            return {'booking_uncertain': True}
            
        if not response_data:
            frappe.log_error("Empty JSON data from LetMeShip API")
            return {}
            
        if response_data.get('shipmentId'):
            shipment_id = response_data['shipmentId']
            # Retain the carrier reference before interpreting any optional response data.
            frappe.db.set_value('Shipment', shipment, {
                'shipment_id': str(shipment_id), 'service_provider': 'LetMeShip',
                'carrier': service_info['carrier'], 'carrier_service': service_info['service_name'],
                'carrier_booking_data': json.dumps({'letmeship': {**attempt, 'attempt_state': 'accepted'}}),
            })
            frappe.db.commit()
            # Safe access to nested dictionaries
            service = response_data.get('service', {})
            base_service_details = service.get('baseServiceDetails', {}) if service else {}
            price_info = base_service_details.get('priceInfo', {}) if base_service_details else {}
            
            base_price = price_info.get('basePrice', 0)
            net_price = net_charge(price_info)
            total_vat = price_info.get('totalVat', 0)
            shipment_amount = price_info.get('totalPrice', 0)
            awb_number = ''
            
            shipment_id = response_data.get('shipmentId')
            if not shipment_id:
                frappe.log_error("Missing shipmentId in response")
                return {}
                
            frappe.db.set_value('Shipment', shipment, {
                'shipment_id': str(shipment_id), 'service_provider': 'LetMeShip',
                'carrier': service_info['carrier'], 'carrier_service': service_info['service_name'],
                'pickup_date': payload['shipmentDetails']['pickupInterval']['date'],
                'base_price': base_price, 'net_price': net_price,
                'total_vat': total_vat, 'shipment_amount': shipment_amount,
                'carrier_booking_data': json.dumps({'letmeship': {**attempt, 'attempt_state': 'accepted'}}),
            })
            frappe.db.commit()
            booking_values = letmeship_booking_values(None, payload['shipmentDetails']['shipmentSettings'])
            try:
                state = get_letmeship_booking_state(shipment_id)
                booking_values = letmeship_booking_values(state, payload['shipmentDetails']['shipmentSettings'])
                if booking_values['status'] != 'Booked':
                    frappe.msgprint(_letmeship_booking_message(state), indicator='orange')
            except Exception:
                # The create succeeded: never lose its ID because a later GET failed.
                frappe.msgprint(_('LetMeShip accepted the request; its booking result is not yet confirmed. '
                                  'Use Update Tracking and Cost to check again.'), indicator='orange')
            details = json.loads(booking_values['carrier_booking_data'])
            details['letmeship'].update({**attempt, 'attempt_state': 'accepted'})
            details['letmeship']['acknowledged_settings'] = (response_data.get('shipmentDetails') or {}).get('shipmentSettings')
            booking_values['carrier_booking_data'] = json.dumps(details)
            try:
                tracking_response = requests.get(
                    f'https://api.letmeship.com/v1/shipments/{shipment_id}',
                    auth=(service_provider.api_key, service_provider.api_password),
                    headers=headers, timeout=20
                )
                
                if tracking_response is None or not tracking_response.text:
                    frappe.log_error(
                        message=_letmeship_response_diagnostics(tracking_response),
                        title="Empty tracking response from LetMeShip API",
                    )
                    tracking_response_data = {}
                else:
                    tracking_response_data = json.loads(tracking_response.text)
            except Exception as track_exc:
                frappe.log_error(f"Error getting tracking data: {str(track_exc)}")
                tracking_response_data = {}
            
            if tracking_response_data and 'trackingData' in tracking_response_data and tracking_response_data.get('trackingData') and 'parcelList' in tracking_response_data.get('trackingData', {}):
                for parcel in tracking_response_data.get('trackingData', {}).get('parcelList', []):
                    if parcel and 'awbNumber' in parcel:
                        awb_number = parcel['awbNumber']
            if booking_values['status'] == 'Booking Failed':
                awb_number = ''
            return {
                'service_provider': 'LetMeShip',
                'shipment_id': response_data['shipmentId'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                'base_price': base_price,
                'net_price': net_charge(price_info),
                'total_vat': total_vat,
                'shipment_amount': shipment_amount,
                'awb_number': awb_number or '',
                **booking_values,
            }
        elif 'message' in response_data:
            error_msg = response_data.get('message', 'Unknown error')
            frappe.log_error(f"Error occurred while creating Shipment: {error_msg}")
            frappe.throw(_('Error occurred while creating Shipment: {0}').format(error_msg))
        else:
            frappe.throw(_("LetMeShip did not return a shipment ID."))
        return {}  # not reached: frappe.throw raises
    except frappe.ValidationError:
        raise
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        frappe.log_error(f"Error in create_letmeship_shipment: {str(exc)}\nTraceback: {error_trace}")
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                          ).format(str(exc)), indicator='orange',
                        alert=True)
        if shipment_id:
            return {'service_provider': 'LetMeShip', 'carrier': service_info['carrier'],
                    'carrier_service': service_info['service_name'], 'shipment_id': shipment_id,
                    'status': 'Booking Pending', 'base_price': 0, 'net_price': 0,
                    'total_vat': 0, 'shipment_amount': 0,
                    'carrier_booking_data': json.dumps({'letmeship': {**attempt, 'attempt_state': 'accepted'}})}
        return {'booking_uncertain': True, 'shipment_id': None, 'status': 'Booking Pending'}


def get_letmeship_label(shipment_id):

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    shipment_label_response = \
        requests.get('https://api.letmeship.com/v1/shipments/{id}/documents?types=LABEL'.format(id=shipment_id),
                     auth=(service_provider.api_key,
                           service_provider.api_password), headers=headers, timeout=20)
    shipment_label_response.raise_for_status()
    shipment_label_response_data = \
        json.loads(shipment_label_response.text)
    if 'documents' in shipment_label_response_data:
        for label in shipment_label_response_data['documents']:
            if label.get('data'):
                return json.dumps(label['data'])
    else:
        error_msg = shipment_label_response_data.get('message', 'Unknown error')
        frappe.log_error(f"Error occurred while printing Shipment: {error_msg}")
        frappe.throw(_('Error occurred while printing Shipment: {0}'
                       ).format(error_msg))
    state = get_letmeship_booking_state(shipment_id)
    frappe.throw(_('{0}. No shipping label is available.').format(_letmeship_booking_message(state)))


TRACKING_TIMEOUT_LOG_INTERVAL = 7 * 24 * 3600
TRACKING_TIMEOUT_CACHE_KEY = 'shipment:letmeship-tracking-timeout:{}'


def _log_tracking_timeout(shipment_id, message):
    """Log a slow tracking answer once a week per booking; the daily job retries it anyway."""
    key = TRACKING_TIMEOUT_CACHE_KEY.format(shipment_id)
    if frappe.cache.get_value(key):
        frappe.logger('letmeship', allow_site=True).warning(message)
        return
    frappe.cache.set_value(key, 1, expires_in_sec=TRACKING_TIMEOUT_LOG_INTERVAL)
    frappe.log_error(message)


def get_letmeship_tracking_data(shipment_id, shipment_doc_name=None):
    """ return letmeship tracking data """

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    state = get_letmeship_booking_state(shipment_id)
    booking_values = letmeship_booking_values(state)
    if booking_values['status'] != 'Booked':
        if state['shipmentState'] == 'CANCELLED':
            return {**booking_values, 'awb_number': '', 'tracking_url': '', 'tracking_status': ''}
        # A pending/locked/unknown booking is not proof that an existing AWB is invalid.
        return booking_values
    try:
        tracking_data_response = \
            requests.get('https://api.letmeship.com/v1/tracking?shipmentid={id}'.format(id=shipment_id),
                         auth=(service_provider.api_key,
                               service_provider.api_password), headers=headers, timeout=20)
        tracking_data_response.raise_for_status()
        tracking_data = json.loads(tracking_data_response.text)
        if tracking_data.get('awbNumber'):
            tracking_status = 'In Progress'
            if tracking_data.get('lmsTrackingStatus') and tracking_data['lmsTrackingStatus'].startswith('DELIVERED'):
                tracking_status = 'Delivered'
            if tracking_data.get('lmsTrackingStatus') == 'RETURNED':
                tracking_status = 'Returned'
            if tracking_data.get('lmsTrackingStatus') == 'LOST':
                tracking_status = 'Lost'
            tracking_url = get_tracking_url(carrier=tracking_data['carrier'
                                                                  ], tracking_number=tracking_data['awbNumber'])
            return {
                **booking_values,
                'awb_number': tracking_data['awbNumber'],
                'tracking_status': tracking_status,
                'tracking_status_info': tracking_data.get('lmsTrackingStatus'),
                'tracking_url': tracking_url,
            }
        elif 'message' in tracking_data:
            shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
            frappe.throw(_('Error occurred while updating Shipment {0}{1}'
                           ).format(shipment_info, tracking_data['message']))
        # Carrier tracking can lag a confirmed booking; do not fabricate an AWB.
        return booking_values
    except Exception as exc:
        shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
        message = f"Error occurred while updating Shipment {shipment_info}{str(exc)}"
        if isinstance(exc, requests.Timeout):
            _log_tracking_timeout(shipment_id, message)
        else:
            frappe.log_error(message)
        frappe.msgprint(_('Error occurred while updating Shipment {0}{1}'
                          ).format(shipment_info, str(exc)), indicator='orange',
                        alert=True)
        return booking_values


def get_parcel_list(shipment_parcel, description_of_content):
    parcel_list = []
    if not shipment_parcel:
        return parcel_list
        
    for parcel in shipment_parcel:
        if not parcel:
            continue
            
        formatted_parcel = {}
        formatted_parcel['height'] = parcel.get('height')
        formatted_parcel['width'] = parcel.get('width')
        formatted_parcel['length'] = parcel.get('length')
        formatted_parcel['weight'] = parcel.get('weight')
        formatted_parcel['quantity'] = parcel.get('count')
        formatted_parcel['contentDescription'] = description_of_content
        parcel_list.append(formatted_parcel)
    return parcel_list
