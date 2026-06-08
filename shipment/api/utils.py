import frappe
from frappe import _
from frappe.utils import escape_html
import re
from urllib.parse import quote

LETMESHIP_STREET_LIMIT = 35


def get_address(address_name):
    address = frappe.db.get_value('Address', address_name, [
        'address_title',
        'address_line1',
        'address_line2',
        'city',
        'pincode',
        'country',
        'state'
    ], as_dict=1)
    address.name = address_name
    address.country_code = frappe.db.get_value('Country',
                                               address.country, 'code').upper()
    if not address.pincode or address.pincode == '':
        frappe.throw(_("Postal Code is mandatory to continue. </br> \
                     Please set Postal Code for Address <a href='#Form/Address/{0}'>{1}</a>"
                       ).format(address_name, address_name))
    
    # For Dutch postal codes (NL), format as "1234 AB" (required by LetMeShip)
    # Only auto-format if incomplete (4 digits without letters) or improperly formatted
    if address.country_code == 'NL':
        pincode_clean = address.pincode.replace(' ', '').replace('-', '').upper()
        # Normalize any valid 4-digits + 2-letters pattern to "1234 AB"
        if len(pincode_clean) == 6 and pincode_clean[:4].isdigit() and pincode_clean[4:6].isalpha():
            address.pincode = f"{pincode_clean[:4]} {pincode_clean[4:6]}"
        elif len(pincode_clean) == 4 and pincode_clean.isdigit():
            # Only 4 digits provided - missing letters, try to extract from city
            city_clean = address.city.strip().upper()
            if city_clean and len(city_clean) >= 2 and city_clean[:2].isalpha():
                # Auto-format as fallback: "8606" + " JW" from city "JW SNEEK" = "8606 JW"
                address.pincode = f"{pincode_clean} {city_clean[:2]}"
        # If doesn't match expected patterns, keep as-is
    else:
        # For other countries, remove spaces
        address.pincode = address.pincode.replace(' ', '')
    
    address.city = address.city.strip()
    return address


def get_contact(contact_name):
    contact = frappe.db.get_value('Contact', contact_name, [
        'first_name',
        'last_name',
        'email_id',
        'phone',
        'mobile_no',
        'gender',
    ], as_dict=1)
    if not contact.last_name:
        frappe.throw(_("Last Name is mandatory to continue. </br> \
                     Please set Last Name for Contact <a href='#Form/Contact/{0}'>{1}</a>"
                       ).format(contact_name, contact_name))
    if not contact.phone:
        contact.phone = contact.mobile_no
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.email = contact.email_id
    contact.title = 'MS'
    if contact.gender == 'Male':
        contact.title = 'MR'
    return contact


def get_company_contact():
    contact = frappe.db.get_value('User', frappe.session.user, [
        'first_name',
        'last_name',
        'email',
        'phone',
        'mobile_no',
        'gender',
    ], as_dict=1)
    if not contact.phone:
        contact.phone = contact.mobile_no
    if not contact.phone:
        contact.phone = "+49675216998"
    contact.phone_prefix = contact.phone[:3]
    contact.phone = re.sub('[^A-Za-z0-9]+', '', contact.phone[3:])
    contact.title = 'MS'
    if contact.gender == 'Male':
        contact.title = 'MR'
    contact.email = 'service@newmatik.com'
    return contact


def get_tracking_url(carrier, tracking_number):
    """ Return the formatted Tracking URL"""

    tracking_url = ''
    url_reference = frappe.get_value('Parcel Service', carrier,
                                     'url_reference')
    if url_reference:
        tracking_url = frappe.render_template(url_reference,
                                              {'tracking_number': tracking_number})
        tracking_url_template = \
            '<a href="{{ tracking_url }}" target="_blank"><b>{{ _("Click here to Track Shipment") }}</a></b>'
        tracking_url = frappe.render_template(tracking_url_template,
                                              {'tracking_url': tracking_url})
    return tracking_url


def _split_at_word_boundary(text, limit):
    """Split text into (head, tail) where len(head) <= limit, splitting on the
    last whitespace within the first limit+1 characters. Falls back to a hard
    character split if no whitespace is available.
    """
    if len(text) <= limit:
        return text, ""
    idx = text.rfind(" ", 0, limit + 1)
    if idx > 0:
        head = text[:idx].rstrip()
        tail = text[idx + 1:].strip()
        if len(head) <= limit:
            return head, tail
    return text[:limit], text[limit:].strip()


def _pack_two_slots(parts, limit):
    """Pack a sequence of text parts (joined with ", ") into two slots.

    Returns (slot1, slot2) where slot1 is always <= limit characters (split on
    the last word boundary within the first limit+1 characters, with a hard
    character split as a last resort). slot2 holds everything that didn't fit
    slot1 *verbatim* -- no further splitting -- so nothing is silently
    truncated: when slot2 exceeds limit it is handed to
    validate_letmeship_address, which throws a user-facing, translatable error
    pointing at the oversized field.
    """
    text = ", ".join(p for p in parts if p)
    if not text:
        return "", ""
    slot1, slot2 = _split_at_word_boundary(text, limit)
    return slot1, slot2


