import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text
import models
from database import SessionLocal, engine
import os
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from difflib import SequenceMatcher
from statistics import mean

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
    'MD': 'md'
    # 'Email' column is intentionally excluded — email_status lives in EmailLog table, not FirstLotRequest
}

# Fields that should NOT be passed to FirstLotRequest (they live in other tables)
EXCLUDED_FROM_REQUEST = {'email_status', 'email_sent_at'}

RANK2_SPREADSHEET_ID = "1ZDFneXwtC3Pl-HmVGy-1L43_5H_4JRNGybbZyEh9pnE"
RANK2_YEARS = ["2026", "2025", "2024", "2023"]

def process_and_save_data(df: pd.DataFrame, db: Session):
    # Ensure tables exist
    models.Base.metadata.create_all(bind=engine)
    
    def normalize_ser_no(value):
        if value is None:
            return None
        ser = str(value).strip()
        if ser == "":
            return None
        if ser.endswith(".0"):
            ser = ser[:-2]
        return ser

    # Load existing ser_no to avoid duplicates on sync
    existing_ser_no = {
        normalize_ser_no(s) for (s,) in db.query(models.FirstLotRequest.ser_no)
        .filter(models.FirstLotRequest.ser_no.isnot(None))
        .all()
    }
    existing_ser_no.discard(None)
    
    # Clean mapping keys in df to match COLUMN_MAPPING (remove leading/trailing spaces)
    df.columns = [c.strip() if isinstance(c, str) else c for c in df.columns]
    
    # Rename columns to db field names
    df = df.rename(columns=COLUMN_MAPPING)
    
    # ONLY expected_arrival_date is a Date type in the model — all others are String
    DATE_FIELDS = {'expected_arrival_date'}
    from datetime import datetime
    
    count = 0
    for _, row in df.iterrows():
        # Convert NaN and empty/blank strings to None universally
        raw = row.to_dict()
        data = {}
        for k, v in raw.items():
            if isinstance(v, str):
                data[k] = None if v.strip() == '' else v.strip()
            elif pd.isna(v):
                data[k] = None
            else:
                data[k] = str(v).strip()
        
        # Only keep fields that exist in the COLUMN_MAPPING values (db fields) and are not excluded
        db_data = {k: v for k, v in data.items() if k in COLUMN_MAPPING.values() and k not in EXCLUDED_FROM_REQUEST}
        
        if not db_data.get('item') and not db_data.get('ser_no'):
            continue

        # Sync item_code for linking
        if 'item' in db_data:
            db_data['item_code'] = db_data['item']
        
        # Numeric string cleanup
        for key in ['item', 'item_code']:
            if key in db_data and db_data[key] and db_data[key].endswith('.0'):
                db_data[key] = db_data[key][:-2]
        if 'ser_no' in db_data and db_data['ser_no'] and db_data['ser_no'].endswith('.0'):
            db_data['ser_no'] = db_data['ser_no'][:-2]
                
        # Parse only actual Date columns — try multiple formats, fall back to None
        for date_field in DATE_FIELDS:
            val = db_data.get(date_field)
            if not val:
                db_data[date_field] = None
                continue
            parsed = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    parsed = datetime.strptime(val, fmt).date()
                    break
                except (ValueError, TypeError):
                    continue
            db_data[date_field] = parsed  # None if nothing matched

        # Add only if ser_no is new (preserve existing data continuity)
        ser_no = db_data.get('ser_no')
        if ser_no:
            ser_no_key = normalize_ser_no(ser_no)
            if not ser_no_key or ser_no_key in existing_ser_no:
                continue
            existing_ser_no.add(ser_no_key)
        else:
            # Skip rows without ser_no to avoid accidental duplicates
            continue

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
            
            db.commit()
            return {"status": "success", "message": "Synced Suppliers emails"}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
            
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

def _authorize_google_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file('credentials_m29.json', scopes=scope)
    return gspread.authorize(creds)

def _parse_percent_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('%', '').replace(',', '.').strip()
    try:
        return round(float(text), 2)
    except ValueError:
        return None

