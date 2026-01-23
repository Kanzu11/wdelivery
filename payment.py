import aiohttp
import uuid
import logging

logger = logging.getLogger(__name__)

async def initialize_chapa_payment(amount: float, email: str, first_name: str, last_name: str, phone_number: str, callback_url: str, return_url: str, chapa_token: str):
    """
    Initialize a Chapa payment transaction
    
    Args:
        amount: Amount in ETB
        email: Customer email
        first_name: Customer first name
        last_name: Customer last name
        phone_number: Customer phone number (10 digits: 09xxxxxxxx or 07xxxxxxxx)
        callback_url: URL to receive payment callback
        return_url: URL to redirect after payment
        chapa_token: Chapa authorization token
    
    Returns:
        dict with checkout_url and tx_ref, or None if failed
    """
    url = "https://api.chapa.co/v1/transaction/initialize"
    
    # Generate unique transaction reference
    tx_ref = f"werabe-{uuid.uuid4().hex[:12]}"
    
    # Format phone number (ensure it's 10 digits)
    if phone_number.startswith('+251'):
        phone_number = '0' + phone_number[4:]
    elif phone_number.startswith('251'):
        phone_number = '0' + phone_number[3:]
    
    # Ensure phone is 10 digits
    if len(phone_number) > 10:
        phone_number = phone_number[-10:]
    
    payload = {
        "amount": str(int(amount)),
        "currency": "ETB",
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "phone_number": phone_number,
        "tx_ref": tx_ref,
        "callback_url": callback_url,
        "return_url": return_url,
        "customization": {
            "title": "Werabe Delivery Service",
            "description": "Payment for your order"
        }
    }
    
    # Use token as Bearer token
    # Note: If your token format is different (e.g., Telegram payment provider token),
    # you may need to adjust the authorization header format
    # Standard Chapa keys start with CHASECK_, but Telegram integration may use different format
    headers = {
        'Authorization': f'Bearer {chapa_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get('status') == 'success':
                        return {
                            'checkout_url': data.get('data', {}).get('checkout_url'),
                            'tx_ref': tx_ref,
                            'status': 'success'
                        }
                    else:
                        logger.error(f"Chapa API error: {data}")
                        return None
                else:
                    error_text = await response.text()
                    logger.error(f"Chapa API HTTP error {response.status}: {error_text}")
                    return None
    except Exception as e:
        logger.error(f"Failed to initialize Chapa payment: {e}")
        return None


async def verify_chapa_payment(tx_ref: str, chapa_token: str):
    """
    Verify a Chapa payment transaction
    
    Args:
        tx_ref: Transaction reference
        chapa_token: Chapa authorization token
    
    Returns:
        dict with payment status, or None if failed
    """
    url = f"https://api.chapa.co/v1/transaction/verify/{tx_ref}"
    
    headers = {
        'Authorization': f'Bearer {chapa_token}',
        'Content-Type': 'application/json'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    error_text = await response.text()
                    logger.error(f"Chapa verification HTTP error {response.status}: {error_text}")
                    return None
    except Exception as e:
        logger.error(f"Failed to verify Chapa payment: {e}")
        return None
