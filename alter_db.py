from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))

with engine.begin() as conn:
    print("Altering table...")
    conn.execute(text("ALTER TABLE first_lot_requests ALTER COLUMN expected_arrival_date TYPE DATE USING (CASE WHEN expected_arrival_date = '' THEN NULL ELSE expected_arrival_date::date END);"))
    print("Altered successfully.")
