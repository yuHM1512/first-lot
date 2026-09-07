import os
import re
from time import time, sleep
from types import SimpleNamespace
from threading import Thread, Event
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Query, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text, func, or_
from typing import List, Optional, Tuple
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta

import models, schemas, crud, database
from database import engine, get_db, SessionLocal
from import_data import sync_from_google_sheets, get_rank2_dashboard_data, sync_rank2_from_sheets
from send_email import send_email, build_schedule_alert_email_html

# Create database tables
models.Base.metadata.create_all(bind=engine)

def ensure_master_quality_columns():
    inspector = inspect(engine)
    if "first_lot_master" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("first_lot_master")}
    with engine.begin() as conn:
        if "quality_checked_at" not in columns:
            conn.execute(text("ALTER TABLE first_lot_master ADD COLUMN quality_checked_at TIMESTAMP"))
        if "quality_checked_by" not in columns:
            conn.execute(text("ALTER TABLE first_lot_master ADD COLUMN quality_checked_by VARCHAR(64)"))
        # Backfill for obvious already-checked rows (NOK or has quality reason)
        conn.execute(text("""
            UPDATE first_lot_master
            SET quality_checked_at = COALESCE(quality_checked_at, created_at)
            WHERE quality_checked_at IS NULL
              AND (lot_quality_status IS NOT NULL AND (lot_quality_status <> 'OK' OR lot_quality_reason IS NOT NULL))
        """))

ensure_master_quality_columns()

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

SCHEDULE_EMAIL_SUBJECT_TEMPLATE = "Danh sách vải sắp về nhưng chưa có 1st lot [Ngày gửi - Ngày gửi+14]"
SCHEDULE_EMAIL_BODY_TEMPLATE = (
    "Dear team MD,\n"
    "Dưới đây là danh sách vải sắp về từ [Ngày gửi - Ngày gửi+14] nhưng chưa có 1st lot, "
    "vui lòng kiểm tra và đôn đốc việc xin cấp 1st lot từ nhà cung ứng!"
)

SCHEDULE_EMAIL_STOP = Event()
SCHEDULE_EMAIL_THREAD = None

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

@app.post("/api/clear-cache")
async def clear_cache_api():
    clear_view_caches()
    return {"status": "success", "message": "Caches cleared"}

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

def build_filter_options_from_requests(requests):
    seasons = sorted({r.season for r in requests if getattr(r, "season", None)})
    model_descriptions = sorted({r.model_description for r in requests if getattr(r, "model_description", None)})
    item_codes = sorted({r.item_code for r in requests if getattr(r, "item_code", None)})
    fg_cc_codes = sorted({r.fg_cc_code for r in requests if getattr(r, "fg_cc_code", None)})
    sample_types = sorted({r.sample_type for r in requests if getattr(r, "sample_type", None)})
    supplier_names = sorted({r.supplier_lookup_name for r in requests if getattr(r, "supplier_lookup_name", None)})

    return {
        "seasons": seasons,
        "model_descriptions": model_descriptions,
        "item_codes": item_codes,
        "fg_cc_codes": fg_cc_codes,
        "sample_types": sample_types,
        "supplier_names": supplier_names
    }

def compute_first_lot_view(db: Session, season: str, model_description: str, item_code: str, supplier_name: str,
                           fg_cc_code: str,
                           status: list[str], sample_type: list[str], email_status: list[str],
                           quality_status: str, received_status: str,
                           show_timeout: bool):
    from datetime import date, timedelta
    today = date.today()
    key = (
        season or "",
        model_description or "",
        item_code or "",
        supplier_name or "",
        fg_cc_code or "",
        _normalize_list(status),
        _normalize_list(sample_type),
        _normalize_list(email_status),
        quality_status or "",
        received_status or "",
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
        sample_types=sample_type,
        supplier_name=supplier_name,
        fg_cc_code=fg_cc_code
    )

    pre_filtered = []

    for r in all_requests:
        using_years = r.master_using_time_years or 3

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

