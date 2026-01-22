# languages.py

TEXTS = {
    'en': {
        # ... (keep your existing keys) ...
        'btn_profile': "👤 My Profile",
        'profile_header': "👤 *User Profile*\n\n📞 Phone: `{}`\n🗣️ Language: English\n📍 Location: {}",
        'btn_switch_lang': "🔄 Switch Language",
        'btn_edit_phone': "✏️ Change Phone",
        'location_set': "Set ✅",
        'location_not_set': "Not Set ❌",
        'order_id_label': "Order No", # Label for the long ID
        # ...
    },
    'am': {
        # ... (keep your existing keys) ...
        'btn_profile': "👤 የእኔ መረጃ (Profile)",
        'profile_header': "👤 *የግል መረጃ*\n\n📞 ስልክ: `{}`\n🗣️ ቋንቋ: አማርኛ\n📍 አድራሻ: {}",
        'btn_switch_lang': "🔄 ቋንቋ ቀይር",
        'btn_edit_phone': "✏️ ስልክ ለመቀየር",
        'location_set': "ተመዝግቧል ✅",
        'location_not_set': "አልተመዘገበም ❌",
        'order_id_label': "ትዕዛዝ ቁጥር",
        # ...
    }
}

def get_text(lang, key):
    return TEXTS.get(lang, TEXTS['en']).get(key, key)
