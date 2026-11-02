"""Offline LetMeShip contracts: never call the live provider."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import frappe
from frappe import _dict
from shipment.api import let_me_ship as lms


def response(body):
    return Mock(status_code=200, text=json.dumps(body), json=Mock(return_value=body))


def state(code='BOOKING_IN_PROGRESS'):
    return {'shipmentId': 123, 'shipmentState': code, 'labelState': 'NOT_READY',
            'entryDocumentsState': 'NOT_READY', 'noteType': 'CANCEL_ZIP_ADDRESS_COUNTRY',
            'text': 'The postal code is incorrect.'}


class TestLetMeShip(unittest.TestCase):
    def setUp(self):
        self.db = Mock(get_value=Mock(return_value=_dict(api_key='key', api_password='secret')))
        self.patches = [patch.object(lms, 'now_datetime', return_value=datetime.now()),
                        patch.object(lms.frappe, 'db', self.db),
                        patch.object(lms.frappe, 'msgprint'), patch.object(lms.frappe, 'log_error'),
                        patch.object(lms, '_letmeship_auto_split_enabled', return_value=True),
                        patch.object(lms, 'fit_letmeship_address'), patch.object(lms, 'validate_letmeship_address'),
                        patch.object(lms, 'get_address', side_effect=lambda name: _dict(
                            address_title='Example', country_code='DE' if name == 'SENDER' else 'CH',
                            pincode='10115' if name == 'SENDER' else '3000', city='Berlin' if name == 'SENDER' else 'Bern',
                            address_line1='Example Street 1', address_line2='', state='')),
                        patch.object(lms, 'get_company_contact', return_value=_dict(title='MR', first_name='Alex',
                            last_name='Example', phone='12345', phone_prefix='+49', email='sender@example.invalid')),
                        patch.object(lms, 'get_contact', return_value=_dict(title='MR', first_name='Alex',
                            last_name='Example', phone='12345', phone_prefix='+41', email='recipient@example.invalid')),
                        patch.object(lms.frappe, 'get_doc', return_value=_dict(pickup_from='09:00', pickup_to='17:00'))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def book(self, pickup_type='Self delivery'):
        return lms.create_letmeship_shipment(
            'Company', 'Customer', 'SENDER', 'RECIPIENT',
            json.dumps([{'height': 7, 'width': 20, 'length': 50, 'weight': 1, 'count': 1}]),
            'Electronic accessories', str((datetime.now() + timedelta(days=1)).date()), 100,
            {'id': 62, 'carrier': 'DHL', 'service_name': 'Economy Select'}, '[]', '[]',
            delivery_contact_name='CONTACT', pickup_type=pickup_type, shipment='SHIPMENT-1')

    @patch.object(lms.requests, 'get')
    @patch.object(lms.requests, 'post')
    def test_id_is_retained_for_pending_finished_and_failed_bookings(self, post, get):
        post.return_value = response({'shipmentId': 123})
        for code, expected in [('BOOKING_IN_PROGRESS', 'Booking Pending'), ('BOOKING_FINISHED', 'Booked'),
                               ('CANCELLED', 'Booking Failed'), ('NOT_FOUND', 'Booking Failed'),
                               ('LOCKED', 'Booking Pending'), ('CANCEL_REQUESTED', 'Booking Pending')]:
            with self.subTest(code):
                get.side_effect = [response(state(code)), response({'trackingData': {'parcelList': [{'awbNumber': None}]}})]
                booked = self.book()
                self.assertEqual((booked['shipment_id'], booked['status'], booked['awb_number']), (123, expected, ''))
                self.assertEqual(json.loads(booked['carrier_booking_data'])['letmeship']['text'], state()['text'])

    @patch.object(lms.requests, 'get', side_effect=TimeoutError('state unavailable'))
    @patch.object(lms.requests, 'post')
    def test_state_timeout_after_creation_never_loses_booking_id(self, post, get):
        post.return_value = response({'shipmentId': 123})
        result = self.book()
        self.assertEqual((result['shipment_id'], result['status']), (123, 'Booking Pending'))
        post.assert_called_once()

    @patch.object(lms.requests, 'get')
    @patch.object(lms.requests, 'post')
    def test_false_pickup_and_insurance_are_sent_as_json_booleans_and_audited(self, post, get):
        post.return_value = response({'shipmentId': 123, 'shipmentDetails': {'shipmentSettings': {'pickupOrder': True, 'insurance': True}}})
        get.side_effect = [response(state()), response({})]
        result = self.book()
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertIs(sent['shipmentDetails']['shipmentSettings']['pickupOrder'], False)
        self.assertIs(sent['shipmentDetails']['shipmentSettings']['insurance'], False)
        self.assertEqual(sent['deliveryInfo']['address']['countryCode'], 'CH')
        self.assertEqual(sent['deliveryInfo']['address']['zip'], '3000')
        self.assertEqual(sent['shipmentDetails']['parcelList'][0]['weight'], 1)
        audit = json.loads(result['carrier_booking_data'])['letmeship']
        self.assertIs(audit['requested_settings']['pickupOrder'], False)
        self.assertIs(audit['acknowledged_settings']['pickupOrder'], True)

    @patch.object(lms.requests, 'get')
    @patch.object(lms.requests, 'post')
    def test_pickup_request_is_not_changed_to_self_delivery(self, post, get):
        post.return_value = response({'shipmentId': 123})
        get.side_effect = [response(state()), response({})]
        self.book('Pickup')
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertIs(sent['shipmentDetails']['shipmentSettings']['pickupOrder'], True)

    @patch.object(lms.requests, 'get')
    def test_rejects_state_for_another_booking_or_unknown_state(self, get):
        for invalid in [{**state(), 'shipmentId': 456}, {**state(), 'shipmentState': 'UNKNOWN'}, []]:
            get.return_value = response(invalid)
            with self.assertRaises(ValueError):
                lms.get_letmeship_booking_state(123)

    @patch.object(lms.requests, 'get')
    def test_cancelled_tracking_uses_booking_state_and_never_invents_awb(self, get):
        get.return_value = response(state('CANCELLED'))
        result = lms.get_letmeship_tracking_data(123)
        self.assertEqual((result['status'], result['awb_number'], result['tracking_url']), ('Booking Failed', '', ''))
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args.args[0].endswith('/123/states'))

    @patch.object(lms, 'get_tracking_url')
    @patch.object(lms.requests, 'get')
    def test_null_awb_does_not_make_a_tracking_link(self, get, url):
        get.side_effect = [response(state('BOOKING_FINISHED')), response({'awbNumber': None})]
        result = lms.get_letmeship_tracking_data(123)
        self.assertEqual(result['status'], 'Booked')
        self.assertNotIn('awb_number', result)
        url.assert_not_called()

    @patch.object(lms.requests, 'get')
    @patch.object(lms.frappe, 'throw', side_effect=frappe.ValidationError)
    def test_cancelled_label_surfaces_full_provider_reason(self, throw, get):
        get.side_effect = [response({'documents': [{'type': 'LABEL', 'status': 'SHIPMENT_CANCELLED'}]}),
                           response(state('CANCELLED'))]
        with self.assertRaises(frappe.ValidationError):
            lms.get_letmeship_label(123)
        self.assertIn('postal code is incorrect', throw.call_args.args[0])
        self.assertIn('CANCELLED', throw.call_args.args[0])
