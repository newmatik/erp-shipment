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
    def on_submit(self):
        if not self.shipment_parcel:
            frappe.throw(_('Please enter Shipment Parcel information'))
        if self.value_of_goods == 0:
            frappe.throw(_('Please enter value of goods'))


def get_address(address_name):
    address = frappe.db.get_value('Address', address_name,
                                  ['address_title', 'address_line1', 'address_line2',
                                  'city', 'pincode', 'country'],
                                  as_dict=1)
    address.country_code = frappe.db.get_value('Country',
            address.country, 'code').upper()
    return address


def get_contact(contact_name):
    contact = frappe.db.get_value('Contact', contact_name, ['first_name'
                                  , 'last_name', 'email_id', 'phone',
                                  'mobile_no', 'gender'], as_dict=1)
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
    contact = frappe.db.get_value('User', frappe.session.user,
                                  ['first_name', 'last_name', 'email',
                                  'phone', 'mobile_no', 'gender'], as_dict=1)
    if not contact.phone:
        contact.phone = contact.mobile_no
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.title = 'MS'
    if contact.gender == 'Male':
        contact.title = 'MR'
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

    #LetMeShip have limit of 30 characters for Company field
    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = delivery_address.address_title[:30]

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)

    if not service_provider:
        return

    url = 'https://api.letmeship.com/v1/available'
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
        'company': pickup_address.address_title,
        'person': {'title': pickup_contact.title,
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
        'company': delivery_address.address_title,
        'person': {'title': delivery_contact.title,
                   'firstname': delivery_contact.first_name,
                   'lastname': delivery_contact.last_name},
        'phone': {'phoneNumber': delivery_contact.phone,
                  'phoneNumberPrefix': delivery_contact.phone_prefix},
        'email': delivery_contact.email,
        }, 'shipmentDetails': {
        'contentDescription': description_of_content,
        'transportType': 'EXPRESS',
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
    return []


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

    #LetMeShip have limit of 30 characters for Company field
    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = delivery_address.address_title[:30]

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    if not service_provider:
        return []

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
            'company': pickup_address.address_title,
            'person': {'title': pickup_contact.title,
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
            'company': delivery_address.address_title,
            'person': {'title': delivery_contact.title,
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


#Packlink
def parse_pickup_date(pickup_date):
    return pickup_date.replace('-', '/')

def packlink_get_parcel_list(shipment_parcel):
    parcel_list = []
    for parcel in shipment_parcel:
        for count in range(parcel.get('count')):
            formatted_parcel = {}
            formatted_parcel['height'] = parcel.get('height')
            formatted_parcel['width'] = parcel.get('width')
            formatted_parcel['length'] = parcel.get('length')
            formatted_parcel['weight'] = parcel.get('weight')
            parcel_list.append(formatted_parcel)
    return parcel_list

def get_packlink_available_services(
    pickup_address_name, 
    delivery_address_name,
    shipment_parcel,
    pickup_date
    ):
    pickup_address = get_address(pickup_address_name)
    from_zip = pickup_address.pincode
    from_country_code = pickup_address.country_code

    delivery_address = get_address(delivery_address_name)
    to_zip = delivery_address.pincode
    to_country_code = delivery_address.country_code

    shipment_parcel_params = ''
    for index, parcel in enumerate(packlink_get_parcel_list(json.loads(shipment_parcel))):
        shipment_parcel_params += 'packages[{index}][height]={height}&packages[{index}][length]={length}&packages[{index}][weight]={weight}&packages[{index}][width]={width}&'.format(
            index = index, 
            height = parcel['height'], 
            length = parcel['length'],
            weight = parcel['weight'],
            width = parcel['width']
        )
    
    url = 'https://api.packlink.com/v1/services?from[country]={}&from[zip]={}&to[country]={}&to[zip]={}&{}sortBy=totalPrice&source=PRO'.format(
            from_country_code, from_zip, to_country_code, to_zip, shipment_parcel_params
    )
    api_key = frappe.db.get_value('Shipment Service Provider', 'Packlink', 'api_key')
    if not api_key:
        return []

    try:
        responses = requests.get(url, headers={'Authorization': api_key})
        responses_dict = json.loads(responses.text)

        # If an error occured on the api. Show the error message
        if 'messages' in responses_dict:
            frappe.msgprint(
                _('Packlink: {0}'.format(str(responses_dict['messages'][0]['message']))), 
                indicator='orange',
                alert=True
            )

        available_services = []
        for response in responses_dict:
            if parse_pickup_date(pickup_date) in response['available_dates'].keys():
                available_service = frappe._dict()
                available_service.service_provider = 'Packlink'
                available_service.service_name = response['name']
                available_service.carrier = response['carrier_name']
                available_service.total_price = response['price']['total_price']
                available_service.service_id = response['id']
                available_service.available_dates = response['available_dates']
                available_services.append(available_service)

        return available_services
    except Exception as exc:
        frappe.msgprint(
            _('Error occurred on Packlink: {0}').format(str(exc)), 
            indicator='orange',
            alert=True
        )
    return []

def create_packlink_shipment(
    pickup_from_type,
    delivery_to_type,
    pickup_address_name,
    delivery_address_name,
    shipment_parcel,
    description_of_content,
    pickup_date,
    value_of_goods,
    pickup_contact_name,
    delivery_contact_name,
    service_info
    ):
    api_key = frappe.db.get_value('Shipment Service Provider', 'Packlink', 'api_key')

    pickup_address = get_address(pickup_address_name)
    from_country_code = pickup_address.country_code
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()

    delivery_address = get_address(delivery_address_name)
    to_country_code = delivery_address.country_code
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    data = {
        "additional_data": {
            "postal_zone_id_from": "",
            "postal_zone_name_from": pickup_address.country,
            "postal_zone_id_to": "",
            "postal_zone_name_to": delivery_address.country
        },
        "collection_date": parse_pickup_date(pickup_date),
        "collection_time": "",
        "content": description_of_content,
        "contentvalue": value_of_goods,
        "content_second_hand": False,
        "from": {
            "city": pickup_address.city,
            "company": pickup_address.address_title,
            "country": from_country_code,
            "email": pickup_contact.email,
            "name": pickup_contact.first_name,
            "phone": pickup_contact.phone,
            "state": pickup_address.country,
            "street1": pickup_address.address_line1,
            "surname": pickup_contact.last_name,
            "zip_code": pickup_address.pincode
        },
        "insurance": {
            "amount": 0,
            "insurance_selected": False
        },
        "price": {},
        "packages": packlink_get_parcel_list(json.loads(shipment_parcel)),
        "service_id": service_info['service_id'],
        "to": {
            "city": delivery_address.city,
            "company": delivery_address.address_title,
            "country": to_country_code,
            "email": delivery_contact.email,
            "name": delivery_contact.first_name,
            "phone": delivery_contact.phone,
            "state": delivery_address.country,
            "street1": delivery_address.address_line1,
            "surname": delivery_contact.last_name,
            "zip_code": delivery_address.pincode
        }
    }

    url = 'https://api.packlink.com/v1/shipments'
    headers = {'Authorization': api_key, 'Content-Type': 'application/json'}

    try:
        warehouse_id_response = requests.post(url, json = data, headers=headers)
        warehouse_id = json.loads(warehouse_id_response.text)
        response_data = {
            'service_provider': 'Packlink',
            'shipment_id': warehouse_id['reference'],
            'carrier': service_info['carrier'],
            'carrier_service': service_info['service_name'],
        }
        return response_data
    except Exception as exc:
        frappe.msgprint(
            _('Error occurred while creating Shipment: {0}').format(str(exc)), 
            indicator='orange',
            alert=True
        )

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

    letmeship_prices = packlink_prices = []
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
    packlink_prices = get_packlink_available_services(
        pickup_address_name=pickup_address_name,
        delivery_address_name=delivery_address_name,
        shipment_parcel=shipment_parcel,
        pickup_date=pickup_date
    )
    return letmeship_prices + packlink_prices


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
            service_info=service_info
        )
    return shipment_info
