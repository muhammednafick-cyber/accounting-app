import os
import re

TEMPLATE_DIR = r"d:\Accounting App with Import\web-accounting-app_with import v3\templates"

def update_templates():
    count = 0
    for root, dirs, files in os.walk(TEMPLATE_DIR):
        for file in files:
            if file.endswith(".html"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Regex to find forms with POST method
                # We want to inject the token immediately after the opening tag
                # Pattern matches <form ... method="POST" ... > (case insensitive)
                # We need to handle attributes before and after method="POST"
                
                pattern = re.compile(r'(<form\s+[^>]*method=["\']POST["\'][^>]*>)', re.IGNORECASE)
                
                if pattern.search(content):
                    # Check if csrf_token is already there to avoid duplicates
                    # This is a simple check, might not be perfect if token is far down
                    # But usually we put it at top.
                    
                    def replacer(match):
                        tag = match.group(1)
                        # Look ahead to see if token exists
                        post_match = content[match.end():match.end()+200]
                        if "csrf_token" in post_match:
                            return tag # Already has token probably
                        return tag + '\n    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>'
                    
                    new_content = pattern.sub(replacer, content)
                    
                    if new_content != content:
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        print(f"Updated {filepath}")
                        count += 1
                    else:
                        print(f"Skipped {filepath} (no changes or already present)")
                else:
                    # print(f"No POST forms in {filepath}")
                    pass

    print(f"Total files updated: {count}")

if __name__ == "__main__":
    update_templates()
