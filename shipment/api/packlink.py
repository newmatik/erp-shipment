# -*- coding: utf-8 -*-
# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt
import frappe
import json
import re
import requests
from urllib.parse import quote
from frappe import _
from frappe.rate_limiter import rate_limit
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
from frappe.utils import escape_html, flt
from shipment.api import customs
from shipment.api.utils import get_address, get_company_contact, get_contact, get_tracking_url, international_phone

# Packlink's API only creates drafts (Packlink's own shop modules do the same).
# A draft is bought by paying it in Packlink PRO; until then there is no label and no pickup.
DRAFT_STATES = frozenset({'AWAITING_COMPLETION', 'READY_TO_PURCHASE'})
PRO_DOMAINS = {'DE': 'de', 'ES': 'es', 'FR': 'fr', 'IT': 'it'}


def packlink_draft_url(reference):
    """The Packlink PRO page of a shipment ("DE2026PRO..." -> pro.packlink.de)."""
    domain = PRO_DOMAINS.get((reference or '')[:2].upper(), 'com')
    return 'https://pro.packlink.{0}/private/shipments/{1}'.format(domain, quote(reference or '', safe=''))


def draft_message(reference, state):
    url = escape_html(packlink_draft_url(reference))
    return _('Packlink has not bought shipment {0} yet: it is a draft ({1}). There is no label and no pickup until it is '
             'completed and paid in Packlink PRO: <a href="{2}" target="_blank" rel="noopener noreferrer">{2}</a>. '
             'Then run Update Tracking and Cost.').format(escape_html(reference), escape_html(state), url)


def parse_pickup_date(pickup_date):
    return pickup_date.replace('-', '/')


def packlink_get_parcel_list(shipment_parcel):
    parcel_list = []
    for parcel in shipment_parcel:
        for count in range(parcel.get('count')):
            formatted_parcel = {}
            formatted_parcel['height'] = parcel.get('height')
            formatted_parcel['width'] = parcel.get('width')
            formatted_parcel['length'] = parcel.get('length')
            formatted_parcel['weight'] = parcel.get('weight')
            parcel_list.append(formatted_parcel)
    return parcel_list


def get_packlink_available_services(
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    pickup_date,
):

    pickup_address = get_address(pickup_address_name)
    from_zip = pickup_address.pincode
    from_country_code = pickup_address.country_code

    delivery_address = get_address(delivery_address_name)
    to_zip = delivery_address.pincode
    to_country_code = delivery_address.country_code

    shipment_parcel_params = ''
    for (index, parcel) in \
            enumerate(packlink_get_parcel_list(json.loads(shipment_parcel))):
        shipment_parcel_params += \
            'packages[{index}][height]={height}&packages[{index}][length]={length}&packages[{index}][weight]={weight}&packages[{index}][width]={width}&'.format(index=index,
                                                                                                                                                                height=parcel[
                                                                                                                                                                    'height'], length=parcel['length'],
                                                                                                                                                                weight=parcel['weight'], width=parcel['width'])

    url = \
        'https://api.packlink.com/v1/services?from[country]={}&from[zip]={}&to[country]={}&to[zip]={}&{}sortBy=totalPrice&source=PRO'.format(from_country_code,
                                                                                                                                             from_zip, to_country_code, to_zip, shipment_parcel_params)
    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    if not api_key:
        return []

    try:
        responses = requests.get(url,
                                 headers={'Authorization': api_key})
        responses_dict = json.loads(responses.text)

        # If an error occured on the api. Show the error message

        if 'messages' in responses_dict:
            frappe.msgprint(_('Packlink: {0}'.format(str(responses_dict['messages'
                                                                        ][0]['message']))), indicator='orange',
                            alert=True)

        available_services = []
        for response in responses_dict:
            if parse_pickup_date(pickup_date) \
                    in response['available_dates'].keys():
                available_service = frappe._dict()
                available_service.service_provider = 'Packlink'
                available_service.carrier = response['carrier_name']
                available_service.service_name = \
                    match_parcel_service_type_alias(response['name'],
                                                    response['carrier_name'])
                available_service.is_preferred = \
                    frappe.db.get_value('Parcel Service Type',
                                        available_service.service_name,
                                        'show_in_preferred_services_list')
                available_service.base_price = response['price']['base_price']
                available_service.net_price = response['price']['base_price']
                available_service.total_vat = response['price']['tax_price']
                available_service.actual_price = response['price']['total_price']
                available_service.total_price = response['price']['base_price']
                available_service.service_id = response['id']
                available_service.dropoff = response.get('dropoff') or response.get('delivery_to_parcelshop')
                available_service.available_dates = \
                    response['available_dates']
                available_services.append(available_service)

        return available_services
    except Exception as exc:
        frappe.msgprint(_('Error occurred on Packlink: {0}'
                          ).format(str(exc)), indicator='orange',
                        alert=True)
    return []


