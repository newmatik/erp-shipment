# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact
from shipment.api.let_me_ship import get_letmeship_available_services
from shipment.api.packlink import get_packlink_available_services

class Shipment(Document):
	pass

@frappe.whitelist()
def get_address(ref_doctype, docname):
	'''
		Return address name
	'''	
	return get_party_shipping_address(ref_doctype, docname)

@frappe.whitelist()
def get_contact(ref_doctype, docname):
	'''
		Return address name
	'''
	return get_default_contact(ref_doctype, docname)

@frappe.whitelist()
def fetch_shipping_rates(pickup_from_type, delivery_to_type, pickup_address_name, delivery_address_name,
		shipment_parcel, description_of_content, pickup_date, value_of_goods, pickup_contact_name = None, delivery_contact_name = None):
	'''
		Return Shipping Rates for the various Shipping Providers
	'''	
	letmeship_prices = get_letmeship_available_services()
	packlink_prices = get_packlink_available_services()
	return letmeship_prices, packlink_prices