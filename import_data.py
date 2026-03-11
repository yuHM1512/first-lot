import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text
import models
from database import SessionLocal, engine
import os
import gspread
from google.oauth2.service_account import Credentials

# Mapping Excel/Sheet columns to Database fields
COLUMN_MAPPING = {
    'SER No.': 'ser_no',
    'Item': 'item',
    'Item Description': 'item_description',
    'Model No.': 'model_no',
    'Model Description': 'model_description',
    'FG CC code': 'fg_cc_code',
    'Season': 'season',
    'Passion Brand': 'passion_brand',
    'Match/Contrast information': 'match_contrast_info',
    'PL Name': 'pl_name',
    'Unit': 'unit',
    'FG Sample Pieces': 'fg_sample_pieces',
    'CPT Supplier': 'cpt_supplier',
    'Remark': 'remark',
    'Expected date of arrival': 'expected_arrival_date',
    'PICK UP': 'pick_up',
    'Update ETD': 'update_etd',
    'Courrier Number': 'courrier_number',
    'Creator': 'creator',
    'PO Date': 'po_date',
    'FG Supplier': 'fg_supplier',
    'Sample Type': 'sample_type',
    'Test': 'test',
    'Color': 'color',
    'ActualDeliveryQty': 'actual_delivery_qty',
    'Actual Delivery Date': 'actual_delivery_date',
    'Supplier Code': 'supplier_code',
    'Supplier DPP': 'supplier_dpp',
    'Supplier Process': 'supplier_process',
    'Address': 'address',
    'Status': 'status',
    'KT': 'kt',
    'MD': 'md',
    'Email': 'email_status'
}

def process_and_save_data(df: pd.DataFrame, db: Session):
    # Ensure tables exist
    models.Base.metadata.create_all(bind=engine)
    
    # Truncate existing data for a fresh sync
    db.execute(text('TRUNCATE TABLE first_lot_requests'))
    
    # Clean mapping keys in df to match COLUMN_MAPPING (remove leading/trailing spaces)
    df.columns = [c.strip() if isinstance(c, str) else c for c in df.columns]
    
    # Rename columns to db field names
    df = df.rename(columns=COLUMN_MAPPING)
    
    count = 0
    for _, row in df.iterrows():
        # Get data as dictionary with None for NaNs
        data = {k: (None if pd.isna(v) else str(v).strip()) for k, v in row.to_dict().items()}
        
        # Only keep fields that exist in the COLUMN_MAPPING values (db fields)
        db_data = {k: v for k, v in data.items() if k in COLUMN_MAPPING.values()}
        
        if not db_data.get('item') and not db_data.get('ser_no'):
            continue

        # Sync item_code for linking
        if 'item' in db_data:
            db_data['item_code'] = db_data['item']
        
        # Numeric string cleanup
        for key in ['item', 'item_code']:
            if key in db_data and db_data[key] and db_data[key].endswith('.0'):
                db_data[key] = db_data[key][:-2]

        db_request = models.FirstLotRequest(**db_data)
        db.add(db_request)
        count += 1
    
    db.commit()
    return count

def import_from_excel(file_path: str):
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return

    print(f"Reading Excel: {file_path}...")
    df = pd.read_excel(file_path)
    
    db = SessionLocal()
    try:
        count = process_and_save_data(df, db)
        print(f"Successfully imported {count} rows into first_lot_requests.")
    except Exception as e:
        db.rollback()
        print(f"An error occurred: {e}")
    finally:
        db.close()

def sync_from_google_sheets():
    # Configuration
    JSON_KEY = 'credentials_m29.json'
    SPREADSHEET_ID = "136Nx00jUL24pG82A1FU0inds4HbCG1wszTuB9v-SUak"
    SHEET_NAME = "data_fabric"
    
    print(f"Syncing from Google Sheets: {SHEET_NAME}...")
    
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(JSON_KEY, scopes=scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        db = SessionLocal()
        try:
            count = process_and_save_data(df, db)
            return {"status": "success", "count": count}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
            
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

def sync_supplier_emails_from_sheet():
    """Sync supplier_email table from Google Sheet 'supplier_email' range A1:C"""
    JSON_KEY = 'credentials_m29.json'
    SPREADSHEET_ID = "136Nx00jUL24pG82A1FU0inds4HbCG1wszTuB9v-SUak"
    SHEET_NAME = "supplier_email"
    
    print(f"Syncing supplier emails from Google Sheets: {SHEET_NAME}...")
    
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(JSON_KEY, scopes=scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        # Get columns A:G
        rows = sheet.get('A1:G')
        
        if not rows or len(rows) < 2:
            return {"status": "error", "message": "No data found in sheet"}
        
        data_rows = rows[1:]  # Rest is data
        
        db = SessionLocal()
        try:
            from sqlalchemy import text
            # Clear existing data
            db.execute(text('DELETE FROM supplier_email'))
            db.execute(text('DELETE FROM staff_emails'))
            
            # For SupplierEmail (A:C)
            for row in data_rows:
                if len(row) >= 3:
                    supplier_name = str(row[0]).strip() if row[0] else ""
                    cpt_supplier = str(row[1]).strip() if row[1] else ""
                    email = str(row[2]).strip() if row[2] else ""
                    
                    if cpt_supplier and email:
                        supplier = models.SupplierEmail(
                            supplier_name=supplier_name,
                            cpt_supplier=cpt_supplier,
                            email=email
                        )
                        db.add(supplier)
            
            # For StaffEmail - KT (D:E)
            seen_staff = set()
            for row in data_rows:
                if len(row) >= 5:
                    kt_name = str(row[3]).strip() if row[3] else ""
                    kt_email = str(row[4]).strip() if row[4] else ""
                    if kt_name and kt_email and (f"KT|{kt_name}|{kt_email}") not in seen_staff:
                        db.add(models.StaffEmail(role="KT", name=kt_name, email=kt_email))
                        seen_staff.add(f"KT|{kt_name}|{kt_email}")
                
                if len(row) >= 7:
                    md_name = str(row[5]).strip() if row[5] else ""
                    md_email = str(row[6]).strip() if row[6] else ""
                    if md_name and md_email and (f"MD|{md_name}|{md_email}") not in seen_staff:
                        db.add(models.StaffEmail(role="MD", name=md_name, email=md_email))
                        seen_staff.add(f"MD|{md_name}|{md_email}")
            
            db.commit()
            return {"status": "success", "message": "Synced Suppliers, KT and MD emails"}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
            
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

if __name__ == "__main__":
    # Default to Excel import if run directly, or use Sheets if specified
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--sheets":
        result = sync_from_google_sheets()
        print(result)
    elif len(sys.argv) > 1 and sys.argv[1] == "--sync-emails":
        result = sync_supplier_emails_from_sheet()
        print(result)
    else:
        excel_file = "first_lot_data.xlsx"
        import_from_excel(excel_file)