def _format_percent_value(value):
    if value is None:
        return "--"
    return f"{value:.1f}%"

def _normalize_customer_segment(value):
    text = str(value or "").strip().upper()
    if text in {"ALL", "TATCA", "TẤT CẢ", "TAT CA"}:
        return "ALL"
    if text == "DECATHLON":
        return "DECATHLON"
    return "KHAC"

def _classify_rank(score, thresholds):
    if score is None:
        return "-"
    rounded = round(score, 2)
    for threshold in thresholds:
        lower = threshold["from"]
        upper = threshold["to"]
        if rounded >= lower and rounded <= upper:
            return threshold["rank"]
    if rounded > 100:
        return thresholds[-1]["rank"] if thresholds else "A"
    return thresholds[0]["rank"] if thresholds else "D"

def _fetch_rank2_from_sheets():
    """Pull all Rank 2 data from Google Sheets. Returns dict ready for DB write."""
    client = _authorize_google_client()
    book = client.open_by_key(RANK2_SPREADSHEET_ID)

    thresholds_ws = book.worksheet("chi_tieu")
    threshold_rows = thresholds_ws.get_all_records()
    thresholds = []
    for row in threshold_rows:
        from_value = _parse_percent_value(row.get("Từ"))
        to_value = _parse_percent_value(row.get("Đến"))
        rank_value = str(row.get("Rank") or "").strip().upper()
        if from_value is None or to_value is None or not rank_value:
            continue
        thresholds.append({"from": from_value, "to": to_value, "rank": rank_value})
    thresholds = sorted(thresholds, key=lambda item: item["from"])

    years_payload = {}
    for year in RANK2_YEARS:
        try:
            year_ws = book.worksheet(year)
        except gspread.WorksheetNotFound:
            continue
        raw_rows = year_ws.get_all_records()
        if not raw_rows:
            years_payload[year] = {"criteria_columns": [], "rows": []}
            continue

        criteria_columns = [
            key for key in raw_rows[0].keys()
            if key not in ["Nhà cung ứng", "Score", "Rank", "Khách hàng"]
        ]
        rows = []
        for row in raw_rows:
            supplier_name = str(row.get("Nhà cung ứng") or "").strip()
            if not supplier_name:
                continue
            metrics = {}
            metric_values = []
            for column in criteria_columns:
                parsed_value = _parse_percent_value(row.get(column))
                metrics[column] = parsed_value
                if parsed_value is not None:
                    metric_values.append(parsed_value)
            computed_score = round(mean(metric_values), 1) if metric_values else None
            computed_rank = _classify_rank(computed_score, thresholds)
            rows.append({
                "supplier_name": supplier_name,
                "customer": _normalize_customer_segment(row.get("Khách hàng")),
                "metrics": metrics,
                "score": computed_score,
                "rank": computed_rank,
                "source_score": _parse_percent_value(row.get("Score")),
                "source_rank": str(row.get("Rank") or "").strip().upper(),
            })
        years_payload[year] = {"criteria_columns": criteria_columns, "rows": rows}

    return {"thresholds": thresholds, "years": years_payload}


def sync_rank2_from_sheets(triggered_by: str = "system"):
    """Replace rank2_* tables with fresh data from Google Sheets."""
    payload = _fetch_rank2_from_sheets()
    db: Session = SessionLocal()
    try:
        db.query(models.Rank2SupplierScore).delete()
        db.query(models.Rank2YearMeta).delete()
        db.query(models.Rank2Threshold).delete()

        for item in payload["thresholds"]:
            db.add(models.Rank2Threshold(
                rank=item["rank"],
                from_value=item["from"],
                to_value=item["to"],
            ))

        now = datetime.utcnow()
        total_rows = 0
        for year, year_data in payload["years"].items():
            db.add(models.Rank2YearMeta(
                year=year,
                criteria_columns=json.dumps(year_data["criteria_columns"], ensure_ascii=False),
                last_synced_at=now,
                last_synced_by=triggered_by,
            ))
            for row in year_data["rows"]:
                db.add(models.Rank2SupplierScore(
                    year=year,
                    customer=row["customer"],
                    supplier_name=row["supplier_name"],
                    metrics_json=json.dumps(row["metrics"], ensure_ascii=False),
                    score=row["score"],
                    rank=row["rank"],
                    source_score=row["source_score"],
                    source_rank=row["source_rank"],
                ))
                total_rows += 1
        db.commit()
        return {
            "status": "success",
            "synced_at": now.isoformat(),
            "year_count": len(payload["years"]),
            "row_count": total_rows,
            "threshold_count": len(payload["thresholds"]),
        }
    except Exception as exc:
        db.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        db.close()


