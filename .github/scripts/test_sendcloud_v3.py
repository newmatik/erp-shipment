"""Offline adapter contract tests. No Frappe site or carrier credentials needed."""
import ast
from decimal import Decimal
from datetime import timedelta
import importlib
import json
import hashlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

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
customs = importlib.import_module('shipment.api.customs')
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

    def test_business_addresses_always_carry_a_company_line(self):
        address = AttrDict({**ADDRESS, 'name': 'ADDRESS-1', 'address_title': 'Muster Werk GmbH'})
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Customer', link_name='CUSTOMER-1')]
        frappe.db.get_value.return_value = AttrDict(customer_name='Muster Holding, Inc.', customer_type='Company')
        # The title names the company at that address, even when it differs from the customer.
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Muster Werk GmbH')
        address['address_title'] = ''
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Muster Holding, Inc.')
        address['address_title'] = 'Erika Muster'
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Muster Holding, Inc.')
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Supplier', link_name='SUPPLIER-1')]
        frappe.db.get_value.return_value = AttrDict(supplier_name='Business GmbH', supplier_type='Company')
        address['address_title'] = 'Business GmbH Lager'
        self.assertEqual(v3._address(address, 'Erika Muster')['company_name'], 'Business GmbH Lager')
        # The sender's legal name as both title and name prints once.
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Company', link_name='Newmatik GmbH')]
        frappe.db.get_value.return_value = 'Newmatik GmbH'
        address['address_title'] = 'Newmatik GmbH'
        self.assertNotIn('company_name', v3._address(address, 'Newmatik GmbH'))
        # One site of two companies: the title picks the company, never the first link.
        links = [AttrDict(link_doctype='Company', link_name='SC ESO Electronic S.R.L'), AttrDict(link_doctype='Company', link_name='Newmatik GmbH')]
        frappe.get_doc.return_value.links = links
        frappe.db.get_value.side_effect = lambda doctype, name, field: name
        self.assertNotIn('company_name', v3._address(address, 'Newmatik GmbH'))
        self.assertEqual(v3._sender_address(address, 'Newmatik GmbH'), v3._address(address, 'Newmatik GmbH'))
        address['address_title'] = ''
        self.assertNotIn('company_name', v3._address(address, 'Erika Muster'), 'Two businesses and no title: name neither')
        frappe.db.get_value.side_effect = None
        address['address_title'] = 'Newmatik GmbH'
        # Private customers and unlinked addresses get no company line.
        frappe.get_doc.return_value.links = [AttrDict(link_doctype='Customer', link_name='CUSTOMER-2')]
        frappe.db.get_value.return_value = AttrDict(customer_name='Erika Muster', customer_type='Individual')
        self.assertNotIn('company_name', v3._address(address, 'Erika Muster'))
        frappe.get_doc.return_value.links = []
        self.assertNotIn('company_name', v3._address(address, 'Erika Muster'))

    def customs_shipment(self, **overrides):
        return AttrDict({'name': 'SHIP-1', 'pickup_from_type': 'Company', 'pickup_company': 'Newmatik GmbH',
                         'delivery_to_type': 'Customer', 'delivery_customer': 'Muster Holding, Inc.', 'reason_for_export': 'Sample',
                         'shipment_delivery_notes': [AttrDict(delivery_note='DN-1')],
                         'customs_items': [AttrDict(idx=1, description='Board ', customs_tariff_number='8473 3020',
                                                    country_of_origin='Germany', qty=3, unit_value=250, unit_weight=0.4)], **overrides})

    def customs_db(self, company=('DE 123 456 789', 'DE1234567')):
        values = {('Company', 'Newmatik GmbH'): company, ('Country', 'Germany'): 'de',
                  ('Customer', 'Muster Holding, Inc.'): AttrDict(customer_name='Muster Holding, Inc.', customer_type='Company', tax_id='12-3456789')}
        return lambda doctype, name=None, fields=None, **kwargs: values.get((doctype, name), ('key', 'secret') if doctype == 'Shipment Service Provider' else None)

    @patch.object(v3, '_request')
    def test_a_customs_border_sends_items_reason_and_tax_numbers(self, request):
        us = AttrDict(ADDRESS, country_code='US', pincode='62701', city='Springfield')
        request.side_effect = [{'data': {'341': 'fedex:economy'}}, result()]
        frappe.db.get_value.side_effect = self.customs_db()
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: self.customs_shipment() if doctype == 'Shipment' else Mock(links=[])):
            with self.assertRaises(ValueError):  # result() describes a domestic booking: the details check notices
                v3.create_shipment('SHIP-1', 'SENDER', us, CONTACT, RATE, PARCELS, 'Goods', 750)
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['parcels'][0]['parcel_items'], [{'description': 'Board', 'quantity': 3,
            'weight': {'value': '0.4', 'unit': 'kg'}, 'price': {'value': '250.0', 'currency': 'EUR'}, 'hs_code': '84733020', 'origin_country': 'DE'}])
        info = payload['customs_information']
        self.assertEqual((info['invoice_number'], info['export_reason'], info['export_type']), ('DN-1', 'commercial_sample', 'commercial_b2b'))
        self.assertEqual(info['tax_numbers'], {'sender': [{'name': 'VAT', 'country_code': 'DE', 'value': 'DE123456789'},
                                                          {'name': 'EORI', 'country_code': 'DE', 'value': 'DE1234567'}],
                                               'receiver': [{'name': 'EIN', 'country_code': 'US', 'value': '12-3456789'}]})

    @patch.object(v3, '_request')
    def test_missing_customs_data_stops_before_any_purchase(self, request):
        us = AttrDict(ADDRESS, country_code='US', pincode='62701')
        frappe.db.get_value.side_effect = self.customs_db(company=('DE 123 456 789', None))
        shipment = self.customs_shipment(customs_items=[AttrDict(idx=1, description='Board', customs_tariff_number='', country_of_origin='Unknown', qty=3, unit_value=0)])
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: shipment if doctype == 'Shipment' else Mock(links=[])):
            with self.assertRaisesRegex(ValueError, 'EORI Number of Company Newmatik GmbH'):
                v3.create_shipment('SHIP-1', 'SENDER', us, CONTACT, RATE, PARCELS, 'Goods', 750)
        request.assert_not_called()
        frappe.db.commit.assert_not_called()

    def test_customs_borders(self):
        def address(country, pincode='10115'):
            return AttrDict(country_code=country, pincode=pincode)
        border = customs.crosses_customs_border
        self.assertFalse(border(address('DE'), address('AT')), 'Inside the EU customs union')
        self.assertFalse(border(address('DE'), address('DE')))
        self.assertTrue(border(address('DE'), address('US')))
        self.assertTrue(border(address('DE'), address('CH')))
        self.assertTrue(border(address('DE'), address('ES', '35001')), 'Canary Islands')
        self.assertTrue(border(address('DE'), address('DE', '27498')), 'Helgoland')
        self.assertFalse(border(address('CH', '8000'), address('CH', '3000')))

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
                     'release_absent_sendcloud_booking': v3.release_absent_booking,
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

    @patch.object(v3, '_request')
    def test_absent_booking_is_released_only_after_the_request_timeout(self, request):
        class Doc(AttrDict):
            __setattr__ = dict.__setitem__
        def marker(minutes_ago):
            at = (v3.datetime.now(v3.timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
            return Doc(name='SHIP-1', carrier_booking_data=json.dumps({'sendcloud_uncertain': {
                'external_reference_id': 'erpnext:SHIP-1', 'at': at, 'document_modified': '2026-10-29 13:45:24.605540'}}))
        frappe.db.get_value.return_value = '2026-10-24 06:33:01.797440'
        fresh = marker(1)
        self.assertFalse(v3.release_absent_booking(fresh))
        request.assert_not_called()
        request.return_value = {'data': [result()['data']]}
        self.assertFalse(v3.release_absent_booking(marker(10)), 'An existing booking is recovered, never released')
        for response in ({}, {'data': None}, {'data': {}}):
            request.return_value = response
            self.assertFalse(v3.release_absent_booking(marker(10)), 'Only an explicit empty list proves absence')
        frappe.db.set_value.assert_not_called()
        request.return_value = {'data': []}
        for changed in ('2026-10-29 13:50:00', None):
            frappe.db.get_value.return_value = changed
            self.assertFalse(v3.release_absent_booking(marker(10)), 'Changed credentials may point to another account')
        frappe.db.set_value.assert_not_called()
        frappe.db.get_value.return_value = '2026-10-24 06:33:01.797440'
        stale = marker(10)
        self.assertTrue(v3.release_absent_booking(stale))
        self.assertEqual(request.call_args.args, ('GET', '/shipments?external_reference_id=erpnext%3ASHIP-1'))
        frappe.db.set_value.assert_called_once_with('Shipment', 'SHIP-1', 'carrier_booking_data', None, update_modified=False)
        self.assertIsNone(stale.carrier_booking_data)
        self.assertIsNone(v3.uncertain_booking(stale))
        request.reset_mock()
        self.assertTrue(v3.release_absent_booking(stale))
        request.assert_not_called()
        self.assertFalse(v3.release_absent_booking(Doc(name='SHIP-1', carrier_booking_data=json.dumps({'sendcloud_uncertain': {'reference': 'x'}}))))

    def test_released_booking_lets_another_provider_book(self):
        path = Path(__file__).resolve().parents[2] / 'shipment/shipment/doctype/shipment/shipment.py'
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_shipment']
        nodes[0].decorator_list = []
        booked = {'shipment_id': 'PL-1', 'service_provider': 'Packlink', 'shipment_amount': 9}
        packlink, recovery = Mock(return_value=booked), Mock()
        namespace = {'frappe': frappe, '_': frappe._, 'json': json, 'DHL_PROVIDER': 'DHL',
                     'ensure_no_uncertain_dhl_booking': Mock(), 'ensure_no_uncertain_sendcloud_booking': v3.ensure_no_uncertain_booking,
                     'uncertain_sendcloud_booking': v3.uncertain_booking, 'recover_sendcloud_shipment': recovery,
                     'release_absent_sendcloud_booking': Mock(return_value=True),
                     '_get_delivery_note_names': lambda rows: [], 'create_packlink_shipment': packlink}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
        document = AttrDict(docstatus=1, shipment_id=None, carrier_booking_data=json.dumps({'sendcloud_uncertain': {'reference': 'SHIP-1'}}), check_permission=Mock(), shipment_delivery_notes=[])
        with patch.object(frappe, 'get_doc', return_value=document):
            args = ['SHIP-1', 'Company', 'Customer', 'SENDER', 'RECIPIENT', PARCELS, 'Goods', '2026-01-01', 50]
            self.assertEqual(namespace['create_shipment'](*args, json.dumps({'service_provider': 'Packlink'}), '[]', '[]'), booked)
        packlink.assert_called_once(); recovery.assert_not_called()

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
        frappe.db.set_default.assert_called_once_with('sendcloud_v3_account', legacy._key_fingerprint('key'), parent='SendCloud')
        create.reset_mock(); frappe.db.set_default.reset_mock()
        post.side_effect = legacy.requests.Timeout('response lost')
        self.assertEqual(legacy.create_sendcloud_shipment(*args, pickup_address_name='SENDER'), {})
        create.assert_not_called()
        frappe.db.set_default.assert_not_called()

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


    def offers(self, parcels, v3_key=None):
        # create_sendcloud_shipment stores a fingerprint of the key whose account refused v2.
        frappe.db.get_default.return_value = legacy._key_fingerprint(v3_key) if v3_key else None
        with patch.object(v3, 'fitting_methods', side_effect=lambda ids, *args: set(ids)):
            return legacy.get_sendcloud_available_services('ADDRESS', json.dumps(parcels))

    @patch.object(v3, 'fitting_methods', return_value={341})
    @patch.object(legacy.requests, 'get')
    def test_offers_are_only_what_v3_allows_for_the_dimensions(self, get, fitting):
        small = dict(METHOD, id=2830, name='DHL Kleinpaket 0-1kg')
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD, small]}))
        parcels = [{'count': 1, 'weight': 1, 'length': 32, 'width': 22, 'height': 17}]
        frappe.db.get_default.return_value = legacy._key_fingerprint('key')
        offers = legacy.get_sendcloud_available_services('ADDRESS', json.dumps(parcels), pickup_address_name='SENDER')
        self.assertEqual([o.service_id for o in offers], [341])
        ids, sender, recipient, sent = fitting.call_args.args
        self.assertEqual((ids, sent), ({341: '0.001', 2830: '0.001'}, parcels))
        utils.get_address.assert_any_call('SENDER')
        # A v2 account books from Sendcloud's default sender: the check must not assume the pickup.
        frappe.db.get_default.return_value = None
        utils.get_address.reset_mock()
        legacy.get_sendcloud_available_services('ADDRESS', json.dumps(parcels), pickup_address_name='SENDER')
        self.assertIsNone(fitting.call_args.args[1])
        self.assertNotIn(call('SENDER'), utils.get_address.call_args_list)

    @patch.object(v3, '_request')
    def test_fitting_methods_send_weights_and_dimensions(self, request):
        options = {'data': [{'code': 'dhl_de:dhl_paket'}, {'code': 'dpd:classic'}]}
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket', '2830': 'dhl_de:warenpost'}}, options]
        sender = AttrDict(country_code='DE', pincode='55619')
        recipient = AttrDict(country_code='DE', pincode='52249')
        parcels = [{'count': 2, 'weight': 1.5, 'length': 32, 'width': 22, 'height': 17}, {'weight': 1, 'length': 0, 'width': 10, 'height': 5}]
        self.assertEqual(v3.fitting_methods({341: '0.010', 2830: '0.010', 999: '0.010'}, sender, recipient, parcels), {341})
        compat, rates = request.call_args_list
        self.assertEqual(compat.args, ('POST', '/compat/shipping-options'))
        self.assertEqual(compat.kwargs['json'], {'shipping_method_ids': [341, 999, 2830]})
        self.assertEqual(rates.args, ('POST', '/shipping-options'))
        body = rates.kwargs['json']
        self.assertEqual(body['from_address'], {'country_code': 'DE', 'postal_code': '55619'})
        self.assertEqual(body['to_address'], {'country_code': 'DE', 'postal_code': '52249'})
        boxed = {'weight': {'value': '1.5', 'unit': 'kg'}, 'dimensions': {'length': '32', 'width': '22', 'height': '17', 'unit': 'cm'}}
        # Incomplete dimensions are left out rather than sent as zero.
        self.assertEqual(body['parcels'], [boxed, boxed, {'weight': {'value': '1', 'unit': 'kg'}}])
        request.reset_mock()
        self.assertEqual(v3.fitting_methods({}, sender, recipient, parcels), set())
        self.assertEqual(v3.fitting_methods({341: '0.010'}, sender, recipient, [{'count': 10 ** 9, 'weight': 1}]), set())
        request.assert_not_called()
        request.side_effect = [{'data': {'341': 'dhl_de:dhl_paket'}}, options]
        self.assertEqual(v3.fitting_methods({341: '0.010'}, None, recipient, parcels[:1]), {341})
        self.assertNotIn('from_address', request.call_args.kwargs['json'])

    @patch.object(v3, '_request')
    def test_a_light_box_above_the_size_class_gets_the_next_band(self, request):
        """1 kg, 32 x 22 x 17 cm: DHL Paket 0-2kg (max 60 x 30 x 15 cm) is refused, 2-5kg is offered."""
        codes = {'341': 'dhl_de:dhl_paket', '342': 'dhl_de:dhl_paket', '343': 'dhl_de:dhl_paket', '85': 'dpd:classic/kp'}

        def respond(method, path, json=None, **kwargs):
            if path == '/compat/shipping-options':
                return {'data': codes}
            weight = json['parcels'][0]['weight']['value']
            return {'data': [{'code': 'dpd:classic/kp'}] + ([{'code': 'dhl_de:dhl_paket'}] if Decimal(weight) > 2 else [])}
        request.side_effect = respond
        parcels = [{'count': 1, 'weight': 1, 'length': 32, 'width': 22, 'height': 17}]
        recipient = AttrDict(country_code='DE', pincode='52249')
        bands = {341: '0.010', 342: '2.001', 343: '5.001', 85: '0.001'}
        self.assertEqual(v3.fitting_methods(bands, None, recipient, parcels), {342, 85}, 'One band per option, the lightest that fits')
        weights = [call.kwargs['json']['parcels'][0]['weight']['value'] for call in request.call_args_list if call.args[1] == '/shipping-options']
        self.assertEqual(weights, ['1', '2.001'], 'Each declared weight is asked once; heavier bands are not tried')
        # A flat box stays in the lightest band.
        request.reset_mock()
        request.side_effect = lambda method, path, json=None, **kwargs: (
            {'data': codes} if path == '/compat/shipping-options' else {'data': [{'code': 'dhl_de:dhl_paket'}, {'code': 'dpd:classic/kp'}]})
        self.assertEqual(v3.fitting_methods(bands, None, recipient, parcels), {341, 85})
        self.assertEqual((str(v3.band_weight(1, '2.001')), str(v3.band_weight(3, '2.001')), str(v3.band_weight(1, '99'))), ('2.001', '3', '1'))

    @patch.object(v3, '_request')
    def test_booking_declares_the_band_minimum(self, request):
        request.side_effect = [{'data': {'342': 'dhl_de:dhl_paket'}}, result()]
        with self.assertRaises(ValueError):  # result() was announced at 1 kg: the hash check notices the changed parcel
            v3.create_shipment('SHIP-1', 'SENDER', ADDRESS, CONTACT, dict(RATE, service_id=342, min_weight='2.001'), PARCELS, 'Goods', 50)
        self.assertEqual(request.call_args.kwargs['json']['parcels'][0]['weight'], {'value': '2.001', 'unit': 'kg'})

    @patch.object(legacy.requests, 'get')
    def test_v3_account_gets_no_offers_for_several_parcels(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        for parcels in ([{'count': 2, 'weight': 1}], [{'count': 1, 'weight': 1}, {'weight': 1}]):
            self.assertEqual(self.offers(parcels, v3_key='key'), [])
        get.assert_not_called()
        frappe.db.get_default.assert_called_with('sendcloud_v3_account', parent='SendCloud')
        self.assertIn('supports one parcel', frappe.msgprint.call_args.args[0])

    @patch.object(legacy.requests, 'get')
    def test_v3_account_keeps_offers_for_one_parcel(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        offers = self.offers([{'count': 1, 'weight': 1}], v3_key='key')
        self.assertEqual([(o.service_id, o.total_price) for o in offers], [(341, 7.1)])
        frappe.db.get_default.assert_not_called()

    @patch.object(legacy.requests, 'get')
    def test_v2_account_keeps_offers_for_several_parcels(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        # Never refused v2, or the provider now holds other credentials than the v3 account's.
        for v3_key in (None, 'old-v3-key'):
            offers = self.offers([{'count': 2, 'weight': 1}], v3_key)
            self.assertEqual([(o.service_id, o.total_price) for o in offers], [(341, 14.2)])
        frappe.msgprint.assert_not_called()

    @patch.object(v3, 'MAX_PARCELS', 2)
    @patch.object(legacy.requests, 'get')
    def test_offers_follow_the_v3_parcel_limit(self, get):
        get.return_value = Mock(text=json.dumps({'shipping_methods': [METHOD]}))
        self.assertEqual(len(self.offers([{'count': 2, 'weight': 1}], v3_key='key')), 1)
        self.assertEqual(self.offers([{'count': 3, 'weight': 1}], v3_key='key'), [])

    def test_v3_booking_ids(self):
        self.assertTrue(legacy.is_v3_booking('v3:' + ID))
        self.assertFalse(legacy.is_v3_booking('123, 456'))
        self.assertFalse(legacy.is_v3_booking(None))

if __name__ == '__main__':
    unittest.main()