def compute_history_view(db: Session, item_code: str, fabric_name: str, supplier_name: str, status: list[str]):
    from datetime import date
    today = date.today()
    key = (item_code or "", fabric_name or "", supplier_name or "", _normalize_list(status), today.isoformat())
    cached = _cache_get(HISTORY_CACHE, key)
    if cached:
        return cached

    all_masters = crud.get_first_lot_master(db, skip=0, limit=999999, item_code=item_code, fabric_name=fabric_name)
    filtered_masters = []
    supplier_names = sorted({m.fabric_supplier for m in all_masters if getattr(m, "fabric_supplier", None)})

    for m in all_masters:
        if supplier_name and (not m.fabric_supplier or supplier_name.lower() not in m.fabric_supplier.lower()):
            continue
        if m.received_date:
            expiration_date = m.received_date + relativedelta(years=m.using_time_years or 3)
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

    result = {"filtered": filtered_masters, "stats": stats, "today": today, "supplier_names": supplier_names}
    _cache_set(HISTORY_CACHE, key, result, HISTORY_TTL)
    return result

def _build_schedule_rows(db: Session, start_date: date, end_date: date,
                         season: str = None, iman: str = None, cpt_description: str = None,
                         supplier_name: str = None):
    q = db.query(models.ScheduleFabric)
    if start_date:
        q = q.filter(models.ScheduleFabric.etd >= start_date)
    if end_date:
        q = q.filter(models.ScheduleFabric.etd <= end_date)
    if season:
        q = q.filter(models.ScheduleFabric.season.ilike(f"%{season}%"))
    if iman:
        q = q.filter(models.ScheduleFabric.iman.ilike(f"%{iman}%"))
    if cpt_description:
        q = q.filter(models.ScheduleFabric.cpt_description.ilike(f"%{cpt_description}%"))
    if supplier_name:
        q = q.filter(models.ScheduleFabric.supplier_name.ilike(f"%{supplier_name}%"))

    q = q.order_by(models.ScheduleFabric.etd.asc())
    schedules = q.all()

    item_codes = {s.item_code for s in schedules if s.item_code}
    suppliers = {s.supplier_name for s in schedules if s.supplier_name}

    master_lookup = {}
    if item_codes and suppliers:
        masters = db.query(models.FirstLotMaster) \
            .filter(models.FirstLotMaster.item_code.in_(item_codes)) \
            .filter(models.FirstLotMaster.fabric_supplier.in_(suppliers)) \
            .all()

        for m in masters:
            key = (m.item_code, m.fabric_supplier)
            existing = master_lookup.get(key)
            if not existing:
                master_lookup[key] = m
                continue
            if m.received_date and (not existing.received_date or m.received_date > existing.received_date):
                master_lookup[key] = m

    rows = []
    for s in schedules:
        master = master_lookup.get((s.item_code, s.supplier_name))
        status = "Chưa có"
        status_color = "slate"
        expiration_str = ""

        if master and master.received_date:
            if s.etd and master.received_date > s.etd:
                status = "Chưa có"
                status_color = "slate"
            else:
                using_years = master.using_time_years or 3
                expiration_date = master.received_date + relativedelta(years=using_years)
                compare_date = s.etd or start_date
                expiration_str = expiration_date.strftime("%d/%m/%Y")
                if compare_date <= expiration_date:
                    status = "Còn hiệu lực"
                    status_color = "green"
                else:
                    status = "Hết hiệu lực"
                    status_color = "red"

        rows.append({
            "season": s.season,
            "iman": s.iman,
            "cpt_description": s.cpt_description,
            "model_code": s.model_code,
            "item_code": s.item_code,
            "supplier_name": s.supplier_name,
            "etd": s.etd,
            "etd_str": s.etd.strftime("%d/%m/%Y") if s.etd else "",
            "status": status,
            "status_color": status_color,
            "expiration_str": expiration_str
        })
    return rows

