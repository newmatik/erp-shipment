# Shipment

Shipment is Newmatik's Frappe app for creating carrier bookings, labels, and
tracking updates from ERPNext shipment documents. The current integrations are
LetMeShip, Sendcloud, Packlink, and DHL Parcel DE (DHL Geschäftskunden, booked
directly with DHL). It runs in production on Newmatik's ERPNext
v16 bench, next to the `newmatik` app.

This repository is public: keep customer data, credentials and internal
hostnames out of it. Contributors and AI agents: read [AGENTS.md](./AGENTS.md)
first.

## Requirements

- Frappe Framework and ERPNext v16
- Python 3.14
- Node.js 24 and Redis as required by the parent bench
- The `newmatik` app, installed first

`shipment/hooks.py` declares `required_apps = ["newmatik"]`; Shipment imports
Newmatik's Delivery Note and parcel-service extensions at module load.

## Install

From the bench root:

```bash
bench get-app --branch version-16 shipment \
  git@github.com:newmatik/erp-shipment.git
bench --site <sitename> install-app shipment
bench --site <sitename> migrate
```

For an exact production replica, use the release SHA rather than assuming the
tip of `version-16`. Follow
[`docs/developer-bench-v16.md`](https://github.com/newmatik/eso-newmatik/blob/version-16/docs/developer-bench-v16.md)
for the complete app order and parity workflow.

## Configuration

Create one **Shipment Service Provider** record per carrier integration and
configure its customer ID, API key, API password, tracking URL, and enable
state. Keep provider credentials in the site database; never add them to this
repository or documentation.

| Provider | Record name | Notes |
| --- | --- | --- |
| LetMeShip | `Let Me Ship` | Rates, booking, labels, tracking |
| Packlink | `Packlink` / `PackLink` | Rates, booking, labels, tracking |
| Sendcloud | `SendCloud` | Rates, booking, labels, tracking |
| DHL Parcel DE | `DHL` | DHL Paket, Kleinpaket, Paket International and Warenpost International for Germany and the EU; labels stored on the Shipment, cancellation at DHL, tracking link and optional status polling. See [docs/dhl-parcel-de.md](docs/dhl-parcel-de.md). |

Only enable a provider on a development site when live carrier calls are
intended: a booking with live credentials is a real carrier booking. Use
provider test credentials where available.

## Development

Base PRs on `version-16`, the default branch. Run these from this directory:

```bash
uvx ruff@0.15.21 check shipment/                     # the pinned version CI runs
node --test .github/scripts/test_shipment_form.cjs   # offline form checks, Node 24; also run in CI
```

There is no staging ERP: run tests on a local bench, never against production,
and never let a test reach a live carrier API.

```bash
# Bench-free unit tests, from the bench's sites/ directory
PYTHONPATH=<this-checkout> ../env/bin/python -m unittest \
  shipment.shipment.doctype.shipment.test_shipment shipment.api.test_dhl

# Full suite, from the bench root, on a local site with allow_tests on
bench --site <dev-site> run-tests --app shipment
```

See [AGENTS.md](./AGENTS.md#lint-and-tests) for the one unit test that needs a
site, the formatter ban and the commit conventions.

## License

MIT
