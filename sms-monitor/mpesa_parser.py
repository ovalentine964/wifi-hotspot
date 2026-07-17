"""
M-Pesa SMS Parsing Module
Handles all known M-Pesa SMS formats from Safaricom.
"""

import re
from datetime import datetime
from typing import Optional, Dict, Any


# Transaction code pattern: 8-12 alphanumeric chars, must start with a letter
# M-Pesa codes always start with a letter (e.g. QHK4BXYZ01)
TX_CODE_PATTERN = r'[A-Z][A-Z0-9]{7,11}'
# Kenyan phone numbers: 07xx, 01xx, +2547xx, +2541xx, 2547xx
PHONE_PATTERN = r'(?:\+?254|0)[17]\d{8}'
# Amount pattern: Ksh with optional commas and decimal
AMOUNT_PATTERN = r'Ksh([\d,]+(?:\.\d{2})?)'
# Date pattern: d/m/yy or dd/mm/yy
DATE_PATTERN = r'(\d{1,2}/\d{1,2}/\d{2})'
# Time pattern: h:mm AM/PM
TIME_PATTERN = r'(\d{1,2}:\d{2}\s*(?:AM|PM))'


def parse_amount(raw: str) -> float:
    """Parse Ksh amount string like '1,234.50' into float."""
    cleaned = raw.replace(",", "")
    return float(cleaned)


def parse_datetime(date_str: str, time_str: str) -> str:
    """Combine M-Pesa date and time into ISO format.
    
    M-Pesa dates are like 17/7/26, times like 2:30 PM.
    We assume 20xx for the year.
    """
    try:
        dt_str = f"{date_str} {time_str}"
        # M-Pesa uses 2-digit year
        dt = datetime.strptime(dt_str, "%d/%m/%y %I:%M %p")
        return dt.isoformat()
    except ValueError:
        return datetime.now().isoformat()


def parse_mpesa_sms(body: str, owner_phone: str = "") -> Optional[Dict[str, Any]]:
    """Parse an M-Pesa SMS and return structured data.
    
    Returns None if the SMS is not a recognized M-Pesa format.
    
    Returns dict with:
        - tx_code: str (e.g. "QHK4BXYZ01")
        - amount: float
        - direction: str ("received" | "sent" | "paid")
        - counterparty_name: str or None
        - counterparty_phone: str or None
        - timestamp: str (ISO format)
        - balance: float or None
        - raw_sms: str
    """
    if not body:
        return None

    # Must contain a transaction code
    code_match = re.search(TX_CODE_PATTERN, body)
    if not code_match:
        return None

    # Must contain M-Pesa keywords
    mpesa_keywords = ["Confirmed", "M-Pesa", "mpesa", "Mpesa"]
    if not any(kw in body for kw in mpesa_keywords):
        return None

    tx_code = code_match.group(0)

    # Determine direction
    body_lower = body.lower()
    if "received from" in body_lower:
        direction = "received"
    elif "sent to" in body_lower:
        direction = "sent"
    elif "paid to" in body_lower:
        direction = "paid"
    elif "withdrawn" in body_lower:
        direction = "withdrawn"
    else:
        direction = "unknown"

    # Parse amount
    amount_match = re.search(AMOUNT_PATTERN, body, re.IGNORECASE)
    amount = parse_amount(amount_match.group(1)) if amount_match else 0.0

    # Parse counterparty phone
    phone_match = re.search(PHONE_PATTERN, body)
    counterparty_phone = phone_match.group(0) if phone_match else None

    # Normalize phone to 07xx format
    if counterparty_phone:
        if counterparty_phone.startswith("+254"):
            counterparty_phone = "0" + counterparty_phone[4:]
        elif counterparty_phone.startswith("254"):
            counterparty_phone = "0" + counterparty_phone[3:]

    # Parse counterparty name
    counterparty_name = _extract_name(body, direction)

    # Parse datetime
    date_match = re.search(DATE_PATTERN, body)
    time_match = re.search(TIME_PATTERN, body)
    if date_match and time_match:
        timestamp = parse_datetime(date_match.group(1), time_match.group(1))
    else:
        timestamp = datetime.now().isoformat()

    # Parse balance
    balance = None
    balance_match = re.search(r'(?:M-Pesa|Account)\s+balance\s+is\s+' + AMOUNT_PATTERN, body, re.IGNORECASE)
    if balance_match:
        balance = parse_amount(balance_match.group(1))

    return {
        "tx_code": tx_code,
        "amount": amount,
        "direction": direction,
        "counterparty_name": counterparty_name,
        "counterparty_phone": counterparty_phone,
        "timestamp": timestamp,
        "balance": balance,
        "raw_sms": body,
    }


