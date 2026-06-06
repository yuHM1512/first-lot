
import os
import psycopg2
from dotenv import load_dotenv
import re

load_dotenv()

def migrate():
    url = os.getenv("DATABASE_URL")
    # Parse DATABASE_URL: postgresql://user:pass@host:port/db
    pattern = r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<dbname>.+)"
    match = re.match(pattern, url)
    if not match:
        print("Invalid DATABASE_URL format")
        return

    conn = psycopg2.connect(**match.groupdict())
    cur = conn.cursor()
    
    try:
        # Check if columns exist
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='first_lot_requests' AND column_name='first_lot_received_status'")
        if not cur.fetchone():
            print("Adding first_lot_received_status...")
            cur.execute("ALTER TABLE first_lot_requests ADD COLUMN first_lot_received_status VARCHAR;")
            
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='first_lot_requests' AND column_name='resend_count'")
        if not cur.fetchone():
            print("Adding resend_count...")
            cur.execute("ALTER TABLE first_lot_requests ADD COLUMN resend_count INTEGER DEFAULT 0;")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='email_log' AND column_name='first_lot_received_status'")
        if cur.fetchone():
            print("Dropping first_lot_received_status from email_log...")
            cur.execute("ALTER TABLE email_log DROP COLUMN first_lot_received_status;")
            
        conn.commit()
        print("Migration complete!")
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    migrate()
