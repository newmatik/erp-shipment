# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt


import requests
import frappe
import json
from frappe import _
from newmatik.newmatik.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias
from shipment.api.utils import get_address, get_company_contact, get_contact, get_tracking_url


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
    pickup_type=None
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

    if len(delivery_address.address_line1) > 35:
        counter = 35
        while delivery_address.address_line1[counter] != " ":
            counter -= 1

        delivery_address.update(
            {"address_line1_con": delivery_address.address_line1[counter + 1:len(delivery_address.address_line1) - 1]})
        delivery_address.address_line1 = delivery_address.address_line1[:counter]

    pickupOrder = False
    if pickup_type and pickup_type == "Pickup":
        pickupOrder = True

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
                  'phoneNumberPrefix': pickup_contact.phone_prefix.replace(" ", "")
                  if ' ' in pickup_contact.phone_prefix else pickup_contact.phone_prefix},
        'email': pickup_contact.email,
    }, 'deliveryInfo': {
        'address': {
            'countryCode': delivery_address.country_code,
            'zip': delivery_address.pincode,
            'city': delivery_address.city,
            'street': delivery_address.address_line1,
            'addressInfo1': delivery_address.address_line2 if 'address_line1_con' not in delivery_address else delivery_address.address_line1_con,
            "addressInfo2": '' if 'address_line1_con' not in delivery_address else delivery_address.address_line2,
            'houseNo': '',
            'stateCode': delivery_address.state if delivery_address.state != '' else None
        },
        'company': delivery_address.address_title,
        'person': {'title': delivery_contact.title,
                   'firstname': delivery_contact.first_name,
                   'lastname': delivery_contact.last_name},
        'phone': {'phoneNumber': delivery_contact.phone,
                  'phoneNumberPrefix': delivery_contact.phone_prefix.replace(" ", "")
                  if ' ' in delivery_contact.phone_prefix else delivery_contact.phone_prefix},
        'email': delivery_contact.email,
    }, 'shipmentDetails': {
        'contentDescription': description_of_content,
        'shipmentType': 'PARCEL',
        'shipmentSettings': {
            'saturdayDelivery': False,
            'ddp': False,
            'insurance': False,
            'pickupOrder': pickupOrder,
            'pickupTailLift': False,
            'deliveryTailLift': False,
            'holidayDelivery': False,
        },
        'goodsValue': value_of_goods,
        'parcelList': parcel_list,
        'pickupInterval': {'date': pickup_date},
        'contentDescription': description_of_content
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
    pickup_type=None
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

    if len(delivery_address.address_line1) > 35:
        counter = 35
        while delivery_address.address_line1[counter] != " ":
            counter -= 1

        delivery_address.update(
            {"address_line1_con": delivery_address.address_line1[counter + 1:len(delivery_address.address_line1) - 1]})
        delivery_address.address_line1 = delivery_address.address_line1[:counter]

    pickupOrder = False
    if pickup_type and pickup_type == "Pickup":
        pickupOrder = True

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
                      'phoneNumberPrefix': pickup_contact.phone_prefix.replace(" ", "") if ' ' in pickup_contact.phone_prefix else pickup_contact.phone_prefix},
            'email': pickup_contact.email,
        },
        'deliveryInfo': {
            'address': {
                'countryCode': delivery_address.country_code,
                'zip': delivery_address.pincode,
                'city': delivery_address.city,
                'street': delivery_address.address_line1,
                'addressInfo1': delivery_address.address_line2 if 'address_line1_con' not in delivery_address else delivery_address.address_line1_con,
                'addressInfo2': '' if 'address_line1_con' not in delivery_address else delivery_address.address_line2,
                'houseNo': '',
                'stateCode': delivery_address.state if delivery_address.state != '' else None
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
                'pickupOrder': pickupOrder,
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
            base_price = response_data['service']['baseServiceDetails']['priceInfo']['basePrice']
            net_price = response_data['service']['baseServiceDetails']['priceInfo']['netPrice']
            total_vat = response_data['service']['baseServiceDetails']['priceInfo']['totalVat']
            shipment_amount = response_data['service']['baseServiceDetails']['priceInfo']['totalPrice']
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
                'base_price': base_price,
                'net_price': net_price,
                'total_vat': total_vat,
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
            requests.get('https://api.letmeship.com/v1/tracking?shipmentid={id}'.format(id=shipment_id),
                         auth=(service_provider.api_key,
                               service_provider.api_password), headers=headers)
        tracking_data = json.loads(tracking_data_response.text)
        if 'awbNumber' in tracking_data:
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
