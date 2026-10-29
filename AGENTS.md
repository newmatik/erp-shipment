# AGENTS.md

Guidance for AI coding agents working in the Shipment app. `CLAUDE.md` just
points here; `README.md` is the install and configuration reference.

## What this app is

**Shipment** is Newmatik's Frappe app for booking parcels with carrier
aggregators from ERPNext: rates, booking, labels and tracking for
**LetMeShip**, **Packlink** and **SendCloud**, plus **DHL** booked directly
through DHL's Parcel DE Shipping API (the `service_provider` strings the code
switches on). Frappe/ERPNext **v16**, Python **3.14**. This repo is
public: keep customer data, credentials and internal hostnames out of it.

- It owns the **`Shipment`** DocType (module `Shipment`), which takes the place
  of ERPNext's core Stock `Shipment` where the app is installed.
- `hooks.py` overrides Delivery Note `make_shipment` with
  `make_shipment_from_delivery_note` and runs `update_tracking_info` daily,
  which also writes tracking data back to the linked Delivery Notes.
- Carrier credentials live in **`Shipment Service Provider`** records, one per
  provider; `Shipment Settings` holds the shipping-cost margin. Enable a
  provider on a dev site only when live carrier calls are intended.
- A booked LetMeShip shipment is cancelled in the ERP only by a System Manager
  with the carrier's proof attached (`cancel_letmeship_shipment_after_confirmation`).
- A booked DHL shipment is cancelled through `cancel_dhl_shipment`, which deletes
  the labels at DHL first. DHL never receives recipient email or phone unless the
  provider's `dhl_send_recipient_contact` opt-in is set (GDPR consent). Setup:
  `docs/dhl-parcel-de.md`.

## Layout

```
shipment/api/let_me_ship.py, packlink.py, sendcloud.py, dhl.py   one HTTP client per provider
shipment/api/utils.py                  shared address, contact and tracking-URL helpers
shipment/shipment/doctype/shipment/    controller, whitelisted endpoints, form JS
shipment/shipment/doctype/shipment_*/  parcel, preset, delivery-note link, provider, settings
.github/scripts/test_shipment_form.cjs node test for the Shipment form script
```

## Lint and tests

- CI (`.github/workflows/lint.yml`) runs `ruff check shipment/` with
  `ruff==0.15.21`; the only rule is `S608` (hardcoded SQL), so parameterize SQL.
- The same workflow runs the tests that need neither Frappe nor a site, from the
  repo root with plain Python 3.14: `python .github/scripts/test_sendcloud_v3.py`
  (stubs Frappe; needs `requests`) and `python -m unittest
  shipment.api.test_rates`. A new test module that imports nothing from Frappe
  belongs in that step too.
- **Never run `ruff format`**: files mix tab and 4-space indentation. Match the
  surrounding file.
- Bench-free unit tests, from the bench's `sites/` directory (Frappe's logger
  needs `../logs`): `PYTHONPATH=<this-checkout> ../env/bin/python -m unittest
  shipment.shipment.doctype.shipment.test_shipment shipment.api.test_dhl
  shipment.api.test_rates`. One test in test_shipment
  (`test_rejects_non_finite_and_overflowing_goods_values`) needs a site. Only
  test_rates runs in CI: the others import frappe.
- Full suite on a local dev site with `allow_tests` on, never production:
  `bench --site <dev-site> run-tests --app shipment`.
- Form script: `node --test .github/scripts/test_shipment_form.cjs` (Node 24).
  CI runs it on pull requests and pushes to `version-16`; run it locally when
  you change `shipment.js`. It mocks Frappe and makes no carrier calls.
- Tests must never reach a live carrier API; mock the provider client.
- Conventional commits: `fix(shipment): ...`, `feat(search): ...`, `ci: ...`.

## Related repos

Contracts that cross this repo's boundary.

| Repo | Direction | Contract (where it lives) |
| --- | --- | --- |
| eso-newmatik | this calls | `required_apps`; module-level imports of `newmatik.overrides.accounts_controller.update_child_qty_rate` (Delivery Note items) and `parcel_service_type.match_parcel_service_type_alias` (`shipment/api/`) |
| eso-newmatik | calls this | function-local import in `newmatik/audit/smoke.py`; `newmatik/api/test_no_app_import_cycle.py` there forbids module-level imports back |
