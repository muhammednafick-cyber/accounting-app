import sys
import os

# Ensure the current directory is in the path so we can import modules
sys.path.append(os.getcwd())

from database import initialize_db

if __name__ == "__main__":
    print("Initializing database...")
    initialize_db()
    print("Done.")
