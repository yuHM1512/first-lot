from sqlalchemy.orm import Session
from sqlalchemy import func
import models, schemas
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

def check_first_lot_status(db: Session, item_code: str, supplier_name: str = None):
    # Find matching first lot by item_code AND supplier_name
    query = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == item_code)
    
    if supplier_name:
        query = query.filter(models.FirstLotMaster.fabric_supplier == supplier_name)
    
    # Get the latest received_date if multiple exist
    first_lot = query.order_by(models.FirstLotMaster.received_date.desc()).first()
    
    if not first_lot:
        return "Xin mới"
    
    if not first_lot.received_date:
        return "Xin mới"
    
    # Check expiration (default 2 years or custom using_time_years)
    expiration_date = first_lot.received_date + relativedelta(years=first_lot.using_time_years or 2)
    if date.today() > expiration_date:
        return "Xin mới"
    
    return "OK"

def create_first_lot_request(db: Session, request: schemas.FirstLotRequestCreate):
    # Calculate status based on item_code
    status = check_first_lot_status(db, request.item_code)
    
    db_request = models.FirstLotRequest(
        **request.model_dump(),
        status=status
    )
    db.add(db_request)
    db.commit()
    db.refresh(db_request)
    return db_request

def get_first_lot_requests(db: Session, skip: int = 0, limit: int = 100, season: str = None, 
                           model_description: str = None, item_code: str = None, sample_types: list[str] = None):
    # Subquery to get unique latest FirstLotMaster per (item_code, fabric_supplier)
    # Using row_number() to handle cases where multiple records exist for same key
    master_ranked = db.query(
        models.FirstLotMaster,
        func.row_number().over(
            partition_by=(models.FirstLotMaster.item_code, models.FirstLotMaster.fabric_supplier),
            order_by=models.FirstLotMaster.received_date.desc()
        ).label("rn")
    ).subquery()
    
    query = db.query(
        models.FirstLotRequest,
        master_ranked.c.received_date.label("master_received_date"),
        master_ranked.c.using_time_years.label("master_using_time_years"),
        master_ranked.c.color_test_report_received_date.label("master_color_test_date"),
        master_ranked.c.mtsr_received_date.label("master_mtsr_date"),
        master_ranked.c.lot_info_status.label("master_lot_info_status"),
        master_ranked.c.lot_info_reason.label("master_lot_info_reason"),
        master_ranked.c.lot_quality_status.label("master_lot_quality_status"),
        master_ranked.c.lot_quality_reason.label("master_lot_quality_reason"),
        models.EmailLog.email_status.label("log_email_status"),
        models.EmailLog.email_sent_at.label("log_email_sent_at"),
        models.EmailLog.first_lot_received_status.label("log_received_status"),
        models.EmailLog.resend_count.label("log_resend_count"),
        models.SupplierEmail.supplier_name.label("supplier_lookup_name")
    ).outerjoin(
        models.SupplierEmail,
        models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
    ).outerjoin(
        master_ranked, 
        (models.FirstLotRequest.item_code == master_ranked.c.item_code) & 
        (models.SupplierEmail.supplier_name == master_ranked.c.fabric_supplier) &
        (master_ranked.c.rn == 1)
    ).outerjoin(
        models.EmailLog,
        models.FirstLotRequest.ser_no == models.EmailLog.ser_no
    )
    
    if season:
        query = query.filter(models.FirstLotRequest.season.ilike(f"%{season}%"))
    if model_description:
        query = query.filter(models.FirstLotRequest.model_description.ilike(f"%{model_description}%"))
    if item_code:
        query = query.filter(models.FirstLotRequest.item_code.ilike(f"%{item_code}%"))
    if sample_types and len(sample_types) > 0:
        query = query.filter(models.FirstLotRequest.sample_type.in_(sample_types))
    
    results = query.offset(skip).limit(limit).all()
    
    # Map raw rows to objects with appended values
    requests = []
    for row in results:
        req = row[0]
        req.master_received_date = row[1]
        req.master_using_time_years = row[2]
        req.master_color_test_date = row[3]
        req.master_mtsr_date = row[4]
        req.master_lot_info_status = row[5]
        req.master_lot_info_reason = row[6]
        req.master_lot_quality_status = row[7]
        req.master_lot_quality_reason = row[8]
        
        # New: supplier name for validation
        req.supplier_lookup_name = row[13]
        
        # Recover status from EmailLog join
        req.email_status = row[9]
        req.email_sent_at = row[10]
        req.first_lot_received_status = row[11]
        req.resend_count = row[12] or 0
        
        requests.append(req)
    
    return requests

