#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
import re


def get_address(address_name):
    address = frappe.db.get_value('Address', address_name, [
        'address_title',
        'address_line1',
        'address_line2',
        'city',
        'pincode',
        'country',
    ], as_dict=1)
    address.country_code = frappe.db.get_value('Country',
                                               address.country, 'code').upper()
    if not address.pincode or address.pincode == '':
        frappe.throw(_("Postal Code is mandatory to continue. </br> \
                     Please set Postal Code for Address <a href='#Form/Address/{0}'>{1}</a>"
                       ).format(address_name, address_name))
    address.pincode = address.pincode.replace(' ', '')
    address.city = address.city.strip()
    return address


def get_contact(contact_name):
    contact = frappe.db.get_value('Contact', contact_name, [
        'first_name',
        'last_name',
        'email_id',
        'phone',
        'mobile_no',
        'gender',
    ], as_dict=1)
    if not contact.last_name:
        frappe.throw(_("Last Name is mandatory to continue. </br> \
                     Please set Last Name for Contact <a href='#Form/Contact/{0}'>{1}</a>"
                       ).format(contact_name, contact_name))
    if not contact.phone:
        contact.phone = contact.mobile_no
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.email = contact.email_id
    contact.title = 'MS'
    if contact.gender == 'Male':
        contact.title = 'MR'
    return contact


def get_company_contact():
    contact = frappe.db.get_value('User', frappe.session.user, [
        'first_name',
        'last_name',
        'email',
        'phone',
        'mobile_no',
        'gender',
    ], as_dict=1)
    if not contact.phone:
        contact.phone = contact.mobile_no
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.title = 'MS'
    if contact.gender == 'Male':
        contact.title = 'MR'
    contact.email = 'service@eso-hygiene.com'
    return contact


def get_tracking_url(carrier, tracking_number):
    """ Return the formatted Tracking URL"""

    tracking_url = ''
    url_reference = frappe.get_value('Parcel Service', carrier,
                                     'url_reference')
    if url_reference:
        tracking_url = frappe.render_template(url_reference,
                                              {'tracking_number': tracking_number})
        tracking_url_template = \
            '<a href="{{ tracking_url }}" target="_blank"><b>{{ _("Click here to Track Shipment") }}</a></b>'
        tracking_url = frappe.render_template(tracking_url_template,
                                              {'tracking_url': tracking_url})
    return tracking_url
