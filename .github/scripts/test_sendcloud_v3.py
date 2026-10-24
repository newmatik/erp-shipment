"""Offline adapter contract tests. No Frappe site or carrier credentials needed."""
import importlib
import json
import hashlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class AttrDict(dict):
    __getattr__ = dict.get


def reject(message, *args, **kwargs):
    raise ValueError(message)


frappe = types.ModuleType('frappe')
frappe._ = lambda text: text
frappe._dict = AttrDict
frappe.db = Mock(get_value=Mock(return_value=('key', 'secret')))
frappe.throw = reject
frappe.msgprint = Mock()
frappe.get_doc = Mock()
utils = types.ModuleType('shipment.api.utils')
utils.get_address = Mock()
utils.get_company_contact = Mock()
utils.get_contact = Mock()
utils.format_tracking_url = lambda url: url or ''
frappe_utils = types.ModuleType('frappe.utils')
from html import escape
frappe_utils.escape_html = escape
sys.modules.update({'frappe': frappe, 'frappe.utils': frappe_utils, 'shipment.api.utils': utils})
v3 = importlib.import_module('shipment.api.sendcloud_v3')
legacy = importlib.import_module('shipment.api.sendcloud')

ID = '4df31240-daef-40bf-81fc-7a83184073e6'
ADDRESS = AttrDict(country_code='DE', address_title='Sender GmbH', address_line1='Hauptstrasse 1', address_line2='', city='Berlin', pincode='10115')
CONTACT = AttrDict(first_name='Erika', last_name='Muster', phone='', email='private@example.invalid')
RATE = {'service_id': 341, 'carrier': 'dhl_de', 'service_name': 'DHL Paket 0-2kg', 'total_price': 7.1}
PARCELS = json.dumps([{'count': 1, 'weight': 1, 'length': 50, 'width': 20, 'height': 7}])


def result(tracking='123'):
    parcel = {'weight': {'value': '1', 'unit': 'kg'}, 'dimensions': {'length': '50', 'width': '20', 'height': '7', 'unit': 'cm'}, 'label_notes': ['SHIP-1']}
    spec = {'from_address': v3._address(ADDRESS, ADDRESS.address_title), 'to_address': v3._address(ADDRESS, 'Erika Muster'), 'parcels': [parcel]}
    reference = json.dumps({'version': 1, 'spec_hash': hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(), 'amount': '7.1', 'carrier': RATE['carrier'], 'service': RATE['service_name']})
    return {'data': {'id': ID, 'external_reference_id': 'erpnext:SHIP-1', 'order_number': 'SHIP-1', 'reference': reference,
                     'ship_with': {'properties': {'shipping_option_code': 'dhl_de:dhl_paket'}},
                     'parcels': [{'id': 123, 'tracking_number': tracking, 'status': {'code': 'READY_TO_SEND'}}]}}


