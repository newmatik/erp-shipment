# AGENTS.md

Guidance for AI coding agents working in the Shipment app. `CLAUDE.md` just
points here; `README.md` is the install and configuration reference.

## What this app is

**Shipment** is Newmatik's Frappe app for booking parcels with carrier
aggregators from ERPNext: rates, booking, labels and tracking for
**LetMeShip**, **Packlink** and **SendCloud** (the `service_provider` strings
the code switches on). Frappe/ERPNext **v16**, Python **3.14**. This repo is
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

## Layout

```
shipment/api/let_me_ship.py, packlink.py, sendcloud.py   one HTTP client per provider
shipment/api/utils.py                  shared address, contact and tracking-URL helpers
shipment/shipment/doctype/shipment/    controller, whitelisted endpoints, form JS
shipment/shipment/doctype/shipment_*/  parcel, preset, delivery-note link, provider, settings
.github/scripts/test_shipment_form.cjs node test for the Shipment form script
```

## Lint and tests

- CI (`.github/workflows/lint.yml`) runs `ruff check shipment/` with
  `ruff==0.15.21`; the only rule is `S608` (hardcoded SQL), so parameterize SQL.
- **Never run `ruff format`**: files mix tab and 4-space indentation. Match the
  surrounding file.
- Bench-free unit tests, from the bench's `sites/` directory (Frappe's logger
  needs `../logs`): `PYTHONPATH=<this-checkout> ../env/bin/python -m unittest
  shipment.shipment.doctype.shipment.test_shipment`. One test in it
  (`test_rejects_non_finite_and_overflowing_goods_values`) needs a site.
- Full suite on a local dev site with `allow_tests` on, never production:
  `bench --site <dev-site> run-tests --app shipment`.
- Form script: `node --test .github/scripts/test_shipment_form.cjs` (Node 24).
  CI does not run it; run it when you change `shipment.js`.
- Tests must never reach a live carrier API; mock the provider client.
- Conventional commits: `fix(shipment): ...`, `feat(search): ...`, `ci: ...`.

## Related repos

Contracts that cross this repo's boundary. Full map of Newmatik repos:
[agent-skills/workspace/RELATIONSHIPS.md](https://github.com/newmatik/agent-skills/blob/main/workspace/RELATIONSHIPS.md).

| Repo | Direction | Contract (where it lives) |
| --- | --- | --- |
| eso-newmatik | this calls | `required_apps`; module-level imports of `newmatik.overrides.accounts_controller.update_child_qty_rate` (Delivery Note items) and `parcel_service_type.match_parcel_service_type_alias` (`shipment/api/`) |
| eso-newmatik | calls this | function-local import in `newmatik/audit/smoke.py`; `newmatik/api/test_no_app_import_cycle.py` there forbids module-level imports back |
