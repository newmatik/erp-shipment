#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
from frappe import _
from frappe.model.document import Document
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import today
from shipment.api.let_me_ship import get_letmeship_available_services, create_letmeship_shipment, get_letmeship_label, get_letmeship_tracking_data
from shipment.api.packlink import get_packlink_available_services, create_packlink_shipment, get_packlink_label, get_packlink_tracking_data
from shipment.api.sendcloud import get_sendcloud_available_services, create_sendcloud_shipment, get_sendcloud_label, get_sendcloud_tracking_data
from shipment.api.utils import get_address
from erpnext.controllers.accounts_controller import update_child_qty_rate
from frappe.utils import flt


class Shipment(Document):

    def validate(self):
        self.validate_weight()
        if self.docstatus == 0:
            self.status = 'Draft'

    def on_submit(self):
        if not self.shipment_parcel:
            frappe.throw(_('Please enter Shipment Parcel information'))
        if self.value_of_goods == 0:
            frappe.throw(_('Value of goods cannot be 0'))
        pickup_address = get_address(self.pickup_address_name)
        delivery_address = get_address(self.delivery_address_name)
        if len(pickup_address.address_line1) > 35:
            frappe.throw(
                _('Maximum length of address line 1 for pickup address is 35 characters'))
        if len(delivery_address.address_line1) > 35:
            frappe.throw(
                _('Maximum length of address line 1 for delivery address is 35 characters'))
        self.status = 'Submitted'

    def on_cancel(self):
        self.status = 'Cancelled'

    def validate_weight(self):
        for parcel in self.shipment_parcel:
            if parcel.weight <= 0:
                frappe.throw(_('Parcel weight cannot be 0'))


@frappe.whitelist()
def fetch_shipping_rates(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    pickup_type=None,
    pickup_contact_name=None,
    delivery_contact_name=None,
    delivery_note=None
):
    """Return Shipping Rates for the various Shipping Providers"""
    is_letmeship_enabled = frappe.db.get_value(
        'Shipment Service Provider', 'Let Me Ship', 'enabled')
    is_packlink_enabled = frappe.db.get_value(
        'Shipment Service Provider', 'PackLink', 'enabled')
    is_sendcloud_enabled = frappe.db.get_value(
        'Shipment Service Provider', 'SendCloud', 'enabled')
    
    customer_account = None
    if delivery_note:
        values = frappe.db.get_value('Delivery Note', delivery_note, ['incoterm', 'customer_account'], as_dict=True)
        if values.get('incoterm') == "EXW (Ex Works)" and values.get('customer_account'):
            customer_account = values['customer_account']

    letmeship_prices = []
    packlink_prices = []
    sendcloud_prices = []
    if is_letmeship_enabled:
        letmeship_prices = get_letmeship_available_services(
            pickup_from_type=pickup_from_type,
            delivery_to_type=delivery_to_type,
            pickup_address_name=pickup_address_name,
            delivery_address_name=delivery_address_name,
            shipment_parcel=shipment_parcel,
            description_of_content=description_of_content,
            pickup_date=pickup_date,
            value_of_goods=value_of_goods,
            pickup_contact_name=pickup_contact_name,
            delivery_contact_name=delivery_contact_name,
            pickup_type=pickup_type,
            customer_account=customer_account
        )
    if is_packlink_enabled:
        packlink_prices = \
            get_packlink_available_services(pickup_address_name=pickup_address_name,
                                            delivery_address_name=delivery_address_name,
                                            shipment_parcel=shipment_parcel, pickup_date=pickup_date)
    if pickup_from_type == 'Company' and is_sendcloud_enabled:
        sendcloud_prices = \
            get_sendcloud_available_services(
                delivery_address_name=delivery_address_name, shipment_parcel=shipment_parcel)
    shipment_prices = letmeship_prices + packlink_prices + sendcloud_prices
    shipment_prices = sorted(shipment_prices, key=lambda k:
                             k['total_price'])
    return shipment_prices


