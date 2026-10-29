"""Sendcloud v3 booking for accounts that no longer accept v2 parcel creation.

Rates retain their v2 method IDs; the official compatibility endpoint maps the
selected method to its v3 option. Existing numeric parcel references stay v2.
"""
import json
import hashlib
from decimal import Decimal
from uuid import UUID
from urllib.parse import urlencode
from datetime import datetime, timezone

import frappe
import requests
from frappe import _
from frappe.utils import escape_html
from shipment.api.utils import get_address, format_tracking_url

BASE = 'https://panel.sendcloud.sc/api/v3'
# Parcels one automatic booking may announce. The rate list hides SendCloud for
# larger shipments while the account books through v3 (sendcloud.py).
MAX_PARCELS = 1
# Parcels one rate check may send; the rate endpoint is whitelisted and takes raw counts.
MAX_RATE_PARCELS = 50
# A lost announcement may still complete at Sendcloud after the 60 s request timeout.
# Only once this has passed does "no shipment with our reference" prove nothing was bought.
RELEASE_AFTER_SECONDS = 300


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


def _rate_parcel(row):
    """A parcel row as v3 rates it: weight always, dimensions once all three are set."""
    parcel = {'weight': {'value': str(row.get('weight') or 0), 'unit': 'kg'}}
    dimensions = {key: row.get(key) for key in ('length', 'width', 'height')}
    if all(dimensions.values()):
        parcel['dimensions'] = {**{key: str(value) for key, value in dimensions.items()}, 'unit': 'cm'}
    return parcel


def fitting_method_ids(method_ids, sender, recipient, parcels):
    """The v2 method ids whose v3 shipping option Sendcloud offers for these parcels.

    The v2 method list knows weight bands only (it offered DHL Kleinpaket, at most
    35 x 25 x 8 cm, for a 32 x 22 x 17 cm box). The v3 options also check each
    option's maximum dimensions, and v3 is what books.
    """
    if not method_ids or sum(int(row.get('count') or 1) for row in parcels) > MAX_RATE_PARCELS:
        return set()
    codes = _request('POST', '/compat/shipping-options', json={
        'shipping_method_ids': sorted(int(method_id) for method_id in method_ids)
    }).get('data') or {}

    def address(record):
        return {k: v for k, v in {'country_code': record.country_code, 'postal_code': record.pincode}.items() if v}

    body = {'to_address': address(recipient),
            'parcels': [_rate_parcel(row) for row in parcels for _ in range(int(row.get('count') or 1))]}
    if sender:
        body['from_address'] = address(sender)
    offered = {option.get('code') for option in _request('POST', '/shipping-options', json=body).get('data') or []}
    return {method_id for method_id in method_ids if codes.get(str(method_id)) in offered}


def _company_name(address):
    # Address titles are free labels. Only use a title matching a linked business.
    if not address.get('name'):
        return None
    for link in frappe.get_doc('Address', address.name).links:
        if link.link_doctype == 'Company':
            company = frappe.db.get_value('Company', link.link_name, 'company_name')
        elif link.link_doctype == 'Supplier':
            supplier = frappe.db.get_value('Supplier', link.link_name, ['supplier_name', 'supplier_type'], as_dict=True)
            company = supplier.supplier_name if supplier and supplier.supplier_type == 'Company' else None
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


def _sender_address(address, pickup_company):
    # A pickup address of the shipment's company may carry a brand as its title (e.g. "E-Modell"):
    # the brand goes on the label's first sender line and the legal company below it.
    # Sendcloud prints company_name before name. Without a brand title nothing changes.
    company = None
    if address.get('name') and pickup_company and any(
            link.link_doctype == 'Company' and link.link_name == pickup_company
            for link in frappe.get_doc('Address', address.name).links):
        company = frappe.db.get_value('Company', pickup_company, 'company_name')
    if not company or not address.address_title or company == address.address_title:
        return _address(address, address.address_title)
    return {**_address(address, company), 'company_name': address.address_title}


def _label_reference(shipment):
    # DHL prints the order number as reference: show the delivery note when there is exactly one.
    notes = frappe.get_all('Shipment Delivery Notes', filters={'parent': shipment, 'parenttype': 'Shipment'}, pluck='delivery_note')
    return notes[0] if len(set(notes)) == 1 and notes[0] else shipment


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
        if int(count) != count or not 1 <= count <= MAX_PARCELS:
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
    if not 1 <= len(parcels) <= MAX_PARCELS:
        frappe.throw(_('Sendcloud v3 automatic booking supports one parcel. Book multi-parcel shipments in Sendcloud.'))
    amount = Decimal(str(service_info['total_price']))
    if not amount.is_finite() or amount < 0:
        frappe.throw(_('Invalid Sendcloud quote amount.'))
    reference, order_number = 'erpnext:' + shipment, _label_reference(shipment)
    payload = {
        'external_reference_id': reference, 'order_number': order_number,
        'from_address': _sender_address(sender, frappe.db.get_value('Shipment', shipment, 'pickup_company')),
        'to_address': _address(delivery_address, ' '.join(filter(None, [delivery_contact.first_name, delivery_contact.last_name]))),
        'ship_with': {'type': 'shipping_option_code', 'properties': {'shipping_option_code': option}},
        'total_order_price': {'value': str(value_of_goods), 'currency': 'EUR'},
        'label_details': {'mime_type': 'application/pdf', 'dpi': 72}, 'parcels': parcels,
    }
    spec_hash = hashlib.sha256(json.dumps({key: payload[key] for key in ('from_address', 'to_address', 'parcels', 'total_order_price')}, sort_keys=True).encode()).hexdigest()
    payload['reference'] = json.dumps({'version': 1, 'spec_hash': spec_hash, 'amount': str(amount),
                                       'carrier': service_info['carrier'], 'service': service_info['service_name']})
    expected = {'external_reference_id': reference, 'order_number': order_number, 'option': option,
                'count': len(parcels), 'spec_hash': spec_hash, 'carrier': service_info['carrier'],
                'at': datetime.now(timezone.utc).isoformat(),
                'document_modified': str(frappe.db.get_value('Shipment', shipment, 'modified'))}
    # Commit BEFORE the paid request: even a killed worker cannot lose this boundary.
    frappe.db.set_value('Shipment', shipment, 'carrier_booking_data', json.dumps({'sendcloud_uncertain': expected}), update_modified=False)
    frappe.db.commit()
    data = _request('POST', '/shipments/announce', json=payload).get('data') or {}
    return _booking_result(data, expected)


