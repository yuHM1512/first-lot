import os
from time import time
from types import SimpleNamespace
from fastapi import FastAPI, Depends, HTTPException, Request, Query, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from dateutil.relativedelta import relativedelta

import models, schemas, crud, database
from database import engine, get_db
from import_data import sync_from_google_sheets

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="1st Lot Management System")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Templates and Static files setup
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
class CachedStaticFiles(StaticFiles):
    def set_headers(self, response, path, stat_result):
        super().set_headers(response, path, stat_result)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory="templates")

# Helper for authentication
def get_user_from_cookie(request: Request):
    return request.cookies.get("user_code")

# Simple in-memory caches to speed page switching
USER_CACHE = {}
FILTER_OPTIONS_CACHE = {}
FIRST_LOT_CACHE = {}
HISTORY_CACHE = {}

USER_CACHE_TTL = 300
FILTER_OPTIONS_TTL = 300
FIRST_LOT_TTL = 120
HISTORY_TTL = 120

def _cache_get(cache: dict, key):
    entry = cache.get(key)
    if not entry:
        return None
    if entry["expires_at"] < time():
        cache.pop(key, None)
        return None
    return entry["value"]

def _cache_set(cache: dict, key, value, ttl: int):
    cache[key] = {"value": value, "expires_at": time() + ttl}

def _normalize_list(values: Optional[list[str]]) -> Tuple[str, ...]:
    if not values:
        return tuple()
    return tuple(sorted(values))

def clear_view_caches():
    FIRST_LOT_CACHE.clear()
    HISTORY_CACHE.clear()
    FILTER_OPTIONS_CACHE.clear()

def _build_user_view(staff):
    if not staff:
        return None
    return SimpleNamespace(
        employee_code=getattr(staff, "employee_code", None),
        name=getattr(staff, "name", None),
        role=getattr(staff, "role", None),
        department=getattr(staff, "department", None),
        email=getattr(staff, "email", None),
    )

def get_current_user(db: Session, request: Request):
    user_code = get_user_from_cookie(request)
    if not user_code:
        return None
    if user_code == "admin":
        return _build_user_view(models.StaffEmail(employee_code="admin", name="Administrator", role="admin", department="IT"))
    cached = _cache_get(USER_CACHE, user_code)
    if cached:
        return cached
    staff = db.query(models.StaffEmail).filter(models.StaffEmail.employee_code == user_code).first()
    user_view = _build_user_view(staff)
    if user_view:
        _cache_set(USER_CACHE, user_code, user_view, USER_CACHE_TTL)
    return user_view

def get_filter_options_cached(db: Session):
    cached = _cache_get(FILTER_OPTIONS_CACHE, "filters")
    if cached:
        return cached
    options = crud.get_unique_filter_values(db)
    _cache_set(FILTER_OPTIONS_CACHE, "filters", options, FILTER_OPTIONS_TTL)
    return options

