"""
Email sending utility for 1st Lot system.
Uses Gmail SMTP with App Password.
"""
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.sql import func
import models
from datetime import datetime
from database import SessionLocal

load_dotenv()

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Jinja2 template loader
template_env = Environment(loader=FileSystemLoader("templates"))

def build_email_html(supplier_name, summary_items, detail_rows, md_name="N/A", md_email="N/A", show_1st_lot_note=False):
    """Render the email HTML template with data."""
    template = template_env.get_template("email_template.html")
    return template.render(
        supplier_name=supplier_name,
        summary_items=summary_items,
        detail_rows=detail_rows,
        md_name=md_name,
        md_email=md_email,
        show_1st_lot_note=show_1st_lot_note
    )

def build_missing_1stlot_email_html(supplier_name, row, request_line, intro_line):
    """Render the missing 1st lot email template."""
    template = template_env.get_template("email_template_missing_1stlot.html")
    return template.render(
        supplier_name=supplier_name,
        row=row,
        request_line=request_line,
        intro_line=intro_line
    )

def build_missing_1stlot_bulk_email_html(supplier_name, rows, request_line, intro_line):
    """Render the missing 1st lot email template for multiple rows."""
    template = template_env.get_template("email_template_missing_1stlot_bulk.html")
    return template.render(
        supplier_name=supplier_name,
        rows=rows,
        request_line=request_line,
        intro_line=intro_line
    )

def build_schedule_alert_email_html(body_text, detail_rows, date_range_str):
    """Render the schedule alert email template."""
    template = template_env.get_template("email_template_schedule_alert.html")
    return template.render(
        body_text=body_text,
        detail_rows=detail_rows,
        date_range_str=date_range_str
    )

