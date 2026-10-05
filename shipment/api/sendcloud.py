# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt
import frappe
import json
import requests
from frappe import _
from frappe.utils import escape_html
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

def get_sendcloud_available_services(delivery_address_name, shipment_parcel):
    try:
        api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
        url = 'https://panel.sendcloud.sc/api/v2/shipping_methods'
        responses = requests.get(url, auth=(api_key, api_password))
        responses_dict = json.loads(responses.text)

        delivery_address = get_address(delivery_address_name)
        available_services = []
        for service in responses_dict['shipping_methods']:
            for country in service['countries']:
                if country['iso_2'] == delivery_address.country_code:
                    available_service = frappe._dict()
                    available_service.service_provider = 'SendCloud'
                    available_service.carrier = service['carrier']
                    available_service.service_name = service['name']
                    available_service.total_price = total_parcel_price(country['price'], json.loads(shipment_parcel))
                    available_service.service_id = service['id']
                    available_services.append(available_service)
        return available_services
    except Exception as exc:
        frappe.msgprint(_('Error occurred on SendCloud: {0}'
                            ).format(str(exc)), indicator='orange',
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
    value_of_goods
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
            'weight': parcel.get('weight'),
            'parcel_items': get_parcel_items(parcel, description_of_content, value_of_goods)
        }
        parcels.append(parcel_data)
    data = {
        'parcels': parcels
    }
    try:
        url = 'https://panel.sendcloud.sc/api/v2/parcels?errors=verbose'
        api_key, api_password = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
        response_data = requests.post(url, json=data, auth=(api_key, api_password))
        response_data = json.loads(response_data.text)
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
                          ).format(str(exc)), indicator='orange',
                        alert=True)
        return {}

def get_sendcloud_label(shipment_id):
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