def get_rank2_dashboard_data(year: str, customer: str = "ALL"):
    selected_year = str(year or "").strip()
    if selected_year not in RANK2_YEARS:
        raise ValueError(f"Unsupported year: {selected_year}")
    normalized_customer = _normalize_customer_segment(customer)

    db: Session = SessionLocal()
    try:
        thresholds = [
            {"from": t.from_value, "to": t.to_value, "rank": t.rank}
            for t in db.query(models.Rank2Threshold).order_by(models.Rank2Threshold.from_value).all()
        ]
        meta = db.query(models.Rank2YearMeta).filter(models.Rank2YearMeta.year == selected_year).first()
        criteria_columns = json.loads(meta.criteria_columns) if meta and meta.criteria_columns else []
        last_synced_at = meta.last_synced_at.isoformat() if meta and meta.last_synced_at else None
        last_synced_by = meta.last_synced_by if meta else None

        query = (
            db.query(models.Rank2SupplierScore)
            .filter(models.Rank2SupplierScore.year == selected_year)
        )
        if normalized_customer != "ALL":
            query = query.filter(models.Rank2SupplierScore.customer == normalized_customer)
        db_rows = query.all()
    finally:
        db.close()

    if not db_rows:
        return {
            "year": selected_year,
            "customer": normalized_customer,
            "criteria_columns": criteria_columns,
            "thresholds": thresholds,
            "rows": [],
            "summary": {
                "supplier_count": 0,
                "avg_score": 0,
                "top_rank_count": 0,
                "lowest_rank_count": 0,
                "rank_counts": {"A": 0, "B": 0, "C": 0, "D": 0},
            },
            "criterion_summary": [],
            "last_synced_at": last_synced_at,
            "last_synced_by": last_synced_by,
        }

    rows = []
    for r in db_rows:
        metrics = json.loads(r.metrics_json) if r.metrics_json else {}
        rows.append({
            "supplier_name": r.supplier_name,
            "metrics": metrics,
            "score": r.score,
            "score_display": _format_percent_value(r.score),
            "rank": r.rank,
            "source_score": r.source_score,
            "source_rank": r.source_rank,
            "customer": r.customer,
        })
    rows.sort(key=lambda item: (-(item["score"] if item["score"] is not None else -1), item["supplier_name"]))

    supplier_count = len(rows)
    score_values = [row["score"] for row in rows if row["score"] is not None]
    avg_score = round(mean(score_values), 1) if score_values else 0
    rank_counts = {rank: 0 for rank in ["A", "B", "C", "D"]}
    for row in rows:
        if row["rank"] in rank_counts:
            rank_counts[row["rank"]] += 1

    criterion_summary = []
    for column in criteria_columns:
        values = [row["metrics"][column] for row in rows if row["metrics"].get(column) is not None]
        avg_value = round(mean(values), 1) if values else None
        top_row = max(
            (row for row in rows if row["metrics"].get(column) is not None),
            key=lambda item: item["metrics"][column],
            default=None,
        )
        criterion_summary.append({
            "name": column,
            "avg": avg_value,
            "avg_display": _format_percent_value(avg_value),
            "top_supplier": top_row["supplier_name"] if top_row else "--",
            "top_value": _format_percent_value(top_row["metrics"][column]) if top_row else "--",
        })

    return {
        "year": selected_year,
        "customer": normalized_customer,
        "criteria_columns": criteria_columns,
        "thresholds": thresholds,
        "rows": rows,
        "summary": {
            "supplier_count": supplier_count,
            "avg_score": avg_score,
            "top_rank_count": rank_counts["A"],
            "lowest_rank_count": rank_counts["D"],
            "rank_counts": rank_counts,
        },
        "criterion_summary": criterion_summary,
        "last_synced_at": last_synced_at,
        "last_synced_by": last_synced_by,
    }

