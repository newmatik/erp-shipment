#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt


import requests
import frappe
import json
from datetime import timedelta
from math import ceil
from frappe import _
from frappe.utils import escape_html, now_datetime
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
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


def _normalize_goods_value(value_of_goods):
	"""Return the integer goods value required by LetMeShip."""
	try:
		return ceil(float(value_of_goods))
	except (TypeError, ValueError):
		frappe.throw(_("Value of goods must be a valid number."))


def _get_letmeship_user_error(error_response):
	"""Return a safe, useful message from a LetMeShip error response."""
	status = error_response.get("status") or {}
	messages = status.get("message")
	if messages:
		if not isinstance(messages, list):
			messages = [messages]
		return "<br>".join(escape_html(str(message)) for message in messages)
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

	time_from = f"{requested_from or '09:00'}:00"
	time_to = f"{requested_to or '17:00'}:00"
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
    pickup_type=None
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

    pickup_interval = _get_pickup_interval(
        pickup_date,
        "09:00",
        "17:00",
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

    try:
        available_services = []
        response_data = requests.post(url=url,
                                      auth=(service_provider.api_key,
                                            service_provider.api_password), headers=headers,
                                      data=json.dumps(payload))
        
        # Check HTTP status
        if response_data.status_code != 200:
            error_msg = f"HTTP {response_data.status_code}: {response_data.text[:200]}"
            frappe.log_error(f"LetMeShip API error - {error_msg}")
            
            # Try to parse error message from response
            try:
                error_response = json.loads(response_data.text)
                error_detail = _get_letmeship_user_error(error_response)
                if error_detail:
                    frappe.local.response['letmeship_error'] = error_detail
            except Exception as e:
                frappe.log_error(f"Error parsing error response: {str(e)}")
            
            return []
        
        response_data = json.loads(response_data.text)
        if 'serviceList' in response_data:
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
                available_service.total_price = price_info['netPrice']
                available_service.price_info = price_info
                available_services.append(available_service)
            return available_services
        else:
            # Log error but don't throw - return empty list instead
            error_msg = response_data.get('message', 'No serviceList in response')
            frappe.log_error(f"LetMeShip API returned no services: {error_msg}\nResponse: {json.dumps(response_data)[:500]}")
            
            # Try to extract error message for user
            if 'status' in response_data and 'message' in response_data['status']:
                messages = response_data['status']['message']
                if isinstance(messages, list):
                    error_detail = '<br>'.join(messages)
                else:
                    error_detail = str(messages)
                frappe.local.response['letmeship_error'] = error_detail
            
            return []
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        frappe.log_error(f"Error occurred while fetching LetMeShip Prices: {str(exc)}\nTraceback: {error_trace}")
    return []


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
    shipment=None
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
    
    try:
        response_data = requests.post(url=url,
                                      auth=(service_provider.api_key,
                                            service_provider.api_password), headers=headers,
                                      data=json.dumps(payload))
        
        # Check if response is valid before parsing JSON. Note: do not use
        # `not response_data` here -- requests.Response is falsy for any
        # non-2xx, which would silently swallow 4xx/5xx error bodies that
        # the parse-then-`message` branch below is meant to surface.
        if response_data is None or not response_data.text:
            frappe.log_error(
                message=_letmeship_response_diagnostics(response_data),
                title="Empty response from LetMeShip API",
            )
            return {}

        if not 200 <= response_data.status_code < 300:
            try:
                error_response = json.loads(response_data.text)
            except json.JSONDecodeError:
                error_response = {}
            error_detail = _get_letmeship_user_error(error_response) or _(
                "LetMeShip rejected the shipment request."
            )
            frappe.log_error(
                message=_letmeship_response_diagnostics(response_data),
                title="LetMeShip shipment creation failed",
            )
            frappe.throw(_("LetMeShip could not create the shipment: {0}").format(error_detail))
            
        try:
            response_data = json.loads(response_data.text)
        except Exception as json_exc:
            frappe.log_error(f"Failed to parse JSON response: {str(json_exc)}\nResponse: {response_data.text}")
            return {}
            
        if not response_data:
            frappe.log_error("Empty JSON data from LetMeShip API")
            return {}
            
        if 'shipmentId' in response_data:
            # Safe access to nested dictionaries
            service = response_data.get('service', {})
            base_service_details = service.get('baseServiceDetails', {}) if service else {}
            price_info = base_service_details.get('priceInfo', {}) if base_service_details else {}
            
            base_price = price_info.get('basePrice', 0)
            net_price = price_info.get('netPrice', 0)
            total_vat = price_info.get('totalVat', 0)
            shipment_amount = price_info.get('totalPrice', 0)
            awb_number = ''
            
            shipment_id = response_data.get('shipmentId')
            if not shipment_id:
                frappe.log_error("Missing shipmentId in response")
                return {}
                
            try:
                tracking_response = requests.get(
                    f'https://api.letmeship.com/v1/shipments/{shipment_id}',
                    auth=(service_provider.api_key, service_provider.api_password),
                    headers=headers
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
            return {
                'service_provider': 'LetMeShip',
                'shipment_id': response_data['shipmentId'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                'base_price': base_price,
                'net_price': net_price,
                'total_vat': total_vat,
                'shipment_amount': shipment_amount,
                'awb_number': awb_number,
            }
        elif 'message' in response_data:
            error_msg = response_data.get('message', 'Unknown error')
            frappe.log_error(f"Error occurred while creating Shipment: {error_msg}")
            frappe.throw(_('Error occurred while creating Shipment: {0}').format(error_msg))
        else:
            frappe.throw(_("LetMeShip did not return a shipment ID."))
    except frappe.ValidationError:
        raise
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        frappe.log_error(f"Error in create_letmeship_shipment: {str(exc)}\nTraceback: {error_trace}")
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                          ).format(str(exc)), indicator='orange',
                        alert=True)
        return {}


def get_letmeship_label(shipment_id):

    # return shipment_label

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    shipment_label_response = \
        requests.get('https://api.letmeship.com/v1/shipments/{id}/documents?types=LABEL'.format(id=shipment_id),
                     auth=(service_provider.api_key,
                           service_provider.api_password), headers=headers)
    shipment_label_response_data = \
        json.loads(shipment_label_response.text)
    if 'documents' in shipment_label_response_data:
        for label in shipment_label_response_data['documents']:
            if 'data' in label:
                return json.dumps(label['data'])
    else:
        error_msg = shipment_label_response_data.get('message', 'Unknown error')
        frappe.log_error(f"Error occurred while printing Shipment: {error_msg}")
        frappe.throw(_('Error occurred while printing Shipment: {0}'
                       ).format(error_msg))


def get_letmeship_tracking_data(shipment_id, shipment_doc_name=None):
    """ return letmeship tracking data """

    service_provider = frappe.db.get_value('Shipment Service Provider',
                                           'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    try:
        tracking_data_response = \
            requests.get('https://api.letmeship.com/v1/tracking?shipmentid={id}'.format(id=shipment_id),
                         auth=(service_provider.api_key,
                               service_provider.api_password), headers=headers)
        tracking_data = json.loads(tracking_data_response.text)
        if 'awbNumber' in tracking_data:
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
                'awb_number': tracking_data['awbNumber'],
                'tracking_status': tracking_status,
                'tracking_status_info': tracking_data['lmsTrackingStatus'],
                'tracking_url': tracking_url,
            }
        elif 'message' in tracking_data:
            shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
            frappe.throw(_('Error occurred while updating Shipment {0}{1}'
                           ).format(shipment_info, tracking_data['message']))
            return {}
    except Exception as exc:
        shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
        frappe.log_error(f"Error occurred while updating Shipment {shipment_info}{str(exc)}")
        frappe.msgprint(_('Error occurred while updating Shipment {0}{1}'
                          ).format(shipment_info, str(exc)), indicator='orange',
                        alert=True)
        return {}


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
