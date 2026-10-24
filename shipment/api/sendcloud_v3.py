"""Sendcloud v3 booking for accounts that no longer accept v2 parcel creation.

Rates retain their v2 method IDs; the official compatibility endpoint maps the
selected method to its v3 option. Existing numeric parcel references stay v2.
"""
import json
import hashlib
from decimal import Decimal
from uuid import UUID

import frappe
import requests
from frappe import _
from frappe.utils import escape_html
from shipment.api.utils import get_address, format_tracking_url

BASE = 'https://panel.sendcloud.sc/api/v3'


def _request(method, path, **kwargs):
    auth = frappe.db.get_value('Shipment Service Provider', 'SendCloud', ['api_key', 'api_password'])
    response = requests.request(method, BASE + path, auth=tuple(auth), timeout=60, allow_redirects=False, **kwargs)
    if response.status_code == 409 and path == '/shipments/announce':
        return response.json()  # Same external_reference_id: recover, never buy twice.
    if not 200 <= response.status_code < 300:
        try:
            errors = response.json().get('errors') or response.json().get('error')
        except ValueError:
            errors = None
        frappe.throw(_('Sendcloud API v3 HTTP {0}: {1}').format(response.status_code, escape_html(str(errors or 'Request failed')[:1000])))
    if kwargs.get('headers', {}).get('Accept') == 'application/pdf':
        if not response.content.startswith(b'%PDF-') or len(response.content) > 10_000_000:
            frappe.throw(_('Sendcloud did not return a valid PDF label.'))
        return response.content
    return response.json()


def _company_name(address):
    # Address titles are free labels. Only use a title matching a linked business.
    if not address.get('name'):
        return None
    for link in frappe.get_doc('Address', address.name).links:
        if link.link_doctype == 'Company':
            company = frappe.db.get_value('Company', link.link_name, 'company_name')
        elif link.link_doctype == 'Customer':
            customer = frappe.db.get_value('Customer', link.link_name, ['customer_name', 'customer_type'], as_dict=True)
            company = customer.customer_name if customer and customer.customer_type == 'Company' else None
        else:
            continue
        if company and company == address.address_title:
            return company
    return None


def _address(address, name):
    return {k: v for k, v in {
        'name': name, 'company_name': _company_name(address), 'address_line_1': address.address_line1,
        'address_line_2': address.address_line2, 'city': address.city,
        'postal_code': address.pincode, 'country_code': address.country_code,
    }.items() if v}