API = 'https://api.packlink.com/v1'
# Packlink's reasons for export (its integration core, customs.html).
EXPORT_REASONS = {'Sale': 'purchase_or_sale', 'Sample': 'sample', 'Return': 'return', 'Documents': 'documents'}


def needs_customs(headers, pickup_address, delivery_address):
    """True when Packlink knows no customs union of both postal codes (its own test)."""
    response = requests.post(API + '/customs-unions/search-by-postal-code', headers=headers, timeout=30, json={
        'from': {'postal_code': pickup_address.pincode, 'country_code': pickup_address.country_code},
        'to': {'postal_code': delivery_address.pincode, 'country_code': delivery_address.country_code},
    })
    if not response.ok:
        frappe.throw(_('Packlink could not tell whether this shipment needs customs data: {0}').format(
            escape_html(response.text[:500])))
    return not response.json().get('data')


def customs_invoice(headers, shipment_doc, pickup_address, pickup_contact, delivery_address, delivery_contact, parcels, shipping_cost):
    """Create Packlink's customs invoice for a Shipment and return its id; list everything missing first."""
    parties = customs.require_customs_data(shipment_doc, 'Packlink')
    sender_company, vat_id, eori = parties.sender_company, parties.vat_id, parties.eori
    country_code = customs.country_code
    receiver_company, receiver_tax_id = parties.receiver_company, parties.receiver_tax_id
    sender_name = ' '.join(filter(None, [pickup_contact.first_name, pickup_contact.last_name]))
    body = {
        'invoice_number': customs.invoice_number(shipment_doc),
        'sender': {
            'user_type': 'company' if sender_company else 'private_person',
            'full_name': sender_name,
            'company_name': (pickup_address.address_title or sender_company) if sender_company else '',
            'vat_number': vat_id if sender_company else '',
            'tax_id': '' if sender_company else vat_id,
            'eori_number': eori,
            'address': ' '.join(filter(None, [pickup_address.address_line1, pickup_address.address_line2])),
            'postal_code': pickup_address.pincode, 'city': pickup_address.city,
            'country': pickup_address.country_code, 'phone_number': international_phone(pickup_contact),
        },
        'receiver': {
            'user_type': 'company' if receiver_company else 'private_person',
            'full_name': ' '.join(filter(None, [delivery_contact.first_name, delivery_contact.last_name])),
            'company_name': (delivery_address.address_title or receiver_company) if receiver_company else '',
            'vat_number': receiver_tax_id or '' if receiver_company else '',
            'tax_id': '' if receiver_company else (receiver_tax_id or ''),
            'eori_number': '',
            'address': ' '.join(filter(None, [delivery_address.address_line1, delivery_address.address_line2])),
            'postal_code': delivery_address.pincode, 'city': delivery_address.city,
            'country': delivery_address.country_code, 'phone_number': international_phone(delivery_contact),
        },
        'inventory_of_contents': [{
            'tariff_number': row.customs_tariff_number.replace(' ', ''),
            'description': row.description.strip(),
            'country_of_origin': country_code(row.country_of_origin),
            'item_value': {'currency': 'EUR', 'value': round(flt(row.unit_value), 2)},
            'item_weight': round(flt(row.unit_weight), 3),
            'quantity': flt(row.qty),
        } for row in shipment_doc.customs_items],
        'reason_for_export': EXPORT_REASONS[shipment_doc.reason_for_export],
        'shipment_details': {
            'parcels_size': sum(int(parcel.get('count') or 1) for parcel in parcels),
            'parcels_weight': sum(flt(parcel.get('weight')) * int(parcel.get('count') or 1) for parcel in parcels),
            # Packlink's cost is the shipping charge; the goods value is the inventory.
            'cost': {'currency': 'EUR', 'value': round(flt(shipping_cost), 2)},
        },
        'signature': {'full_name': sender_name, 'city': pickup_address.city},
    }
    response = requests.post(API + '/customs-invoices', json=body, headers=headers, timeout=60)
    if not response.ok or not (response.json() or {}).get('id'):
        frappe.throw(_('Packlink refused the customs data: {0}').format(escape_html(response.text[:1000])))
    return response.json()['id']


