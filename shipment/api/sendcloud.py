# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt
import frappe
import hashlib
import json
import requests
from frappe import _
from frappe.utils import escape_html
from shipment.api.rates import fits_all_parcels, parcel_count, NON_CARRIERS
from shipment.api import sendcloud_v3
from shipment.api.utils import (
    format_tracking_url,
    get_address,
    get_company_contact,
    get_contact,
)

# Terminal SendCloud parcel status ids that mean the receiver has the parcel
# (GET /api/v2/parcels/statuses): 11 "Delivered", 93 "Shipment collected by
# customer" (service-point pickup).
SENDCLOUD_DELIVERED_STATUS_IDS = frozenset({11, 93})

# DefaultValue (parent SendCloud, kept out of the desk boot defaults) holding a
# fingerprint of the API key whose account refused v2 parcel creation.
V3_ACCOUNT_DEFAULT = ('sendcloud_v3_account', 'SendCloud')


def total_parcel_price(parcel_price, shipment_parcel):
    count = 0
    for parcel in shipment_parcel:
        count += parcel.get('count')
    return parcel_price * count

def created_parcels_amount(total_price, shipment, requested_parcels, created_parcels):
    """Share of total_price for the parcels SendCloud actually created.

    total_price is the per-unit rate times the summed `count` of all requested
    rows, and each row is sent as one SendCloud parcel whose order_number is
    "<shipment>-<row index>". Created parcels are matched back to their row by
    that order_number, so a partially failed batch is not billed for the
    parcels that have no label.
    """
    counts = {
        "{}-{}".format(shipment, i): (row.get('count') or 0)
        for i, row in enumerate(requested_parcels, start=1)
    }
    total_count = sum(counts.values())
    if not total_count or not requested_parcels:
        return total_price
    matched = [counts[p.get('order_number')] for p in created_parcels if p.get('order_number') in counts]
    if len(matched) == len(created_parcels):
        return total_price * sum(matched) / total_count
    # SendCloud did not echo the order numbers; fall back to the parcel ratio.
    return total_price * len(created_parcels) / len(requested_parcels)

def is_v3_booking(shipment_id):
    """True for a booking made through Sendcloud API v3, stored as "v3:<uuid>"."""
    return (shipment_id or '').startswith('v3:')

def v2_refused(response):
    """True if Sendcloud refused v2 parcel creation because the account must use API v3."""
    return response.status_code == 403 and 'API v3' in response.text and 'API v2' in response.text

def _key_fingerprint(api_key):
    return hashlib.sha256((api_key or '').encode()).hexdigest()[:16]

def books_through_v3(api_key):
    """True if the account of this API key refused v2 parcel creation (create_sendcloud_shipment).

    Bound to the key, so switching the provider to other credentials lifts it.
    """
    key, parent = V3_ACCOUNT_DEFAULT
    return bool(api_key) and frappe.db.get_default(key, parent=parent) == _key_fingerprint(api_key)

def v3_cannot_book(api_key, parcels):
    """True if the account books through API v3 and v3 cannot book this many parcels at once."""
    return parcel_count(parcels) > sendcloud_v3.MAX_PARCELS and books_through_v3(api_key)