# (rest of existing functions...)

def update_received_status(db: Session, request_id: int, status: str):
    db_request = db.query(models.FirstLotRequest).filter(models.FirstLotRequest.id == request_id).first()
    if not db_request:
        return None
    # Note: Column removed from FirstLotRequest. We only return the object 
    # so the caller (app.py) can get the ser_no and upsert to EmailLog.
    return db_request

def get_first_lot_requests_count(db: Session, season: str = None, model_description: str = None, 
                                 item_code: str = None, sample_types: list[str] = None):
    query = db.query(models.FirstLotRequest)
    if season:
        query = query.filter(models.FirstLotRequest.season.ilike(f"%{season}%"))
    if model_description:
        query = query.filter(models.FirstLotRequest.model_description.ilike(f"%{model_description}%"))
    if item_code:
        query = query.filter(models.FirstLotRequest.item_code.ilike(f"%{item_code}%"))
    if sample_types and len(sample_types) > 0:
        query = query.filter(models.FirstLotRequest.sample_type.in_(sample_types))
    return query.count()

def get_first_lot_stats(db: Session, season: str = None, model_description: str = None, 
                        item_code: str = None, sample_types: list[str] = None):
    query = db.query(models.FirstLotRequest)
    if season:
        query = query.filter(models.FirstLotRequest.season.ilike(f"%{season}%"))
    if model_description:
        query = query.filter(models.FirstLotRequest.model_description.ilike(f"%{model_description}%"))
    if item_code:
        query = query.filter(models.FirstLotRequest.item_code.ilike(f"%{item_code}%"))
    if sample_types and len(sample_types) > 0:
        query = query.filter(models.FirstLotRequest.sample_type.in_(sample_types))
    
    total = query.count()
    ok_count = query.filter(models.FirstLotRequest.status == 'OK').count()
    pending_count = total - ok_count
    
    return {
        "total": total,
        "ok": ok_count,
        "pending": pending_count
    }

def create_first_lot_master(db: Session, master: schemas.FirstLotMasterCreate):
    db_master = models.FirstLotMaster(**master.model_dump())
    db.add(db_master)
    db.commit()
    db.refresh(db_master)
    return db_master

def get_first_lot_master(db: Session, skip: int = 0, limit: int = 100, item_code: str = None, fabric_name: str = None):
    query = db.query(models.FirstLotMaster)
    if item_code:
        query = query.filter(models.FirstLotMaster.item_code.ilike(f"%{item_code}%"))
    if fabric_name:
        query = query.filter(models.FirstLotMaster.fabric_name.ilike(f"%{fabric_name}%"))
    return query.offset(skip).limit(limit).all()

def get_first_lot_master_count(db: Session, item_code: str = None, fabric_name: str = None):
    query = db.query(models.FirstLotMaster)
    if item_code:
        query = query.filter(models.FirstLotMaster.item_code.ilike(f"%{item_code}%"))
    if fabric_name:
        query = query.filter(models.FirstLotMaster.fabric_name.ilike(f"%{fabric_name}%"))
    return query.count()
def get_first_lot_master_by_item(db: Session, item_code: str):
    return db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == item_code).first()

