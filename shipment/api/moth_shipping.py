"""Expose canonical LetMeShip preview, booking and recovery to the staff agent."""
import hashlib
import json
import math

import frappe
from frappe import _
from shipment.api.let_me_ship import get_letmeship_available_services, get_letmeship_label
from shipment.api.letmeship_billing import ensure_no_uncertain_booking


def _document(name, write=False):
    doc = frappe.get_doc('Shipment', name, for_update=write)
    doc.check_permission('write' if write else 'read')
    if doc.docstatus != 1:
        frappe.throw(_('Submit the Shipment before booking.'))
    if doc.shipment_id and doc.service_provider != 'LetMeShip':
        frappe.throw(_('This Shipment belongs to another provider; do not book it again.'))
    return doc


def _arguments(doc):
    return {key: doc.get(key) for key in ('pickup_from_type', 'delivery_to_type',
            'pickup_address_name', 'delivery_address_name', 'description_of_content',
            'pickup_date', 'value_of_goods', 'pickup_type', 'pickup_contact_name', 'delivery_contact_name')} | {
                'shipment_parcel': json.dumps([row.as_dict() for row in doc.shipment_parcel])}


def _summary(doc):
    payer = None
    if doc.shipment_delivery_notes:
        note = frappe.get_doc('Delivery Note', doc.shipment_delivery_notes[0].delivery_note)
        note.check_permission('read')
        payer = note.get('customer_account') if int(note.get('use_customer_account') or 0) else None
    try:
        billing = json.loads(doc.get('carrier_booking_data') or '{}').get('letmeship', {}).get('recipient_billing') or {}
    except (TypeError, ValueError, AttributeError):
        billing = {}
    return {'shipment': doc.name, 'docstatus': doc.docstatus, 'status': doc.status,
            'shipment_id': doc.shipment_id, 'tracking_number': doc.awb_number,
            'service_provider': doc.service_provider, 'carrier': doc.carrier,
            'customer_account': billing.get('accountNumber'), 'requested_customer_account': payer,
            'bill_to': 'recipient' if billing.get('type') == 'RECEIVER_PAYS' else 'unknown',
            'carrier_service': doc.carrier_service, 'pickup_date': str(doc.pickup_date),
            'carrier_accepted': bool(doc.shipment_id), 'carrier_booked': doc.status == 'Booked',
            'delivery_notes': [row.delivery_note for row in doc.shipment_delivery_notes],
            'erp_url': '/app/shipment/' + doc.name}


def _plan(doc):
    ensure_no_uncertain_booking(doc)
    if not frappe.db.get_value('Shipment Service Provider', 'Let Me Ship', 'enabled'):
        frappe.throw(_('Enable LetMeShip before requesting a quote or booking.'))
    if len(doc.shipment_delivery_notes) != 1 or len(doc.shipment_parcel) != 1:
        frappe.throw(_('This workflow supports one Delivery Note and one parcel.'))
    parcel = doc.shipment_parcel[0]
    try:
        valid = float(parcel.get('count')) == 1 and all(
            math.isfinite(float(parcel.get(key))) and float(parcel.get(key)) > 0
            for key in ('length', 'width', 'height', 'weight'))
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        frappe.throw(_('Specify one parcel with positive finite dimensions and weight.'))
    dn = frappe.get_doc('Delivery Note', doc.shipment_delivery_notes[0].delivery_note)
    dn.check_permission('read')
    if (dn.docstatus != 1 or dn.company != doc.pickup_company or dn.customer != doc.delivery_customer
            or dn.parcel_service != 'UPS' or dn.parcel_service_type != 'Standard Paket National'):
        frappe.throw(_('This workflow requires a submitted domestic UPS Standard delivery for the same parties.'))
    if not int(dn.use_customer_account or 0):
        frappe.throw(_('Set the explicitly authorized recipient UPS account on the Delivery Note.'))
    address_versions = {}
    for name, kind, party in ((doc.pickup_address_name, 'Company', doc.pickup_company),
                              (doc.delivery_address_name, 'Customer', doc.delivery_customer)):
        address = frappe.get_doc('Address', name)
        address.check_permission('read')
        address_versions[name] = str(address.modified)
        if not any(row.link_doctype == kind and row.link_name == party for row in address.links):
            frappe.throw(_('The shipping address does not belong to the saved party.'))
        if address.country != 'Germany':
            frappe.throw(_('This recipient-billing workflow currently supports domestic German parcels.'))
    billing_address = frappe.get_doc('Address', dn.customer_address)
    billing_address.check_permission('read')
    address_versions[dn.customer_address] = str(billing_address.modified)
    rates = get_letmeship_available_services(**_arguments(doc), shipment=doc.name)
    choices = [row for row in rates if row.carrier == 'UPS'
               and row.service_name == dn.parcel_service_type and row.recipient_billing]
    if len(choices) != 1:
        frappe.throw(_('LetMeShip returned no unique UPS Standard service with recipient billing.'))
    rate = choices[0]
    plan = {**_summary(doc), 'schema': 1, 'customer_account': dn.customer_account,
            'bill_to': 'recipient', 'provider': 'LetMeShip', 'service': rate.service_name,
            'handling_net_eur': rate.total_price, 'handling_gross_eur': rate.price_info.get('totalPrice'),
            'pickup_interval': rate.pickup_interval, 'parcel': [{key: row.get(key) for key in ('length', 'width', 'height', 'weight', 'count')} for row in doc.shipment_parcel],
            'sender_address': doc.pickup_address_name, 'recipient_address': doc.delivery_address_name}
    binding = {'plan': plan, 'modified': str(doc.modified), 'delivery_modified': str(dn.modified), 'service_id': rate.id, 'address_versions': address_versions}
    plan['preview_token'] = hashlib.sha256(json.dumps(binding, sort_keys=True, default=str).encode()).hexdigest()
    return plan, rate