def compute_first_lot_view(db: Session, season: str, model_description: str, item_code: str,
                           status: list[str], sample_type: list[str], email_status: list[str],
                           show_timeout: bool):
    from datetime import date, timedelta
    today = date.today()
    key = (
        season or "",
        model_description or "",
        item_code or "",
        _normalize_list(status),
        _normalize_list(sample_type),
        _normalize_list(email_status),
        bool(show_timeout),
        today.isoformat()
    )
    cached = _cache_get(FIRST_LOT_CACHE, key)
    if cached:
        return cached

    all_requests = crud.get_first_lot_requests(
        db,
        skip=0,
        limit=999999,
        season=season,
        model_description=model_description,
        item_code=item_code,
        sample_types=sample_type
    )

    pre_filtered = []

    for r in all_requests:
        using_years = r.master_using_time_years or 2

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

        info_reason = f"Lỗi thông tin: {r.master_lot_info_reason or 'Không rõ'}" if getattr(r, 'master_lot_info_status', None) == 'NOK' else ""
        quality_reason = f"Lỗi chất lượng: {r.master_lot_quality_reason or 'Không rõ'}" if getattr(r, 'master_lot_quality_status', None) == 'NOK' else ""
        r.reasons_str = " | ".join([rr for rr in [info_reason, quality_reason] if rr])

        r.is_timeout = False
        if r.validity_status != "Còn hiệu lực":
            if r.email_status == "SENT" and r.email_sent_at and today > (r.email_sent_at.date() + timedelta(days=7)):
                r.is_timeout = True

            if r.sample_type and ("1st lot" in r.sample_type.lower() or "1st" in r.sample_type.lower()):
                if hasattr(r, 'expected_arrival_date') and r.expected_arrival_date and today >= r.expected_arrival_date:
                    r.is_timeout = True

        if r.is_timeout:
            r.display_email_status = "Cần tái yêu cầu"
            r.email_status_color = "red"
        elif r.resend_count and r.resend_count > 0:
            r.display_email_status = "Tái yêu cầu"
            r.email_status_color = "amber"
        elif r.email_status == "SENT":
            r.display_email_status = "Đã yêu cầu"
            r.email_status_color = "emerald"
        else:
            r.display_email_status = "Chưa yêu cầu"
            r.email_status_color = "slate"

        if status and len(status) > 0 and r.validity_status not in status:
            continue
        if show_timeout and not r.is_timeout:
            continue
        if email_status and len(email_status) > 0 and r.display_email_status not in email_status:
            continue

        pre_filtered.append(r)

    da_gui = sum(1 for req in pre_filtered if req.email_status in ['SENT', 'NOT RECEIVED'])
    chua_gui = sum(1 for req in pre_filtered if req.email_status not in ['SENT', 'NOT RECEIVED'])
    qua_han = sum(1 for req in pre_filtered if req.is_timeout)

    result = {
        "pre_filtered": pre_filtered,
        "stats": {
            "total": len(pre_filtered),
            "da_gui": da_gui,
            "chua_gui": chua_gui,
            "qua_han": qua_han
        }
    }
    _cache_set(FIRST_LOT_CACHE, key, result, FIRST_LOT_TTL)
    return result

def compute_history_view(db: Session, item_code: str, fabric_name: str, status: list[str]):
    from datetime import date
    today = date.today()
    key = (item_code or "", fabric_name or "", _normalize_list(status), today.isoformat())
    cached = _cache_get(HISTORY_CACHE, key)
    if cached:
        return cached

    all_masters = crud.get_first_lot_master(db, skip=0, limit=999999, item_code=item_code, fabric_name=fabric_name)
    filtered_masters = []

    for m in all_masters:
        if m.received_date:
            expiration_date = m.received_date + relativedelta(years=m.using_time_years or 2)
            m.expiration_date = expiration_date
            m.is_valid = today <= expiration_date
        else:
            m.expiration_date = None
            m.is_valid = False

        m.status_vn = "Còn hiệu lực" if m.is_valid else "Hết hạn"

        if status and len(status) > 0 and m.status_vn not in status:
            continue

        filtered_masters.append(m)

    con_hieu_luc = sum(1 for m in filtered_masters if m.is_valid)
    stats = {
        "total": len(filtered_masters),
        "con_hieu_luc": con_hieu_luc,
        "het_hieu_luc": len(filtered_masters) - con_hieu_luc
    }

    result = {"filtered": filtered_masters, "stats": stats, "today": today}
    _cache_set(HISTORY_CACHE, key, result, HISTORY_TTL)
    return result

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: Optional[str] = None):
    return templates.TemplateResponse("login.html", {"request": request, "next": next})