def _get_schedule_alert_rows(db: Session, start_date: date, end_date: date):
    rows = _build_schedule_rows(db, start_date, end_date)
    return [r for r in rows if r["status"] in ["Chưa có", "Hết hiệu lực"]]

def _format_date_range(start_date: date, end_date: date) -> str:
    return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"

def _apply_date_range_template(text: str, start_date: date, end_date: date) -> str:
    if text is None:
        return ""
    formatted_range = _format_date_range(start_date, end_date)

    # Backward-compat with old placeholders (+7 / +14).
    text = text.replace("[Ngày gửi - Ngày gửi+7]", formatted_range)
    text = text.replace("[Ngày gửi - Ngày gửi+14]", formatted_range)

    # Some saved settings already contain a concrete date range from an older preview.
    # Replace any existing dd/mm/yyyy - dd/mm/yyyy range so subject/body always match today -> today+14.
    return re.sub(r"\b\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}\b", formatted_range, text)

def _resolve_staff_emails(db: Session, names: list[str]) -> list[str]:
    if not names:
        return []
    rows = db.query(models.StaffEmail).filter(models.StaffEmail.name.in_(names)).all()
    emails = []
    for r in rows:
        if not r.email:
            continue
        for e in str(r.email).split(","):
            e = e.strip()
            if e:
                emails.append(e)
    return sorted(list(set(emails)))

def _send_schedule_alert_email(db: Session, recipient_names: list[str], subject: Optional[str], body: Optional[str]):
    tz = ZoneInfo("Asia/Bangkok")
    range_start = datetime.now(tz).date()
    range_end = range_start + timedelta(days=14)

    rows = _get_schedule_alert_rows(db, range_start, range_end)
    if not rows:
        return {"status": "skipped", "message": "No matching rows to send"}

    to_emails = _resolve_staff_emails(db, recipient_names)
    if not to_emails:
        return {"status": "error", "message": "No valid recipient emails selected"}

    subject_final = _apply_date_range_template(subject or SCHEDULE_EMAIL_SUBJECT_TEMPLATE, range_start, range_end)
    body_final = _apply_date_range_template(body or SCHEDULE_EMAIL_BODY_TEMPLATE, range_start, range_end)
    date_range_str = _format_date_range(range_start, range_end)

    # Backward-compat: normalize legacy mojibake values to proper Vietnamese.
    status_map = {
        "Chưa có": "Chưa có",
        "Hết hiệu lực": "Hết hiệu lực",
        "Còn hiệu lực": "Còn hiệu lực",
        "ChÆ°a cÃ³": "Chưa có",
        "Háº¿t hiá»‡u lá»±c": "Hết hiệu lực",
        "CÃ²n hiá»‡u lá»±c": "Còn hiệu lực",
    }
    rows_email = []
    for r in rows:
        r_copy = dict(r)
        r_copy["status"] = status_map.get(r_copy.get("status"), r_copy.get("status"))
        rows_email.append(r_copy)

    html_body = build_schedule_alert_email_html(body_final, rows_email, date_range_str)
    send_email(
        to_emails=",".join(to_emails),
        subject=subject_final,
        html_body=html_body
    )
    return {"status": "success", "count": len(rows), "recipients": to_emails}