@frappe.whitelist()
def create_shipment(
    shipment,
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    service_data,
    shipment_notific_email,
    tracking_notific_email,
    pickup_type=None,
    pickup_contact_name=None,
    delivery_contact_name=None,
    delivery_notes=[],
):
    """Create Shipment for the selected provider"""

    service_info = json.loads(service_data)
    shipment_info = []

    customer_account = None
    if len(json.loads(delivery_notes)) > 0:
        dn = json.loads(delivery_notes)[0]
        values = frappe.db.get_value('Delivery Note', dn, ['incoterm', 'customer_account'], as_dict=True)
        if values.get('incoterm') == "EXW (Ex Works)" and values.get('customer_account'):
            customer_account = values['customer_account']
    if service_info['service_provider'] == 'LetMeShip':
        shipment_info = create_letmeship_shipment(
            pickup_from_type=pickup_from_type,
            delivery_to_type=delivery_to_type,
            pickup_address_name=pickup_address_name,
            delivery_address_name=delivery_address_name,
            shipment_parcel=shipment_parcel,
            description_of_content=description_of_content,
            pickup_date=pickup_date,
            value_of_goods=value_of_goods,
            pickup_contact_name=pickup_contact_name,
            delivery_contact_name=delivery_contact_name,
            service_info=service_info,
            pickup_type=pickup_type,
            shipment_notific_email=shipment_notific_email,
            tracking_notific_email=tracking_notific_email,
            customer_account=customer_account
        )

    if service_info['service_provider'] == 'Packlink':
        shipment_info = create_packlink_shipment(
            pickup_from_type=pickup_from_type,
            delivery_to_type=delivery_to_type,
            pickup_address_name=pickup_address_name,
            delivery_address_name=delivery_address_name,
            shipment_parcel=shipment_parcel,
            description_of_content=description_of_content,
            pickup_date=pickup_date,
            value_of_goods=value_of_goods,
            pickup_contact_name=pickup_contact_name,
            delivery_contact_name=delivery_contact_name,
            service_info=service_info,
        )
    if service_info['service_provider'] == 'SendCloud':
        shipment_info = create_sendcloud_shipment(
            shipment=shipment,
            delivery_to_type=delivery_to_type,
            delivery_address_name=delivery_address_name,
            delivery_contact_name=delivery_contact_name,
            service_info=service_info,
            shipment_parcel=shipment_parcel,
            description_of_content=description_of_content,
            value_of_goods=value_of_goods
        )
    if shipment_info:
        frappe.db.set_value('Shipment', shipment, 'service_provider',
                            shipment_info.get('service_provider'))
        frappe.db.set_value('Shipment', shipment, 'carrier',
                            shipment_info.get('carrier'))
        frappe.db.set_value('Shipment', shipment, 'carrier_service',
                            shipment_info.get('carrier_service'))
        frappe.db.set_value('Shipment', shipment, 'shipment_id',
                            shipment_info.get('shipment_id'))
        frappe.db.set_value('Shipment', shipment, 'base_price',
                            shipment_info.get('base_price'))
        frappe.db.set_value('Shipment', shipment, 'net_price',
                            shipment_info.get('net_price'))
        frappe.db.set_value('Shipment', shipment, 'total_vat',
                            shipment_info.get('total_vat'))
        frappe.db.set_value('Shipment', shipment, 'shipment_amount',
                            shipment_info.get('shipment_amount'))
        frappe.db.set_value('Shipment', shipment, 'awb_number',
                            shipment_info.get('awb_number'))
        frappe.db.set_value('Shipment', shipment, 'status', 'Booked')
        if delivery_notes:
            update_delivery_note(delivery_notes=delivery_notes,
                                 shipment_info=shipment_info)
    return shipment_info


def update_delivery_note(delivery_notes, shipment_info=None,
                         tracking_info=None):
    """
        Update Shipment Info in Delivery Note
        Using db_set since some services might not exist
    """

    if type(delivery_notes) != str:
        delivery_notes_ = '["'+delivery_notes[0].delivery_note+'"]'
    else:
        delivery_notes_ = delivery_notes

    for delivery_note in json.loads(delivery_notes_):
        dl_doc = frappe.get_doc('Delivery Note', delivery_note)

        if shipment_info:
            dl_doc.db_set('delivery_type', 'Parcel Service')
            dl_doc.db_set('parcel_service', shipment_info.get('carrier'
                                                              ))
            dl_doc.db_set('parcel_service_type',
                          shipment_info.get('carrier_service'))
        if tracking_info:
            dl_doc.db_set('tracking_number',
                          tracking_info.get('awb_number'))
            dl_doc.db_set('tracking_url',
                          tracking_info.get('tracking_url'))
            dl_doc.db_set('tracking_status',
                          tracking_info.get('tracking_status'))
            dl_doc.db_set('tracking_status_info',
                          tracking_info.get('tracking_status_info'))


