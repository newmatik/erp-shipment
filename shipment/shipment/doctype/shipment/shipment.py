# -*- coding: utf-8 -*-

# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
import requests
import re
from frappe import _
from frappe.model.document import Document
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact


class Shipment(Document):

    pass

def get_address(address_name):
    address = frappe.db.get_value('Address', address_name,
                                  ['address_line1', 'address_line2',
                                  'city', 'pincode', 'country'],
                                  as_dict=1)
    address.country_code = frappe.db.get_value('Country',
            address.country, 'code').upper()
    return address


def get_contact(contact_name):
    contact = frappe.db.get_value('Contact', contact_name, ['first_name'
                                  , 'last_name', 'email_id', 'phone',
                                  'mobile_no'], as_dict=1)
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.email = contact.email_id
    return contact


def get_company_contact():
    contact = frappe.db.get_value('User', frappe.session.user,
                                  ['first_name', 'last_name', 'email',
                                  'phone', 'mobile_no'], as_dict=1)
    contact.phone_prefix = contact.phone[:3]
    contact.phone = contact.phone[3:].strip()
    return contact


def get_parcel_list(shipment_parcel, description_of_content):
    parcel_list = []
    for parcel in shipment_parcel:
        formatted_parcel = {}
        formatted_parcel['height'] = parcel.get('height')
        formatted_parcel['width'] = parcel.get('width')
        formatted_parcel['length'] = parcel.get('length')
        formatted_parcel['weight'] = parcel.get('weight')
        formatted_parcel['quantity'] = parcel.get('count')
        formatted_parcel['contentDescription'] = description_of_content
        parcel_list.append(formatted_parcel)
    return parcel_list


