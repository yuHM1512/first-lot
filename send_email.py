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
                # Split Logic as per request:
                if is_1st_lot:
                    # Logic 1: 1st Lot -> Only if validity is "Xin mới"
                    # Use our refined check_first_lot_status logic
                    validity = crud.check_first_lot_status(db, r.item_code, res[4])
                    if validity != "OK":
                        rows.append(r)
                else:
                    # Logic 2: Other samples -> Only if not SENT
                    if r.email_status != 'SENT':
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

        # Get CC emails (Temporarily commented out for testing)
        cc_emails_str = None
        """
        cc_emails_list = []
        
        # 1. KT Emails (matched by name)
        kt_names = list(set(r.kt for r in rows if r.kt))
        if kt_names:
            kt_emails = db.query(models.StaffEmail.email).filter(
                models.StaffEmail.role == 'KT',
                models.StaffEmail.name.in_(kt_names)
            ).all()
            cc_emails_list.extend([e[0] for e in kt_emails if e[0]])
            
        # 2. Add MD to CC list
        if md_email != "N/A":
            cc_emails_list.append(md_email)

        # 3. MAU Emails (All)
        mau_emails = db.query(models.StaffEmail.email).filter(models.StaffEmail.role == 'MAU').all()
        for e in mau_emails:
            if e[0]:
                cc_emails_list.extend([email.strip() for email in e[0].split(",")])

        # 4. QA Emails (All)
        qa_emails = db.query(models.StaffEmail.email).filter(models.StaffEmail.role == 'QA').all()
        cc_emails_list.extend([e[0] for e in qa_emails if e[0]])
            
        cc_emails_str = ",".join(list(set(cc_emails_list)))
        """

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

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        result = send_email_to_supplier(sys.argv[1])
    else:
        result = send_all_pending_emails()
    print(result)
