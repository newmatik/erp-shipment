#!/usr/bin/python
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
from frappe.contacts.doctype.address.address import get_address_display
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
from frappe.utils import today


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
        self.status = 'Submitted'

    def on_cancel(self):
        self.status = 'Cancelled'

    def validate_weight(self):
        for parcel in self.shipment_parcel:
            if parcel.weight <= 0:
                frappe.throw(_('Parcel weight cannot be 0'))


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

    # LetMeShip have limit of 30 characters for Company field

    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = \
            delivery_address.address_title[:30]

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)

    if not service_provider:
        return []

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
            'addressInfo1': pickup_address.address_line2,
            'houseNo': '',
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
            'addressInfo1': delivery_address.address_line2,
            'houseNo': '',
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
                available_service.carrier = basic_info['carrier']
                available_service.service_name = \
                    match_parcel_service_type_alias(basic_info['name'],
                        basic_info['carrier'])
                available_service.is_preferred = \
                    frappe.db.get_value('Parcel Service Type',
                        available_service.service_name,
                        'show_in_preferred_services_list')
                available_service.real_weight = price_info['realWeight']
                available_service.total_price = price_info['netPrice']
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

    # LetMeShip have limit of 30 characters for Company field

    if len(pickup_address.address_title) > 30:
        pickup_address.address_title = pickup_address.address_title[:30]
    if len(delivery_address.address_title) > 30:
        delivery_address.address_title = \
            delivery_address.address_title[:30]

    parcel_list = get_parcel_list(json.loads(shipment_parcel),
                                  description_of_content)

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    if not service_provider:
        return []

    url = 'https://api.letmeship.com/v1/shipments'
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
                'addressInfo1': pickup_address.address_line2,
                'houseNo': '',
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
                'addressInfo1': delivery_address.address_line2,
                'houseNo': '',
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
            shipment_amount = response_data['service']['priceInfo'
                    ]['totalPrice']
            awb_number = ''
            tracking_response = \
                requests.get('https://api.letmeship.com/v1/shipments/{id}'.format(id=response_data['shipmentId'
                             ]), auth=(service_provider.api_key,
                             service_provider.api_password),
                             headers=headers)
            tracking_response_data = json.loads(tracking_response.text)
            if 'trackingData' in tracking_response_data:
                for parcel in tracking_response_data['trackingData'
                        ]['parcelList']:
                    if 'awbNumber' in parcel:
                        awb_number = parcel['awbNumber']
            return {
                'service_provider': 'LetMeShip',
                'shipment_id': response_data['shipmentId'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                'shipment_amount': shipment_amount,
                'awb_number': awb_number,
                }
        elif 'message' in response_data:
            frappe.throw(_('Error occurred while creating Shipment: {0}'
                         ).format(response_data['message']))
    except Exception as exc:
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)


def get_letmeship_label(shipment_id):

    # return shipment_label

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    shipment_label_response = \
        requests.get('https://api.letmeship.com/v1/shipments/{id}/documents?types=LABEL'.format(id=shipment_id),
                     auth=(service_provider.api_key,
                     service_provider.api_password), headers=headers)
    shipment_label_response_data = \
        json.loads(shipment_label_response.text)
    if 'documents' in shipment_label_response_data:
        for label in shipment_label_response_data['documents']:
            if 'data' in label:
                return json.dumps(label['data'])
    else:
        frappe.throw(_('Error occurred while printing Shipment: {0}'
                     ).format(shipment_label_response_data['message']))


