
import sqlite3
import os
import json

DB_PATH = os.path.join(os.path.abspath("."), "Talab_Mart.db")

def fix_config():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Get current config
    cursor.execute("SELECT allowed_groups FROM voucher_type_configs WHERE voucher_type = 'Receipt' AND side = 'Debit'")
    row = cursor.fetchone()
    if row:
        current_groups_json = row[0]
        try:
            current_groups = json.loads(current_groups_json)
        except:
            current_groups = []
        
        print(f"Current allowed groups (Debit): {current_groups}")
        
        # Add Cash (G005) and Bank (G006)
        if "G005" not in current_groups:
            current_groups.append("G005")
        if "G006" not in current_groups:
            current_groups.append("G006")
            
        new_json = json.dumps(current_groups)
        print(f"New allowed groups (Debit): {current_groups}")
        
        cursor.execute("UPDATE voucher_type_configs SET allowed_groups = ? WHERE voucher_type = 'Receipt' AND side = 'Debit'", (new_json,))
        conn.commit()
        print("Updated configuration.")
        
    else:
        print("No config row found for Receipt Debit. Creating one.")
        allowed_groups = ["G005", "G006"]
        new_json = json.dumps(allowed_groups)
        cursor.execute("INSERT INTO voucher_type_configs (voucher_type, side, allowed_groups, allowed_sub_groups) VALUES ('Receipt', 'Debit', ?, '[]')", (new_json,))
        conn.commit()
        print("Created configuration.")

    conn.close()

if __name__ == "__main__":
    fix_config()