def _extract_name(body: str, direction: str) -> Optional[str]:
    """Extract counterparty name from SMS body."""
    if direction == "received":
        # "received from JOHN DOE 0712345678"
        m = re.search(r'received\s+from\s+([A-Z][A-Z\s.]+?)(?:\s+\+?\d{10,12}|\s+\.|\s+on\s)', body, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
    elif direction == "sent":
        # "sent to JOHN DOE 0712345678"
        m = re.search(r'sent\s+to\s+([A-Z][A-Z\s.]+?)(?:\s+\+?\d{10,12}|\s+\.|\s+on\s)', body, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
    elif direction == "paid":
        # "paid to WIFI HOTSPOT. on 17/7/26"
        m = re.search(r'paid\s+to\s+([A-Z][A-Z\s.]+?)\.\s+on\s', body, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # "paid to WIFI HOTSPOT. M-Pesa balance"
        m = re.search(r'paid\s+to\s+([A-Z][A-Z\s.]+?)\.\s', body, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # Generic fallback: look for capitalized words between keywords and phone/date
    # Handle +254 prefix phones too
    m = re.search(r'(?:from|to)\s+([A-Z][A-Z\s.]{2,30}?)(?:\s+\+?\d|\s+\.)', body)
    if m:
        return m.group(1).strip().rstrip(".")
    return None


def is_valid_mpesa_sms(body: str) -> bool:
    """Quick check if an SMS looks like an M-Pesa message."""
    if not body:
        return False
    has_code = bool(re.search(TX_CODE_PATTERN, body))
    has_keyword = any(kw in body for kw in ["Confirmed", "M-Pesa", "mpesa"])
    return has_code and has_keyword


if __name__ == "__main__":
    # Test parsing with sample messages
    test_messages = [
        "QHK4BXYZ01 Confirmed. Ksh50.00 received from JOHN DOE 0712345678 on 17/7/26 at 2:30 PM. M-Pesa balance is Ksh1,234.50.",
        "QHK4BXYZ01 Confirmed. Ksh50.00 sent to JOHN DOE 0712345678 on 17/7/26 at 2:30 PM.",
        "QHK4BXYZ01 Confirmed. Ksh50.00 paid to WIFI HOTSPOT. on 17/7/26 at 2:30 PM. M-Pesa balance is Ksh1,234.50.",
        "QHK4BXYZ01 Confirmed. Ksh50.00 received from JOHN DOE 0712345678. Account balance is Ksh1,234.50.",
        "QHK4BXYZ01 Confirmed. Ksh1,500.00 sent to JANE SMITH +254712345678 on 17/7/26 at 10:15 AM.",
    ]
    for msg in test_messages:
        result = parse_mpesa_sms(msg)
        print(f"\n--- SMS ---\n{msg}")
        if result:
            print(f"  Code: {result['tx_code']}")
            print(f"  Amount: {result['amount']}")
            print(f"  Direction: {result['direction']}")
            print(f"  Name: {result['counterparty_name']}")
            print(f"  Phone: {result['counterparty_phone']}")
            print(f"  Time: {result['timestamp']}")
            print(f"  Balance: {result['balance']}")
        else:
            print("  NOT PARSED")
