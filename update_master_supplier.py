import pandas as pd
from sqlalchemy.orm import Session
import models
from database import SessionLocal
import os

def update_fabric_supplier(file_path: str):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    print(f"Reading {file_path}...")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading Excel: {e}")
        return
    
    # Check if necessary columns exist
    if 'Item code' not in df.columns or 'Fabric supplier' not in df.columns:
        print("Missing required columns: 'Item code' or 'Fabric supplier'")
        return

    df = df.dropna(subset=['Item code'])
    df = df.drop_duplicates(subset=['Item code'], keep='last')
    
    db = SessionLocal()
    try:
        count = 0
        for _, row in df.iterrows():
            item_code = str(row['Item code']).strip()
            fabric_supplier = str(row['Fabric supplier']).strip() if pd.notna(row['Fabric supplier']) else None
            
            if not item_code:
                continue

            existing = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == item_code).first()
            if existing and existing.fabric_supplier != fabric_supplier:
                existing.fabric_supplier = fabric_supplier
                count += 1
        
        db.commit()
        print(f"Successfully updated {count} records for fabric_supplier.")
    except Exception as e:
        db.rollback()
        print(f"An error occurred: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    excel_file = "first_lot_store.xlsx"
    update_fabric_supplier(excel_file)