def get_letmeship_tracking_data(shipment_id):
    """ return letmeship tracking data """

    service_provider = frappe.db.get_value('Shipment Service Provider',
            'Let Me Ship', ['api_key', 'api_password'], as_dict=1)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json',
               'Access-Control-Allow-Origin': 'string'}
    try:
        tracking_data_response = \
            requests.get('http://api.letmeship.com/v1/tracking?shipmentid={id}'.format(id=shipment_id),
                         auth=(service_provider.api_key,
                         service_provider.api_password), headers=headers)
        tracking_data = json.loads(tracking_data_response.text)
        if 'lmsTrackingStatus' in tracking_data:
            tracking_status = 'In Progress'
            if tracking_data['lmsTrackingStatus'].startswith('DELIVERED'):
                tracking_status = 'Delivered'
            if tracking_data['lmsTrackingStatus'] == 'RETURNED':
                tracking_status = 'Returned'
            if tracking_data['lmsTrackingStatus'] == 'LOST':
                tracking_status = 'Lost'
            tracking_url = get_tracking_url(carrier=tracking_data['carrier'
                    ], tracking_number=tracking_data['awbNumber'])
            return {
                'awb_number': tracking_data['awbNumber'],
                'tracking_status': tracking_status,
                'tracking_status_info': tracking_data['lmsTrackingStatus'],
                'tracking_url': tracking_url,
                }
        elif 'message' in tracking_data:
            frappe.throw(_('Error occurred while updating Shipment: {0}'
                         ).format(tracking_data['message']))
    except Exception as exc:
        frappe.msgprint(_('Error occurred while updating Shipment: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)


# Packlink

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
    pickup_date,
    ):

    pickup_address = get_address(pickup_address_name)
    from_zip = pickup_address.pincode
    from_country_code = pickup_address.country_code

    delivery_address = get_address(delivery_address_name)
    to_zip = delivery_address.pincode
    to_country_code = delivery_address.country_code

    shipment_parcel_params = ''
    for (index, parcel) in \
        enumerate(packlink_get_parcel_list(json.loads(shipment_parcel))):
        shipment_parcel_params += \
            'packages[{index}][height]={height}&packages[{index}][length]={length}&packages[{index}][weight]={weight}&packages[{index}][width]={width}&'.format(index=index,
                height=parcel['height'], length=parcel['length'],
                weight=parcel['weight'], width=parcel['width'])

    url = \
        'https://api.packlink.com/v1/services?from[country]={}&from[zip]={}&to[country]={}&to[zip]={}&{}sortBy=totalPrice&source=PRO'.format(from_country_code,
            from_zip, to_country_code, to_zip, shipment_parcel_params)
    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    if not api_key:
        return []

    try:
        responses = requests.get(url,
                                 headers={'Authorization': api_key})
        responses_dict = json.loads(responses.text)

        # If an error occured on the api. Show the error message

        if 'messages' in responses_dict:
            frappe.msgprint(_('Packlink: {0}'.format(str(responses_dict['messages'
                            ][0]['message']))), indicator='orange',
                            alert=True)

        available_services = []
        for response in responses_dict:
            if parse_pickup_date(pickup_date) \
                in response['available_dates'].keys():
                available_service = frappe._dict()
                available_service.service_provider = 'Packlink'
                available_service.carrier = response['carrier_name']
                available_service.service_name = \
                    match_parcel_service_type_alias(response['name'],
                        response['carrier_name'])
                available_service.is_preferred = \
                    frappe.db.get_value('Parcel Service Type',
                        available_service.service_name,
                        'show_in_preferred_services_list')
                available_service.total_price = response['price'
                        ]['base_price']
                available_service.actual_price = response['price'
                        ]['total_price']
                available_service.service_id = response['id']
                available_service.available_dates = \
                    response['available_dates']
                available_services.append(available_service)

        return available_services
    except Exception as exc:
        frappe.msgprint(_('Error occurred on Packlink: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)
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
    service_info,
    ):

    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')

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
        'additional_data': {
            'postal_zone_id_from': '',
            'postal_zone_name_from': pickup_address.country,
            'postal_zone_id_to': '',
            'postal_zone_name_to': delivery_address.country,
            },
        'collection_date': parse_pickup_date(pickup_date),
        'collection_time': '',
        'content': description_of_content,
        'contentvalue': value_of_goods,
        'content_second_hand': False,
        'from': {
            'city': pickup_address.city,
            'company': pickup_address.address_title,
            'country': from_country_code,
            'email': pickup_contact.email,
            'name': pickup_contact.first_name,
            'phone': pickup_contact.phone,
            'state': pickup_address.country,
            'street1': pickup_address.address_line1,
            'street2': pickup_address.address_line2,
            'surname': pickup_contact.last_name,
            'zip_code': pickup_address.pincode,
            },
        'insurance': {'amount': 0, 'insurance_selected': False},
        'price': {},
        'packages': packlink_get_parcel_list(json.loads(shipment_parcel)),
        'service_id': service_info['service_id'],
        'to': {
            'city': delivery_address.city,
            'company': delivery_address.address_title,
            'country': to_country_code,
            'email': delivery_contact.email,
            'name': delivery_contact.first_name,
            'phone': delivery_contact.phone,
            'state': delivery_address.country,
            'street1': delivery_address.address_line1,
            'street2': delivery_address.address_line2,
            'surname': delivery_contact.last_name,
            'zip_code': delivery_address.pincode,
            },
        }

    url = 'https://api.packlink.com/v1/shipments'
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}

    try:
        response_data = requests.post(url, json=data, headers=headers)
        response_data = json.loads(response_data.text)
        if 'reference' in response_data:
            return {
                'service_provider': 'Packlink',
                'shipment_id': response_data['reference'],
                'carrier': service_info['carrier'],
                'carrier_service': service_info['service_name'],
                'shipment_amount': service_info['actual_price'],
                'awb_number': '',
                }
    except Exception as exc:
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                        ).format(str(exc)), indicator='orange',
                        alert=True)


