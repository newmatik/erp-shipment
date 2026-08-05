# Shipment

Shipment is Newmatik's Frappe app for creating carrier bookings, labels, and
tracking updates from ERPNext shipment documents. The current integrations are
LetMeShip, Sendcloud, and Packlink.

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

Only enable a provider on a development site when live carrier calls are
intended. Use provider test credentials where available.

## Development

Run commands from the bench root:

```bash
ruff check apps/shipment/shipment/
bench --site <sitename> run-tests --app shipment
```

## License

MIT
