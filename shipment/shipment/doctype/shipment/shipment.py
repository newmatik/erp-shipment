#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
from frappe.model.document import Document
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact
from shipment.shipment.doctype.shipment.let_me_ship import get_letmeship_available_services, create_letmeship_shipment
from shipment.shipment.doctype.shipment.packlink import get_packlink_available_services


class Shipment(Document):

    pass


@frappe.whitelist()
def get_address(ref_doctype, docname):
    """ Return address name """
    return get_party_shipping_address(ref_doctype, docname)


@frappe.whitelist()
def get_contact(ref_doctype, docname):
    """ Return address name """
    return get_default_contact(ref_doctype, docname)


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
    pickup_contact_name=None,
    delivery_contact_name=None,
    ):
    """Return Shipping Rates for the various Shipping Providers"""

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
        )
    packlink_prices = get_packlink_available_services()
    return letmeship_prices


@frappe.whitelist()
def create_shipment(
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
    pickup_contact_name=None,
    delivery_contact_name=None,
    ):
    """Create Shipment for the selected provider"""

    service_info = json.loads(service_data)
    shipment_info = []
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
            shipment_notific_email=shipment_notific_email,
            tracking_notific_email=tracking_notific_email,
            )
    return shipment_info
