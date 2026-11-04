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

    @patch.object(lms, 'get_tracking_url', return_value='https://example.invalid/track')
    @patch.object(lms.requests, 'get')
    def test_locked_booking_with_ready_label_keeps_its_real_tracking(self, get, url):
        get.side_effect = [response({**state('LOCKED'), 'labelState': 'READY'}),
                           response({'awbNumber': 'AWB-123', 'carrier': 'UPS', 'lmsTrackingStatus': 'DELIVERED_OK'})]
        result = lms.get_letmeship_tracking_data(123)
        self.assertEqual((result['status'], result['awb_number'], result['tracking_status']), ('Booked', 'AWB-123', 'Delivered'))

    @patch.object(lms.requests, 'get')
    def test_unconfirmed_state_does_not_clear_existing_tracking(self, get):
        for code in ['BOOKING_IN_PROGRESS', 'LOCKED', 'CANCEL_REQUESTED', 'NOT_FOUND']:
            with self.subTest(code):
                get.return_value = response(state(code))
                result = lms.get_letmeship_tracking_data(123)
                self.assertNotIn('awb_number', result)
                self.assertNotIn('tracking_url', result)
                self.assertNotIn('tracking_status', result)

    @patch.object(lms.frappe, 'logger', create=True)
    @patch.object(lms.requests, 'get')
    def test_repeated_tracking_timeout_is_logged_once_a_week(self, get, logger):
        stored = {}
        cache = Mock(get_value=Mock(side_effect=stored.get),
                     set_value=Mock(side_effect=lambda key, value, **kwargs: stored.__setitem__(key, value)))
        with patch.object(lms.frappe, 'cache', cache, create=True):
            for _night in range(2):
                get.side_effect = [response(state('BOOKING_FINISHED')), lms.requests.ReadTimeout('read timed out')]
                result = lms.get_letmeship_tracking_data(123, 'SHIPMENT-1')
                self.assertEqual(result['status'], 'Booked')
        lms.frappe.log_error.assert_called_once()
        self.assertIn('SHIPMENT-1', lms.frappe.log_error.call_args.args[0])
        self.assertEqual(cache.set_value.call_args.kwargs['expires_in_sec'], 7 * 24 * 3600)
        logger.return_value.warning.assert_called_once()

    @patch.object(lms.requests, 'get')
    def test_other_tracking_errors_are_logged_every_time(self, get):
        for _night in range(2):
            get.side_effect = [response(state('BOOKING_FINISHED')), Mock(raise_for_status=Mock(
                side_effect=lms.requests.HTTPError('500 Server Error')))]
            lms.get_letmeship_tracking_data(123)
        self.assertEqual(lms.frappe.log_error.call_count, 2)

    @patch.object(lms.requests, 'get')
    @patch.object(lms.frappe, 'throw', side_effect=frappe.ValidationError)
    def test_cancelled_label_surfaces_full_provider_reason(self, throw, get):
        get.side_effect = [response({'documents': [{'type': 'LABEL', 'status': 'SHIPMENT_CANCELLED'}]}),
                           response(state('CANCELLED'))]
        with self.assertRaises(frappe.ValidationError):
            lms.get_letmeship_label(123)
        self.assertIn('postal code is incorrect', throw.call_args.args[0])
        self.assertIn('CANCELLED', throw.call_args.args[0])

    def test_frappe_saved_time_formats_match_the_preview_interval(self):
        for start in ['09:00', '09:00:00', '09:00:00.000000', timedelta(hours=9)]:
            with self.subTest(start=start):
                interval = lms._get_pickup_interval('2026-11-04', start, '17:00:00.000000', True, now=datetime(2026, 11, 3, 10))
                self.assertEqual(interval, {'date': '2026-11-04', 'timeFrom': '09:00:00', 'timeTo': '17:00:00'})

