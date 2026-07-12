import PyInstaller.__main__
import os
import sys
import shutil
import traceback

# Add current directory to path
sys.path.append(os.getcwd())

print(f"Current working directory: {os.getcwd()}")

# Clean up
if os.path.exists("dist"):
    try:
        shutil.rmtree("dist")
    except Exception as e:
        print(f"Could not remove dist: {e}")

if os.path.exists("build"):
    try:
        shutil.rmtree("build")
    except Exception as e:
        print(f"Could not remove build: {e}")

if os.path.exists("TalabMartApp.spec"):
    try:
        os.remove("TalabMartApp.spec")
    except Exception as e:
        print(f"Could not remove spec file: {e}")

try:
    print("Starting PyInstaller build...")
    
    # Define paths
    templates_path = "templates"
    static_path = "static"
    
    # Verify paths exist
    if not os.path.exists(templates_path):
        print(f"WARNING: {templates_path} not found!")
    if not os.path.exists(static_path):
        print(f"WARNING: {static_path} not found!")

    PyInstaller.__main__.run([
        'app.py',
        '--name=TalabMartApp',
        '--onefile',
        '--clean',
        '--console',
        f'--add-data={templates_path};templates',
        f'--add-data={static_path};static',
        
        # Hidden imports
        '--hidden-import=flask',
        '--hidden-import=pandas',
        '--hidden-import=openpyxl',
        '--hidden-import=xlsxwriter',
        '--hidden-import=engineio.async_drivers.threading',
        
        # Explicitly point to paths if needed (usually not for simple app structure)
        '--paths=.',
    ])
    
    print("Build process completed.")
    
    if os.path.exists(os.path.join("dist", "TalabMartApp.exe")):
        print("SUCCESS: TalabMartApp.exe created in 'dist' folder.")
    else:
        print("FAILURE: TalabMartApp.exe not found in 'dist' folder.")
        # List contents of current directory to see if it ended up somewhere else
        print("Contents of current directory:", os.listdir("."))

except Exception:
    traceback.print_exc()
