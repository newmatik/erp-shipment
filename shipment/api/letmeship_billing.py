"""Keep recipient billing and non-repeatable LetMeShip attempts on the Shipment."""
import json
import re
from copy import deepcopy
from uuid import uuid4

import frappe
from frappe import _
from shipment.api.utils import get_address


def recipient_billing(doc, delivery_info):
    """Resolve the payer from submitted Delivery Notes, never from caller billing data."""
    rows = doc.get('shipment_delivery_notes') or []
    notes = [frappe.get_doc('Delivery Note', row.get('delivery_note')) for row in rows]
    for note in notes:
        note.check_permission('read')
    flags = [int(note.get('use_customer_account') or 0) == 1 for note in notes]
    if not any(flags):
        return None
    if not all(flags):
        frappe.throw(_('Do not combine customer-account and sender-paid deliveries in one Shipment.'))
    accounts = {str(note.get('customer_account') or '').strip() for note in notes}
    if len(accounts) != 1 or not re.fullmatch(r'[A-Za-z0-9]{6}', next(iter(accounts))):
        frappe.throw(_('Specify one valid six-character UPS customer account on the Delivery Notes.'))
    for note in notes:
        if (note.docstatus != 1 or note.company != doc.pickup_company
                or note.customer != doc.delivery_customer or note.parcel_service != 'UPS'):
            frappe.throw(_('Customer-account booking requires submitted UPS deliveries for this company and customer.'))
    addresses = {note.get('customer_address') for note in notes}
    if len(addresses) != 1 or not next(iter(addresses)):
        frappe.throw(_('Specify one customer billing address on the Delivery Notes.'))
    name = next(iter(addresses))
    address_doc = frappe.get_doc('Address', name)
    address_doc.check_permission('read')
    if not any(row.link_doctype == 'Customer' and row.link_name == doc.delivery_customer
               for row in address_doc.links):
        frappe.throw(_('The payer address must belong to the Shipment customer.'))
    address = get_address(name)
    if address.country_code != 'DE' or not re.fullmatch(r'\d{5}', address.pincode):
        frappe.throw(_('Recipient billing currently requires a verified German UPS billing address.'))
    contact = deepcopy(delivery_info)
    contact['address'] = {'countryCode': address.country_code, 'zip': address.pincode,
                          'city': address.city, 'street': address.address_line1,
                          'addressInfo1': address.address_line2 or '', 'addressInfo2': '', 'houseNo': ''}
    return {'type': 'RECEIVER_PAYS', 'accountNumber': next(iter(accounts)), 'address': contact}


def net_charge(price):
    """Include the Ex works handling fee, which LetMeShip excludes from netPrice."""
    if price.get('totalPrice') is not None and price.get('totalVat') is not None:
        return round(float(price['totalPrice']) - float(price['totalVat']), 2)
    return float(price.get('netPrice') or 0)


def ensure_no_uncertain_booking(doc):
    """Refuse another carrier request after a lost LetMeShip response."""
    try:
        saved = json.loads(doc.get('carrier_booking_data') or '{}').get('letmeship', {})
    except (TypeError, ValueError, AttributeError):
        frappe.throw(_('The carrier booking state is unreadable; reconcile it before booking again.'))
    if saved.get('attempt_state') == 'uncertain':
        frappe.throw(_('A LetMeShip booking may already exist. Reconcile the saved attempt; do not book again.'))


def mark_attempt(shipment, settings, billing):
    """Commit uncertainty before crossing the non-idempotent carrier boundary."""
    details = {'attempt_state': 'uncertain', 'request_id': str(uuid4()),
               'requested_settings': settings, 'recipient_billing': billing}
    frappe.db.set_value('Shipment', shipment, {'status': 'Booking Pending',
                        'carrier_booking_data': json.dumps({'letmeship': details})})
    frappe.db.commit()
    return details