@app.post("/api/login")
async def api_login(response: Response, employee_code: str = Form(...), db: Session = Depends(get_db)):
    if employee_code == "admin":
        staff = models.StaffEmail(employee_code="admin", name="Administrator", role="admin", department="IT")
    else:
        staff = db.query(models.StaffEmail).filter(models.StaffEmail.employee_code == employee_code).first()
        if not staff:
            raise HTTPException(status_code=401, detail="Mã nhân viên không tồn tại trong hệ thống")
    
    response.set_cookie(key="user_code", value=staff.employee_code, max_age=86400 * 30) # 30 days
    
    return {
        "status": "success",
        "employee_code": staff.employee_code,
        "name": staff.name,
        "role": staff.role,
        "department": staff.department
    }

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login")
    response.delete_cookie("user_code")
    return response

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/first-lot/", response_class=HTMLResponse)
async def read_first_lot(request: Request, page: int = 1, limit: int = 100, 
                  season: str = None, model_description: str = None, item_code: str = None,
                  fabric_name: str = None,
                  status: list[str] = Query(default=[]),
                  sample_type: list[str] = Query(default=[]),
                  email_status: list[str] = Query(default=[]),
                  show_timeout: bool = False,
                  db: Session = Depends(get_db)):
    
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    # KT Role: Force sample_type filter if not specified
    if user and user.role == "KT" and not sample_type:
        sample_type = ["CPT 1st lot &PPS", "1st Lot"] # Adjust based on actual data values
    
    skip = (page - 1) * limit
    view = compute_first_lot_view(
        db,
        season,
        model_description,
        item_code,
        status,
        sample_type,
        email_status,
        show_timeout
    )
    pre_filtered = view["pre_filtered"]
    stats = view["stats"]
    total_count = stats["total"]
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    requests_paged = pre_filtered[skip:skip+limit]
    
    pagination = {
        "page": page,
        "limit": limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1
    }

    # Get unique filter options for datalists
    filter_options = get_filter_options_cached(db)

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
            "email_status": email_status or [],
            "show_timeout": show_timeout
        },
        "user": user,
        "filter_options": filter_options
    })

@app.get("/requests/", response_model=List[schemas.FirstLotRequest])
def read_requests(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    requests = crud.get_first_lot_requests(db, skip=skip, limit=limit)
    return requests

@app.post("/requests/", response_model=schemas.FirstLotRequest)
def create_request(request: schemas.FirstLotRequestCreate, db: Session = Depends(get_db)):
    created = crud.create_first_lot_request(db=db, request=request)
    clear_view_caches()
    return created

@app.get("/master/", response_model=List[schemas.FirstLotMaster])
def read_master(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    master_data = crud.get_first_lot_master(db, skip=skip, limit=limit)
    return master_data

@app.post("/master/", response_model=schemas.FirstLotMaster)
def create_master(master: schemas.FirstLotMasterCreate, db: Session = Depends(get_db)):
    created = crud.create_first_lot_master(db=db, master=master)
    clear_view_caches()
    return created

@app.post("/api/sync")
async def sync_data():
    result = sync_from_google_sheets()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    clear_view_caches()
    return result

@app.get("/dashboard/", response_class=HTMLResponse)
async def read_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")
        
    performance_data = crud.get_supplier_performance(db)
    
    # Calculate Overview Metrics
    total_suppliers = len(performance_data)
    total_deliveries = sum(row["total_deliveries"] for row in performance_data)
    total_on_time = sum(row["on_time_deliveries"] for row in performance_data)
    
    avg_on_time_rate = (total_on_time / total_deliveries * 100) if total_deliveries > 0 else 0
    critical_suppliers = sum(1 for row in performance_data if row["status"] == "Critical")
    
    overview = {
        "avg_on_time_rate": round(avg_on_time_rate, 1),
        "total_suppliers": total_suppliers,
        "critical_count": critical_suppliers
    }
        
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "performance_data": performance_data,
        "overview": overview
    })