def get_packlink_label(shipment_id):
    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}
    shipment_label_response = \
        requests.get('https://api.packlink.com/v1/shipments/{id}/labels'.format(id=shipment_id),
                     headers=headers)
    shipment_label = json.loads(shipment_label_response.text)
    if shipment_label:
        return shipment_label
    else:
        frappe.msgprint(_('Shipment ID not found'))


def get_packlink_tracking_data(shipment_id):
    """ Get Packlink Tracking Info"""

    api_key = frappe.db.get_value('Shipment Service Provider',
                                  'Packlink', 'api_key')
    headers = {'Authorization': api_key,
               'Content-Type': 'application/json'}
    try:
        tracking_data_response = \
           requests.get('https://api.packlink.com/v1/shipments/{id}'.format(id=shipment_id),
                        headers=headers)
        tracking_data = json.loads(tracking_data_response.text)
        if 'trackings' in tracking_data:
            tracking_status = 'In Progress'
            if tracking_data['state'] == 'DELIVERED':
                tracking_status = 'Delivered'
            if tracking_data['state'] == 'RETURNED':
                tracking_status = 'Returned'
            if tracking_data['state'] == 'LOST':
                tracking_status = 'Lost'
            tracking_url = get_tracking_url(carrier=tracking_data['carrier'
                    ], tracking_number=tracking_data['trackings'][0])
            return {
                'awb_number': tracking_data['trackings'][0],
                'tracking_status': tracking_status,
                'tracking_status_info': tracking_data['state'],
                'tracking_url': tracking_url,
                }
    except Exception as exc:
        frappe.msgprint(_('Error occurred while updating Shipment: {0}').format(str(exc)), indicator='orange', alert=True)
    return []


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
    packlink_prices = \
        get_packlink_available_services(pickup_address_name=pickup_address_name,
            delivery_address_name=delivery_address_name,
            shipment_parcel=shipment_parcel, pickup_date=pickup_date)
    shipment_prices = letmeship_prices + packlink_prices
    shipment_prices = sorted(shipment_prices, key=lambda k: \
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
    pickup_contact_name=None,
    delivery_contact_name=None,
    delivery_notes=[],
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
            service_info=service_info,
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
    for delivery_note in json.loads(delivery_notes):
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
    else:
        shipping_label = get_packlink_label(shipment_id)
    return shipping_label


@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=[]):
    """ Update Tracking info in Shipment """
    if service_provider == 'LetMeShip':
        tracking_data = get_letmeship_tracking_data(shipment_id)
    else:
        tracking_data = get_packlink_tracking_data(shipment_id)
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
