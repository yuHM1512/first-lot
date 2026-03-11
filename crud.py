from sqlalchemy.orm import Session
import models, schemas
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

def check_first_lot_status(db: Session, item_code: str):
    # Find matching first lot
    first_lot = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == item_code).first()
    
    if not first_lot:
        return "Xin mới"
    
    if not first_lot.received_date:
        return "Xin mới"
    
    # Check expiration (2 years or custom using_time_years)
    expiration_date = first_lot.received_date + relativedelta(years=first_lot.using_time_years)
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
    query = db.query(
        models.FirstLotRequest,
        models.FirstLotMaster.received_date.label("master_received_date"),
        models.FirstLotMaster.using_time_years.label("master_using_time_years"),
        models.FirstLotMaster.color_test_report_received_date.label("master_color_test_date"),
        models.FirstLotMaster.mtsr_received_date.label("master_mtsr_date"),
        models.FirstLotMaster.lot_info_status.label("master_lot_info_status"),
        models.FirstLotMaster.lot_info_reason.label("master_lot_info_reason"),
        models.FirstLotMaster.lot_quality_status.label("master_lot_quality_status"),
        models.FirstLotMaster.lot_quality_reason.label("master_lot_quality_reason")
    ).outerjoin(
        models.FirstLotMaster, 
        models.FirstLotRequest.item_code == models.FirstLotMaster.item_code
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
        requests.append(req)
    
    return requests

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
    master = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == update_data.item_code).first()
    
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
                        change_type=history_type,
                        old_date=old_val,
                        new_date=new_val
                    )
                    db.add(history)
                
                setattr(master, field, new_val)
    
    db.commit()
    db.refresh(master)
    return master

def update_first_lot_master_dates(db: Session, update_data: schemas.FirstLotDateUpdate):
    return save_first_lot_master(db, update_data)

def get_first_lot_history(db: Session, item_code: str):
    return db.query(models.FirstLotHistory).filter(models.FirstLotHistory.item_code == item_code).order_by(models.FirstLotHistory.changed_at.desc()).all()

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