def save_first_lot_master(db: Session, update_data: schemas.FirstLotDateUpdate):
    # Lookup by BOTH item_code and fabric_supplier
    master = db.query(models.FirstLotMaster).filter(
        models.FirstLotMaster.item_code == update_data.item_code,
        models.FirstLotMaster.fabric_supplier == update_data.fabric_supplier
    ).first()
    
    # If not exists, create it
    if not master:
        # We might need more fields from the request if creating for the first time
        # For now, let's allow basic creation with the fields we have in schemas.FirstLotDateUpdate
        # But to be robust, we should probably have a more complete schema for creation.
        # However, the user wants to pre-fill from request.
        
        # Let's assume the frontend sends what it can.
        master = models.FirstLotMaster(item_code=update_data.item_code)
        db.add(master)
        db.flush() # Get an ID but don't commit yet
    
    # Track changes and update fields
    fields = {
        'received_date': update_data.received_date,
        'color_test_report_received_date': update_data.color_test_report_received_date,
        'mtsr_received_date': update_data.mtsr_received_date,
        'lot_info_status': update_data.lot_info_status,
        'lot_quality_status': update_data.lot_quality_status,
        'fabric_name': update_data.fabric_name,
        'model_code': update_data.model_code,
        'fabric_supplier': update_data.fabric_supplier,
        'usable_width': update_data.usable_width,
        'unit': update_data.unit,
        'color': update_data.color,
        'description': update_data.description
    }
    
    # Update reasons
    if update_data.lot_info_reason is not None:
        master.lot_info_reason = update_data.lot_info_reason
    if update_data.lot_quality_reason is not None:
        master.lot_quality_reason = update_data.lot_quality_reason
    
    for field, new_val in fields.items():
        if new_val is not None:
            old_val = getattr(master, field)
            if old_val != new_val:
                # Log history for dates
                if isinstance(new_val, (date, datetime)):
                    history_type = field
                    if field == 'color_test_report_received_date': history_type = 'color_test'
                    elif field == 'mtsr_received_date': history_type = 'mtsr'
                    
                    history = models.FirstLotHistory(
                        item_code=master.item_code,
                        fabric_supplier=master.fabric_supplier,
                        change_type=history_type,
                        old_date=old_val,
                        new_date=new_val
                    )
                    db.add(history)
                
                setattr(master, field, new_val)
    
    # Update linked request if ID is provided
    if update_data.request_id:
        req = db.query(models.FirstLotRequest).filter(models.FirstLotRequest.id == update_data.request_id).first()
        if req:
            req.first_lot_received_status = update_data.received_status
            if update_data.remark is not None:
                req.remark = update_data.remark
    
    db.commit()
    db.refresh(master)
    return master

def update_first_lot_master_dates(db: Session, update_data: schemas.FirstLotDateUpdate):
    return save_first_lot_master(db, update_data)

def get_first_lot_master_by_item(db: Session, item_code: str, supplier_name: str = None):
    query = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == item_code)
    if supplier_name:
        query = query.filter(models.FirstLotMaster.fabric_supplier == supplier_name)
    # Always return the latest entry if multiple exist
    return query.order_by(models.FirstLotMaster.received_date.desc()).first()

def update_received_status(db: Session, request_id: int, status: str):
    req = db.query(models.FirstLotRequest).filter(models.FirstLotRequest.id == request_id).first()
    if not req:
        return None
    req.first_lot_received_status = status
    db.commit()
    db.refresh(req)
    return req

def get_first_lot_history(db: Session, item_code: str, supplier_name: str = None):
    query = db.query(models.FirstLotHistory).filter(models.FirstLotHistory.item_code == item_code)
    if supplier_name:
        query = query.filter(models.FirstLotHistory.fabric_supplier == supplier_name)
    return query.order_by(models.FirstLotHistory.changed_at.desc()).all()

def get_unique_filter_values(db: Session):
    seasons = [r[0] for r in db.query(models.FirstLotRequest.season).distinct().all() if r[0]]
    models_desc = [r[0] for r in db.query(models.FirstLotRequest.model_description).distinct().all() if r[0]]
    items = [r[0] for r in db.query(models.FirstLotRequest.item_code).distinct().all() if r[0]]
    sample_types = [r[0] for r in db.query(models.FirstLotRequest.sample_type).distinct().all() if r[0]]
    
    return {
        "seasons": sorted(seasons),
        "model_descriptions": sorted(models_desc),
        "item_codes": sorted(items),
        "sample_types": sorted(sample_types)
    }

# ── Supplier Email CRUD ──────────────────────────────
def get_supplier_emails(db: Session):
    return db.query(models.SupplierEmail).order_by(models.SupplierEmail.supplier_name).all()

def get_supplier_email_by_id(db: Session, supplier_id: int):
    return db.query(models.SupplierEmail).filter(models.SupplierEmail.id == supplier_id).first()

def update_supplier_email(db: Session, supplier_id: int, data: schemas.SupplierEmailCreate):
    supplier = db.query(models.SupplierEmail).filter(models.SupplierEmail.id == supplier_id).first()
    if not supplier:
        return None
    supplier.supplier_name = data.supplier_name
    supplier.cpt_supplier = data.cpt_supplier
    supplier.email = data.email
    db.commit()
    db.refresh(supplier)
    return supplier

def create_supplier_email(db: Session, data: schemas.SupplierEmailCreate):
    supplier = models.SupplierEmail(**data.model_dump())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier

def delete_supplier_email(db: Session, supplier_id: int):
    supplier = db.query(models.SupplierEmail).filter(models.SupplierEmail.id == supplier_id).first()
    if not supplier:
        return False
    db.delete(supplier)
    db.commit()
    return True
# ── Staff Email CRUD ──────────────────────────────
def get_staff_emails(db: Session):
    return db.query(models.StaffEmail).order_by(models.StaffEmail.role, models.StaffEmail.name).all()

def update_staff_email(db: Session, staff_id: int, data: schemas.StaffEmailCreate):
    staff = db.query(models.StaffEmail).filter(models.StaffEmail.id == staff_id).first()
    if not staff:
        return None
    staff.employee_code = data.employee_code
    staff.department = data.department
    staff.role = data.role
    staff.name = data.name
    staff.email = data.email
    db.commit()
    db.refresh(staff)
    return staff

def create_staff_email(db: Session, data: schemas.StaffEmailCreate):
    staff = models.StaffEmail(**data.model_dump())
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff

def delete_staff_email(db: Session, staff_id: int):
    staff = db.query(models.StaffEmail).filter(models.StaffEmail.id == staff_id).first()
    if not staff:
        return False
    db.delete(staff)
    db.commit()
    return True

# ── Supplier Performance Analytics ──────────────────────────────
def get_supplier_performance(db: Session):
    from sqlalchemy import text
    from datetime import timedelta
    from dateutil import parser
    
    query = text("""
        SELECT r.cpt_supplier, s.supplier_name,
               e.email_sent_at, e.updated_at, e.first_lot_received_status,
               m.received_date as master_received_date, r.actual_delivery_date
        FROM first_lot_requests r
        JOIN email_log e ON r.ser_no = e.ser_no
        LEFT JOIN supplier_email s ON r.cpt_supplier = s.cpt_supplier
        LEFT JOIN first_lot_master m ON r.item_code = m.item_code AND r.cpt_supplier = m.fabric_supplier
        WHERE r.sample_type ILIKE '%1st lot%' AND e.email_status = 'SENT'
    """)
    results = db.execute(query).fetchall()
    
    supplier_perf = {}
    
    for row in results:
        supp_code = row[0]
        supp_name = row[1] or supp_code
        if not supp_code:
            continue
            
        if supp_code not in supplier_perf:
            supplier_perf[supp_code] = {"name": supp_name, "total": 0, "on_time": 0}
            
        supplier_perf[supp_code]["total"] += 1
        
        sent_at = row[2]
        if not sent_at:
            continue
            
        is_on_time = False
        
        # 1. Check master_received_date
        if row[5]:
            if (row[5] - sent_at.date()).days <= 7:
                is_on_time = True
        
        # 2. Check email_log updated_at (if status is marked as received)
        if not is_on_time and row[4] and row[3]:
            # Assuming row[4] being NOT NULL means someone updated the status
            if (row[3] - sent_at).days <= 7:
                is_on_time = True
                
        # 3. Check actual_delivery_date
        if not is_on_time and row[6]:
            try:
                actual_date = parser.parse(row[6]).date()
                if (actual_date - sent_at.date()).days <= 7:
                    is_on_time = True
            except:
                pass
                
        if is_on_time:
            supplier_perf[supp_code]["on_time"] += 1
            
    final_results = []
    for code, data in supplier_perf.items():
        rate = (data["on_time"] / data["total"]) * 100 if data["total"] > 0 else 0
        
        status = "Standard"
        status_color = "slate"
        if rate >= 95:
            status = "Elite"
            status_color = "green"
        elif rate >= 85:
            status = "Good"
            status_color = "brand"
        elif rate >= 70:
            status = "Under Review"
            status_color = "amber"
        else:
            status = "Critical"
            status_color = "red"
            
        # Calculate trend (dummy logic for now as we need historical snapshots for real trend)
        trend = "+0.0%"
        trend_class = "text-slate-400"
        
        final_results.append({
            "code": code,
            "name": data["name"],
            "category": "Fabric/Textile", # Defaulting as this system is for fabrics
            "total_deliveries": data["total"],
            "on_time_deliveries": data["on_time"],
            "on_time_rate": round(rate, 1),
            "status": status,
            "status_color": status_color,
            "trend": trend,
            "trend_class": trend_class
        })
        
    return sorted(final_results, key=lambda x: x["on_time_rate"], reverse=True)
