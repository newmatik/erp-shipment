# DHL Parcel DE (Geschäftskunden)

The DHL provider books DHL business customer labels directly through DHL's
**Parcel DE Shipping API v2 (Post & Parcel Germany)**, without an aggregator.
Code: `shipment/api/dhl.py`. API reference:
<https://developer.dhl.com/api-reference/parcel-de-shipping-post-parcel-germany-v2>.

## What it does

| Area | Behaviour |
| --- | --- |
| Products | `V01PAK` DHL Paket and `V62KP` DHL Kleinpaket (Germany); `V53WPAK` DHL Paket International and `V66WPI` Warenpost International (other EU member states). `V62WP` Warenpost was replaced by `V62KP` on 2025-01-01. |
| Destinations | Germany and the 26 other EU member states. Destinations that need customs data are refused with a message: every non-EU country (including CH, NO, GB) and the territories outside the EU customs or VAT area (Helgoland 27498 and Büsingen 78266 in Germany, Canary Islands, Ceuta, Melilla, Livigno, Campione d'Italia, Åland, Mount Athos). Book those with another provider. The pickup address must be in Germany. |
| Rates | DHL's shipping API returns no prices. The rate list shows the contract net prices you enter per product (optionally per weight tier and destination country). The offered products are the ones whose weight and size limits fit every parcel. |
| Booking | DHL has no multi-piece shipments, so every parcel (row × count, at most 30; a count of 0 is refused, not booked as one) becomes its own DHL shipment. The Shipment records the DHL environment and the price of each label, so a partial booking or partial deletion bills, and adds the shipping margin for, only the labels that exist. Parcels of one Shipment are marked `1/2`, `2/2` in the label's cost-centre field, and the Shipment's reference number is printed on each label. All shipment numbers are stored comma-separated in the Shipment's Shipment ID and AWB Number (text fields). The Delivery Note's tracking number is a 140-character field, so it gets as many whole numbers as fit plus `(+N)`. A failed Delivery Note update never undoes a booking DHL has already made. The request is first sent with `validate=true` (DHL's dry run). Any error stops the booking and DHL's per-field messages are shown. Warnings, such as an address DHL could not encode, are shown after booking; the label is still valid. |
| Labels | Each label PDF is stored as a private File on the Shipment (`DHL-label-<number>.pdf`). **Print Shipping Label** opens the stored files and downloads any missing label again from DHL. If DHL returns no label for some parcels, they are listed by shipment number with DHL's reason. DHL keeps labels until about three days after the end-of-day closing. |
| Cancellation | **Cancel DHL Booking** (submitted DHL Shipments) deletes the shipments at DHL and then cancels the Shipment. The plain Cancel is blocked while DHL labels exist. DHL deletes labels only before the end-of-day closing (Tagesabschluss, 17:45 by default). After that, DHL refuses and the Shipment stays booked; contact DHL. |
| Tracking | Shipments get one dhl.de tracking link per parcel (`verfolgen.html?piececode=…`). Status polling through the **DHL Shipment Tracking – Unified API** is optional (off by default). It uses the same developer.dhl.com API key, but only if the app includes that API. It is skipped in the sandbox. A manual tracking update in the form writes the links at once and queues the status poll as a background job (`refresh_dhl_tracking_status`, short queue, one job per Shipment), so the desk never waits 5 s per parcel. The daily `update_tracking_info` job covers DHL like the other providers. Without a fresh answer for every parcel the last confirmed status is kept. |
| Pickups | Not ordered. The API only creates labels; hand the parcels to DHL or use your regular DHL pickup. A booking with Pickup Type "Pickup" shows a reminder. |

Prices are net contract prices. `total_vat` is written as 0.

## Recipient email and phone (GDPR)

DHL's API documentation requires the recipient's consent before their email
address or phone number is passed to DHL (Art. 4 No. 11 GDPR). By default the
integration sends **neither**, and labels work without them. Tick
**Send Recipient Email/Phone to DHL** on the provider only if recipients have
agreed, for example so that DHL sends its parcel notification. Even then, phone
numbers are only sent for destinations outside Germany, because the API does
not accept them for German addresses. The shipper email is our own company
contact and is only sent when the pickup party is the Company.

## Configure

Create one **Shipment Service Provider** record named exactly `DHL`
(Shipment Provider Name = `DHL`). The DHL section appears once the name is set.

| Field | What to enter |
| --- | --- |
| API Key | The API key ("client_id") of an app on developer.dhl.com that includes *Parcel DE Shipping (Post & Parcel Germany)*. Optionally add *Shipment Tracking – Unified* to the same app. Production access is approved manually by DHL. |
| API Secret | The app's API secret. With it, calls use OAuth2 (recommended). Without it, they use the API key plus Basic Auth with the GKP user, which DHL has deprecated and will drop in a later API version. |
| GKP Username / GKP Password | A Geschäftskundenportal user, preferably a **system user** (it cannot log in to the portal; password valid 365 days). |
| EKP | The 10-digit DHL customer number. |
| User Group Profile | The GKP user group allowed to use the billing numbers; `STANDARD_GRUPPENPROFIL` unless your administrator set up another. |
| Label Print Format | Empty uses the GKP printer setting. `910-300-600` / `910-300-610` / `910-300-400` / `910-300-410` are thermal formats; `A4` and `910-300-700/300` are laser formats. `100x70mm` works only for V62KP and V66WPI. |
| Use DHL Sandbox | On by default. Turn off only for real labels. Each booking remembers whether it was made in the sandbox or in production. Cancelling it or downloading its labels again is refused while the provider is set to the other environment; switch back to manage it. |
| Send Recipient Email/Phone to DHL | Off by default; see above. |
| Update Status from DHL Tracking API | Off by default. The default Unified Tracking service level is 250 calls per day and one call every 5 s; the integration claims one call slot per 5 s and API key in Redis, shared by all workers, and stops polling for that run if no slot frees up. Request a higher level from DHL for production volumes. |
| DHL Products | One row per product and contract participation: **Product**, **Participation** (2 characters, e.g. `01`), optional **Destination Country** (a country row wins over a generic row), optional **Max Weight (kg)** for weight-tiered prices (0 = product limit), and **Net Price per Parcel**. |

The billing number sent to DHL is **EKP (10) + procedure (2) + participation
(2)**. The procedure comes from the product: `01` V01PAK, `62` V62KP, `53`
V53WPAK, `66` V66WPI. Example: EKP `1234567890`, V01PAK, participation `01` →
`12345678900101`. Participations come from the DHL contract; a product can
have several (e.g. a separate GoGreen Plus participation). Enter the one to
book on. Saving the record checks the EKP, the participations and the weight
limits. It cannot be enabled without credentials and at least one product row.

Make sure a **Parcel Service** named `DHL` exists, because booked Delivery
Notes get `parcel_service = DHL`. Parcel Service Type aliases for "DHL Paket",
"DHL Kleinpaket", "DHL Paket International" and "Warenpost International" map
the services to your Parcel Service Types and the preferred list, as they do
for LetMeShip.

### Sandbox

DHL publishes a shared sandbox business customer (`user-valid`, with the
password given in the API's user guide) and the EKP `3333333333`. Sandbox
billing numbers include `33333333330101` / `…0102` (V01PAK), `33333333336201`
(V62KP), `33333333335301` (V53WPAK) and `33333333336601` (V66WPI). So for the
sandbox, enter EKP `3333333333` and participation `01` for each product. You
still need your own developer.dhl.com API key and secret. Sandbox labels
cannot be shipped.

## Not supported yet

- Customs data (CN22/CN23, export documents), so no non-EU destinations.
- Value-added services (Premium, insurance, cash on delivery, ident check,
  Retoure labels, GoGreen Plus as a service) and DHL Europaket (`V54EPAK`).
- Packstation and post office (Filiale) addresses.
- Manifesting (`POST /manifests`): DHL closes the day automatically.