def get_sendcloud_available_services(delivery_address_name, shipment_parcel, pickup_address_name=None):
    try:
        api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
        parcels = json.loads(shipment_parcel)
        # Offers that sendcloud_v3.create_shipment would refuse are not offered.
        if v3_cannot_book(api_key, parcels):
            frappe.msgprint(_('No SendCloud offers: Sendcloud v3 automatic booking supports one parcel. '
                              'Book multi-parcel shipments in Sendcloud.'), indicator='blue', alert=True)
            return []
        url = 'https://panel.sendcloud.sc/api/v2/shipping_methods'
        responses = requests.get(url, auth=(api_key, api_password))
        responses_dict = json.loads(responses.text)

        delivery_address = get_address(delivery_address_name)
        # SendCloud lists every method of the account regardless of the parcels;
        # the chosen method is booked for every parcel, so none may exceed its weight band.
        # A lighter parcel can still need a heavier band for its size (fitting_methods).
        available_services = []
        for service in responses_dict['shipping_methods']:
            if service.get('carrier') in NON_CARRIERS or not fits_all_parcels(None, service.get('max_weight'), parcels):
                continue
            for country in service['countries']:
                if country['iso_2'] == delivery_address.country_code:
                    available_service = frappe._dict()
                    available_service.service_provider = 'SendCloud'
                    available_service.carrier = service['carrier']
                    available_service.service_name = service['name']
                    available_service.total_price = total_parcel_price(country['price'], parcels)
                    available_service.service_id = service['id']
                    available_service.min_weight = service.get('min_weight')
                    available_services.append(available_service)
        # Weight bands are not enough: only what v3 allows for these dimensions is offered.
        # v2 books from Sendcloud's default sender, v3 from the selected pickup address.
        sender = get_address(pickup_address_name) if pickup_address_name and books_through_v3(api_key) else None
        allowed = sendcloud_v3.fitting_methods(
            {service.service_id: service.min_weight for service in available_services}, sender, delivery_address, parcels)
        return [service for service in available_services if service.service_id in allowed]
    except Exception as exc:
        frappe.msgprint(_('Error occurred on SendCloud: {0}'
                            ).format(escape_html(str(exc))), indicator='orange',
                            alert=True)
        # fetch_shipping_rates concatenates provider lists; None would raise.
        return []

def create_sendcloud_shipment(
    shipment,
    delivery_to_type,
    delivery_address_name,
    delivery_contact_name,
    service_info,
    shipment_parcel,
    description_of_content,
    value_of_goods,
    pickup_address_name=None,
):
    delivery_address = get_address(delivery_address_name)
    to_country_code = delivery_address.country_code
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    parcels = []
    for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
        parcel_data = {
            'name': "{} {}".format(delivery_contact.first_name, delivery_contact.last_name),
            'company_name': delivery_address.address_title,
            'address': delivery_address.address_line1,
            'address_2': delivery_address.address_line2,
            'city': delivery_address.city,
            'postal_code': delivery_address.pincode,
            'telephone': delivery_contact.phone,
            'request_label': True,
            'email': delivery_contact.email,
            'data': [],
            'country': to_country_code,
            'shipment': {
                'id': service_info['service_id']
            },
            'order_number': "{}-{}".format(shipment, i),
            'external_reference': "{}-{}".format(shipment, i),
            'weight': str(sendcloud_v3.band_weight(parcel.get('weight'), service_info.get('min_weight'))),
            'parcel_items': get_parcel_items(parcel, description_of_content, value_of_goods)
        }
        parcels.append(parcel_data)
    data = {
        'parcels': parcels
    }
    try:
        url = 'https://panel.sendcloud.sc/api/v2/parcels?errors=verbose'
        api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
        response = requests.post(url, json=data, auth=(api_key, api_password), timeout=60)
        # A definitive v2 rejection cannot have bought a label. Never fall back
        # after timeouts, server errors or an ambiguous/partially successful call.
        if v2_refused(response):
            # Saved with the v3 booking's commit; the rate list then hides what v3 cannot book.
            key, parent = V3_ACCOUNT_DEFAULT
            frappe.db.set_default(key, _key_fingerprint(api_key), parent=parent)
            from shipment.api.sendcloud_v3 import create_shipment
            return create_shipment(shipment, pickup_address_name, delivery_address,
                                   delivery_contact, service_info, shipment_parcel,
                                   description_of_content, value_of_goods)
        response_data = json.loads(response.text)
        if not (response.status_code == 400 and response_data.get('failed_parcels')):
            response.raise_for_status()
        created = response_data.get('parcels') or []
        shipment_amount = service_info['total_price']
        failed = response_data.get('failed_parcels')
        if failed:
            # Persistent (not a toast): the packer must see which labels are missing.
            message = _('SendCloud created {0} of {1} parcels; {2} failed and have no label. First error: {3}').format(
                len(created), len(parcels), len(failed), escape_html(str(failed[0].get('errors'))))
            if created:
                message += '<br>' + _('This Shipment is booked for the created parcels only. '
                                      'Ship the failed parcels with a new Shipment.')
            frappe.msgprint(message, title=_('SendCloud parcels failed'), indicator='red')
            # errors=verbose still creates the valid parcels of a mixed batch;
            # record those so they are not orphaned at SendCloud.
            if not created:
                return {}
            shipment_amount = created_parcels_amount(
                shipment_amount, shipment, json.loads(shipment_parcel), created)
        shipment_id = ', '.join([str(x['id']) for x in created])
        awb_number = ', '.join([str(x['tracking_number']) for x in created])
        return {
            'service_provider': 'SendCloud',
            'shipment_id': shipment_id,
            'carrier': service_info['carrier'],
            'carrier_service': service_info['service_name'],
            'shipment_amount': shipment_amount,
            'awb_number': awb_number
        }
    except Exception as exc:
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                          ).format(escape_html(str(exc))), indicator='orange',
                        alert=True)
        return {}

