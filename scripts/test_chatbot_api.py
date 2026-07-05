import requests
import json

# Test the chatbot API endpoint
url = "http://localhost:5000/api/analyze_voucher_message"

test_cases = [
    "Received 5000 from AL AIN FARMS FOR LIVESTOCK PRODUCTION by cash",
    "Paid 5000 to AL AIN FARMS FOR LIVESTOCK PRODUCTION by bank today",
    "Deposited to bank from cash 5000",
    "Salary expense incurred to Salary payable 5000"
]

for msg in test_cases:
    print(f"\n{'='*70}")
    print(f"MESSAGE: {msg}")
    print('='*70)
    
    try:
        response = requests.post(url, json={"message": msg}, headers={"Content-Type": "application/json"})
        data = response.json()
        
        if data.get('success'):
            print("AI Response:")
            print(json.dumps(data['data'], indent=2))
        else:
            print(f"ERROR: {data.get('message')}")
    except Exception as e:
        print(f"EXCEPTION: {e}")