@frappe.whitelist(methods=['GET', 'POST'])
def preview(shipment):
    """Quote the saved parcel using the recipient's account; do not book anything."""
    doc = _document(shipment)
    if doc.shipment_id:
        return {**_summary(doc), 'already_booked': True, 'schema': 1}
    return _plan(doc)[0]


@frappe.whitelist(methods=['POST'])
def book(shipment, preview_token):
    """Book only the reviewed canonical preview, retaining uncertain outcomes durably."""
    doc = _document(shipment, write=True)
    if doc.shipment_id:
        return refresh(shipment)
    plan, rate = _plan(doc)
    if not preview_token or preview_token != plan['preview_token']:
        frappe.throw(_('The Shipment or quote changed. Review a fresh preview before booking.'))
    from shipment.shipment.doctype.shipment.shipment import create_shipment
    create_shipment(shipment=doc.name, **_arguments(doc), service_data=json.dumps(dict(rate)),
                    shipment_notific_email='[]', tracking_notific_email='[]', label_email=False)
    saved = _document(shipment)
    if not saved.shipment_id:
        frappe.throw(_('LetMeShip did not confirm a booking ID. Reconcile the retained attempt; do not rebook.'))
    return refresh(shipment)


@frappe.whitelist(methods=['POST'])
def refresh(shipment):
    """Recover the existing booking and label without creating another carrier booking."""
    doc = _document(shipment, write=True)
    if not doc.shipment_id:
        ensure_no_uncertain_booking(doc)
        frappe.throw(_('This Shipment has no carrier booking to refresh.'))
    from shipment.shipment.doctype.shipment.shipment import update_tracking
    warning = None
    try:
        update_tracking('LetMeShip', doc.shipment_id, doc.name)
    except Exception:
        warning = _('The existing booking is retained; its final carrier status could not be refreshed.')
    doc = _document(shipment)
    result = _summary(doc)
    if warning:
        result['warning'] = warning
    try:
        result['label_file_url'] = _label_file(doc)
    except Exception:
        result['label_pending'] = True
        result['next_step'] = _('Refresh this same Shipment to retrieve its label. Never book another parcel.')
    return result


def _label_file(doc):
    filename = 'LetMeShip-label-' + str(doc.shipment_id) + '.pdf'
    existing = frappe.db.get_value('File', {'attached_to_doctype': 'Shipment',
                    'attached_to_name': doc.name, 'file_name': filename, 'is_private': 1}, 'file_url')
    if existing:
        return existing
    values = json.loads(get_letmeship_label(doc.shipment_id))
    if not isinstance(values, list) or not values or any(type(v) is not int or not -128 <= v <= 255 for v in values):
        frappe.throw(_('LetMeShip returned an invalid label byte array.'))
    pdf = bytes(v & 255 for v in values)
    if not pdf.startswith(b'%PDF-') or len(pdf) > 10_000_000:
        frappe.throw(_('LetMeShip returned an invalid or oversized PDF label.'))
    from frappe.utils.file_manager import save_file
    return save_file(filename, pdf, 'Shipment', doc.name, is_private=1).file_url