def create_packlink_shipment(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    pickup_contact_name,
    delivery_contact_name,
    service_info,
    shipment=None,
):

    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')

    pickup_address = get_address(pickup_address_name)
    from_country_code = pickup_address.country_code
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()

    delivery_address = get_address(delivery_address_name)
    to_country_code = delivery_address.country_code
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    data = {
        'additional_data': {
            'postal_zone_id_from': '',
            'postal_zone_name_from': pickup_address.country,
            'postal_zone_id_to': '',
            'postal_zone_name_to': delivery_address.country,
        },
        'collection_date': parse_pickup_date(pickup_date),
        'collection_time': '',
        'content': description_of_content,
        'contentvalue': value_of_goods,
        'content_second_hand': False,
        'from': {
            'city': pickup_address.city,
            'company': pickup_address.address_title,
            'country': from_country_code,
            'email': pickup_contact.email,
            'name': pickup_contact.first_name,
            'phone': international_phone(pickup_contact),
            'state': pickup_address.country,
            'street1': pickup_address.address_line1,
            'street2': pickup_address.address_line2,
            'surname': pickup_contact.last_name,
            'zip_code': pickup_address.pincode,
        },
        'insurance': {'amount': 0, 'insurance_selected': False},
        'price': {},
        'packages': packlink_get_parcel_list(json.loads(shipment_parcel)),
        'service_id': service_info['service_id'],
        'to': {
            'city': delivery_address.city,
            'company': delivery_address.address_title,
            'country': to_country_code,
            'email': delivery_contact.email,
            'name': delivery_contact.first_name,
            'phone': international_phone(delivery_contact),
            'state': delivery_address.country,
            'street1': delivery_address.address_line1,
            'street2': delivery_address.address_line2,
            'surname': delivery_contact.last_name,
            'zip_code': delivery_address.pincode,
        },
    }

    url = 'https://api.packlink.com/v1/shipments'
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}
    # Across a customs border the draft carries the customs invoice; without one Packlink
    # keeps it incomplete and asks for everything in its own checkout.
    if needs_customs(headers, pickup_address, delivery_address):
        shipment_doc = frappe.get_doc('Shipment', shipment)
        data['has_customs'] = True
        data['customs'] = {'customs_invoice_id': customs_invoice(
            headers, shipment_doc, pickup_address, pickup_contact, delivery_address, delivery_contact,
            json.loads(shipment_parcel), service_info.get('base_price') or service_info.get('total_price'))}

    try:
        response_data = requests.post(url, json=data, headers=headers)
        response_data = json.loads(response_data.text)
        if 'reference' in response_data:
            return {
                'service_provider': 'Packlink',
                'shipment_id': response_data['reference'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                'base_price': service_info['base_price'],
                'net_price': service_info['net_price'],
                'total_vat': service_info['total_vat'],
                'shipment_amount': service_info['actual_price'],
                'awb_number': '',
            }
        # Add return empty dict when no reference
        return {}
    except Exception as exc:
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                          ).format(str(exc)), indicator='orange',
                        alert=True)
        return {}


def get_packlink_label(shipment_id):
    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}
    shipment_label_response = \
        requests.get('https://api.packlink.com/v1/shipments/{id}/labels'.format(id=shipment_id),
                     headers=headers)
    shipment_label = json.loads(shipment_label_response.text)
    if shipment_label:
        return shipment_label
    frappe.msgprint(_('Shipment ID not found'))
    return None


# Events Packlink's integration core acts on; each one is answered by re-reading the shipment.
WEBHOOK_EVENTS = frozenset({
    'shipment.carrier.success', 'shipment.label.ready', 'shipment.tracking.update',
    'shipment.delivered', 'shipment.carrier.delivered',
})
REFERENCE = re.compile(r'^[A-Z]{2}\d{4}PRO\d{6,12}$')
WEBHOOK_PATH = '/api/method/shipment.api.packlink.webhook'