class TestRecipientBilling(TestLetMeShip):
    def test_customer_account_and_full_contact_are_sent_to_letmeship(self):
        from shipment.api.letmeship_billing import recipient_billing
        note = _dict(docstatus=1, company='Example Co', customer='Customer Co', parcel_service='UPS',
                     use_customer_account=1, customer_account='ABC123', customer_address='BILLING',
                     check_permission=Mock())
        address = _dict(links=[_dict(link_doctype='Customer', link_name='Customer Co')], check_permission=Mock())
        doc = _dict(pickup_company='Example Co', delivery_customer='Customer Co',
                    shipment_delivery_notes=[_dict(delivery_note='DN-1')])
        delivery = {'company': 'Customer Co', 'person': {'firstname': 'Alex', 'lastname': 'Example'},
                    'phone': {'phoneNumberPrefix': '+49', 'phoneNumber': '12345'}, 'email': 'customer@example.invalid'}
        with patch.object(frappe, 'get_doc', side_effect=[note, address]), patch('shipment.api.letmeship_billing.get_address', return_value=_dict(country_code='DE', pincode='10115', city='Berlin', address_line1='A particularly long example street name house number 123', address_line2='Building B')):
            result = recipient_billing(doc, delivery)
        self.assertEqual(result['type'], 'RECEIVER_PAYS')
        self.assertEqual(result['accountNumber'], 'ABC123')
        self.assertEqual(result['address']['address']['zip'], '10115')
        self.assertTrue(all(len(result['address']['address'][key]) <= 35 for key in ('street', 'addressInfo1', 'addressInfo2')))
        self.assertIn('123', ' '.join(result['address']['address'][key] for key in ('street', 'addressInfo1', 'addressInfo2')))
        self.assertEqual(result['address']['person'], delivery['person'])
        self.assertNotIn('address', delivery)

    def test_mixed_payers_are_refused_before_any_carrier_request(self):
        from shipment.api.letmeship_billing import recipient_billing
        notes = [_dict(use_customer_account=value, check_permission=Mock()) for value in (0, 1)]
        with patch.object(frappe, 'get_doc', side_effect=notes), self.assertRaises(frappe.ValidationError):
            recipient_billing(_dict(shipment_delivery_notes=[_dict(delivery_note='a'), _dict(delivery_note='b')]), {})

    @patch.object(lms.requests, 'post', side_effect=TimeoutError('lost response'))
    def test_lost_response_retains_committed_uncertainty(self, post):
        result = self.book()
        self.assertTrue(result['booking_uncertain'])
        marker = self.db.set_value.call_args_list[0].args[2]['carrier_booking_data']
        self.assertEqual(json.loads(marker)['letmeship']['attempt_state'], 'uncertain')
        self.db.commit.assert_called()
        from shipment.api.letmeship_billing import ensure_no_uncertain_booking
        with self.assertRaises(frappe.ValidationError):
            ensure_no_uncertain_booking(_dict(carrier_booking_data=marker))
        post.assert_called_once()

    @patch.object(lms.requests, 'get')
    @patch.object(lms.requests, 'post')
    def test_recipient_payload_and_id_are_committed_before_followup(self, post, get):
        billing = {'type': 'RECEIVER_PAYS', 'accountNumber': 'ABC123', 'address': {'address': {'countryCode': 'DE', 'zip': '10115'}}}
        post.return_value = response({'shipmentId': 123, 'service': {'baseServiceDetails': {'priceInfo': {'netPrice': 0, 'totalPrice': 4.52, 'totalVat': .72}}}})
        def followup(*args, **kwargs):
            writes = [call.args[2] for call in self.db.set_value.call_args_list]
            self.assertTrue(any(row.get('shipment_id') == '123' for row in writes))
            self.assertGreaterEqual(self.db.commit.call_count, 2)
            raise TimeoutError('state unavailable')
        get.side_effect = followup
        with patch.object(lms, 'recipient_billing', return_value=billing):
            # Existing helper books DHL; explicitly select UPS for this account.
            original = lms.create_letmeship_shipment
            result = original('Company', 'Customer', 'SENDER', 'RECIPIENT', json.dumps([{'height': 7, 'width': 20, 'length': 50, 'weight': 1, 'count': 1}]), 'Electronic parts', str((datetime.now() + timedelta(days=1)).date()), 1, {'id': 62, 'carrier': 'UPS', 'service_name': 'Standard Paket National'}, '[]', '[]', delivery_contact_name='CONTACT', shipment='SHIPMENT-1', label_email=False)
        sent = json.loads(post.call_args.kwargs['data'])
        self.assertEqual(sent['shipmentDetails']['exWorks'], billing)
        self.assertFalse(sent['labelEmail'])
        self.assertEqual(result['shipment_id'], 123)
        self.assertEqual(result['net_price'], 3.8)

    @patch.object(lms.requests, 'post')
    def test_changed_pickup_interval_is_refused_before_purchase(self, post):
        with self.assertRaises(frappe.ValidationError):
            lms.create_letmeship_shipment('Company', 'Customer', 'SENDER', 'RECIPIENT', '[]',
                'Parts', str((datetime.now() + timedelta(days=1)).date()), 1,
                {'id': 62, 'carrier': 'UPS', 'service_name': 'Standard Paket National',
                 'pickup_interval': {'date': 'stale', 'from': '10:00', 'to': '12:00'}},
                '[]', '[]', delivery_contact_name='CONTACT', shipment='SHIPMENT-1')
        post.assert_not_called()

