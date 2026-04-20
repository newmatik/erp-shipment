#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2020, Newmatik and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import escape_html
import re


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


LETMESHIP_STREET_LIMIT = 35


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
    """Pack a sequence of text parts (joined with ", ") into two length-limited
    slots, splitting on word boundaries when possible.

    Returns (slot1, slot2). Content that can't fit the two slots is dropped;
    that case is then caught by validate_letmeship_address which throws a
    user-facing, translatable error rather than silently truncating.
    """
    text = ", ".join(p for p in parts if p)
    if not text:
        return "", ""
    slot1, rest = _split_at_word_boundary(text, limit)
    slot2, _dropped = _split_at_word_boundary(rest, limit)
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
        if "address_line1_con" in address:
            address.address_line1_con = ""

    split_happened = (
        street != original_line1
        or address.address_line2 != original_line2
        or address.get("address_line1_con")
    )
    if split_happened:
        audit_message = (
            f"Address: {address.get('name') or '?'} (role={role or '?'})\n"
            f"line1: {original_line1!r} -> {street!r}\n"
            f"line2: {original_line2!r} -> {address.address_line2!r}\n"
            f"address_line1_con: {address.get('address_line1_con')!r}"
        )
        try:
            frappe.log_error(
                message=audit_message,
                title="LetMeShip address auto-split",
            )
        except Exception:
            # frappe.log_error writes an Error Log DocType row, which can fail
            # (mid-rollback, DB issues, Frappe's 140-char title limit, etc.).
            # Fall back to the file logger so the audit trail isn't lost
            # silently; this is a best-effort, never-raise path.
            try:
                frappe.logger("letmeship", allow_site=True).exception(
                    "LetMeShip address auto-split audit: %s", audit_message
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
    # Escape the Address name for HTML. Frappe DocType names can contain
    # characters like ' or & that would break the href attribute quoting and
    # allow injection into the rendered frappe.throw dialog. Escaping once
    # protects both the href (attribute context) and the visible anchor text.
    name = address.get("name") or ""
    escaped_name = escape_html(name)
    link = "<a href='/app/address/{0}'>{1}</a>".format(escaped_name, escaped_name)
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
