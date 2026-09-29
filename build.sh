#!/usr/bin/env bash
# exit on error
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt || {
  echo "Failed to install dependencies. Trying with --no-deps..."
  pip install --no-deps -r requirements.txt
}

# Create necessary directories
mkdir -p static/images/doctors
mkdir -p instance
mkdir -p data

# Set permissions
chmod -R 755 static
chmod -R 755 instance
chmod -R 755 data

# Initialize the database
echo "Initializing database..."
python -c "from database import init_db; init_db()"

# Verify database was created and tables exist
echo "Verifying database..."
python -c "
import sqlite3
import os
from database import get_db_path

db_path = get_db_path()
print(f'Database path: {db_path}')
print(f'Database exists: {os.path.exists(db_path)}')

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if tables exist
    cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"')
    tables = cursor.fetchall()
    print(f'Tables in database: {tables}')
    
    # Check if staff table exists
    cursor.execute('SELECT * FROM staff LIMIT 1')
    staff = cursor.fetchall()
    print(f'Staff records: {staff}')
    
    conn.close()
" 