@frappe.whitelist(allow_guest=True, methods=['POST'])
@rate_limit(limit=120, seconds=60)
def webhook():
    """Packlink's shipment callback (registered with register_webhook).

    The payload only names a shipment: nothing in it is trusted or stored. A booked
    Packlink Shipment with that reference is refreshed from Packlink's API in the
    background, the way Update Tracking and Cost does it. Every request gets the same answer.
    """
    queue_webhook_refresh(frappe.request.get_json(silent=True) if frappe.request else None)
    return {'status': 'ok'}


def queue_webhook_refresh(payload):
    """Queue the refresh of the booked Packlink Shipment a webhook payload names, if any."""
    payload = payload if isinstance(payload, dict) else {}
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    reference = data.get('shipment_reference')
    if payload.get('event') in WEBHOOK_EVENTS and isinstance(reference, str) and REFERENCE.match(reference):
        name = frappe.db.get_value('Shipment', {'service_provider': 'Packlink', 'shipment_id': reference, 'docstatus': 1}, 'name')
        if name:
            frappe.enqueue('shipment.api.packlink.refresh_from_webhook', queue='short', job_id='packlink-webhook-' + name,
                           deduplicate=True, enqueue_after_commit=True, shipment=name)


def refresh_from_webhook(shipment):
    """Background job: tracking, status and Delivery Note links of a Packlink Shipment."""
    # Function-local: the Shipment controller imports this module.
    from shipment.shipment.doctype.shipment.shipment import _update_tracking

    # Queued from a guest request, the job would run as Guest, who may not write Shipments.
    # It only re-reads Packlink's own data for a booked Shipment, as the daily job does.
    frappe.set_user('Administrator')
    booking = frappe.db.get_value('Shipment', shipment, ['shipment_id', 'service_provider', 'docstatus'], as_dict=True)
    if booking and booking.docstatus == 1 and booking.service_provider == 'Packlink':
        _update_tracking('Packlink', booking.shipment_id, shipment)


def register_webhook(site_url):
    """Point Packlink's shipment callback at this site (bench execute, once per account)."""
    api_key = frappe.db.get_value('Shipment Service Provider', 'Packlink', 'api_key')
    response = requests.post(API + '/shipments/callback', json={'url': site_url.rstrip('/') + WEBHOOK_PATH},
                             headers={'Authorization': api_key, 'Content-Type': 'application/json'}, timeout=30)
    if not response.ok:
        frappe.throw(_('Packlink did not register the webhook: {0}').format(escape_html(response.text[:500])))
    return response.status_code


def get_packlink_tracking_data(shipment_id):
    """ Get Packlink Tracking Info"""

    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}
    try:
        tracking_data_response = \
            requests.get('https://api.packlink.com/v1/shipments/{id}'.format(id=shipment_id),
                         headers=headers)
        tracking_data = json.loads(tracking_data_response.text)
        if 'trackings' in tracking_data:
            tracking_status = 'In Progress'
            if tracking_data['state'] == 'DELIVERED':
                tracking_status = 'Delivered'
            if tracking_data['state'] == 'RETURNED':
                tracking_status = 'Returned'
            if tracking_data['state'] == 'LOST':
                tracking_status = 'Lost'
            if tracking_data['state'] in DRAFT_STATES:
                frappe.msgprint(draft_message(shipment_id, tracking_data['state']),
                                title=_('Complete in Packlink PRO'), indicator='orange')
            if not tracking_data['trackings']:
                # Packlink assigns tracking numbers only once the shipment is paid in Packlink PRO.
                if tracking_data['state'] not in DRAFT_STATES:
                    frappe.msgprint(_('Packlink has no tracking number for this shipment yet (state {0}).').format(
                        escape_html(tracking_data['state'])), indicator='orange', alert=True)
                return {
                    'tracking_status': tracking_status,
                    'tracking_status_info': tracking_data['state'],
                }
            tracking_url = get_tracking_url(carrier=tracking_data['carrier'
                                                                  ], tracking_number=tracking_data['trackings'][0])
            return {
                'awb_number': tracking_data['trackings'][0],
                'tracking_status': tracking_status,
                'tracking_status_info': tracking_data['state'],
                'tracking_url': tracking_url,
            }
        return {}
    except Exception as exc:
        frappe.msgprint(_('Error occurred while updating Shipment: {0}').format(
            str(exc)), indicator='orange', alert=True)
        return {}