@app.get("/history/", response_class=HTMLResponse)
async def read_history_page(request: Request, page: int = 1, limit: int = 100, item_code: str = None, fabric_name: str = None,
                            status: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    skip = (page - 1) * limit
    view = compute_history_view(db, item_code, fabric_name, status)
    filtered_masters = view["filtered"]
    stats = view["stats"]
    today = view["today"]
    total_count = stats["total"]
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    masters = filtered_masters[skip:skip+limit]
    
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
        "user": user,
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
    clear_view_caches()
    return {"status": "success", "message": "Dates updated successfully"}

@app.get("/api/master/get/{item_code}", response_model=Optional[schemas.FirstLotMaster])
async def get_master_by_item(item_code: str, supplier_name: str = Query(None), db: Session = Depends(get_db)):
    return crud.get_first_lot_master_by_item(db, item_code, supplier_name)

@app.get("/api/master/history/{item_code}")
async def get_item_history(item_code: str, supplier_name: str = Query(None), db: Session = Depends(get_db)):
    history = crud.get_first_lot_history(db, item_code, supplier_name)
    return history

@app.put("/api/requests/{request_id}/received-status")
async def update_request_received_status(request_id: int, status_data: dict, db: Session = Depends(get_db)):
    status = status_data.get("status")
    if status not in ["Đã tiếp nhận", "Đã bàn giao", None]:
        raise HTTPException(status_code=400, detail="Invalid status")
    updated = crud.update_received_status(db, request_id, status)
    if not updated:
        raise HTTPException(status_code=404, detail="Request not found")

    # ðŸ”’ Persist to email_log by ser_no (survives future data resyncs)
    if updated.ser_no:
        # Get clean supplier name if possible
        supplier_name = None
        if updated.cpt_supplier:
            supp = db.query(models.SupplierEmail).filter_by(cpt_supplier=updated.cpt_supplier).first()
            if supp:
                supplier_name = supp.supplier_name

        log = db.query(models.EmailLog).filter_by(ser_no=updated.ser_no).first()
        if log:
            log.first_lot_received_status = status
            if updated.cpt_supplier:
                log.fabric_supplier = updated.cpt_supplier
                log.supplier_name = supplier_name
        else:
            db.add(models.EmailLog(
                ser_no=updated.ser_no,
                item=updated.item,
                season=updated.season,
                fabric_supplier=updated.cpt_supplier,
                supplier_name=supplier_name,
                first_lot_received_status=status
            ))
        db.commit()

    clear_view_caches()
    return {"status": "success", "message": "Received status updated"}

# â”€â”€ Supplier Email Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.get("/emails/", response_class=HTMLResponse)
async def email_list_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    # Restriction: Only admin and MD can access email list
    if user.role not in ["admin", "MD"]:
        return HTMLResponse(content="<h3>Bạn không có quyền truy cập trang này</h3>", status_code=403)

    suppliers = crud.get_supplier_emails(db)
    staff_emails = crud.get_staff_emails(db)
    return templates.TemplateResponse("email_list.html", {
        "request": request,
        "user": user,
        "suppliers": suppliers,
        "staff_emails": staff_emails,
        "total": len(suppliers),
        "total_staff": len(staff_emails)
    })

# â”€â”€ Staff Email API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.post("/api/staff-email")
async def create_staff_email_api(data: schemas.StaffEmailCreate, db: Session = Depends(get_db)):
    created = crud.create_staff_email(db, data)
    return {"status": "success", "id": created.id}

@app.put("/api/staff-email/{staff_id}")
async def update_staff_email_api(staff_id: int, data: schemas.StaffEmailCreate, db: Session = Depends(get_db)):
    updated = crud.update_staff_email(db, staff_id, data)
    if not updated:
        raise HTTPException(status_code=404, detail="Staff not found")
    return {"status": "success", "message": "Staff updated"}

@app.delete("/api/staff-email/{staff_id}")
async def delete_staff_email_api(staff_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_staff_email(db, staff_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Staff not found")
    return {"status": "success", "message": "Staff deleted"}

# â”€â”€ Supplier Email API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    port = int(os.getenv("PORT", 8010))
    uvicorn.run(app, host=host, port=port)







