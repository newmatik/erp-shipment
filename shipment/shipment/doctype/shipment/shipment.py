# -*- coding: utf-8 -*-
# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact

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