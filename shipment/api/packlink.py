# -*- coding: utf-8 -*-
# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt
import frappe
import json
import requests
from frappe import _

def get_address(address_name):
    return frappe.get_doc('Address', address_name)

def get_contact(contact_name):
    contact = frappe.get_doc('Contact', contact_name)
    contact.email = contact.email_id
    return contact

def get_company_contact():
    return frappe.get_doc('User', frappe.session.user)

def parse_pickup_date(pickup_date):
    return pickup_date.replace('-', '/')

def get_parcel_list(shipment_parcel):
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
    pickup_address = frappe.get_doc('Address', pickup_address_name)
    from_zip = pickup_address.pincode
    from_country_code = frappe.get_value('Country', pickup_address.country, 'code').upper()

    delivery_address = frappe.get_doc('Address', delivery_address_name)
    to_zip = delivery_address.pincode
    to_country_code = frappe.get_value('Country', delivery_address.country, 'code').upper()

    shipment_parcel_params = ''
    for index, parcel in enumerate(get_parcel_list(json.loads(shipment_parcel))):
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
            return []

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
    from_country_code = frappe.get_value('Country', pickup_address.country, 'code').upper()
    if pickup_from_type != 'Company':
        pickup_contact = get_contact(pickup_contact_name)
    else:
        pickup_contact = get_company_contact()

    delivery_address = get_address(delivery_address_name)
    to_country_code = frappe.get_value('Country', delivery_address.country, 'code').upper()
    if delivery_to_type != 'Company':
        delivery_contact = get_contact(delivery_contact_name)
    else:
        delivery_contact = get_company_contact()

    data = {
        "additional_data": {
            "postal_zone_id_from": "",
            "postal_zone_name_from": pickup_address.country,
            "postal_zone_id_to": "",
            "postal_zone_name_to": delivery_address.county
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
        "packages": get_parcel_list(json.loads(shipment_parcel)),
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