def sync_staff_emails_from_sheet():
    """Sync staff_emails table from Google Sheet 'staff_email' range A1:E"""
    JSON_KEY = 'credentials_m29.json'
    SPREADSHEET_ID = "136Nx00jUL24pG82A1FU0inds4HbCG1wszTuB9v-SUak"
    SHEET_NAME = "staff_email"
    
    print(f"Syncing staff emails from Google Sheets: {SHEET_NAME}...")
    
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(JSON_KEY, scopes=scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        rows = sheet.get('A1:E')
        
        if not rows or len(rows) < 2:
            return {"status": "error", "message": "No data found in sheet"}
        
        data_rows = rows[1:]
        
        db = SessionLocal()
        try:
            from sqlalchemy import text
            db.execute(text('DELETE FROM staff_emails'))
            
            inserted = 0
            seen = set()
            for row in data_rows:
                role = str(row[0]).strip() if len(row) > 0 and row[0] else ""
                name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                email = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                employee_code = str(row[3]).strip() if len(row) > 3 and row[3] else None
                department = str(row[4]).strip() if len(row) > 4 and row[4] else None
                
                if not role or not name or not email:
                    continue
                
                dedupe_key = employee_code or f"{role}|{name}|{email}"
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                
                db.add(models.StaffEmail(
                    role=role,
                    name=name,
                    email=email,
                    employee_code=employee_code,
                    department=department
                ))
                inserted += 1
            
            db.commit()
            return {"status": "success", "count": inserted}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()
            
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

def _normalize_supplier_text(value: str) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").strip().lower()
    # Collapse multiple spaces
    return " ".join(text.split())

def _build_supplier_match_map(db: Session) -> dict:
    rows = db.query(models.FabricSupplierMatch.schedule_name, models.FabricSupplierMatch.standard_name).all()
    mapping = {}
    for schedule_name, standard_name in rows:
        if not schedule_name or not standard_name:
            continue
        mapping[_normalize_supplier_text(schedule_name)] = _normalize_supplier_text(standard_name)
    return mapping

def _best_supplier_name_match(raw_supplier: str, suppliers: list[dict], min_ratio: float = 0.6,
                              schedule_map: dict | None = None) -> str | None:
    query = _normalize_supplier_text(raw_supplier)
    if schedule_map and query in schedule_map:
        query = schedule_map[query]
    if not query:
        return None

    best_ratio = 0.0
    best_name = None

    for s in suppliers:
        candidate = s.get("cpt_supplier_norm", "")
        if not candidate:
            continue

        if query in candidate or candidate in query:
            ratio = 1.0
        else:
            ratio = SequenceMatcher(None, query, candidate).ratio()

        if ratio > best_ratio:
            best_ratio = ratio
            best_name = s.get("supplier_name") or None

    if best_ratio >= min_ratio:
        return best_name
    return None

def sync_schedule_fabric_from_sheet():
    """Sync schedule_fabric table from Google Sheet 'schedule_fabric' range A1:G"""
    JSON_KEY = 'credentials_m29.json'
    SPREADSHEET_ID = "136Nx00jUL24pG82A1FU0inds4HbCG1wszTuB9v-SUak"
    SHEET_NAME = "schedule_fabric"

    print(f"Syncing schedule_fabric from Google Sheets: {SHEET_NAME}...")

    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(JSON_KEY, scopes=scope)
        client = gspread.authorize(creds)

        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        rows = sheet.get('A1:G')

        if not rows or len(rows) < 2:
            return {"status": "error", "message": "No data found in sheet"}

        data_rows = rows[1:]

        # Preload supplier_email for matching
        db = SessionLocal()
        try:
            models.Base.metadata.create_all(bind=engine)

            suppliers_raw = db.query(
                models.SupplierEmail.supplier_name,
                models.SupplierEmail.cpt_supplier
            ).all()
            suppliers = [
                {
                    "cpt_supplier_norm": _normalize_supplier_text(supplier_name),
                    "supplier_name": supplier_name
                }
                for supplier_name, _ in suppliers_raw
                if supplier_name
            ]
            schedule_map = _build_supplier_match_map(db)

            # Clear existing data to keep in sync
            db.execute(text('DELETE FROM schedule_fabric'))

            from datetime import datetime
            inserted = 0

            for row in data_rows:
                if len(row) < 4:
                    continue

                season = str(row[0]).strip() if row[0] else None
                item_code = str(row[1]).strip() if row[1] else None
                raw_supplier = str(row[2]).strip() if row[2] else None
                etd_raw = str(row[3]).strip() if row[3] else None
                iman = str(row[4]).strip() if len(row) > 4 and row[4] else None
                model_code = str(row[5]).strip() if len(row) > 5 and row[5] else None
                cpt_description = str(row[6]).strip() if len(row) > 6 and row[6] else None

                etd = None
                if etd_raw:
                    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                        try:
                            etd = datetime.strptime(etd_raw, fmt).date()
                            break
                        except (ValueError, TypeError):
                            continue

                supplier_name = _best_supplier_name_match(raw_supplier, suppliers, schedule_map=schedule_map)

                record = models.ScheduleFabric(
                    season=season,
                    item_code=item_code,
                    supplier_name=supplier_name,
                    etd=etd,
                    iman=iman,
                    model_code=model_code,
                    cpt_description=cpt_description
                )
                db.add(record)
                inserted += 1

            db.commit()
            return {"status": "success", "count": inserted}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}