def get_letmeship_available_services(
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
    pickup_address = get_address(pickup_address_name)
    delivery_address = get_address(delivery_address_name)
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()
    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)

    url = 'https://api.test.letmeship.com/v1/available'
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    payload = {'pickupInfo': {
        'address': {
            'countryCode': pickup_address.country_code,
            'zip': pickup_address.pincode,
            'city': pickup_address.city,
            'street': pickup_address.address_line1,
            'houseNo': pickup_address.address_line2,
            },
        'person': {'title': 'MR',
                   'firstname': pickup_contact.first_name,
                   'lastname': pickup_contact.last_name},
        'phone': {'phoneNumber': pickup_contact.phone,
                  'phoneNumberPrefix': pickup_contact.phone_prefix},
        'email': pickup_contact.email,
        }, 'deliveryInfo': {
        'address': {
            'countryCode': delivery_address.country_code,
            'zip': delivery_address.pincode,
            'city': delivery_address.city,
            'street': delivery_address.address_line1,
            'houseNo': delivery_address.address_line2,
            },
        'person': {'title': 'MR',
                   'firstname': delivery_contact.first_name,
                   'lastname': delivery_contact.last_name},
        'phone': {'phoneNumber': delivery_contact.phone,
                  'phoneNumberPrefix': delivery_contact.phone_prefix},
        'email': delivery_contact.email,
        }, 'shipmentDetails': {
        'contentDescription': description_of_content,
        'shipmentType': 'PARCEL',
        'shipmentSettings': {
            'saturdayDelivery': False,
            'ddp': False,
            'insurance': False,
            'pickupOrder': False,
            'pickupTailLift': False,
            'deliveryTailLift': False,
            'holidayDelivery': False,
            },
        'goodsValue': value_of_goods,
        'parcelList': parcel_list,
        'pickupInterval': {'date': pickup_date},
        }}

    try:
        available_services = []
        response_data = requests.post(url=url,
                auth=(service_provider.api_key,
                service_provider.api_password), headers=headers,
                data=json.dumps(payload))
        response_data = json.loads(response_data.text)
        if 'serviceList' in response_data:
            for response in response_data['serviceList']:
                available_service = frappe._dict()
                basic_info = response['baseServiceDetails']
                price_info = basic_info['priceInfo']
                available_service.service_provider = 'LetMeShip'
                available_service.id = basic_info['id']
                available_service.service_name = basic_info['name']
                available_service.carrier = basic_info['carrier']
                available_service.real_weight = price_info['realWeight']
                available_service.total_price = price_info['totalPrice']
                available_service.price_info = price_info
                available_services.append(available_service)
            return available_services
        else:
            frappe.throw(_('Error occurred while fetching LetMeShip prices: {0}'
                         ).format(response_data['message']))
    except Exception as exc:
        frappe.msgprint(_('Error occurred while fetching LetMeShip Prices: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)


def create_letmeship_shipment(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    service_info,
    shipment_notific_email,
    tracking_notific_email,
    pickup_contact_name=None,
    delivery_contact_name=None,
    ):

    pickup_address = get_address(pickup_address_name)
    delivery_address = get_address(delivery_address_name)
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)

    url = 'https://api.test.letmeship.com/v1/shipments'
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    payload = {
        'pickupInfo': {
            'address': {
                'countryCode': pickup_address.country_code,
                'zip': pickup_address.pincode,
                'city': pickup_address.city,
                'street': pickup_address.address_line1,
                'houseNo': pickup_address.address_line2,
                },
            'company': 'ESO Electronic',
            'person': {'title': 'MR',
                       'firstname': pickup_contact.first_name,
                       'lastname': pickup_contact.last_name},
            'phone': {'phoneNumber': pickup_contact.phone,
                      'phoneNumberPrefix': pickup_contact.phone_prefix},
            'email': pickup_contact.email,
            },
        'deliveryInfo': {
            'address': {
                'countryCode': delivery_address.country_code,
                'zip': delivery_address.pincode,
                'city': delivery_address.city,
                'street': delivery_address.address_line1,
                'houseNo': delivery_address.address_line2,
                },
            'person': {'title': 'MR',
                       'firstname': delivery_contact.first_name,
                       'lastname': delivery_contact.last_name},
            'phone': {'phoneNumber': delivery_contact.phone,
                      'phoneNumberPrefix': delivery_contact.phone_prefix},
            'email': delivery_contact.email,
            },
        'service': {
            'baseServiceDetails': {
                'id': service_info['id'],
                'name': service_info['service_name'],
                'carrier': service_info['carrier'],
                'priceInfo': service_info['price_info'],
                },
            'supportedExWorkType': [],
            'messages': [''],
            'description': '',
            'serviceInfo': '',
            },
        'shipmentDetails': {
            'contentDescription': description_of_content,
            'shipmentType': 'PARCEL',
            'shipmentSettings': {
                'saturdayDelivery': False,
                'ddp': False,
                'insurance': False,
                'pickupOrder': False,
                'pickupTailLift': False,
                'deliveryTailLift': False,
                'holidayDelivery': False,
                },
            'goodsValue': value_of_goods,
            'parcelList': parcel_list,
            'pickupInterval': {'date': pickup_date},
            'contentDescription': description_of_content,
            },
        'shipmentNotification': {'trackingNotification': {
            'deliveryNotification': True,
            'problemNotification': True,
            'emails': [tracking_notific_email],
            'notificationText': '',
            }, 'recipientNotification': {'notificationText': '',
                    'emails': [shipment_notific_email]}},
        'labelEmail': True,
        }
    try:
        response_data = requests.post(url=url,
                auth=(service_provider.api_key,
                service_provider.api_password), headers=headers,
                data=json.dumps(payload))
        response_data = json.loads(response_data.text)
        if 'shipmentId' in response_data:
            return {
                'service_provider': 'LetMeShip',
                'shipment_id': response_data['shipmentId'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                }
        elif 'message' in response_data:
            frappe.throw(_('Error occurred while creating Shipment: {0}'
                         ).format(response_data['message']))
    except Exception as exc:

        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)


@frappe.whitelist()
def get_address_name(ref_doctype, docname):
    """ Return address name """
    return get_party_shipping_address(ref_doctype, docname)


@frappe.whitelist()
def get_contact_name(ref_doctype, docname):
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