def update_tracking_info():
    """
        Daily scheduled event to update Tracking info for not delivered Shipments
        Also Updates the related Delivery Notes
    """

    shipments = frappe.get_all('Shipment', filters={
        'docstatus': 1,
        'status': 'Booked',
        'shipment_id': ['!=', ''],
        'tracking_status': ['!=', 'Delivered'],
    })
    try:
        for shipment in shipments:
            shipment_doc = frappe.get_doc('Shipment', shipment.name)
            tracking_info = \
                update_tracking(shipment_doc.service_provider,
                                shipment_doc.shipment_id,
                                shipment,
                                shipment_doc.shipment_delivery_notes)
            if tracking_info:
                shipment_doc.db_set('awb_number',
                                    tracking_info.get('awb_number'))
                shipment_doc.db_set('tracking_url',
                                    tracking_info.get('tracking_url'))
                shipment_doc.db_set('tracking_status',
                                    tracking_info.get('tracking_status'
                                                      ))
                shipment_doc.db_set('tracking_status_info',
                                    tracking_info.get('tracking_status_info'
                                                      ))
        print('Shipments updated Successfully')
    except Exception as exc:
        print(str(exc))


@frappe.whitelist()
def get_address_name(ref_doctype, docname):
    """ Return address name """

    return get_party_shipping_address(ref_doctype, docname)


@frappe.whitelist()
def get_contact_name(ref_doctype, docname):
    """ Return address name """

    return get_default_contact(ref_doctype, docname)


@frappe.whitelist()
def make_shipment(
    pickup_company,
    delivery_customer,
    delivery_address_name,
    delivery_address,
    delivery_contact_name,
    pickup_address_name,
    pickup_address,
    delivery_note,
    grand_total,
    is_mask,
):
    """ Make new Shipment doc from Delivery Note """

    delivery_contact_info = frappe.db.get_value('Contact',
                                                delivery_contact_name, ['first_name', 'last_name',
                                                                        'email_id', 'phone', 'mobile_no'], as_dict=1)
    if not (delivery_contact_info.last_name
            and delivery_contact_info.email_id
            and delivery_contact_info.phone):
        frappe.throw(_("Last Name, Email or Phone/Mobile of the Contact are mandatory to continue. </br> \
								Please set Last Name, Email and Phone for the contact <a href='#Form/Contact/{0}'>{1}</a>"
                       ).format(delivery_contact_name,
                                delivery_contact_name))
    delivery_contact = delivery_contact_info.first_name \
        + delivery_contact_info.last_name + '<br>' \
        + delivery_contact_info.email_id + '<br>' \
        + delivery_contact_info.phone or delivery_contact_info.mobile_no

    pickup_contact_info = frappe.db.get_value('User',
                                              frappe.session.user, ['full_name', 'email', 'phone',
                                                                    'mobile_no'], as_dict=1)
    if is_mask == 'true':
        pickup_contact_info.email = 'service@eso-hygiene.com'
        pickup_address_name = 'ESO Hygiene-Versand'
        pickup_address = get_address_display(pickup_address_name)

    if not (pickup_contact_info.email and pickup_contact_info.phone):
        frappe.throw(_("Email and Phone/Mobile of the User are mandatory to continue. </br> \
								Please set Email/Phone for the user <a href='#Form/User/{0}'>{1}</a>"
                       ).format(frappe.session.user, frappe.session.user))
    pickup_contact = pickup_contact_info.full_name + '<br>' \
        + pickup_contact_info.email + '<br>' \
        + pickup_contact_info.phone or pickup_contact_info.mobile_no
    shipment = frappe.new_doc('Shipment')
    shipment.pickup_company = pickup_company
    shipment.delivery_customer = delivery_customer
    shipment.delivery_address_name = delivery_address_name
    shipment.delivery_address = delivery_address
    shipment.delivery_contact_name = delivery_contact_name
    shipment.delivery_contact = delivery_contact
    shipment.delivery_contact_email = delivery_contact_info.email_id
    shipment.pickup_address_name = pickup_address_name
    shipment.pickup_address = pickup_address
    shipment.pickup_contact = pickup_contact
    shipment.value_of_goods = grand_total
    if is_mask == 'true':
        shipment.description_of_content = 'Einmal-Mundschutz'
        shipment.pickup_type = 'Self delivery'
        shipment.pickup_date = today()
        if frappe.db.get_value('Delivery Note Item',
                               {'parent': delivery_note,
                                'item_code': ['in', ('990593', '990588'
                                                     )]}, 'qty') == 10:
            shipment.preset = 'Faltkarton 4'
            shipment_parcel = frappe.get_doc('Shipment Parcel Preset',
                                             'Faltkarton 4')
            shipment.append('shipment_parcel', {
                'length': shipment_parcel.length,
                'width': shipment_parcel.width,
                'height': shipment_parcel.height,
                'weight': shipment_parcel.weight,
                'count': 1,
            })

    shipment.append('shipment_delivery_notes',
                    {'delivery_note': delivery_note,
                     'grand_total': grand_total})
    return shipment