def fit_letmeship_address(address, auto_split=True, role=""):
    """Normalize an address dict (as returned by get_address) so it fits
    LetMeShip's 35-character street / addressInfo1 / addressInfo2 limits.

    Operates on the in-memory frappe._dict only; the Address DocType is never
    mutated. When auto_split is True, overflow from address_line1 and
    address_line2 is repacked across the two addressInfo slots used by the
    LetMeShip payload builder.

    Returns True when the resulting address fits every limit, False otherwise
    (in which case validate_letmeship_address is expected to raise).
    """
    line1 = re.sub(r"\s+", " ", (address.address_line1 or "").replace("\t", " ")).strip()
    line2 = re.sub(r"\s+", " ", (address.address_line2 or "").replace("\t", " ")).strip()

    original_line1 = line1
    original_line2 = line2

    # Always clear any prior continuation so the payload builder never sees a
    # stale addressInfo1/addressInfo2 if fit_letmeship_address is invoked more
    # than once on the same dict or if the input dict was enriched elsewhere.
    address.address_line1_con = ""

    if len(line1) <= LETMESHIP_STREET_LIMIT and len(line2) <= LETMESHIP_STREET_LIMIT:
        address.address_line1 = line1
        address.address_line2 = line2
        return True

    if not auto_split:
        address.address_line1 = line1
        address.address_line2 = line2
        return False

    street = line1
    cont1 = ""
    if len(street) > LETMESHIP_STREET_LIMIT:
        street, cont1 = _split_at_word_boundary(street, LETMESHIP_STREET_LIMIT)

    overflow_parts = []
    if cont1:
        overflow_parts.append(cont1)
    if line2:
        overflow_parts.append(line2)

    info1, info2 = _pack_two_slots(overflow_parts, LETMESHIP_STREET_LIMIT)

    address.address_line1 = street
    if info2:
        address.address_line1_con = info1
        address.address_line2 = info2
    else:
        address.address_line2 = info1

    split_happened = (
        street != original_line1
        or address.address_line2 != original_line2
        or address.get("address_line1_con")
    )
    if split_happened:
        # Audit trail: file-logger only, and scrubbed of street content.
        # The Address name is a stable foreign key, so operators can look up
        # the current values on demand rather than having PII copied here.
        line1_con = address.get("address_line1_con") or ""
        line2_final = address.address_line2 or ""
        payload_info1 = line1_con or line2_final
        payload_info2 = line2_final if line1_con else ""
        try:
            frappe.logger("letmeship", allow_site=True).info(
                "LetMeShip address auto-split | address=%s role=%s "
                "street_len=%s addressInfo1_len=%s addressInfo2_len=%s "
                "orig_line1_len=%s orig_line2_len=%s",
                address.get("name") or "?",
                role or "?",
                len(street),
                len(payload_info1),
                len(payload_info2),
                len(original_line1),
                len(original_line2),
            )
        except Exception as audit_exc:
            # Don't let audit logging break a shipment, but surface a warning
            # so operators aren't blind to repeated audit failures. Nested
            # try/except preserves the never-raise contract when the logger
            # factory itself is the thing failing (no site context, broken
            # log path, etc.).
            try:
                frappe.logger("letmeship", allow_site=True).warning(
                    "LetMeShip audit logging failed, audit skipped: %s",
                    str(audit_exc),
                )
            except Exception:
                pass

    continuation_field = address.get("address_line1_con") or ""
    return (
        len(street) <= LETMESHIP_STREET_LIMIT
        and len(address.address_line2 or "") <= LETMESHIP_STREET_LIMIT
        and len(continuation_field) <= LETMESHIP_STREET_LIMIT
    )


def validate_letmeship_address(address, role):
    """Raise a user-facing, translatable error if an address still exceeds
    LetMeShip's length limits after fit_letmeship_address has run.

    Expected to be a no-op for the overwhelming majority of addresses.
    """
    role_label = _("pickup address") if role == "pickup" else _("delivery address")
    # Address names are derived from user-supplied address_title (autoname
    # field:address_title) and can contain characters reserved in URL paths
    # (/, #, ?, &) as well as HTML-special characters (', ", &). URL-encode
    # the name for the href path segment (safe="" encodes everything except
    # unreserved chars) and HTML-escape the visible anchor text separately.
    name = address.get("name") or ""
    href_name = escape_html(quote(name, safe=""))
    text_name = escape_html(name)
    link = "<a href='/desk/address/{0}'>{1}</a>".format(href_name, text_name)
    limit = LETMESHIP_STREET_LIMIT

    checks = (
        ("address_line1", _("Address line 1"), address.get("address_line1") or ""),
        ("address_line2", _("Address line 2"), address.get("address_line2") or ""),
        ("address_line1_con", _("Address line 1"), address.get("address_line1_con") or ""),
    )
    for _key, field_label, value in checks:
        if len(value) > limit:
            frappe.throw(_(
                "LetMeShip requires the {role} {field} to be at most {limit} characters "
                "(currently {actual}). Please shorten the Address: {link}"
            ).format(
                role=role_label,
                field=field_label,
                limit=limit,
                actual=len(value),
                link=link,
            ))
