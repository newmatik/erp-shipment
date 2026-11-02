"""Run the mocked LetMeShip adapter tests without a Frappe bench or carrier access."""
import sys
import types
import unittest
from datetime import datetime
from html import escape
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class AttrDict(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class ValidationError(Exception):
    pass


def reject(message, *args, **kwargs):
    raise ValidationError(message)


frappe = types.ModuleType('frappe')
frappe._ = lambda value: value
frappe._dict = AttrDict
frappe.ValidationError = ValidationError
frappe.throw = reject
frappe.db = Mock()
frappe.msgprint = Mock()
frappe.log_error = Mock()
frappe.get_doc = Mock()
utils = types.ModuleType('frappe.utils')
utils.escape_html = escape
utils.now_datetime = datetime.now
utils.today = lambda: str(datetime.now().date())
frappe.utils = utils
alias = types.ModuleType('newmatik.newmatik.doctype.parcel_service_type.parcel_service_type')
alias.match_parcel_service_type_alias = lambda name, carrier: name
sys.modules.update({'frappe': frappe, 'frappe.utils': utils,
                    'newmatik.newmatik.doctype.parcel_service_type.parcel_service_type': alias})

if __name__ == '__main__':
    # Any missed mock must fail closed, even on machines with live credentials.
    with patch('requests.sessions.Session.request', side_effect=AssertionError('Live carrier call forbidden')):
        suite = unittest.defaultTestLoader.loadTestsFromName('shipment.api.test_let_me_ship')
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