def get_sendcloud_label(shipment_id):
    if is_v3_booking(shipment_id):
        from shipment.api.sendcloud_v3 import get_labels
        return get_labels(shipment_id)
    api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
    shipment_id_list = shipment_id.split(', ')
    label_urls = [] 
    for ship_id in shipment_id_list:
        shipment_label_response = \
            requests.get('https://panel.sendcloud.sc/api/v2/labels/{id}'.format(id=ship_id), auth=(api_key, api_password))
        shipment_label = json.loads(shipment_label_response.text)
        label_urls.append(shipment_label['label']['label_printer'])
    if len(label_urls):
        return label_urls
    frappe.msgprint(_('Shipment ID not found'))
    return None

def get_sendcloud_tracking_data(shipment_id):
    if is_v3_booking(shipment_id):
        from shipment.api.sendcloud_v3 import get_tracking
        return get_tracking(shipment_id)
    try:
        api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
        shipment_id_list = shipment_id.split(', ')
        tracking_url = ''
        awb_number = []
        delivered = []
        tracking_status_info = []
        for ship_id in shipment_id_list:
            tracking_data_response = \
                requests.get('https://panel.sendcloud.sc/api/v2/parcels/{id}'.format(id=ship_id), auth=(api_key, api_password))
            tracking_data = json.loads(tracking_data_response.text)
            safe_tracking_url = format_tracking_url(tracking_data['parcel']['tracking_url'])
            if safe_tracking_url:
                tracking_url += safe_tracking_url + '<br>'
            awb_number.append(tracking_data['parcel']['tracking_number'])
            status = tracking_data['parcel']['status']
            delivered.append(status.get('id') in SENDCLOUD_DELIVERED_STATUS_IDS)
            tracking_status_info.append(status['message'])
        return {
            'awb_number': ', '.join(awb_number),
            # tracking_status is a Select (In Progress/Delivered/Returned/Lost) like
            # the other providers set; the provider's own wording goes to _info.
            'tracking_status': 'Delivered' if delivered and all(delivered) else 'In Progress',
            'tracking_status_info': ', '.join(tracking_status_info),
            'tracking_url': tracking_url
        }
    except Exception as exc:
        frappe.msgprint(_('Error occurred while updating Shipment: {0}').format(
            str(exc)), indicator='orange', alert=True)
        return {}

def get_parcel_items(parcel, description_of_content, value_of_goods):
    parcel_list = []
    formatted_parcel = {}
    formatted_parcel['description'] = description_of_content
    formatted_parcel['quantity'] = parcel.get('count')
    formatted_parcel['weight'] = parcel.get('weight')
    formatted_parcel['value'] = value_of_goods
    parcel_list.append(formatted_parcel)
    return parcel_list