def send_email(to_emails, subject, html_body, cc_emails=None):
    """Send an HTML email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_emails
    msg["Subject"] = subject
    if cc_emails:
        msg["Cc"] = cc_emails

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    all_recipients = [e.strip() for e in to_emails.split(",")]
    if cc_emails:
        all_recipients += [e.strip() for e in cc_emails.split(",")]

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, all_recipients, msg.as_string())

    print(f"[OK] Email sent to: {to_emails}" + (f" (CC: {cc_emails})" if cc_emails else ""))

def _normalize_sample_type(value: str) -> str:
    if not value:
        return ""
    return str(value).strip().lower()

def _resolve_staff_emails_by_names(db, names: list[str]) -> list[str]:
    if not names:
        return []
    name_list = [n.strip() for n in names if n and str(n).strip()]
    if not name_list:
        return []
    rows = db.query(models.StaffEmail.email).filter(models.StaffEmail.name.in_(name_list)).all()
    return [r[0] for r in rows if r and r[0]]

def build_cc_emails_by_sample_type(db, rows: list):
    """
    CC logic based on sample types:
    - Sample Request / Selection Request: CC "MS. VAN" + "thuvan@hachiba.com.vn"
    - 1st lot & PPS: CC "MS. VAN", "TOMAU", all QA, and KTDH for those rows
    - Hard CC always: hoailinh@hachiba.com.vn, quynhthi@hachiba.com.vn
    If multiple sample types exist, union all recipients.
    """
    has_sample_req = False
    has_selection_req = False
    has_first_lot = False

    for r in rows:
        st = _normalize_sample_type(getattr(r, "sample_type", None))
        if "sample request" in st:
            has_sample_req = True
        if "selection request" in st:
            has_selection_req = True
        if st == "cpt 1st lot &pps":
            has_first_lot = True

    cc_emails_list = []

    # Always CC MD(s) that are referenced by the rows being sent
    md_names = list(set(getattr(r, "md", None) for r in rows if getattr(r, "md", None)))
    if md_names:
        md_emails = db.query(models.StaffEmail.email).filter(
            models.StaffEmail.role == 'MD',
            models.StaffEmail.name.in_(md_names)
        ).all()
        cc_emails_list.extend([e[0] for e in md_emails if e and e[0]])

    # Sample Request / Selection Request -> MS. VAN + explicit email
    if has_sample_req or has_selection_req:
        cc_emails_list.extend(_resolve_staff_emails_by_names(db, ["MS. VAN"]))
        cc_emails_list.append("thuvan@hachiba.com.vn")

    # 1st lot & PPS -> MS. VAN, TOMAU, QA all, KTDH for rows
    if has_first_lot:
        cc_emails_list.extend(_resolve_staff_emails_by_names(db, ["MS. VAN", "TOMAU"]))
        qa_emails = db.query(models.StaffEmail.email).filter(models.StaffEmail.role == 'QA').all()
        cc_emails_list.extend([e[0] for e in qa_emails if e[0]])
        ktdh_names = list(set(getattr(r, "kt", None) for r in rows if getattr(r, "kt", None)))
        if ktdh_names:
            ktdh_emails = db.query(models.StaffEmail.email).filter(
                models.StaffEmail.role == 'KTDH',
                models.StaffEmail.name.in_(ktdh_names)
            ).all()
            cc_emails_list.extend([e[0] for e in ktdh_emails if e[0]])

    # Hard CC (always)
    cc_emails_list.append("hoailinh@hachiba.com.vn")
    cc_emails_list.append("quynhthi@hachiba.com.vn")

    cc_emails_str = ",".join(list(set(cc_emails_list))) if cc_emails_list else None
    return cc_emails_str

def send_email_to_supplier(cpt_supplier: str, mode: str = 'pending'):
    """
    Send email for a specific CPT Supplier.
    mode='pending': Send rows where email_status is not 'SENT' (New)
    mode='timeout': Resend rows where email_status is 'SENT' but timed out (>7 days)
    """
    db = SessionLocal()
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta
    today = date.today()

    try:
        # Get supplier email info
        supplier = db.query(models.SupplierEmail).filter(models.SupplierEmail.cpt_supplier == cpt_supplier).first()
        if not supplier:
            print(f"[SKIP] Supplier not found in email list: {cpt_supplier}")
            return {"status": "skipped", "message": f"Supplier not found: {cpt_supplier}"}
        
        # Skip if email is null/empty
        if not supplier.email or not supplier.email.strip():
            print(f"[SKIP] Supplier has no email, skipping: {cpt_supplier}")
            return {"status": "skipped", "message": f"No email for supplier: {cpt_supplier}"}

        # Base query with EmailLog join - NO Sample Type filter initially
        query = db.query(
            models.FirstLotRequest,
            models.EmailLog.email_status.label("log_email_status"),
            models.EmailLog.email_sent_at.label("log_email_sent_at"),
            models.EmailLog.resend_count.label("log_resend_count"),
            models.SupplierEmail.supplier_name.label("supplier_lookup_name")
        ).join(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no
        ).filter(
            models.FirstLotRequest.cpt_supplier == cpt_supplier
        )

        results = query.all()
        rows = []
        import crud

        for res in results:
            r = res[0]
            r.email_status = res[1]
            r.email_sent_at = res[2]
            r.resend_count = res[3] or 0
            # res[4] is supplier_lookup_name
            
            is_1st_lot = r.sample_type == "CPT 1st lot &PPS"
            
            if mode == 'pending':
                # Rule: if already SENT in email_log, never send again via "Gửi email mới"
                if r.email_status == 'SENT':
                    continue
                # Split Logic as per request:
                if is_1st_lot:
                    # Logic 1: 1st Lot -> Only if validity is "Xin mới"
                    # Use our refined check_first_lot_status logic
                    validity = crud.check_first_lot_status(db, r.item_code, res[4])
                    if validity != "OK":
                        rows.append(r)
                else:
                    # Logic 2: Other samples -> Only if not SENT
                    rows.append(r)
            
            elif mode == 'timeout':
                # Timeout mainly targets 1st lots that are pending completion
                if is_1st_lot:
                    validity = crud.check_first_lot_status(db, r.item_code, res[4])
                    if validity != "OK":
                        is_timeout = False
                        if r.email_status == "SENT" and r.email_sent_at and today > (r.email_sent_at.date() + timedelta(days=7)):
                            is_timeout = True
                        if r.expected_arrival_date and today >= r.expected_arrival_date:
                            is_timeout = True
                        if is_timeout:
                            rows.append(r)
                # (Samples typically don't have timeout resend requirement specified yet, but we skip them here to avoid noise)

        if not rows:
            print(f"[SKIP] No matching rows for mode {mode} (Validity/Sent filter): {cpt_supplier}")
            return {"status": "skipped", "message": "No relevant rows to send"}

        # Build summary...
        # ... (rest of function)
        show_1st_lot_note = any(r.sample_type == "CPT 1st lot &PPS" for r in rows)

        # Build summary (unique FG CC Code + Season pairs)
        seen = set()
        summary_items = []
        for r in rows:
            key = f"{r.fg_cc_code}|{r.season}"
            if key not in seen:
                seen.add(key)
                summary_items.append({"fg_cc_code": r.fg_cc_code, "season": r.season})

        # Build subject
        unique_fgcc = list(set(r.fg_cc_code for r in rows if r.fg_cc_code))
        fgcc_short = ", ".join(unique_fgcc)
        if len(fgcc_short) > 100:
            fgcc_short = fgcc_short[:100] + "..."
        
        prefix = "[REMINDER] " if mode == 'timeout' else ""
        subject = f"{prefix}Requested sample orders for {fgcc_short}, {supplier.supplier_name}"

        # MD info
        md_name = rows[0].md if rows[0].md else "N/A"
        
        # MD Email fetching (Keep for template but exclude from CC)
        md_email_row = db.query(models.StaffEmail.email).filter(
            models.StaffEmail.role == 'MD',
            models.StaffEmail.name == md_name
        ).first()
        md_email = md_email_row[0] if md_email_row else "N/A"

        # Get CC emails based on Sample Type rules
        cc_emails_str = build_cc_emails_by_sample_type(db, rows)

        # Render HTML
        html_body = build_email_html(
            supplier_name=supplier.supplier_name,
            summary_items=summary_items,
            detail_rows=rows,
            md_name=md_name,
            md_email=md_email,
            show_1st_lot_note=show_1st_lot_note
        )

        # Send
        send_email(
            to_emails=supplier.email,
            subject=subject,
            html_body=html_body,
            cc_emails=cc_emails_str
        )

        # Mark as SENT in database (via EmailLog)
        now = datetime.now()
        for r in rows:
            # We no longer update r.email_status etc. because they aren't Columns.
            # We simply use r.resend_count that was mapped above for logic.
            new_resend_count = (r.resend_count or 0)
            if mode == 'timeout':
                new_resend_count += 1
            
            # 🔒 Persist to email_log (survives future data resyncs)
            if r.ser_no:
                log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
                if log:
                    log.email_status = "SENT"
                    log.email_sent_at = now
                    log.resend_count = new_resend_count
                    log.supplier_name = supplier.supplier_name
                    if r.cpt_supplier:
                        log.fabric_supplier = r.cpt_supplier
                else:
                    db.add(models.EmailLog(
                        ser_no=r.ser_no,
                        item=r.item,
                        season=r.season,
                        fabric_supplier=r.cpt_supplier,
                        supplier_name=supplier.supplier_name,
                        email_status="SENT",
                        email_sent_at=now,
                        resend_count=new_resend_count
                    ))
        db.commit()

        return {"status": "success", "to": supplier.email, "cc": cc_emails_str, "rows": len(rows)}

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

def send_all_pending_emails():
    """Identify distinct suppliers with pending rows and send emails to each."""
    db = SessionLocal()
    try:
        pending_suppliers = db.query(models.FirstLotRequest.cpt_supplier).filter(
            models.FirstLotRequest.cpt_supplier.isnot(None)
        ).distinct().all()

        if not pending_suppliers:
            return {"status": "success", "sent_suppliers": 0, "rows_total": 0}

        sent_count = 0
        total_rows = 0
        for (cpt_supplier,) in pending_suppliers:
            result = send_email_to_supplier(cpt_supplier, mode='pending')
            if result["status"] == "success":
                sent_count += 1
                total_rows += result["rows"]
            # status == "skipped" is silently ignored (no email, no error)
        
        return {"status": "success", "sent_suppliers": sent_count, "rows_total": total_rows}
    finally:
        db.close()

def send_all_timeout_emails():
    """Identify distinct suppliers with timeout rows and resend emails."""
    db = SessionLocal()
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta
    today = date.today()
    
    try:
        suppliers = db.query(models.FirstLotRequest.cpt_supplier).filter(
            models.FirstLotRequest.cpt_supplier.isnot(None)
        ).distinct().all()
        
        timeout_suppliers = set()
        for (cpt_supplier,) in suppliers:
             # We let send_email_to_supplier do the heavy filtering for timeout
             # To minimize calls, we could pre-filter for SENT rows, but to be simple and robust:
             timeout_suppliers.add(cpt_supplier)

        if not timeout_suppliers:
            return {"status": "success", "sent_suppliers": 0, "rows_total": 0}

        sent_count = 0
        total_rows = 0
        for cpt_supplier in timeout_suppliers:
            result = send_email_to_supplier(cpt_supplier, mode='timeout')
            if result["status"] == "success":
                sent_count += 1
                total_rows += result["rows"]
        
        return {"status": "success", "sent_suppliers": sent_count, "rows_total": total_rows}
    finally:
        db.close()

def send_timeout_email_for_row(ser_no: str):
    """Send a timeout reminder email for a single row (by ser_no)."""
    db = SessionLocal()
    from datetime import date, timedelta
    today = date.today()

    try:
        if not ser_no:
            return {"status": "error", "message": "Missing ser_no"}

        result = db.query(
            models.FirstLotRequest,
            models.EmailLog.email_status.label("log_email_status"),
            models.EmailLog.email_sent_at.label("log_email_sent_at"),
            models.EmailLog.resend_count.label("log_resend_count"),
            models.SupplierEmail.supplier_name.label("supplier_lookup_name"),
            models.SupplierEmail.email.label("supplier_email")
        ).outerjoin(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no
        ).filter(
            models.FirstLotRequest.ser_no == ser_no
        ).first()

        if not result:
            return {"status": "error", "message": f"Row not found for ser_no: {ser_no}"}

        r = result[0]
        r.email_status = result[1]
        r.email_sent_at = result[2]
        r.resend_count = result[3] or 0
        supplier_lookup_name = result[4]
        supplier_email = result[5]

        if not r.cpt_supplier:
            return {"status": "error", "message": "Missing CPT supplier for this row"}

        # Supplier email required
        if not supplier_email or not supplier_email.strip():
            return {"status": "error", "message": "Supplier email missing for this row"}

        import crud
        # Emergency resend: allow all rows regardless of timeout/validity
        is_1st_lot = r.sample_type == "CPT 1st lot &PPS"

        # Build summary (single row)
        summary_items = []
        if r.fg_cc_code or r.season:
            summary_items.append({"fg_cc_code": r.fg_cc_code, "season": r.season})

        show_1st_lot_note = is_1st_lot

        unique_fgcc = list(set([r.fg_cc_code] if r.fg_cc_code else []))
        fgcc_short = ", ".join(unique_fgcc)
        if len(fgcc_short) > 100:
            fgcc_short = fgcc_short[:100] + "..."

        subject = f"[REMINDER] Requested sample orders for {fgcc_short}, {supplier_lookup_name or r.cpt_supplier}"

        md_name = r.md if r.md else "N/A"
        md_email_row = db.query(models.StaffEmail.email).filter(
            models.StaffEmail.role == 'MD',
            models.StaffEmail.name == md_name
        ).first()
        md_email = md_email_row[0] if md_email_row else "N/A"

        html_body = build_email_html(
            supplier_name=supplier_lookup_name or r.cpt_supplier,
            summary_items=summary_items,
            detail_rows=[r],
            md_name=md_name,
            md_email=md_email,
            show_1st_lot_note=show_1st_lot_note
        )

        # Build CC emails based on Sample Type rules
        cc_emails_str = build_cc_emails_by_sample_type(db, [r])

        send_email(
            to_emails=supplier_email,
            subject=subject,
            html_body=html_body,
            cc_emails=cc_emails_str
        )

        # Update email log
        now = datetime.now()
        new_resend_count = (r.resend_count or 0) + 1
        log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
        if log:
            log.email_status = "SENT"
            log.email_sent_at = now
            log.resend_count = new_resend_count
            log.supplier_name = supplier_lookup_name
            if r.cpt_supplier:
                log.fabric_supplier = r.cpt_supplier
        else:
            db.add(models.EmailLog(
                ser_no=r.ser_no,
                item=r.item,
                season=r.season,
                fabric_supplier=r.cpt_supplier,
                supplier_name=supplier_lookup_name,
                email_status="SENT",
                email_sent_at=now,
                resend_count=new_resend_count
            ))
        db.commit()

        return {"status": "success", "to": supplier_email, "rows": 1}

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

def send_timeout_email_for_rows(ser_nos: list[str]):
    """Send a timeout reminder email for multiple rows, grouped by supplier."""
    db = SessionLocal()

    try:
        if not ser_nos or not isinstance(ser_nos, list):
            return {"status": "error", "message": "Missing ser_nos"}

        cleaned = []
        seen = set()
        for s in ser_nos:
            s = str(s).strip() if s is not None else ""
            if not s or s in seen:
                continue
            cleaned.append(s)
            seen.add(s)

        if not cleaned:
            return {"status": "error", "message": "No valid ser_nos"}

        results = db.query(
            models.FirstLotRequest,
            models.EmailLog.email_status.label("log_email_status"),
            models.EmailLog.email_sent_at.label("log_email_sent_at"),
            models.EmailLog.resend_count.label("log_resend_count"),
            models.SupplierEmail.supplier_name.label("supplier_lookup_name"),
            models.SupplierEmail.email.label("supplier_email")
        ).outerjoin(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no
        ).filter(
            models.FirstLotRequest.ser_no.in_(cleaned)
        ).all()

        if not results:
            return {"status": "error", "message": "No rows found for provided ser_nos"}

        groups: dict[tuple[str, str, str], list] = {}
        skipped_rows: list[dict] = []

        for res in results:
            r = res[0]
            r.email_status = res[1]
            r.email_sent_at = res[2]
            r.resend_count = res[3] or 0
            supplier_lookup_name = res[4]
            supplier_email = res[5]

            if not r.cpt_supplier:
                skipped_rows.append({"ser_no": r.ser_no, "reason": "Missing CPT supplier"})
                continue
            if not supplier_email or not str(supplier_email).strip():
                skipped_rows.append({"ser_no": r.ser_no, "reason": "Supplier email missing"})
                continue

            supplier_name_display = supplier_lookup_name or r.cpt_supplier
            key = (r.cpt_supplier, supplier_email.strip(), supplier_name_display)
            groups.setdefault(key, []).append(r)

        if not groups:
            return {"status": "skipped", "message": "No rows eligible to send", "skipped_rows": skipped_rows}

        sent_suppliers = 0
        total_rows = 0

        for (_, supplier_email, supplier_name_display), rows in groups.items():
            # Build summary (unique FG CC Code + Season pairs)
            seen_pairs = set()
            summary_items = []
            for r in rows:
                key = f"{r.fg_cc_code}|{r.season}"
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    summary_items.append({"fg_cc_code": r.fg_cc_code, "season": r.season})

            show_1st_lot_note = any(r.sample_type == "CPT 1st lot &PPS" for r in rows)

            unique_fgcc = list(set(r.fg_cc_code for r in rows if r.fg_cc_code))
            fgcc_short = ", ".join(unique_fgcc)
            if len(fgcc_short) > 100:
                fgcc_short = fgcc_short[:100] + "..."

            subject = f"[REMINDER] Requested sample orders for {fgcc_short}, {supplier_name_display}"

            md_name = rows[0].md if rows[0].md else "N/A"
            md_email_row = db.query(models.StaffEmail.email).filter(
                models.StaffEmail.role == 'MD',
                models.StaffEmail.name == md_name
            ).first()
            md_email = md_email_row[0] if md_email_row else "N/A"

            html_body = build_email_html(
                supplier_name=supplier_name_display,
                summary_items=summary_items,
                detail_rows=rows,
                md_name=md_name,
                md_email=md_email,
                show_1st_lot_note=show_1st_lot_note
            )

            cc_emails_str = build_cc_emails_by_sample_type(db, rows)

            send_email(
                to_emails=supplier_email,
                subject=subject,
                html_body=html_body,
                cc_emails=cc_emails_str
            )

            now = datetime.now()
            for r in rows:
                new_resend_count = (r.resend_count or 0) + 1
                if r.ser_no:
                    log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
                    if log:
                        log.email_status = "SENT"
                        log.email_sent_at = now
                        log.resend_count = new_resend_count
                        log.supplier_name = supplier_name_display
                        if r.cpt_supplier:
                            log.fabric_supplier = r.cpt_supplier
                    else:
                        db.add(models.EmailLog(
                            ser_no=r.ser_no,
                            item=r.item,
                            season=r.season,
                            fabric_supplier=r.cpt_supplier,
                            supplier_name=supplier_name_display,
                            email_status="SENT",
                            email_sent_at=now,
                            resend_count=new_resend_count
                        ))
            db.commit()

            sent_suppliers += 1
            total_rows += len(rows)

        return {
            "status": "success",
            "sent_suppliers": sent_suppliers,
            "rows_total": total_rows,
            "skipped_rows": skipped_rows
        }

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

def send_missing_first_lot_email_for_rows(ser_nos: list[str]):
    """
    Send a missing 1st lot follow-up email for multiple rows, grouped by supplier.
    Only applies to sample_type == 'CPT 1st lot &PPS' rows; others are skipped.
    """
    db = SessionLocal()

    try:
        if not ser_nos or not isinstance(ser_nos, list):
            return {"status": "error", "message": "Missing ser_nos"}

        cleaned = []
        seen = set()
        for s in ser_nos:
            s = str(s).strip() if s is not None else ""
            if not s or s in seen:
                continue
            cleaned.append(s)
            seen.add(s)

        if not cleaned:
            return {"status": "error", "message": "No valid ser_nos"}

        results = db.query(
            models.FirstLotRequest,
            models.SupplierEmail.supplier_name.label("supplier_lookup_name"),
            models.SupplierEmail.email.label("supplier_email"),
            models.EmailLog.resend_count.label("log_resend_count")
        ).outerjoin(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no
        ).filter(
            models.FirstLotRequest.ser_no.in_(cleaned)
        ).all()

        if not results:
            return {"status": "error", "message": "No rows found for provided ser_nos"}

        groups: dict[tuple[str, str, str], list] = {}
        skipped_rows: list[dict] = []

        for (r, supplier_lookup_name, supplier_email, resend_count) in results:
            r.resend_count = resend_count or 0

            if not r.ser_no:
                skipped_rows.append({"ser_no": None, "reason": "Missing ser_no"})
                continue
            if r.sample_type != "CPT 1st lot &PPS":
                skipped_rows.append({"ser_no": r.ser_no, "reason": "Not 1st lot sample"})
                continue
            if not r.cpt_supplier:
                skipped_rows.append({"ser_no": r.ser_no, "reason": "Missing CPT supplier"})
                continue
            if not supplier_email or not str(supplier_email).strip():
                skipped_rows.append({"ser_no": r.ser_no, "reason": "Supplier email missing"})
                continue

            supplier_name_display = supplier_lookup_name or r.cpt_supplier
            key = (r.cpt_supplier, supplier_email.strip(), supplier_name_display)
            groups.setdefault(key, []).append(r)

        if not groups:
            return {"status": "skipped", "message": "No rows eligible to send", "skipped_rows": skipped_rows}

        request_line = "Standard fabric card (1st lot)"
        intro_line = (
            "We have received the fabric for the below sample order. "
            "However, we have not yet received the Standard Fabric Card (1st lot) as required."
        )

        sent_suppliers = 0
        total_rows = 0

        for (_, supplier_email, supplier_name_display), rows in groups.items():
            unique_fgcc = list(set(r.fg_cc_code for r in rows if r.fg_cc_code))
            fgcc_short = ", ".join(unique_fgcc)
            if len(fgcc_short) > 100:
                fgcc_short = fgcc_short[:100] + "..."

            subject = f"[FOLLOW UP] Missing 1st lot for {fgcc_short}, {supplier_name_display}"

            html_body = build_missing_1stlot_bulk_email_html(
                supplier_name=supplier_name_display,
                rows=rows,
                request_line=request_line,
                intro_line=intro_line
            )

            cc_emails_str = build_cc_emails_by_sample_type(db, rows)

            send_email(
                to_emails=supplier_email,
                subject=subject,
                html_body=html_body,
                cc_emails=cc_emails_str
            )

            now = datetime.now()
            for r in rows:
                new_resend_count = (r.resend_count or 0) + 1
                log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
                if log:
                    log.email_status = "SENT"
                    log.email_sent_at = now
                    log.resend_count = new_resend_count
                    log.supplier_name = supplier_name_display
                    if r.cpt_supplier:
                        log.fabric_supplier = r.cpt_supplier
                else:
                    db.add(models.EmailLog(
                        ser_no=r.ser_no,
                        item=r.item,
                        season=r.season,
                        fabric_supplier=r.cpt_supplier,
                        supplier_name=supplier_name_display,
                        email_status="SENT",
                        email_sent_at=now,
                        resend_count=new_resend_count
                    ))
            db.commit()

            sent_suppliers += 1
            total_rows += len(rows)

        return {
            "status": "success",
            "sent_suppliers": sent_suppliers,
            "rows_total": total_rows,
            "skipped_rows": skipped_rows
        }

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

def send_missing_first_lot_email_for_row(ser_no: str, request_items: list[str]):
    """Send a missing 1st lot / documents email for a single row (by ser_no)."""
    db = SessionLocal()

    try:
        if not ser_no:
            return {"status": "error", "message": "Missing ser_no"}

        if not request_items or not isinstance(request_items, list):
            return {"status": "error", "message": "Missing request items"}

        result = db.query(
            models.FirstLotRequest,
            models.SupplierEmail.supplier_name.label("supplier_lookup_name"),
            models.SupplierEmail.email.label("supplier_email"),
            models.EmailLog.resend_count.label("log_resend_count")
        ).outerjoin(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no
        ).filter(
            models.FirstLotRequest.ser_no == ser_no
        ).first()

        if not result:
            return {"status": "error", "message": f"Row not found for ser_no: {ser_no}"}

        r = result[0]
        supplier_lookup_name = result[1]
        supplier_email = result[2]
        resend_count = result[3] or 0

        if not r.cpt_supplier:
            return {"status": "error", "message": "Missing CPT supplier for this row"}

        if not supplier_email or not supplier_email.strip():
            return {"status": "error", "message": "Supplier email missing for this row"}

        item_map = {
            "1st_lot": "Standard fabric card (1st lot)",
            "color_test": "Test color report",
            "mtsr": "MTSR"
        }
        requested = [item_map.get(i) for i in request_items if item_map.get(i)]
        if not requested:
            return {"status": "error", "message": "No valid request items selected"}

        request_line = " + ".join(requested)
        if "Standard fabric card (1st lot)" in requested:
            intro_line = "We have received the fabric for the below sample order. However, we have not yet received the Standard Fabric Card (1st lot) as required."
        else:
            intro_line = "We have received the fabric for the below sample order. However, we have not yet received the requested items as required."

        unique_fgcc = list(set([r.fg_cc_code] if r.fg_cc_code else []))
        fgcc_short = ", ".join(unique_fgcc)
        if len(fgcc_short) > 100:
            fgcc_short = fgcc_short[:100] + "..."

        subject = f"[FOLLOW UP] Missing 1st lot / reports for {fgcc_short}, {supplier_lookup_name or r.cpt_supplier}"

        html_body = build_missing_1stlot_email_html(
            supplier_name=supplier_lookup_name or r.cpt_supplier,
            row=r,
            request_line=request_line,
            intro_line=intro_line
        )

        # Build CC emails based on Sample Type rules
        cc_emails_str = build_cc_emails_by_sample_type(db, [r])

        send_email(
            to_emails=supplier_email,
            subject=subject,
            html_body=html_body,
            cc_emails=cc_emails_str
        )

        now = datetime.now()
        new_resend_count = resend_count + 1
        log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
        if log:
            log.email_status = "SENT"
            log.email_sent_at = now
            log.resend_count = new_resend_count
            log.supplier_name = supplier_lookup_name
            if r.cpt_supplier:
                log.fabric_supplier = r.cpt_supplier
        else:
            db.add(models.EmailLog(
                ser_no=r.ser_no,
                item=r.item,
                season=r.season,
                fabric_supplier=r.cpt_supplier,
                supplier_name=supplier_lookup_name,
                email_status="SENT",
                email_sent_at=now,
                resend_count=new_resend_count
            ))
        db.commit()

        return {"status": "success", "to": supplier_email, "rows": 1}

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = send_email_to_supplier(sys.argv[1])
    else:
        result = send_all_pending_emails()
    print(result)