def _schedule_email_worker():
    tz = ZoneInfo("Asia/Bangkok")
    while not SCHEDULE_EMAIL_STOP.is_set():
        db = SessionLocal()
        try:
            settings = db.query(models.ScheduleEmailSetting).first()
            if settings and settings.is_active and settings.send_time:
                now = datetime.now(tz)
                try:
                    send_hour, send_min = [int(x) for x in settings.send_time.split(":")]
                except Exception:
                    send_hour, send_min = None, None

                if send_hour is not None and send_min is not None:
                    if now.weekday() == settings.send_day and now.hour == send_hour and now.minute == send_min:
                        last_sent = settings.last_sent_at
                        if not last_sent or last_sent.date() != now.date():
                            result = _send_schedule_alert_email(
                                db,
                                [n.strip() for n in (settings.recipient_names or "").split(",") if n.strip()],
                                settings.subject,
                                settings.body
                            )
                            if result.get("status") == "success":
                                settings.last_sent_at = now
                                db.commit()
        except Exception as e:
            print(f"[ScheduleEmail] Error: {e}")
        finally:
            db.close()

        sleep(30)

@app.on_event("startup")
def start_schedule_email_worker():
    global SCHEDULE_EMAIL_THREAD
    if SCHEDULE_EMAIL_THREAD and SCHEDULE_EMAIL_THREAD.is_alive():
        return
    SCHEDULE_EMAIL_STOP.clear()
    SCHEDULE_EMAIL_THREAD = Thread(target=_schedule_email_worker, daemon=True)
    SCHEDULE_EMAIL_THREAD.start()

@app.on_event("shutdown")
def stop_schedule_email_worker():
    SCHEDULE_EMAIL_STOP.set()

def _parse_date_param(value: Optional[str], fallback: date) -> date:
    if not value:
        return fallback
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except (ValueError, TypeError):
            continue
    return fallback

def get_qa_pending_count(db: Session) -> int:
    r = models.FirstLotRequest
    s = models.SupplierEmail
    m = models.FirstLotMaster
    count = db.query(func.count(func.distinct(r.ser_no))).select_from(r) \
        .outerjoin(s, r.cpt_supplier == s.cpt_supplier) \
        .outerjoin(m, (r.item_code == m.item_code) & (s.supplier_name == m.fabric_supplier)) \
        .filter(r.ser_no.isnot(None)) \
        .filter(r.first_lot_received_status == "Đã bàn giao") \
        .filter(m.quality_checked_at.is_(None)) \
        .scalar()
    return int(count or 0)

def get_kt_pending_email_count(db: Session) -> int:
    r = models.FirstLotRequest
    e = models.EmailLog
    kt_sample_types = ["CPT 1st lot &PPS", "1st Lot"]
    q = db.query(func.count()).select_from(r).outerjoin(e, r.ser_no == e.ser_no)
    q = q.filter(r.sample_type.in_(kt_sample_types))
    q = q.filter(or_(e.email_status.is_(None), ~e.email_status.in_(["SENT", "NOT RECEIVED"])))
    count = q.scalar()
    return int(count or 0)

def get_md_timeout_count(db: Session) -> int:
    view = compute_first_lot_view(
        db,
        season=None,
        model_description=None,
        item_code=None,
        supplier_name=None,
        fg_cc_code=None,
        status=[],
        sample_type=[],
        email_status=[],
        quality_status=None,
        received_status=None,
        show_timeout=False
    )
    return sum(1 for req in view["pre_filtered"] if getattr(req, "is_timeout", False))

def compute_notifications(db: Session, user):
    if not user:
        return {"count": 0, "items": []}
    items = []
    total = 0
    if user.role == "QA":
        pending = get_qa_pending_count(db)
        if pending > 0:
            items.append({
                "message": f"Có {pending} 1st lot mới vừa được bàn giao, cần kiểm tra",
                "hint": "Mở danh sách để cập nhật Kiểm chất lượng."
            })
            total += pending
    elif user.role == "KT":
        pending = get_kt_pending_email_count(db)
        if pending > 0:
            items.append({
                "message": f"Có {pending} yêu cầu 1st lot chưa gửi email",
                "hint": "Kiểm tra và gửi email cho NCC."
            })
            total += pending
    elif user.role in ["MD", "admin"]:
        pending = get_md_timeout_count(db)
        if pending > 0:
            items.append({
                "message": f"Có {pending} yêu cầu chậm phản hồi (>7 ngày)",
                "hint": "Cần xử lý hoặc tái yêu cầu."
            })
            total += pending
    return {"count": total, "items": items}

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

