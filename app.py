import os
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional

import models, schemas, crud, database
from database import engine, get_db
from import_data import sync_from_google_sheets

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="1st Lot Management System")

# Templates and Static files setup
# (We will create these directories and files later)
# app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

from fastapi import FastAPI, Depends, HTTPException, Request, Query

# ... (keep imports) ...

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/first-lot/", response_class=HTMLResponse)
async def read_first_lot(request: Request, page: int = 1, limit: int = 100, 
                  season: str = None, model_description: str = None, item_code: str = None,
                  status: list[str] = Query(default=[]),
                  sample_type: list[str] = Query(default=[]),
                  show_timeout: bool = False,
                  db: Session = Depends(get_db)):
    skip = (page - 1) * limit
    all_requests = crud.get_first_lot_requests(db, skip=0, limit=999999, 
                                         season=season, model_description=model_description, 
                                         item_code=item_code, sample_types=sample_type)
    
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta
    today = date.today()
    
    pre_filtered = []
    
    # 1st Pass: Calculate all statuses globally (Validity + Timeout + Reasons + Dates)
    for r in all_requests:
        using_years = r.master_using_time_years or 2
        
        # Validity Logic
        if r.master_received_date:
            expiration_date = r.master_received_date + relativedelta(years=using_years)
            if today <= expiration_date:
                r.validity_status = "Còn hiệu lực"
                r.validity_color = "green"
            else:
                r.validity_status = "Hết hiệu lực"
                r.validity_color = "red"
            r.main_expiration_str = expiration_date.strftime("%d/%m/%Y")
        else:
            r.validity_status = "Chưa có"
            r.validity_color = "slate"
            r.main_expiration_str = ""

        # Color/MTSR Dates
        if hasattr(r, 'master_color_test_date') and r.master_color_test_date:
            c_exp = r.master_color_test_date + relativedelta(years=using_years)
            r.color_exp_str = c_exp.strftime("%d/%m/%Y")
        else:
            r.color_exp_str = ""
            
        if hasattr(r, 'master_mtsr_date') and r.master_mtsr_date:
            m_exp = r.master_mtsr_date + relativedelta(years=using_years)
            r.mtsr_exp_str = m_exp.strftime("%d/%m/%Y")
        else:
            r.mtsr_exp_str = ""

        # Reasons
        info_reason = f"Lỗi thông tin: {r.master_lot_info_reason or 'Không rõ'}" if getattr(r, 'master_lot_info_status', None) == 'NOK' else ""
        quality_reason = f"Lỗi chất lượng: {r.master_lot_quality_reason or 'Không rõ'}" if getattr(r, 'master_lot_quality_status', None) == 'NOK' else ""
        r.reasons_str = " | ".join([rr for rr in [info_reason, quality_reason] if rr])

        # Timeout Logic (Global)
        r.is_timeout = False
        if r.email_status == "SENT" and r.email_sent_at:
            if today > (r.email_sent_at.date() + timedelta(days=7)) and r.validity_status != "Còn hiệu lực":
                r.email_status = "NOT RECEIVED"
                r.is_timeout = True
        
        # Apply filters (Multi-status + Timeout)
        if status and len(status) > 0 and r.validity_status not in status:
            continue
        if show_timeout and not r.is_timeout:
            continue
            
        pre_filtered.append(r)

    total_count = len(pre_filtered)
    requests_paged = pre_filtered[skip:skip+limit]
    
    da_gui = sum(1 for req in pre_filtered if req.email_status in ['SENT', 'NOT RECEIVED'])
    chua_gui = sum(1 for req in pre_filtered if req.email_status not in ['SENT', 'NOT RECEIVED'])
    qua_han = sum(1 for req in pre_filtered if req.is_timeout)
    
    stats = {
        "total": total_count,
        "da_gui": da_gui,
        "chua_gui": chua_gui,
        "qua_han": qua_han
    }
    
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    
    pagination = {
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }

    # Get unique filter options for datalists
    filter_options = crud.get_unique_filter_values(db)

    return templates.TemplateResponse("first_lot.html", {
        "request": request, 
        "requests": requests_paged, 
        "stats": stats,
        "pagination": pagination,
        "filters": {
            "season": season or "",
            "model_description": model_description or "",
            "item_code": item_code or "",
            "status": status or [],
            "sample_type": sample_type or [],
            "show_timeout": show_timeout
        },
        "filter_options": filter_options
    })

