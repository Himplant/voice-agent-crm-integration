import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Environment variables for Zoho API
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
ZOHO_ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN")

# Refresh Zoho OAuth token
def refresh_access_token():
    url = "https://accounts.zoho.com/oauth/v2/token"
    data = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        new_token = response.json()["access_token"]
        global ZOHO_ACCESS_TOKEN
        ZOHO_ACCESS_TOKEN = new_token
        print("Access token refreshed successfully.")
        return new_token
    else:
        print(f"Failed to refresh access token: {response.status_code} - {response.text}")
    return None

# Search for Contact or Lead by phone number
def search_module(module, phone):
    url = f"https://www.zohoapis.com/crm/v2/{module}/search"
    headers = {
        "Authorization": f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
    }
    params = { "phone": phone }
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 401:
        refresh_access_token()
        headers["Authorization"] = f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
        response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        if "data" in data and len(data["data"]) > 0:
            return {
                "id": data["data"][0].get("id"),
                "First_Name": data["data"][0].get("First_Name", ""),
                "Last_Name": data["data"][0].get("Last_Name", ""),
                "Email": data["data"][0].get("Email", ""),
                "Surgeon_Name": data["data"][0].get("Pick_Your_Surgeon") or data["data"][0].get("Surgeon Name", ""),
                "Lead_Status": data["data"][0].get("Lead_Status", "") if module == "Leads" else "",
                "module": module
            }
    return None

# Get Notes for a given record (optional, used by lookup)
def get_notes(module, record_id, max_notes=3):
    url = f"https://www.zohoapis.com/crm/v2/{module}/{record_id}/Notes"
    headers = {
        "Authorization": f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
    }
    params = {
        "per_page": max_notes,
        "page": 1,
        "sort_order": "desc"
    }
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 401:
        refresh_access_token()
        headers["Authorization"] = f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
        response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        notes = []
        for note in data.get("data", []):
            notes.append({
                "title": note.get("Note_Title", ""),
                "content": note.get("Note_Content", ""),
                "created_time": note.get("Created_Time", "")
            })
        return notes
    return []

# /lookup endpoint for Voice Agent
@app.route("/lookup", methods=["POST"])
def lookup():
    data = request.json
    phone = data.get("phone")
    if not phone:
        return jsonify({"error": "Phone number is required"}), 400

    # Prefer Leads first, fallback to Contacts
    record = search_module("Leads", phone) or search_module("Contacts", phone)

    notes = []
    if record:
        notes = get_notes(record.get("module"), record.get("id"))

    if record:
        return jsonify({
            "first_name": record.get("First_Name", ""),
            "last_name": record.get("Last_Name", ""),
            "email": record.get("Email", ""),
            "surgeon": record.get("Surgeon_Name", ""),
            "lead_status": record.get("Lead_Status", ""),
            "record_id": record.get("id"),
            "module": record.get("module"),
            "notes": notes
        })
    else:
        return jsonify({"error": "No match found"}), 404

# /update_status endpoint for Voice Agent followup
@app.route("/update_status", methods=["POST"])
def update_status():
    data = request.json
    phone = data.get("phone")
    record_id = data.get("record_id")
    module = data.get("module")

    # If no record_id/module provided → search by phone
    if not (record_id and module):
        if not phone:
            return jsonify({"error": "Either phone or record_id/module must be provided"}), 400
        record = search_module("Leads", phone) or search_module("Contacts", phone)
        if not record:
            return jsonify({"error": "No matching record found for provided phone number"}), 404
        record_id = record.get("id")
        module = record.get("module")
        lead_status = record.get("Lead_Status", "")
    else:
        # If record_id/module provided → fetch record details to get Lead_Status
        url = f"https://www.zohoapis.com/crm/v2/{module}/{record_id}"
        headers = {
            "Authorization": f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 401:
            refresh_access_token()
            headers["Authorization"] = f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
            response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return jsonify({"error": f"Failed to fetch record details: {response.status_code}"}), 500
        data_rec = response.json().get("data", [{}])[0]
        lead_status = data_rec.get("Lead_Status", "") if module == "Leads" else ""

    # Determine new AI_Agent_Status
    if lead_status == "No Questionnaire":
        new_status = "Questionnaire Requested"
    elif lead_status in ["Eligible", "Circumcision", "Circumcision-Therapy", "Therapy"]:
        new_status = "Free Consultation Booking"
    else:
        new_status = "Paid Consultation Booking"

    # PATCH update AI_Agent_Status
    update_url = f"https://www.zohoapis.com/crm/v2/{module}/{record_id}"
    headers = {
        "Authorization": f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
    }
    update_payload = {
        "data": [
            {
                "AI_Agent_Status": new_status
            }
        ]
    }
    update_response = requests.patch(update_url, headers=headers, json=update_payload)
    if update_response.status_code == 401:
        refresh_access_token()
        headers["Authorization"] = f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
        update_response = requests.patch(update_url, headers=headers, json=update_payload)
    if update_response.status_code != 200:
        return jsonify({"error": f"Failed to update AI_Agent_Status: {update_response.status_code}"}), 500

    # POST a generic Note
    notes_url = f"https://www.zohoapis.com/crm/v2/{module}/{record_id}/Notes"
    notes_payload = {
        "data": [
            {
                "Note_Title": "Voice Agent Contact",
                "Note_Content": f"Patient contacted AI Voice Agent on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}. Interaction recorded."
            }
        ]
    }
    notes_response = requests.post(notes_url, headers=headers, json=notes_payload)
    if notes_response.status_code == 401:
        refresh_access_token()
        headers["Authorization"] = f"Zoho-oauthtoken {ZOHO_ACCESS_TOKEN}"
        notes_response = requests.post(notes_url, headers=headers, json=notes_payload)
    # FIXED HERE: accept 201 as success
    if notes_response.status_code not in [200, 201]:
        return jsonify({"error": f"Failed to post Note: {notes_response.status_code}"}), 500

    return jsonify({
        "result": "success",
        "ai_agent_status_set_to": new_status,
        "note_added": True,
        "record_id": record_id,
        "module": module
    })

# Root endpoint
@app.route("/", methods=["GET"])
def home():
    return "Himplant Lookup API is running."