class TestAgentBooking(unittest.TestCase):
    def setUp(self):
        from shipment.api import moth_shipping
        self.api = moth_shipping
        row = _dict(length=32, width=22, height=17, weight=1, count=1)
        row.as_dict = lambda: {'length': 32, 'width': 22, 'height': 17, 'weight': 1, 'count': 1}
        self.doc = _dict(name='SHIPMENT-1', modified='v1', docstatus=1, shipment_id=None,
                         status='Submitted', service_provider='', carrier='', carrier_service='', awb_number='',
                         pickup_company='Example Co', delivery_customer='Customer Co', pickup_date='2026-11-03',
                         pickup_address_name='FROM', delivery_address_name='TO', shipment_parcel=[row],
                         shipment_delivery_notes=[_dict(delivery_note='DN-1')], check_permission=Mock())
        self.note = _dict(docstatus=1, company='Example Co', customer='Customer Co', parcel_service='UPS',
                         parcel_service_type='Standard Paket National', use_customer_account=1,
                         customer_account='ABC123', customer_address='TO', modified='v1', check_permission=Mock())
        self.addresses = {name: _dict(country='Germany', modified='v1', links=[_dict(link_doctype=kind, link_name=party)], check_permission=Mock()) for name, kind, party in [('FROM', 'Company', 'Example Co'), ('TO', 'Customer', 'Customer Co')]}
        self.rate = _dict(carrier='UPS', service_name='Standard Paket National', recipient_billing={'type': 'RECEIVER_PAYS'}, total_price=3.8, price_info={'totalPrice': 4.52}, pickup_interval={'date': '2026-11-03'}, id=1)
        def get_doc(kind, name, **kwargs):
            return self.doc if kind == 'Shipment' else self.note if kind == 'Delivery Note' else self.addresses[name]
        self.patches = [patch.object(self.api.frappe, 'db', Mock(get_value=Mock(return_value=1))), patch.object(self.api.frappe, 'get_doc', side_effect=get_doc), patch.object(self.api, 'get_letmeship_available_services', return_value=[self.rate])]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_preview_binds_payer_quote_and_address_versions(self):
        first = self.api.preview('SHIPMENT-1')
        self.assertEqual((first['bill_to'], first['customer_account'], first['handling_net_eur']), ('recipient', 'ABC123', 3.8))
        self.addresses['TO'].modified = 'changed'
        self.assertNotEqual(first['preview_token'], self.api.preview('SHIPMENT-1')['preview_token'])

    def test_stale_approval_cannot_call_carrier_booking(self):
        with self.assertRaises(frappe.ValidationError):
            self.api.book('SHIPMENT-1', 'stale')

    def test_existing_booking_recovers_without_a_rate_or_purchase(self):
        self.doc.shipment_id = '123'
        self.doc.service_provider = 'LetMeShip'
        with patch.object(self.api, 'refresh', return_value={'shipment_id': '123'}) as refresh:
            self.assertEqual(self.api.book('SHIPMENT-1', 'old'), {'shipment_id': '123'})
        refresh.assert_called_once_with('SHIPMENT-1')
        self.api.get_letmeship_available_services.assert_not_called()

    def test_uncertain_attempt_cannot_get_another_quote_or_booking(self):
        self.doc.carrier_booking_data = json.dumps({'letmeship': {'attempt_state': 'uncertain'}})
        with self.assertRaises(frappe.ValidationError):
            self.api.preview('SHIPMENT-1')
        self.api.get_letmeship_available_services.assert_not_called()

    def test_invalid_or_multiple_parcels_cannot_reach_rate_lookup(self):
        for key, value in [('count', 2), ('weight', 0), ('length', float('nan')), ('height', -1)]:
            with self.subTest(key=key):
                original = self.doc.shipment_parcel[0][key]
                self.doc.shipment_parcel[0][key] = value
                with self.assertRaises(frappe.ValidationError):
                    self.api.preview('SHIPMENT-1')
                self.doc.shipment_parcel[0][key] = original
        self.api.get_letmeship_available_services.assert_not_called()

    def test_disabled_provider_cannot_quote_or_book(self):
        self.api.frappe.db.get_value.return_value = 0
        with self.assertRaises(frappe.ValidationError):
            self.api.preview('SHIPMENT-1')
        self.api.get_letmeship_available_services.assert_not_called()

    def test_saved_child_timestamps_are_not_serialized_into_carrier_parcels(self):
        self.doc.shipment_parcel[0].creation = datetime(2026, 11, 3, 10)
        self.doc.shipment_parcel[0].modified = datetime(2026, 11, 3, 11)
        self.doc.shipment_parcel[0].as_dict = lambda: dict(self.doc.shipment_parcel[0])
        parcel = json.loads(self.api._arguments(self.doc)['shipment_parcel'])[0]
        self.assertEqual(parcel, {'length': 32, 'width': 22, 'height': 17, 'weight': 1, 'count': 1})


class TestNetCharge(unittest.TestCase):
    """The price an offer and a booking carry, from LetMeShip's priceInfo."""

    def setUp(self):
        from shipment.api.letmeship_billing import net_charge
        self.net_charge = net_charge

    def test_total_minus_vat_includes_the_ex_works_fee_missing_from_net_price(self):
        self.assertEqual(self.net_charge({'netPrice': 3.0, 'totalPrice': '4.52', 'totalVat': '0.72'}), 3.8)

    def test_zero_vat_is_a_total_not_a_missing_one(self):
        self.assertEqual(self.net_charge({'netPrice': 9.0, 'totalPrice': 12.5, 'totalVat': 0}), 12.5)

    def test_without_totals_the_net_price_is_used(self):
        self.assertEqual(self.net_charge({'netPrice': '7.10', 'totalPrice': 8.45}), 7.1)
        self.assertEqual(self.net_charge({'netPrice': None}), 0.0)
        self.assertEqual(self.net_charge({}), 0.0)
