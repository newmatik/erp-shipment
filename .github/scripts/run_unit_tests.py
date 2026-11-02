"""Run the app's unit tests against real Frappe and ERPNext, with no bench or site.

Why this exists
---------------
Most tests here import frappe, so CI used to run only the few that do not
(test_rates and the stubbed adapter scripts). The rest never ran outside a
developer's bench, and a PR could be green while its tests had never executed
(#214). The tests themselves need no database: each patches the Frappe seams it
touches. What they need is for `import frappe` and `import erpnext` to resolve.

This bootstraps just enough Frappe to import the app -- a sites dir with an
`apps.txt`, and a `MariaDBDatabase` that is constructed but never connected --
then runs the tests. It is modelled on eso-newmatik's
`.github/scripts/run_unit_tests.py`. A test that reaches past its mocks to real
SQL fails on the dead connection, which is the correct outcome: it belongs under
`bench run-tests` and in BENCH_ONLY below, with its reason.

eso-newmatik
------------
Shipment imports two functions from the private eso-newmatik app at module level
(`match_parcel_service_type_alias`, `update_child_qty_rate`). This repository is
public, and CI here gets no secret that could read eso-newmatik. When `newmatik`
is not importable, the runner therefore registers stand-ins for exactly those two
functions (see `_stub_newmatik`). On a bench the real app is importable and is
used instead.

Usage:  python .github/scripts/run_unit_tests.py [module ...]
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Apps whose hooks the suite may resolve; the effective list is the importable
# subset (see `_installed_apps`).
CANDIDATE_APPS = ("frappe", "erpnext", "newmatik", "shipment")

# Tests that genuinely need a site. A key is a module, `module.ClassName` or
# `module.ClassName.test_method`, and every entry carries its reason. Do not add
# one to make CI green: if a test fails here because the code under test touches
# the database where it did not before, that is the finding.
BENCH_ONLY = {}

# Apps replaced by stand-ins in this run (see `_stub_newmatik`).
STUBBED_APPS = set()


def _stub_newmatik():
	"""Register stand-ins for the two eso-newmatik functions Shipment imports.

	`match_parcel_service_type_alias` is eso-newmatik's function line for line
	(newmatik/newmatik/doctype/parcel_service_type/parcel_service_type.py): it only
	reads `frappe.db`, which the tests that reach it mock. Keep it in step with
	eso-newmatik. `update_child_qty_rate` writes documents, so its stand-in raises:
	every test that reaches it patches it, and one that forgets to must fail rather
	than run against behaviour that is not eso-newmatik's.
	"""
	import frappe

	def match_parcel_service_type_alias(parcel_service_type, parcel_service):
		"""Match and return Parcel Service Type Alias to Parcel Service Type if exists"""
		if frappe.db.exists("Parcel Service", parcel_service):
			matched_parcel_service_type = frappe.db.get_value(
				"Parcel Service Type Alias",
				{"parcel_type_alias": parcel_service_type, "parcel_service": parcel_service},
				"parent",
			)
			if matched_parcel_service_type:
				parcel_service_type = matched_parcel_service_type
		return parcel_service_type

	def update_child_qty_rate(*args, **kwargs):
		raise AssertionError(
			"update_child_qty_rate is eso-newmatik code, which this CI run cannot import. Patch it in the test."
		)

	modules = {}
	for name in (
		"newmatik",
		"newmatik.overrides",
		"newmatik.overrides.accounts_controller",
		"newmatik.newmatik",
		"newmatik.newmatik.doctype",
		"newmatik.newmatik.doctype.parcel_service_type",
		"newmatik.newmatik.doctype.parcel_service_type.parcel_service_type",
	):
		module = types.ModuleType(name)
		module.__path__ = []
		modules[name] = module
		if "." in name:
			parent, _, child = name.rpartition(".")
			setattr(modules[parent], child, module)

	modules["newmatik.overrides.accounts_controller"].update_child_qty_rate = update_child_qty_rate
	modules[
		"newmatik.newmatik.doctype.parcel_service_type.parcel_service_type"
	].match_parcel_service_type_alias = match_parcel_service_type_alias
	sys.modules.update(modules)
	STUBBED_APPS.add("newmatik")


def _installed_apps():
	"""Return the candidate apps that are importable; stand-ins have no hooks."""
	return [app for app in CANDIDATE_APPS if app not in STUBBED_APPS and importlib.util.find_spec(app)]


def _init_frappe():
	"""Stand up the minimum frappe needs to be imported, with no database anywhere.

	1. A sites dir with an `apps.txt`: `frappe.init` imports every app named there.
	   Shipment is absent on purpose; it is imported from the repo via sys.path.
	2. Something bound to `frappe.db`: a `MariaDBDatabase` whose `.connect()` is
	   never called, so real SQL dies on a refused connection.
	3. cwd == the sites dir with a sibling `logs/`: `frappe.logger()` opens
	   `../logs/...` relative to the current directory, as on a bench.
	4. System Settings bound directly, so `flt(value, precision)` rounds instead of
	   silently returning 0.0 after a failed settings read. Same values as
	   eso-newmatik's runner, which mirror the production configuration.
	5. The installed-app list, so `frappe.get_hooks()` works without the database.
	"""
	bench = pathlib.Path(tempfile.mkdtemp(prefix="shipment-unit-"))
	sites = bench / "sites"
	sites.mkdir()
	(bench / "logs").mkdir()
	(sites / "apps.txt").write_text("frappe\nerpnext\n")
	(sites / "common_site_config.json").write_text(json.dumps({}))

	os.chdir(sites)

	import frappe

	frappe.init(site="", sites_path=".")

	from frappe.database.mariadb.database import MariaDBDatabase

	frappe.local.db = MariaDBDatabase()

	frappe.local.system_settings = frappe._dict(
		{
			"rounding_method": "Commercial Rounding",
			"time_zone": "Europe/Berlin",
			"first_day_of_the_week": "Monday",
			"default_app": "erpnext",
			"float_precision": "2",
			"currency_precision": "2",
			"number_format": "#.###,##",
			"date_format": "dd.mm.yyyy",
			"time_format": "HH:mm:ss",
			"language": "en",
			"country": "Germany",
		}
	)

	frappe.get_installed_apps = lambda **kwargs: _installed_apps()


def _bench_only(test):
	"""Return True when a test, its class or its module is listed in BENCH_ONLY."""
	module = type(test).__module__
	cls = f"{module}.{type(test).__name__}"
	return module in BENCH_ONLY or cls in BENCH_ONLY or f"{cls}.{test._testMethodName}" in BENCH_ONLY


def _without_bench_only(suite):
	"""Return `suite` with every BENCH_ONLY test removed."""
	kept = unittest.TestSuite()
	for test in suite:
		if isinstance(test, unittest.TestSuite):
			kept.addTest(_without_bench_only(test))
		elif not _bench_only(test):
			kept.addTest(test)
	return kept


def _discover():
	"""Return every `test_*.py` module in the app."""
	return [
		".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
		for path in sorted((REPO_ROOT / "shipment").rglob("test_*.py"))
	]


def main():
	sys.path.insert(0, str(REPO_ROOT))
	if not importlib.util.find_spec("newmatik"):
		_stub_newmatik()
	_init_frappe()

	modules = sys.argv[1:] or _discover()

	suite = unittest.TestSuite()
	loader = unittest.defaultTestLoader
	for module in modules:
		suite.addTests(_without_bench_only(loader.loadTestsFromName(module)))

	if loader.errors:
		for error in loader.errors:
			print(error, file=sys.stderr)
		return 1

	print("running {} test modules\n".format(len(modules)))
	result = unittest.TextTestRunner(verbosity=1, buffer=False).run(suite)
	return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
	sys.exit(main())
