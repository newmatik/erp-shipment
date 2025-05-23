#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2018, ESO Electronic Service Ottenbreit GmbH
# For license information, please see license.txt


import requests
import frappe
import json
from datetime import datetime, timedelta
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
    pickup_type=None,
    pickup_from=None,
    pickup_to=None
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

    # Get current time and ensure pickup time is in the future
    current_date = datetime.now().strftime('%Y-%m-%d')
    current_time = datetime.now().strftime('%H:%M:%S')
    
    # Use ERP values or default pickup time window
    time_from = f"{pickup_from}:00" if pickup_from else "09:00:00"
    time_to = f"{pickup_to}:00" if pickup_to else "18:00:00"
    
    # If pickup is today, ensure pickup time is at least 5 minutes in the future
    if pickup_date == current_date:
        # Calculate 5 minutes from now
        next_time = (datetime.now() + timedelta(minutes=5)).strftime('%H:%M:%S')
        # Use the later of: ERP time or 5 minutes from now
        if next_time > time_from:
            time_from = next_time
    
    # Prepare pickupInterval with proper time information
    pickup_interval = {'date': pickup_date}
    
    # Add time information for pickup orders
    if pickupOrder:
        pickup_interval.update({
            'timeFrom': time_from,
            'timeTo': time_to
        })

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
        'pickupInterval': pickup_interval,
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
            frappe.log_error(f"Error occurred while fetching LetMeShip prices: {response_data['message']}")
            frappe.throw(_('Error occurred while fetching LetMeShip prices: {0}'
                           ).format(response_data['message']))
    except Exception as exc:
        frappe.log_error(f"Error occurred while fetching LetMeShip Prices: {str(exc)}")
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
    pickup_type=None,
    pickup_from=None,
    pickup_to=None
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

    # Get current time and ensure pickup time is in the future
    current_date = datetime.now().strftime('%Y-%m-%d')
    current_time = datetime.now().strftime('%H:%M:%S')
    
    # Use ERP values or default pickup time window
    time_from = f"{pickup_from}:00" if pickup_from else "09:00:00"
    time_to = f"{pickup_to}:00" if pickup_to else "18:00:00"
    
    # If pickup is today, ensure pickup time is at least 5 minutes in the future
    if pickup_date == current_date:
        # Calculate 5 minutes from now
        next_time = (datetime.now() + timedelta(minutes=5)).strftime('%H:%M:%S')
        # Use the later of: ERP time or 5 minutes from now
        if next_time > time_from:
            time_from = next_time
    
    # Prepare pickupInterval with proper time information
    pickup_interval = {'date': pickup_date}
    
    # Add time information for pickup orders
    if pickupOrder:
        pickup_interval.update({
            'timeFrom': time_from,
            'timeTo': time_to
        })

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
                'id': service_info.get('id'),
                'name': service_info.get('service_name'),
                'carrier': service_info.get('carrier'),
                'priceInfo': service_info.get('price_info', {}),
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
            'pickupInterval': pickup_interval,
        },
        'shipmentNotification': {'trackingNotification': {
            'deliveryNotification': True,
            'problemNotification': True,
            'emails': [] if not tracking_notific_email or tracking_notific_email == '[]' else (tracking_notific_email if isinstance(tracking_notific_email, list) else [tracking_notific_email]),
            'notificationText': '',
        }, 'recipientNotification': {'notificationText': '',
                                     'emails': [] if not shipment_notific_email or shipment_notific_email == '[]' else (shipment_notific_email if isinstance(shipment_notific_email, list) else [shipment_notific_email])}},
        'labelEmail': True,
    }


    try:
        response_data = requests.post(url=url,
                                      auth=(service_provider.api_key,
                                            service_provider.api_password), headers=headers,
                                      data=json.dumps(payload))
        
        # Check if response is valid before parsing JSON
        if not response_data or not response_data.text:
            frappe.log_error("Empty response from LetMeShip API")
            return {}
            
        try:
            response_data = json.loads(response_data.text)
        except Exception as json_exc:
            frappe.log_error(f"Failed to parse JSON response: {str(json_exc)}\nResponse: {response_data.text}")
            return {}
            
        if not response_data:
            frappe.log_error("Empty JSON data from LetMeShip API")
            return {}
            
        if 'shipmentId' in response_data:
            # Safe access to nested dictionaries
            service = response_data.get('service', {})
            base_service_details = service.get('baseServiceDetails', {}) if service else {}
            price_info = base_service_details.get('priceInfo', {}) if base_service_details else {}
            
            base_price = price_info.get('basePrice', 0)
            net_price = price_info.get('netPrice', 0)
            total_vat = price_info.get('totalVat', 0)
            shipment_amount = price_info.get('totalPrice', 0)
            awb_number = ''
            
            shipment_id = response_data.get('shipmentId')
            if not shipment_id:
                frappe.log_error("Missing shipmentId in response")
                return {}
                
            try:
                tracking_response = requests.get(
                    f'https://api.letmeship.com/v1/shipments/{shipment_id}',
                    auth=(service_provider.api_key, service_provider.api_password),
                    headers=headers
                )
                
                if not tracking_response or not tracking_response.text:
                    frappe.log_error("Empty tracking response")
                    tracking_response_data = {}
                else:
                    tracking_response_data = json.loads(tracking_response.text)
            except Exception as track_exc:
                frappe.log_error(f"Error getting tracking data: {str(track_exc)}")
                tracking_response_data = {}
            
            if tracking_response_data and 'trackingData' in tracking_response_data and tracking_response_data.get('trackingData') and 'parcelList' in tracking_response_data.get('trackingData', {}):
                for parcel in tracking_response_data.get('trackingData', {}).get('parcelList', []):
                    if parcel and 'awbNumber' in parcel:
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
            error_msg = response_data.get('message', 'Unknown error')
            frappe.log_error(f"Error occurred while creating Shipment: {error_msg}")
            frappe.throw(_('Error occurred while creating Shipment: {0}').format(error_msg))
            return {}
    except Exception as exc:
        import traceback
        error_trace = traceback.format_exc()
        frappe.log_error(f"Error in create_letmeship_shipment: {str(exc)}\nTraceback: {error_trace}\nPayload: {json.dumps(payload)}")
        frappe.msgprint(_('Error occurred while creating Shipment: {0}'
                          ).format(str(exc)), indicator='orange',
                        alert=True)
        return {}


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
        error_msg = shipment_label_response_data.get('message', 'Unknown error')
        frappe.log_error(f"Error occurred while printing Shipment: {error_msg}")
        frappe.throw(_('Error occurred while printing Shipment: {0}'
                       ).format(error_msg))