@app.get("/requests/", response_model=List[schemas.FirstLotRequest])
def read_requests(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    requests = crud.get_first_lot_requests(db, skip=skip, limit=limit)
    return requests

@app.post("/requests/", response_model=schemas.FirstLotRequest)
def create_request(request: schemas.FirstLotRequestCreate, db: Session = Depends(get_db)):
    return crud.create_first_lot_request(db=db, request=request)

@app.get("/master/", response_model=List[schemas.FirstLotMaster])
def read_master(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    master_data = crud.get_first_lot_master(db, skip=skip, limit=limit)
    return master_data

@app.post("/master/", response_model=schemas.FirstLotMaster)
def create_master(master: schemas.FirstLotMasterCreate, db: Session = Depends(get_db)):
    return crud.create_first_lot_master(db=db, master=master)

@app.post("/api/sync")
async def sync_data():
    result = sync_from_google_sheets()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.get("/history/", response_class=HTMLResponse)
async def read_history_page(request: Request, page: int = 1, limit: int = 100, item_code: str = None, fabric_name: str = None,
                            status: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    skip = (page - 1) * limit
    all_masters = crud.get_first_lot_master(db, skip=0, limit=999999, item_code=item_code, fabric_name=fabric_name)
    
    from datetime import date
    from dateutil.relativedelta import relativedelta
    today = date.today()
    
    filtered_masters = []
    
    # Enrich masters with status and filter
    for m in all_masters:
        if m.received_date:
            expiration_date = m.received_date + relativedelta(years=m.using_time_years or 2)
            m.expiration_date = expiration_date
            m.is_valid = today <= expiration_date
        else:
            m.expiration_date = None
            m.is_valid = False
            
        m.status_vn = "Còn hiệu lực" if m.is_valid else "Hết hạn"
        
        # Apply status multi-filter
        if status and len(status) > 0 and m.status_vn not in status:
            continue
            
        filtered_masters.append(m)

    total_count = len(filtered_masters)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    masters = filtered_masters[skip:skip+limit]
    
    # Calculate stats
    con_hieu_luc = sum(1 for m in filtered_masters if m.is_valid)
    het_hieu_luc = total_count - con_hieu_luc
    
    stats = {
        "total": total_count,
        "con_hieu_luc": con_hieu_luc,
        "het_hieu_luc": het_hieu_luc
    }
    
    pagination = {
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }

    return templates.TemplateResponse("first_lot_history.html", {
        "request": request,
        "masters": masters,
        "pagination": pagination,
        "stats": stats,
        "today": today,
        "filters": {
            "item_code": item_code or "",
            "fabric_name": fabric_name or "",
            "status": status
        }
    })

@app.post("/api/master/update-dates")
async def update_master_dates(update_data: schemas.FirstLotDateUpdate, db: Session = Depends(get_db)):
    updated = crud.update_first_lot_master_dates(db, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"status": "success", "message": "Dates updated successfully"}

@app.get("/api/master/get/{item_code}", response_model=Optional[schemas.FirstLotMaster])
async def get_master_by_item(item_code: str, db: Session = Depends(get_db)):
    return crud.get_first_lot_master_by_item(db, item_code)

@app.get("/api/master/history/{item_code}")
async def get_item_history(item_code: str, db: Session = Depends(get_db)):
    history = crud.get_first_lot_history(db, item_code)
    return history

# ── Supplier Email Routes ──────────────────────────────
@app.get("/emails/", response_class=HTMLResponse)
async def email_list_page(request: Request, db: Session = Depends(get_db)):
    suppliers = crud.get_supplier_emails(db)
    return templates.TemplateResponse("email_list.html", {
        "request": request,
        "suppliers": suppliers,
        "total": len(suppliers)
    })

@app.put("/api/supplier-email/{supplier_id}")
async def update_supplier_email_api(supplier_id: int, data: schemas.SupplierEmailCreate, db: Session = Depends(get_db)):
    updated = crud.update_supplier_email(db, supplier_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return {"status": "success", "message": "Supplier updated"}

@app.post("/api/supplier-email")
async def create_supplier_email_api(data: schemas.SupplierEmailCreate, db: Session = Depends(get_db)):
    created = crud.create_supplier_email(db, data)
    return {"status": "success", "id": created.id}

@app.delete("/api/supplier-email/{supplier_id}")
async def delete_supplier_email_api(supplier_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_supplier_email(db, supplier_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return {"status": "success", "message": "Supplier deleted"}

@app.post("/api/supplier-email/sync")
async def sync_supplier_emails():
    from import_data import sync_supplier_emails_from_sheet
    result = sync_supplier_emails_from_sheet()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/send-emails-bulk")
async def send_emails_bulk_api():
    from send_email import send_all_pending_emails
    result = send_all_pending_emails()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/send-emails-timeout")
async def send_emails_timeout_api():
    from send_email import send_all_timeout_emails
    result = send_all_timeout_emails()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.get("/email-preview/", response_class=HTMLResponse)
async def email_preview(request: Request, cpt_supplier: str = None, db: Session = Depends(get_db)):
    """Preview what the email will look like for a given supplier"""
    from datetime import date
    from dateutil.relativedelta import relativedelta
    
    # Get all supplier emails for dropdown
    suppliers = crud.get_supplier_emails(db)
    
    # Default to first supplier if none specified
    if not cpt_supplier and suppliers:
        cpt_supplier = suppliers[0].cpt_supplier
    
    supplier_info = None
    for s in suppliers:
        if s.cpt_supplier == cpt_supplier:
            supplier_info = s
            break
    
    if not supplier_info:
        return templates.TemplateResponse("email_template.html", {
            "request": request,
            "supplier_name": "N/A",
            "summary_items": [],
            "detail_rows": [],
            "md_name": "N/A",
            "md_email": "N/A"
        })
    
    # Get rows matching this CPT Supplier with sample_type containing '1st lot'
    rows = db.query(models.FirstLotRequest).filter(
        models.FirstLotRequest.cpt_supplier == cpt_supplier,
        models.FirstLotRequest.sample_type.ilike('%1st lot%')
    ).all()
    
    # Build summary (unique FG CC Code + Season pairs)
    seen = set()
    summary_items = []
    for r in rows:
        key = f"{r.fg_cc_code}|{r.season}"
        if key not in seen:
            seen.add(key)
            summary_items.append({"fg_cc_code": r.fg_cc_code, "season": r.season})
    
    # MD info from first row
    md_name = rows[0].md if rows else "N/A"
    md_email = "N/A"
    
    return templates.TemplateResponse("email_template.html", {
        "request": request,
        "supplier_name": supplier_info.supplier_name,
        "summary_items": summary_items,
        "detail_rows": rows,
        "md_name": md_name,
        "md_email": md_email
    })

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8009))
    uvicorn.run(app, host=host, port=port)
