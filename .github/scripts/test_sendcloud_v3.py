"""Offline adapter contract tests. No Frappe site or carrier credentials needed."""
import ast
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
DELIVERY_NOTES = []
def get_all(doctype, **kwargs):
    # The child DocType of Shipment.shipment_delivery_notes is plural.
    assert doctype == 'Shipment Delivery Notes', doctype
    assert kwargs['filters'] == {'parent': 'SHIP-1', 'parenttype': 'Shipment'}
    return list(DELIVERY_NOTES)
frappe.get_all = Mock(side_effect=get_all)
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
METHOD = {'id': 341, 'carrier': 'dhl_de', 'name': 'DHL Paket 0-2kg', 'min_weight': '0.001', 'max_weight': '2.001',
          'countries': [{'iso_2': 'DE', 'price': 7.1}]}


def result(tracking='123'):
    parcel = {'weight': {'value': '1', 'unit': 'kg'}, 'dimensions': {'length': '50', 'width': '20', 'height': '7', 'unit': 'cm'}, 'label_notes': ['SHIP-1']}
    spec = {'from_address': v3._address(ADDRESS, ADDRESS.address_title), 'to_address': v3._address(ADDRESS, 'Erika Muster'), 'parcels': [parcel], 'total_order_price': {'value': '50', 'currency': 'EUR'}}
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
        frappe.db.get_value.side_effect = None

    @patch.object(v3, '_request')
    def test_single_delivery_note_is_the_label_reference(self, request):
        DELIVERY_NOTES[:] = ['DN-1']
        booked = result(); booked['data']['order_number'] = 'DN-1'
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, booked]
        try:
            self.book()
            payload = request.call_args.kwargs['json']
            self.assertEqual((payload['order_number'], payload['external_reference_id']), ('DN-1', 'erpnext:SHIP-1'))
            self.assertEqual(json.loads(frappe.db.set_value.call_args.args[3])['sendcloud_uncertain']['order_number'], 'DN-1')
            DELIVERY_NOTES[:] = ['DN-1', 'DN-2']
            self.assertEqual(v3._label_reference('SHIP-1'), 'SHIP-1')
        finally:
            DELIVERY_NOTES[:] = []

    def test_brand_title_on_company_pickup_address_prints_above_the_company(self):
        address = AttrDict({**ADDRESS, 'name': 'E-Modell-Shipping', 'address_title': 'E-Modell'})
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Company', link_name='Newmatik GmbH')]
        frappe.db.get_value.return_value = 'Newmatik GmbH'
        sender = v3._sender_address(address, 'Newmatik GmbH')
        self.assertEqual((sender['company_name'], sender['name'], sender['address_line_1']), ('E-Modell', 'Newmatik GmbH', 'Hauptstrasse 1'))
        # Only the Shipment's own pickup company is ever printed as legal sender.
        self.assertEqual(v3._sender_address(address, 'Other GmbH'), v3._address(address, 'E-Modell'))
        address['address_title'] = 'Newmatik GmbH'
        self.assertEqual(v3._sender_address(address, 'Newmatik GmbH'), v3._address(address, 'Newmatik GmbH'))
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Customer', link_name='CUSTOMER-1')]
        frappe.db.get_value.return_value = AttrDict(customer_name='Someone', customer_type='Individual')
        address['address_title'] = 'E-Modell'
        self.assertEqual(v3._sender_address(address, 'Newmatik GmbH'), v3._address(address, 'E-Modell'))
        self.assertNotIn('company_name', v3._sender_address(address, 'Newmatik GmbH'))

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
        self.assertNotIn('company_name', payload['to_address'])
        self.assertNotIn('email', payload['to_address'])
        self.assertNotIn('phone_number', payload['to_address'])

    def test_company_name_requires_matching_linked_business(self):
        address = AttrDict({**ADDRESS, 'name': 'ADDRESS-1'})
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Customer', link_name='CUSTOMER-1')]
        frappe.db.get_value.return_value = AttrDict(customer_name='Business GmbH', customer_type='Company')
        self.assertNotIn('company_name', v3._address(address, 'Erika Muster'))
        address.address_title = 'Business GmbH'
        address['address_title'] = 'Business GmbH'
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Business GmbH')
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Supplier', link_name='SUPPLIER-1')]
        frappe.db.get_value.return_value = AttrDict(supplier_name='Business GmbH', supplier_type='Company')
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Business GmbH')

    @patch.object(v3, '_request')
    def test_multi_parcel_stops_before_announcement(self, request):
        request.return_value = {'data': {'341': 'dhl_de:dhl_paket'}}
        for parcels in (json.dumps([{**json.loads(PARCELS)[0], 'count': 2}]), json.dumps(json.loads(PARCELS) * 2)):
            with self.assertRaisesRegex(ValueError, 'supports one parcel'):
                v3.create_shipment('SHIP-1', 'SENDER', ADDRESS, CONTACT, RATE, parcels, 'Goods', 50)
        self.assertTrue(all(call.args[1] == '/compat/shipping-options' for call in request.call_args_list))

    @patch.object(v3, '_request')
    def test_changed_goods_value_requires_reconciliation(self, request):
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, result()]
        with self.assertRaisesRegex(ValueError, 'booking details changed'):
            v3.create_shipment('SHIP-1', 'SENDER', ADDRESS, CONTACT, RATE, PARCELS, 'Goods', 60)

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

    @patch.object(v3, '_request')
    def test_lost_response_commits_boundary_and_recovers_read_only(self, request):
        def respond(method, path, **kwargs):
            if path == '/compat/shipping-options':
                return {'data': {'341': 'dhl_de:dhl_paket'}}
            frappe.db.commit.assert_called_once()
            raise legacy.requests.Timeout('response lost')
        request.side_effect = respond
        with self.assertRaises(legacy.requests.Timeout):
            self.book()
        marker = frappe.db.set_value.call_args.args[3]
        document = {'carrier_booking_data': marker, 'modified': str(('key', 'secret'))}
        with self.assertRaisesRegex(ValueError, 'may already exist'):
            v3.ensure_no_uncertain_booking(document)
        request.reset_mock(); request.side_effect = None; request.return_value = {'data': [result()['data']]}
        self.assertEqual(v3.recover_shipment(document)['shipment_id'], 'v3:' + ID)
        self.assertEqual(request.call_args.args[0], 'GET')
        document['modified'] = 'changed'
        request.reset_mock()
        with self.assertRaisesRegex(ValueError, 'changed after'):
            v3.recover_shipment(document)
        request.assert_not_called()

    def test_controller_blocks_replacement_and_cancel_but_allows_read_only_recovery(self):
        path = Path(__file__).resolve().parents[2] / 'shipment/shipment/doctype/shipment/shipment.py'
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('Shipment', 'create_shipment')]
        for node in nodes:
            node.decorator_list = []
        recovered = {'shipment_id': 'v3:' + ID, 'service_provider': 'SendCloud', 'shipment_amount': 7.1}
        recovery, purchase = Mock(return_value=recovered), Mock()
        namespace = {'frappe': frappe, '_': frappe._, 'json': json, 'Document': object, 'DHL_PROVIDER': 'DHL',
                     'ensure_no_uncertain_dhl_booking': Mock(), 'ensure_no_uncertain_sendcloud_booking': v3.ensure_no_uncertain_booking,
                     'uncertain_sendcloud_booking': v3.uncertain_booking, 'recover_sendcloud_shipment': recovery,
                     '_get_delivery_note_names': lambda rows: [], 'create_sendcloud_shipment': purchase}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
        document = AttrDict(docstatus=1, shipment_id=None, flags={}, carrier_booking_data=json.dumps({'sendcloud_uncertain': {'reference': 'SHIP-1'}}), check_permission=Mock(), shipment_delivery_notes=[])
        with self.assertRaisesRegex(ValueError, 'may already exist'):
            namespace['Shipment'].before_cancel(document)
        with patch.object(frappe, 'get_doc', return_value=document):
            args = ['SHIP-1', 'Company', 'Customer', 'SENDER', 'RECIPIENT', PARCELS, 'Goods', '2026-01-01', 50]
            with self.assertRaisesRegex(ValueError, 'may already exist'):
                namespace['create_shipment'](*args, json.dumps({'service_provider': 'Packlink'}), '[]', '[]')
            self.assertEqual(namespace['create_shipment'](*args, json.dumps({'service_provider': 'SendCloud'}), '[]', '[]'), recovered)
        purchase.assert_not_called(); recovery.assert_called_once_with(document)
        values = frappe.db.set_value.call_args.args[2]
        self.assertIsNone(values['carrier_booking_data'])
        self.assertEqual(values['base_price'], 7.1, 'Shipping-cost calculation must retain the carrier quotation')
        for field in ('net_price', 'total_vat'):
            self.assertEqual(values[field], 0, 'Currency fields must never receive NULL when a provider omits the breakdown')

    @patch.object(v3, '_shipment')
    def test_tracking_status_fits_erp_field(self, shipment):
        frappe.db.get_value.return_value = 'SHIP-1'
        shipment.return_value = {'parcels': [{'status': {'message': 'x' * 200}}]}
        self.assertEqual(len(v3.get_tracking('v3:' + ID)['tracking_status_info']), 140)
        shipment.return_value = {'parcels': [{'status': {'message': None}}, {'status': None}]}
        self.assertEqual(v3.get_tracking('v3:' + ID)['tracking_status_info'], ', ')

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
        frappe.get_doc.return_value.insert.assert_called_with(ignore_permissions=True)
        frappe.db.get_value.side_effect = ['SHIP-1', '/private/files/label.pdf']
        request.reset_mock(); request.side_effect = [result()]
        self.assertEqual(v3.get_labels('v3:' + ID), ['/private/files/label.pdf'])
        request.assert_called_once()
        frappe.db.get_value.side_effect = None


    def offers(self, parcels, latest_booking=None):
        # The latest SendCloud booking on record tells v3 from v2 accounts.
        frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: latest_booking if doctype == 'Shipment' else ('key', 'secret')
        try:
            return legacy.get_sendcloud_available_services('ADDRESS', json.dumps(parcels))
        finally:
            frappe.db.get_value.side_effect = None

    @patch.object(legacy.requests, 'get')
    def test_v3_account_gets_no_offers_for_several_parcels(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        for parcels in ([{'count': 2, 'weight': 1}], [{'count': 1, 'weight': 1}, {'weight': 1}]):
            self.assertEqual(self.offers(parcels, 'v3:' + ID), [])
        get.assert_not_called()
        self.assertEqual(frappe.db.get_value.call_args_list[-1].args[:3],
                         ('Shipment', {'service_provider': 'SendCloud', 'shipment_id': ['is', 'set']}, 'shipment_id'))
        self.assertEqual(frappe.db.get_value.call_args_list[-1].kwargs, {'order_by': 'creation desc'})
        self.assertIn('supports one parcel', frappe.msgprint.call_args.args[0])

    @patch.object(legacy.requests, 'get')
    def test_v3_account_keeps_offers_for_one_parcel(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        offers = self.offers([{'count': 1, 'weight': 1}], 'v3:' + ID)
        self.assertEqual([(o.service_id, o.total_price) for o in offers], [(341, 7.1)])
        self.assertNotIn('Shipment', [call.args[0] for call in frappe.db.get_value.call_args_list])

    @patch.object(legacy.requests, 'get')
    def test_v2_account_keeps_offers_for_several_parcels(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        # No booking yet, or the latest one went through v2 (e.g. after switching back to a v2 account).
        for latest_booking in (None, '123, 456'):
            offers = self.offers([{'count': 2, 'weight': 1}], latest_booking)
            self.assertEqual([(o.service_id, o.total_price) for o in offers], [(341, 14.2)])
        frappe.msgprint.assert_not_called()

    @patch.object(v3, 'MAX_PARCELS', 2)
    @patch.object(legacy.requests, 'get')
    def test_offers_follow_the_v3_parcel_limit(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        self.assertEqual(len(self.offers([{'count': 2, 'weight': 1}], 'v3:' + ID)), 1)
        self.assertEqual(self.offers([{'count': 3, 'weight': 1}], 'v3:' + ID), [])

    def test_v3_booking_ids(self):
        self.assertTrue(legacy.is_v3_booking('v3:' + ID))
        self.assertFalse(legacy.is_v3_booking('123, 456'))
        self.assertFalse(legacy.is_v3_booking(None))

if __name__ == '__main__':
    unittest.main()