class SendcloudV3Tests(unittest.TestCase):
    def setUp(self):
        frappe.msgprint.reset_mock()
        frappe.db.reset_mock()
        frappe.db.get_value.return_value = ('key', 'secret')
        utils.get_address.return_value = ADDRESS
        utils.get_contact.return_value = CONTACT

    def book(self):
        return v3.create_shipment('SHIP-1', 'SENDER', ADDRESS, CONTACT, RATE, PARCELS, 'Goods', 50)

    @patch.object(v3, '_request')
    def test_dimensions_recipient_and_unique_reference(self, request):
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, result()]
        booked = self.book()
        self.assertEqual(booked['shipment_id'], 'v3:' + ID)
        self.assertEqual(booked['shipment_amount'], 7.1)
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['external_reference_id'], 'erpnext:SHIP-1')
        self.assertEqual(payload['parcels'][0]['dimensions'], {'length': '50', 'width': '20', 'height': '7', 'unit': 'cm'})
        self.assertEqual(payload['to_address']['name'], 'Erika Muster')
        self.assertEqual(payload['to_address']['company_name'], 'Sender GmbH')
        self.assertNotIn('email', payload['to_address'])
        self.assertNotIn('phone_number', payload['to_address'])

    @patch.object(v3, '_request')
    def test_pending_announcement_keeps_provider_id(self, request):
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, result('')]
        booked = self.book()
        self.assertEqual(booked['shipment_id'], 'v3:' + ID)
        self.assertEqual(booked['shipment_amount'], 7.1)
        frappe.msgprint.assert_called_once()

    @patch.object(v3, '_request')
    def test_unrelated_conflict_is_rejected(self, request):
        wrong = result(); wrong['data']['external_reference_id'] = 'another-order'
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, wrong]
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            self.book()

    @patch.object(v3, '_request')
    def test_recovery_restores_original_quote_and_validates_destination(self, request):
        changed_rate = {**RATE, 'total_price': 9, 'service_name': 'New quotation'}
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, result()]
        booked = v3.create_shipment('SHIP-1', 'SENDER', ADDRESS, CONTACT, changed_rate, PARCELS, 'Goods', 50)
        self.assertEqual(booked['shipment_amount'], 7.1)
        self.assertEqual(booked['carrier_service'], RATE['service_name'])
        changed_address = AttrDict({**ADDRESS, 'pincode': '99999'})
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, result()]
        with self.assertRaisesRegex(ValueError, 'booking details changed'):
            v3.create_shipment('SHIP-1', 'SENDER', changed_address, CONTACT, RATE, PARCELS, 'Goods', 50)

    @patch.object(v3, '_request')
    def test_changed_service_and_parcels_require_reconciliation(self, request):
        for change in ('service', 'count', 'missing'):
            wrong = result()
            if change == 'service': wrong['data']['ship_with']['properties']['shipping_option_code'] = 'other:option'
            elif change == 'count': wrong['data']['parcels'] = []
            else: wrong['data'].pop('ship_with')
            request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, wrong]
            with self.assertRaisesRegex(ValueError, 'different service or parcel set'):
                self.book()

    @patch.object(v3, '_shipment')
    def test_tracking_checks_read_permission_before_provider_call(self, shipment):
        frappe.db.get_value.return_value = 'SHIP-1'
        frappe.get_doc.return_value.check_permission.side_effect = PermissionError('denied')
        try:
            with self.assertRaises(PermissionError):
                v3.get_tracking('v3:' + ID)
            shipment.assert_not_called()
        finally:
            frappe.get_doc.return_value.check_permission.side_effect = None

    @patch.object(v3.requests, 'request')
    def test_only_announcement_conflict_is_recoverable(self, request):
        request.return_value = Mock(status_code=409, json=Mock(return_value=result()))
        self.assertEqual(v3._request('POST', '/shipments/announce')['data']['id'], ID)
        with self.assertRaisesRegex(ValueError, '409'):
            v3._request('POST', '/compat/shipping-options')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])

    @patch.object(v3.requests, 'request')
    def test_payment_error_is_visible_and_does_not_retry(self, request):
        request.return_value = Mock(status_code=402, json=Mock(return_value={'errors': [{'detail': 'Payment required'}]}))
        with self.assertRaisesRegex(ValueError, 'Payment required'):
            v3._request('POST', '/shipments/announce')
        request.assert_called_once()

    @patch.object(v3, 'create_shipment')
    @patch.object(legacy.requests, 'post')
    def test_only_definitive_v2_rejection_uses_v3(self, post, create):
        post.return_value = Mock(status_code=403, text='Creating parcels via API v2 is not available for this account. Please use API v3.')
        create.return_value = {'shipment_id': 'v3:' + ID}
        args = ('SHIP-1', 'Customer', 'ADDRESS', 'CONTACT', RATE, PARCELS, 'Goods', 50)
        self.assertEqual(legacy.create_sendcloud_shipment(*args, pickup_address_name='SENDER'), create.return_value)
        create.assert_called_once()
        create.reset_mock()
        post.side_effect = legacy.requests.Timeout('response lost')
        self.assertEqual(legacy.create_sendcloud_shipment(*args, pickup_address_name='SENDER'), {})
        create.assert_not_called()

    @patch.object(v3, '_shipment', return_value=result()['data'])
    def test_labels_check_erp_permissions_before_fetching(self, shipment):
        frappe.db.get_value.return_value = 'SHIP-1'
        frappe.get_doc.return_value.check_permission.side_effect = PermissionError('denied')
        with self.assertRaises(PermissionError):
            v3.get_labels('v3:' + ID)
        shipment.assert_not_called()
        frappe.get_doc.return_value.check_permission.side_effect = None

    @patch.object(v3, '_request')
    def test_labels_are_private_attachments_and_reused(self, request):
        frappe.db.get_value.side_effect = ['SHIP-1', None]
        request.side_effect = [result(), b'%PDF-1.4 label']
        frappe.get_doc.return_value.insert.return_value.file_url = '/private/files/label.pdf'
        self.assertEqual(v3.get_labels('v3:' + ID), ['/private/files/label.pdf'])
        fields = frappe.get_doc.call_args.args[0]
        self.assertEqual(fields['is_private'], 1)
        self.assertEqual(fields['attached_to_name'], 'SHIP-1')
        frappe.db.get_value.side_effect = ['SHIP-1', '/private/files/label.pdf']
        request.reset_mock(); request.side_effect = [result()]
        self.assertEqual(v3.get_labels('v3:' + ID), ['/private/files/label.pdf'])
        request.assert_called_once()
        frappe.db.get_value.side_effect = None


if __name__ == '__main__':
    unittest.main()
