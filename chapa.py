import aiohttp
import logging
import uuid
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)

def format_phone_number(phone: str) -> str:
    """
    Format phone number to Telebirr format (09xxxxxxxx or 07xxxxxxxx).
    Removes country code, spaces, and ensures 10 digits.
    """
    # Remove any non-digit characters
    phone = ''.join(filter(str.isdigit, phone))
    
    # Remove country code if present (251, +251, 00251)
    if phone.startswith('251') and len(phone) > 10:
        phone = phone[3:]
    elif phone.startswith('00251') and len(phone) > 10:
        phone = phone[5:]
    
    # Remove leading 0 if present and length is 10
    if len(phone) == 10 and phone.startswith('0'):
        return phone
    
    # If 9 digits, add leading 0
    if len(phone) == 9:
        return '0' + phone
    
    # If already 10 digits, return as is
    if len(phone) == 10:
        return phone
    
    # If still not valid, return original (will be validated by Chapa)
    logger.warning(f"Phone number format may be invalid: {phone}")
    return phone

async def initialize_payment(
    amount: float,
    phone: str,
    tx_ref: Optional[str] = None,
    email: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
) -> Dict:
    """
    Initialize a Chapa Telebirr payment transaction using Direct Charge API.
    
    Args:
        amount: Amount in ETB
        phone: Customer phone number (will be formatted to 09xxxxxxxx or 07xxxxxxxx)
        tx_ref: Transaction reference (auto-generated if not provided)
        email: Customer email (optional)
        first_name: Customer first name (optional)
        last_name: Customer last name (optional)
    
    Returns:
        Dict with payment initialization response
    """
    if not tx_ref:
        tx_ref = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    
    # Format phone number for Telebirr
    formatted_phone = format_phone_number(phone)
    
    # Use Direct Charge API for Telebirr
    url = f"{config.CHAPA_BASE_URL}/charges?type=telebirr"
    headers = {
        "Authorization": f"Bearer {config.CHAPA_SECRET_KEY}"
    }
    
    # Create multipart form data
    data = aiohttp.FormData()
    data.add_field('amount', str(amount))
    data.add_field('currency', 'ETB')
    data.add_field('tx_ref', tx_ref)
    data.add_field('mobile', formatted_phone)
    
    if email:
        data.add_field('email', email)
    if first_name:
        data.add_field('first_name', first_name)
    if last_name:
        data.add_field('last_name', last_name)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers) as response:
                result = await response.json()
                if response.status == 200:
                    # For USSD-based payments (like Telebirr), the response contains status and message
                    if result.get("status") == "success":
                        return {
                            "success": True,
                            "tx_ref": tx_ref,
                            "message": result.get("message", "Payment initiated. Please check your phone for USSD prompt."),
                            "data": result.get("data", {})
                        }
                    else:
                        logger.error(f"Chapa Telebirr payment init failed: {result}")
                        return {
                            "success": False,
                            "error": result.get("message", "Payment initialization failed")
                        }
                else:
                    logger.error(f"Chapa API error: {response.status} - {result}")
                    return {
                        "success": False,
                        "error": result.get("message", f"API error: {response.status}")
                    }
    except Exception as e:
        logger.error(f"Chapa API error: {e}")
        return {
            "success": False,
            "error": str(e)
        }

async def verify_payment(tx_ref: str) -> Dict:
    """
    Verify a payment transaction using transaction reference.
    
    Args:
        tx_ref: Transaction reference
    
    Returns:
        Dict with payment verification response
    """
    url = f"{config.CHAPA_BASE_URL}/transaction/verify/{tx_ref}"
    headers = {
        "Authorization": f"Bearer {config.CHAPA_SECRET_KEY}"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                result = await response.json()
                if response.status == 200:
                    data = result.get("data", {})
                    status = data.get("status", "")
                    return {
                        "success": True,
                        "verified": status == "success",
                        "status": status,
                        "amount": data.get("amount"),
                        "currency": data.get("currency"),
                        "tx_ref": data.get("tx_ref")
                    }
                else:
                    logger.error(f"Chapa verification failed: {result}")
                    return {
                        "success": False,
                        "verified": False,
                        "error": result.get("message", "Verification failed")
                    }
    except Exception as e:
        logger.error(f"Chapa verification error: {e}")
        return {
            "success": False,
            "verified": False,
            "error": str(e)
        }
