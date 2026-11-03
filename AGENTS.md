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

- CI (`.github/workflows/ci.yml`, one workflow; runner from `vars.LINUX_RUNNER`,
  GitHub-hosted until the org shares it with public repos; fork PRs need maintainer
  approval) runs `ruff check shipment/` with
  `ruff==0.15.21`; the only rule is `S608` (hardcoded SQL), so parameterize SQL.
- The same `ruff` job runs `python .github/scripts/test_sendcloud_v3.py`
  (needs `requests`), which stubs Frappe itself. `ruff` and `unit tests` are the
  required checks. `python .github/scripts/test_letmeship.py` runs the LetMeShip
  tests with a stubbed Frappe and every live HTTP call blocked; use it locally
  without a bench (`unit tests` runs the same module against real Frappe).
- **Never run `ruff format`**: files mix tab and 4-space indentation. Match the
  surrounding file.
- Unit tests: the `unit tests` job runs every `test_*.py` under
  `shipment/` through `.github/scripts/run_unit_tests.py`, against public
  upstream Frappe and ERPNext at the releases production's core forks are built
  on, with no database or site. eso-newmatik is private, so in CI the runner
  stands in for the two functions Shipment imports from it; on a bench the real
  app is used. The same run locally, from this checkout's root with the
  bench's interpreter: `<bench>/env/bin/python .github/scripts/run_unit_tests.py`
  (optionally followed by module names). A test that needs a site goes in the
  runner's `BENCH_ONLY` with its reason. Bump `FRAPPE_*`/`ERPNEXT_*` in
  `ci.yml` when eso-newmatik's `deploy/production/core-versions.json` moves.
- Full suite on a local dev site with `allow_tests` on, never production:
  `bench --site <dev-site> run-tests --app shipment`.
- Form script: `node --test .github/scripts/test_shipment_form.cjs` (Node 24).
  CI (`form tests` job) runs it on pull requests and pushes to `version-16`; run it locally when
  you change `shipment.js`. It mocks Frappe and makes no carrier calls.
- Tests must never reach a live carrier API; mock the provider client.
- Conventional commits: `fix(shipment): ...`, `feat(search): ...`, `ci: ...`.

## Related repos

Contracts that cross this repo's boundary.

| Repo | Direction | Contract (where it lives) |
| --- | --- | --- |
| eso-newmatik | this calls | `required_apps`; module-level imports of `newmatik.overrides.accounts_controller.update_child_qty_rate` (Delivery Note items) and `parcel_service_type.match_parcel_service_type_alias` (`shipment/api/`) |
| eso-newmatik | calls this | function-local import in `newmatik/audit/smoke.py`; `newmatik/api/test_no_app_import_cycle.py` there forbids module-level imports back |

Moth calls `shipment.api.moth_shipping.preview`, `book`, and `refresh` (schema 1).
Deploy these endpoints before the matching Moth release. They derive recipient
billing from the saved Delivery Note; carrier credentials stay in ERP.