@frappe.whitelist()
def print_shipping_label(service_provider, shipment_id):
    if service_provider == 'LetMeShip':
        shipping_label = get_letmeship_label(shipment_id)
    elif service_provider == 'Packlink':
        shipping_label = get_packlink_label(shipment_id)
    else:
        shipping_label = get_sendcloud_label(shipment_id)
    return shipping_label


@frappe.whitelist()
def update_tracking(service_provider, shipment_id, shipment, delivery_notes=[]):
    """ Update Tracking info in Shipment """
    if service_provider == 'LetMeShip':
        tracking_data = get_letmeship_tracking_data(shipment_id)
    elif service_provider == 'Packlink':
        tracking_data = get_packlink_tracking_data(shipment_id)
    else:
        tracking_data = get_sendcloud_tracking_data(shipment_id)
    if tracking_data:
        if delivery_notes:
            update_delivery_note(delivery_notes=delivery_notes,
                                 tracking_info=tracking_data)
        frappe.db.set_value('Shipment', shipment, 'awb_number',
                            tracking_data.get('awb_number'))
        frappe.db.set_value('Shipment', shipment, 'tracking_status',
                            tracking_data.get('tracking_status'))
        frappe.db.set_value('Shipment', shipment, 'tracking_status_info',
                            tracking_data.get('tracking_status_info'))
        frappe.db.set_value('Shipment', shipment, 'tracking_url',
                            tracking_data.get('tracking_url'))


@frappe.whitelist()
def is_mask_shipment(delivery_note):
    if frappe.db.exists('Delivery Note Item', {'parent': delivery_note,
                                               'item_code': ['in', ('990593', '990588')]}):
        is_mask = True
        qty = frappe.db.get_value('Delivery Note Item',
                                  {'parent': delivery_note,
                                   'item_code': ['in', ('990593',
                                                        '990588')]}, 'qty')
        return {'is_mask': is_mask, 'qty': qty}

@frappe.whitelist()
def get_holidays(company = 'Newmatik GmbH', exclude_weekend = True, from_date = None, to_date = None):
    """
        Return list of holidays
    """	
    exclude_weekend = json.loads(exclude_weekend)
    holiday_list = frappe.get_cached_value('Company', company, "default_holiday_list")
    condition = " parent='%s'" % holiday_list
    condition += " and holiday_date between '%s' and '%s'" % (from_date or "1900-01-01", to_date or "9999-12-31")
    if exclude_weekend:
        condition += "and description not in ('Sunday', 'Saturday')"


    holidays = frappe.db.sql('''select holiday_date from `tabHoliday` where %s''' % condition, as_dict=1)
    
    holidays = sorted(holidays, key=lambda k:k['holiday_date'])

    return holidays



@frappe.whitelist()
def calculate_shipping_cost(data):
    data = json.loads(data)
    settings = frappe.get_doc('Shipment Settings')

    # calculation of new_rate 
    # new_rate = base_price + (x * count)
    base_price = data['base_price']
    x = settings.margin_cost
    count = 0
    for parcel in data['shipment_parcel']: 
        count += parcel['count']

    new_rate = flt(( base_price + (x * count) ) / len(data['shipment_delivery_notes']), 2)


    value_of_goods = 0
    for dn in data['shipment_delivery_notes']: 
        fields = ['name', "name as docname", "name", "item_code" ,"conversion_factor", "qty", "rate", "idx", "weight_kg", "weight_per_unit"]
        trans_items = frappe.db.get_list("Delivery Note Item", {"parent": dn['delivery_note']}, fields)

        shipping_item = {
                'docname': '',
                'name': '',
                'item_code': 'Shipping', 
                'conversion_factor': 1, 
                'qty': 1, 
                'rate': new_rate, 
                'idx': len(trans_items)+1,
                "weight_kg": 0,
                "weight_per_unit": 0
            }
        has_shipping = False
        for item in trans_items: 
            if item.item_code == "Shipping":
                item.update({
                    'rate': new_rate, 
                })
                has_shipping = True

        if not has_shipping:
            trans_items.append(shipping_item)

        # update delivery note
        update_child_qty_rate("Delivery Note", json.dumps(trans_items, default=str), dn['delivery_note'], child_docname="items")
        
        # set delivery note grand_total
        dn_total = frappe.db.get_value("Delivery Note", dn['delivery_note'], 'grand_total')
        frappe.db.set_value("Shipment Delivery Notes", {"parent": dn['parent'], "delivery_note": dn['delivery_note']}, 'grand_total', dn_total)
        
        value_of_goods += dn_total

    frappe.db.set_value("Shipment", data['name'], 'value_of_goods', flt(value_of_goods, 2))

    return 