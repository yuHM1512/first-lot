import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text
import models
from database import SessionLocal, engine
import os
import datetime
import argparse

# Mapping Excel columns to Database fields for Master Data
# A: Fabric name, B: Model code, C: Fabric supplier, D: Usable Width, E: Unit, 
# F: Item code, G: Color, H: Description, I: Provider, J: received date, 
# K: Using time (years), L: Color Test Report received date, M: MTSR received date

COLUMN_MAPPING = {
    'Fabric name': 'fabric_name',
    'Model code': 'model_code',
    'Fabric supplier': 'fabric_supplier',
    'Usable Width': 'usable_width',
    'Unit': 'unit',
    'Item code': 'item_code',
    'Color': 'color',
    'Description': 'description',
    'Provider': 'provider',
    'received date': 'received_date',
    'Using time (years)': 'using_time_years',
    'Color Test Report received date': 'color_test_report_received_date',
    'MTSR received date': 'mtsr_received_date'
}

def import_master_excel(file_path: str):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    print(f"Reading {file_path}...")
    # Load all columns specified in mapping
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return
    
    # Rename columns based on mapping
    df = df.rename(columns=COLUMN_MAPPING)

    # Clean data: drop rows with no item_code and handle duplicates
    df = df.dropna(subset=['item_code'])
    df = df.drop_duplicates(subset=['item_code'], keep='last')
    
    db = SessionLocal()
    try:
        # Create/Update tables
        models.Base.metadata.create_all(bind=engine)
        
        count = 0
        for _, row in df.iterrows():
            # Convert row to dictionary
            row_dict = row.to_dict()
            
            # Extract relevant fields and handle data types
            data = {}
            for excel_col, db_field in COLUMN_MAPPING.items():
                val = row_dict.get(db_field)
                
                if pd.isna(val):
                    data[db_field] = None
                elif "date" in db_field:
                    try:
                        # Handle varied date formats
                        if isinstance(val, (datetime.datetime, datetime.date)):
                            data[db_field] = val.date() if isinstance(val, datetime.datetime) else val
                        else:
                            data[db_field] = pd.to_datetime(val).date()
                    except:
                        data[db_field] = None
                elif db_field == "using_time_years":
                    try:
                        data[db_field] = int(val)
                    except:
                        data[db_field] = 3 # Default
                else:
                    data[db_field] = str(val)

            if not data.get('item_code'):
                continue

            # Check if item_code already exists, if so update, else create
            existing = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == data['item_code']).first()
            if existing:
                for key, value in data.items():
                    setattr(existing, key, value)
            else:
                db_master = models.FirstLotMaster(**data)
                db.add(db_master)
            
            count += 1
        
        db.commit()
        print(f"Successfully imported/updated {count} records in first_lot_master.")
    except Exception as e:
        db.rollback()
        print(f"An error occurred: {e}")
    finally:
        db.close()

def reset_master_from_excel(file_path: str, clear_history: bool = False):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    db = SessionLocal()
    try:
        models.Base.metadata.create_all(bind=engine)
        db.execute(text("DELETE FROM first_lot_master"))
        if clear_history:
            db.execute(text("DELETE FROM first_lot_history"))
        db.commit()
        print("Cleared first_lot_master" + (" and first_lot_history" if clear_history else "") + ".")
    except Exception as e:
        db.rollback()
        print(f"Error clearing tables: {e}")
        db.close()
        return
    finally:
        db.close()

    import_master_excel(file_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import or reset first_lot_master from Excel.")
    parser.add_argument("--file", default="first_lot_store.xlsx", help="Path to Excel file")
    parser.add_argument("--reset", action="store_true", help="Delete all rows in first_lot_master before import")
    parser.add_argument("--clear-history", action="store_true", help="Also delete all rows in first_lot_history")
    args = parser.parse_args()

    if args.reset:
        reset_master_from_excel(args.file, clear_history=args.clear_history)
    else:
        import_master_excel(args.file)