@app.get("/kpi-dashboard/", response_class=HTMLResponse)
async def read_kpi_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    return templates.TemplateResponse("kpi_dashboard.html", {"request": request, "user": user})

@app.get("/kpi-dashboard/cpt-rank-2/", response_class=HTMLResponse)
async def read_cpt_rank_2_dashboard(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(url="/kpi-dashboard/cpt-rank-2/2026/")

@app.get("/kpi-dashboard/cpt-rank-2/{year}/", response_class=HTMLResponse)
async def read_cpt_rank_2_dashboard_by_year(
    request: Request,
    year: str,
    customer: str = "ALL",
    db: Session = Depends(get_db)
):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    try:
        dashboard = get_rank2_dashboard_data(year=year, customer=customer)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return templates.TemplateResponse("cpt_rank_2_dashboard.html", {
        "request": request,
        "user": user,
        "dashboard": dashboard,
        "years": ["2026", "2025", "2024", "2023"],
        "customer_options": [
            {"label": "All", "value": "ALL"},
            {"label": "Decathlon", "value": "DECATHLON"},
            {"label": "Khác", "value": "KHAC"},
        ],
    })

@app.post("/api/cpt-rank-2/refresh")
async def refresh_cpt_rank_2(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)
    triggered_by = getattr(user, "email", None) or getattr(user, "name", None) or "user"
    result = sync_rank2_from_sheets(triggered_by=triggered_by)
    status_code = 200 if result.get("status") == "success" else 500
    return JSONResponse(result, status_code=status_code)
@app.get("/first-lot/", response_class=HTMLResponse)
async def read_first_lot(
    request: Request,
    page: int = 1,
    limit: int = 100,
    season: str = None,
    model_description: str = None,
    fg_cc_code: str = None,
    item_code: str = None,
    supplier_name: str = None,
    fabric_name: str = None,
    status: list[str] = Query(default=[]),
    sample_type: list[str] = Query(default=[]),
    email_status: list[str] = Query(default=[]),
    quality_status: str = None,
    received_status: str = None,
    show_timeout: bool = False,
    db: Session = Depends(get_db),
):
    
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    # QA/KT Role: Force sample_type filter to CPT 1st lot &PPS
    if user and user.role in ["KT", "QA"]:
        sample_type = ["CPT 1st lot &PPS"]
    
    skip = (page - 1) * limit
    view = compute_first_lot_view(
        db,
        season,
        model_description,
        item_code,
        supplier_name,
        fg_cc_code,
        status,
        sample_type,
        email_status,
        quality_status,
        received_status,
        show_timeout
    )
    pre_filtered = view["pre_filtered"]
    stats = view["stats"]
    if user and user.role == "QA":
        pre_filtered = [r for r in pre_filtered if r.first_lot_received_status in ["Đã tiếp nhận", "Đã bàn giao"]]
        pre_filtered.sort(key=lambda r: 0 if r.first_lot_received_status == "Đã bàn giao" else 1)
        if received_status:
            pre_filtered = [r for r in pre_filtered if r.first_lot_received_status == received_status]
        if quality_status:
            if quality_status == "Đã kiểm":
                pre_filtered = [r for r in pre_filtered if r.master_quality_checked_at is not None]
            elif quality_status == "Chưa kiểm":
                pre_filtered = [r for r in pre_filtered if r.master_quality_checked_at is None]
        stats["total"] = len(pre_filtered)
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

    # Build dynamic filter options based on current filtered dataset
    filter_options = build_filter_options_from_requests(pre_filtered)
    notifications = compute_notifications(db, user)
    notifications_items = notifications.get("items", [])
    notifications_count = notifications.get("count", 0)

    return templates.TemplateResponse("first_lot.html", {
        "request": request, 
        "requests": requests_paged, 
        "stats": stats,
        "pagination": pagination,
        "filters": {
            "season": season or "",
            "model_description": model_description or "",
            "fg_cc_code": fg_cc_code or "",
            "item_code": item_code or "",
            "supplier_name": supplier_name or "",
            "status": status or [],
            "sample_type": sample_type or [],
            "email_status": email_status or [],
            "quality_status": quality_status or "",
            "received_status": received_status or "",
            "show_timeout": show_timeout
        },
        "user": user,
        "filter_options": filter_options,
        "notifications": notifications,
        "notifications_items": notifications_items,
        "notifications_count": notifications_count
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

@app.get("/guide/", response_class=HTMLResponse)
async def read_guide(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")
    return templates.TemplateResponse("guide.html", {
        "request": request,
        "user": user
    })

@app.get("/fabric-schedule/", response_class=HTMLResponse)
async def read_fabric_schedule(
    request: Request,
    from_date: str = None,
    to_date: str = None,
    page: int = 1,
    limit: int = 50,
    season: str = None,
    iman: str = None,
    cpt_description: str = None,
    supplier_name: str = None,
    validity_status: list[str] = Query(default=[]),
    db: Session = Depends(get_db)
):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    today = date.today()
    default_from = today
    default_to = today + timedelta(days=7)
    start_date = _parse_date_param(from_date, default_from)
    end_date = _parse_date_param(to_date, default_to)

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    q = db.query(models.ScheduleFabric)
    if start_date:
        q = q.filter(models.ScheduleFabric.etd >= start_date)
    if end_date:
        q = q.filter(models.ScheduleFabric.etd <= end_date)
    if season:
        q = q.filter(models.ScheduleFabric.season.ilike(f"%{season}%"))
    if iman:
        q = q.filter(models.ScheduleFabric.iman.ilike(f"%{iman}%"))
    if cpt_description:
        q = q.filter(models.ScheduleFabric.cpt_description.ilike(f"%{cpt_description}%"))
    if supplier_name:
        q = q.filter(models.ScheduleFabric.supplier_name.ilike(f"%{supplier_name}%"))
    # Calculate interdependent filter options for dropdowns based on current query
    all_filtered = q.with_entities(
        models.ScheduleFabric.season, 
        models.ScheduleFabric.iman, 
        models.ScheduleFabric.cpt_description, 
        models.ScheduleFabric.supplier_name
    ).all()
    
    filter_options = {
        "seasons": sorted(list({r[0] for r in all_filtered if r[0]})),
        "imans": sorted(list({r[1] for r in all_filtered if r[1]})),
        "cpt_descriptions": sorted(list({r[2] for r in all_filtered if r[2]})),
        "supplier_names": sorted(list({r[3] for r in all_filtered if r[3]}))
    }
    
    rows = _build_schedule_rows(
        db,
        start_date,
        end_date,
        season=season,
        iman=iman,
        cpt_description=cpt_description,
        supplier_name=supplier_name
    )

    filter_options["validity_statuses"] = sorted({r["status"] for r in rows if r.get("status")})

    if validity_status and len(validity_status) > 0:
        rows = [r for r in rows if r["status"] in validity_status]

    total_count = len(rows)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * limit
    rows = rows[offset:offset + limit]


    return templates.TemplateResponse("fabric-schedule.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "from_date": start_date.strftime("%Y-%m-%d"),
        "to_date": end_date.strftime("%Y-%m-%d"),
        "default_from": default_from.strftime("%Y-%m-%d"),
        "default_to": default_to.strftime("%Y-%m-%d"),
        "filters": {
            "season": season or "",
            "iman": iman or "",
            "cpt_description": cpt_description or "",
            "supplier_name": supplier_name or "",
            "validity_status": validity_status or []
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1
        },
        "filter_options": filter_options
    })

@app.get("/email-setup/", response_class=HTMLResponse)
async def read_email_setup(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")
    
    # Restriction: Only admin and MD can access email setup
    if user.role not in ["admin", "MD"]:
        return HTMLResponse(content="<h3>Bạn không có quyền truy cập trang này</h3>", status_code=403)

    staff_emails = crud.get_staff_emails(db)
    settings = db.query(models.ScheduleEmailSetting).first()

    tz = ZoneInfo("Asia/Bangkok")
    range_start = datetime.now(tz).date()
    range_end = range_start + timedelta(days=14)

    if settings:
        selected_names = [n.strip() for n in (settings.recipient_names or "").split(",") if n.strip()]
        current_subject = _apply_date_range_template(settings.subject or SCHEDULE_EMAIL_SUBJECT_TEMPLATE, range_start, range_end)
        current_body = _apply_date_range_template(settings.body or SCHEDULE_EMAIL_BODY_TEMPLATE, range_start, range_end)
        current_day = settings.send_day if settings.send_day is not None else 0
        current_time = settings.send_time or "09:00"
        current_active = True if settings.is_active is None else settings.is_active
    else:
        selected_names = []
        current_subject = _apply_date_range_template(SCHEDULE_EMAIL_SUBJECT_TEMPLATE, range_start, range_end)
        current_body = _apply_date_range_template(SCHEDULE_EMAIL_BODY_TEMPLATE, range_start, range_end)
        current_day = 0
        current_time = "09:00"
        current_active = True

    return templates.TemplateResponse("email-setup.html", {
        "request": request,
        "user": user,
        "staff_emails": staff_emails,
        "schedule_email": {
            "selected_names": selected_names,
            "subject": current_subject,
            "body": current_body,
            "send_day": current_day,
            "send_time": current_time,
            "is_active": current_active
        }
    })

@app.get("/history/", response_class=HTMLResponse)
async def read_history_page(request: Request, page: int = 1, limit: int = 100, item_code: str = None, fabric_name: str = None,
                            supplier_name: str = None,
                            status: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    user = get_current_user(db, request)
    if not user:
        return RedirectResponse(url=f"/login?next={request.url.path}")

    skip = (page - 1) * limit
    view = compute_history_view(db, item_code, fabric_name, supplier_name, status)
    filtered_masters = view["filtered"]
    stats = view["stats"]
    today = view["today"]
    supplier_names = view.get("supplier_names", [])
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
            "supplier_name": supplier_name or "",
            "status": status
        },
        "supplier_names": supplier_names
    })

@app.post("/api/master/update-dates")
async def update_master_dates(update_data: schemas.FirstLotDateUpdate, request: Request, db: Session = Depends(get_db)):
    if (update_data.fabric_supplier is None or str(update_data.fabric_supplier).strip() == "") and update_data.request_id:
        req = db.query(models.FirstLotRequest).filter(models.FirstLotRequest.id == update_data.request_id).first()
        if req and req.cpt_supplier:
            supp = db.query(models.SupplierEmail).filter_by(cpt_supplier=req.cpt_supplier).first()
            if supp and supp.supplier_name:
                update_data.fabric_supplier = supp.supplier_name

    updated = crud.update_first_lot_master_dates(db, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Item not found")
    user = get_current_user(db, request)
    if user and user.role == "QA":
        updated.quality_checked_at = datetime.utcnow()
        updated.quality_checked_by = user.employee_code or user.name
        db.commit()
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

@app.post("/api/staff-email/sync")
async def sync_staff_emails():
    from import_data import sync_staff_emails_from_sheet
    result = sync_staff_emails_from_sheet()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

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

@app.post("/api/schedule-fabric/sync")
async def sync_schedule_fabric():
    from import_data import sync_schedule_fabric_from_sheet
    result = sync_schedule_fabric_from_sheet()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/schedule-fabric/sync-mapping")
async def sync_schedule_fabric_mapping():
    from import_data import sync_fabric_supplier_match_from_sheet
    result = sync_fabric_supplier_match_from_sheet()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@app.post("/api/schedule-email/send-now")
async def send_schedule_email_now(payload: schemas.ScheduleEmailSendNow, db: Session = Depends(get_db)):
    result = _send_schedule_alert_email(db, payload.recipient_names, payload.subject, payload.body)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result

@app.post("/api/schedule-email/settings")
async def save_schedule_email_settings(payload: schemas.ScheduleEmailSettings, db: Session = Depends(get_db)):
    settings = db.query(models.ScheduleEmailSetting).first()
    if not settings:
        settings = models.ScheduleEmailSetting(
            recipient_names=",".join(payload.recipient_names or []),
            subject=payload.subject,
            body=payload.body,
            send_day=payload.send_day,
            send_time=payload.send_time,
            is_active=payload.is_active
        )
        db.add(settings)
    else:
        settings.recipient_names = ",".join(payload.recipient_names or [])
        settings.subject = payload.subject
        settings.body = payload.body
        settings.send_day = payload.send_day
        settings.send_time = payload.send_time
        settings.is_active = payload.is_active
    db.commit()
    return {"status": "success"}

@app.post("/api/send-emails-bulk")
async def send_emails_bulk_api(sample_kind: Optional[str] = None):
    from send_email import send_all_pending_emails, PENDING_SAMPLE_TYPES
    if sample_kind is not None and sample_kind not in PENDING_SAMPLE_TYPES:
        raise HTTPException(status_code=422, detail="Invalid sample kind")
    result = send_all_pending_emails(sample_kind=sample_kind)
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

@app.post("/api/send-email-timeout-row")
async def send_email_timeout_row_api(data: schemas.TimeoutEmailRowRequest):
    from send_email import send_timeout_email_for_row
    result = send_timeout_email_for_row(data.ser_no)
    if result.get("status") == "success":
        return result
    if result.get("status") == "skipped":
        raise HTTPException(status_code=400, detail=result.get("message", "Row not eligible for resend"))
    raise HTTPException(status_code=500, detail=result.get("message", "Failed to send email"))

@app.post("/api/send-email-timeout-rows")
async def send_email_timeout_rows_api(data: schemas.TimeoutEmailRowsRequest):
    from send_email import send_timeout_email_for_rows
    result = send_timeout_email_for_rows(data.ser_nos)
    if result.get("status") == "success":
        return result
    if result.get("status") == "skipped":
        raise HTTPException(status_code=400, detail=result.get("message", "No rows eligible for resend"))
    raise HTTPException(status_code=500, detail=result.get("message", "Failed to send emails"))

@app.post("/api/send-email-missing-1stlot-rows")
async def send_email_missing_1stlot_rows_api(data: schemas.MissingFirstLotRowsRequest):
    from send_email import send_missing_first_lot_email_for_rows
    result = send_missing_first_lot_email_for_rows(data.ser_nos)
    if result.get("status") == "success":
        return result
    if result.get("status") == "skipped":
        raise HTTPException(status_code=400, detail=result.get("message", "No rows eligible to send"))
    raise HTTPException(status_code=500, detail=result.get("message", "Failed to send emails"))

@app.post("/api/send-email-missing-1stlot")
async def send_email_missing_1stlot_api(data: dict):
    ser_no = data.get("ser_no")
    items = data.get("items")
    if not ser_no:
        raise HTTPException(status_code=400, detail="Missing ser_no")
    if not isinstance(items, list) or len(items) == 0:
        raise HTTPException(status_code=400, detail="Missing items")
    from send_email import send_missing_first_lot_email_for_row
    result = send_missing_first_lot_email_for_row(ser_no, items)
    if result.get("status") == "success":
        return result
    raise HTTPException(status_code=500, detail=result.get("message", "Failed to send email"))

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