def uncertain_booking(shipment_doc):
    value = shipment_doc.get('carrier_booking_data')
    try:
        data = json.loads(value) if isinstance(value, str) else value
        record = data.get('sendcloud_uncertain') if isinstance(data, dict) else None
        return record if isinstance(record, dict) else None
    except (ValueError, TypeError):
        return None


def ensure_no_uncertain_booking(shipment_doc):
    if uncertain_booking(shipment_doc):
        frappe.throw(_('A Sendcloud booking may already exist. Recover the same Sendcloud booking or reconcile it before changing provider or cancelling. '
                       'If Sendcloud has none, try again five minutes after the failed booking.'))


def release_absent_booking(shipment_doc):
    """Clear an uncertain booking that Sendcloud confirms it never created. True when none is left."""
    expected = uncertain_booking(shipment_doc)
    if not expected:
        return True
    try:
        started = datetime.fromisoformat(expected['at'])
    except (KeyError, TypeError, ValueError):
        return False
    if (datetime.now(timezone.utc) - started).total_seconds() < RELEASE_AFTER_SECONDS:
        return False
    # The lookup proves absence only in the account that announced: refuse once the credentials changed.
    provider_modified = frappe.db.get_value('Shipment Service Provider', 'SendCloud', 'modified')
    try:
        if not provider_modified or datetime.fromisoformat(str(provider_modified)) >= datetime.fromisoformat(expected['document_modified']):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    found = _request('GET', '/shipments?' + urlencode({'external_reference_id': expected['external_reference_id']})).get('data')
    if not isinstance(found, list) or found:
        return False
    frappe.db.set_value('Shipment', shipment_doc.name, 'carrier_booking_data', None, update_modified=False)
    # A cancel saves the in-memory document: it must not write the boundary back.
    shipment_doc.carrier_booking_data = None
    return True


def recover_shipment(shipment_doc):
    expected = uncertain_booking(shipment_doc)
    if not expected:
        frappe.throw(_('This Shipment has no uncertain Sendcloud booking.'))
    if str(shipment_doc.get('modified')) != expected.get('document_modified'):
        frappe.throw(_('The Shipment changed after the uncertain booking. Reconcile it before recovery.'))
    data = _request('GET', '/shipments?' + urlencode({'external_reference_id': expected['external_reference_id']})).get('data') or []
    if len(data) != 1:
        frappe.throw(_('Sendcloud has no unique matching booking. Reconcile the provider account before releasing this Shipment.'))
    return _booking_result(data[0], expected)


def _booking_result(data, expected):
    # 409 is only recoverable for this exact ERP shipment and selected service.
    if data.get('external_reference_id') != expected['external_reference_id'] or data.get('order_number') != expected['order_number']:
        frappe.throw(_('Sendcloud returned an unrecognized shipment reference; reconcile it before retrying.'))
    created = data.get('parcels') or []
    returned_option = (data.get('ship_with') or {}).get('properties', {}).get('shipping_option_code')
    if returned_option != expected['option'] or len(created) != expected['count']:
        frappe.throw(_('Sendcloud returned a different service or parcel set; reconcile it before retrying.'))
    original = json.loads(data.get('reference') or '{}')
    if original.get('version') != 1 or original.get('spec_hash') != expected['spec_hash'] or original.get('carrier') != expected['carrier']:
        frappe.throw(_('Sendcloud booking details changed; reconcile it before retrying.'))
    original_amount = Decimal(str(original.get('amount')))
    if not original_amount.is_finite() or original_amount < 0 or not original.get('service'):
        frappe.throw(_('Sendcloud returned invalid booking metadata.'))
    shipment_id = str(UUID(str(data.get('id'))))
    successful = [p for p in created if p.get('tracking_number')]
    if data.get('errors') or len(successful) != expected['count']:
        frappe.msgprint(_('Sendcloud created shipment {0}, but not all labels are ready. '
                         'Check this shipment in Sendcloud; do not create a replacement. {1}').format(shipment_id, escape_html(str(data.get('errors') or '')[:1000])), indicator='orange')
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
            }).insert(ignore_permissions=True).file_url
        urls.append(existing)
    return urls


def get_tracking(shipment_id):
    _require_read(shipment_id)
    data = _shipment(shipment_id)
    parcels = data.get('parcels') or []
    return {
        'awb_number': ', '.join(p['tracking_number'] for p in parcels if p.get('tracking_number')),
        'tracking_status': 'Delivered' if parcels and all((p.get('status') or {}).get('code') == 'DELIVERED' for p in parcels) else 'In Progress',
        'tracking_status_info': ', '.join(((p.get('status') or {}).get('message') or '') for p in parcels)[:140],
        'tracking_url': '<br>'.join(filter(None, (format_tracking_url(p.get('tracking_url')) for p in parcels))),
    }