def sync_fabric_supplier_match_from_sheet():
    """Sync fabric_supplier_match table from Google Sheet 'Match NCU' range A2:B"""
    JSON_KEY = 'credentials_m29.json'
    SPREADSHEET_ID = "136Nx00jUL24pG82A1FU0inds4HbCG1wszTuB9v-SUak"
    SHEET_NAME = "Match NCU"

    print(f"Syncing fabric supplier match from Google Sheets: {SHEET_NAME}...")

    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_file(JSON_KEY, scopes=scope)
        client = gspread.authorize(creds)

        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
        rows = sheet.get('A2:B')

        if not rows:
            return {"status": "error", "message": "No data found in sheet"}

        db = SessionLocal()
        try:
            models.Base.metadata.create_all(bind=engine)
            try:
                db.execute(text('DELETE FROM fabric_supplier_match'))
            except Exception:
                # Clear failed transaction then retry after ensuring table exists
                db.rollback()
                models.Base.metadata.create_all(bind=engine)
                db.execute(text('DELETE FROM fabric_supplier_match'))

            # De-duplicate by normalized schedule_name (case/space-insensitive)
            deduped = {}
            for row in rows:
                schedule_name = str(row[0]).strip() if len(row) > 0 and row[0] else ""
                standard_name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                if not schedule_name or not standard_name:
                    continue
                key = _normalize_supplier_text(schedule_name)
                if not key:
                    continue
                deduped[key] = (schedule_name, standard_name)

            inserted = 0
            for _, (schedule_name, standard_name) in deduped.items():
                db.add(models.FabricSupplierMatch(
                    schedule_name=schedule_name,
                    standard_name=standard_name
                ))
                inserted += 1

            db.commit()
            return {"status": "success", "count": inserted}
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
    elif len(sys.argv) > 1 and sys.argv[1] == "--sync-schedule-fabric":
        result = sync_schedule_fabric_from_sheet()
        print(result)
    elif len(sys.argv) > 1 and sys.argv[1] == "--sync-supplier-map":
        result = sync_fabric_supplier_match_from_sheet()
        print(result)
    else:
        excel_file = "first_lot_data.xlsx"
        import_from_excel(excel_file)