def get_letmeship_tracking_data(shipment_id, shipment_doc_name=None):
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
            if tracking_data.get('lmsTrackingStatus') and tracking_data['lmsTrackingStatus'].startswith('DELIVERED'):
                tracking_status = 'Delivered'
            if tracking_data.get('lmsTrackingStatus') == 'RETURNED':
                tracking_status = 'Returned'
            if tracking_data.get('lmsTrackingStatus') == 'LOST':
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
            shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
            frappe.throw(_('Error occurred while updating Shipment {0}{1}'
                           ).format(shipment_info, tracking_data['message']))
            return {}
    except Exception as exc:
        shipment_info = f"{shipment_doc_name}: " if shipment_doc_name else ""
        frappe.log_error(f"Error occurred while updating Shipment {shipment_info}{str(exc)}")
        frappe.msgprint(_('Error occurred while updating Shipment {0}{1}'
                          ).format(shipment_info, str(exc)), indicator='orange',
                        alert=True)
        return {}


def get_parcel_list(shipment_parcel, description_of_content):
    parcel_list = []
    if not shipment_parcel:
        return parcel_list
        
    for parcel in shipment_parcel:
        if not parcel:
            continue
            
        formatted_parcel = {}
        formatted_parcel['height'] = parcel.get('height')
        formatted_parcel['width'] = parcel.get('width')
        formatted_parcel['length'] = parcel.get('length')
        formatted_parcel['weight'] = parcel.get('weight')
        formatted_parcel['quantity'] = parcel.get('count')
        formatted_parcel['contentDescription'] = description_of_content
        parcel_list.append(formatted_parcel)
    return parcel_list