def create_shipment(shipment, pickup_address_name, delivery_address, delivery_contact,
                    service_info, shipment_parcel, description_of_content, value_of_goods):
    if not pickup_address_name:
        frappe.throw(_('Select a pickup address before booking with Sendcloud.'))
    sender = get_address(pickup_address_name)
    # The generic parcel rows do not contain the HS codes/origin needed for customs.
    if sender.country_code != delivery_address.country_code:
        frappe.throw(_('Sendcloud v3 cross-border shipments require customs/service configuration; book them in Sendcloud.'))
    option = _request('POST', '/compat/shipping-options', json={
        'shipping_method_ids': [int(service_info['service_id'])]
    }).get('data', {}).get(str(service_info['service_id']))
    if not option:
        frappe.throw(_('No Sendcloud v3 shipping option exists for the selected rate.'))
    parcels = []
    for row in json.loads(shipment_parcel):
        count = row.get('count', 1)
        if int(count) != count or count != 1:
            frappe.throw(_('Sendcloud v3 automatic booking supports one parcel. Book multi-parcel shipments in Sendcloud.'))
        values = {key: Decimal(str(row.get(key, 0))) for key in ('weight', 'length', 'width', 'height')}
        if any(not value.is_finite() or value <= 0 for value in values.values()):
            frappe.throw(_('Set a positive weight and dimensions for every parcel.'))
        parcel = {
            'weight': {'value': str(values['weight']), 'unit': 'kg'},
            'dimensions': {**{key: str(values[key]) for key in ('length', 'width', 'height')}, 'unit': 'cm'},
            'label_notes': [shipment],
        }
        parcels.extend(dict(parcel) for _ in range(int(count)))
    if len(parcels) != 1:
        frappe.throw(_('Sendcloud v3 automatic booking supports one parcel. Book multi-parcel shipments in Sendcloud.'))
    amount = Decimal(str(service_info['total_price']))
    if not amount.is_finite() or amount < 0:
        frappe.throw(_('Invalid Sendcloud quote amount.'))
    reference = 'erpnext:' + shipment
    payload = {
        'external_reference_id': reference, 'order_number': shipment,
        'from_address': _address(sender, sender.address_title),
        'to_address': _address(delivery_address, ' '.join(filter(None, [delivery_contact.first_name, delivery_contact.last_name]))),
        'ship_with': {'type': 'shipping_option_code', 'properties': {'shipping_option_code': option}},
        'total_order_price': {'value': str(value_of_goods), 'currency': 'EUR'},
        'label_details': {'mime_type': 'application/pdf', 'dpi': 72}, 'parcels': parcels,
    }
    spec_hash = hashlib.sha256(json.dumps({key: payload[key] for key in ('from_address', 'to_address', 'parcels', 'total_order_price')}, sort_keys=True).encode()).hexdigest()
    payload['reference'] = json.dumps({'version': 1, 'spec_hash': spec_hash, 'amount': str(amount),
                                       'carrier': service_info['carrier'], 'service': service_info['service_name']})
    data = _request('POST', '/shipments/announce', json=payload).get('data') or {}
    # 409 is only recoverable for this exact ERP shipment and selected service.
    if data.get('external_reference_id') != reference or data.get('order_number') != shipment:
        frappe.throw(_('Sendcloud returned an unrecognized shipment reference; reconcile it before retrying.'))
    created = data.get('parcels') or []
    returned_option = (data.get('ship_with') or {}).get('properties', {}).get('shipping_option_code')
    if returned_option != option or len(created) != len(parcels):
        frappe.throw(_('Sendcloud returned a different service or parcel set; reconcile it before retrying.'))
    original = json.loads(data.get('reference') or '{}')
    if original.get('version') != 1 or original.get('spec_hash') != spec_hash or original.get('carrier') != service_info['carrier']:
        frappe.throw(_('Sendcloud booking details changed; reconcile it before retrying.'))
    original_amount = Decimal(str(original.get('amount')))
    if not original_amount.is_finite() or original_amount < 0 or not original.get('service'):
        frappe.throw(_('Sendcloud returned invalid booking metadata.'))
    shipment_id = str(UUID(str(data.get('id'))))
    successful = [p for p in created if p.get('tracking_number')]
    if data.get('errors') or len(successful) != len(parcels):
        frappe.msgprint(_('Sendcloud created shipment {0}, but not all labels are ready. '
                         'Check this shipment in Sendcloud; do not create a replacement.').format(shipment_id), indicator='orange')
    # Persist the provider UUID even if announcement failed or is still pending.
    return {
        'service_provider': 'SendCloud', 'shipment_id': 'v3:' + shipment_id,
        'carrier': original['carrier'], 'carrier_service': original['service'],
        # Retain the original quotation even while announcement is pending.
        'shipment_amount': float(original_amount),
        'awb_number': ', '.join(p['tracking_number'] for p in successful),
    }


def _shipment(shipment_id):
    identifier = str(UUID(shipment_id.removeprefix('v3:')))
    return _request('GET', '/shipments/' + identifier)['data']


def _require_read(shipment_id):
    name = frappe.db.get_value('Shipment', {'service_provider': 'SendCloud', 'shipment_id': shipment_id}, 'name')
    if not name:
        frappe.throw(_('No matching Sendcloud shipment found.'))
    frappe.get_doc('Shipment', name).check_permission('read')
    return name


def get_labels(shipment_id):
    name = _require_read(shipment_id)
    data = _shipment(shipment_id)
    urls = []
    for parcel in data.get('parcels') or []:
        parcel_id = int(parcel['id'])
        filename = 'Sendcloud-label-{}.pdf'.format(parcel_id)
        existing = frappe.db.get_value('File', {
            'attached_to_doctype': 'Shipment', 'attached_to_name': name,
            'file_name': filename, 'is_private': 1,
        }, 'file_url')
        if not existing:
            content = _request('GET', '/parcels/{}/documents/label'.format(parcel_id), headers={'Accept': 'application/pdf'})
            existing = frappe.get_doc({
                'doctype': 'File', 'file_name': filename, 'content': content, 'is_private': 1,
                'attached_to_doctype': 'Shipment', 'attached_to_name': name,
            }).insert().file_url
        urls.append(existing)
    return urls


def get_tracking(shipment_id):
    _require_read(shipment_id)
    data = _shipment(shipment_id)
    parcels = data.get('parcels') or []
    return {
        'awb_number': ', '.join(p['tracking_number'] for p in parcels if p.get('tracking_number')),
        'tracking_status': 'Delivered' if parcels and all(p.get('status', {}).get('code') == 'DELIVERED' for p in parcels) else 'In Progress',
        'tracking_status_info': ', '.join(p.get('status', {}).get('message', '') for p in parcels)[:140],
        'tracking_url': '<br>'.join(filter(None, (format_tracking_url(p.get('tracking_url')) for p in parcels))),
    }
