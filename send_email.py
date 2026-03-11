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

def build_email_html(supplier_name, summary_items, detail_rows, md_name="N/A", md_email="N/A"):
    """Render the email HTML template with data."""
    template = template_env.get_template("email_template.html")
    return template.render(
        supplier_name=supplier_name,
        summary_items=summary_items,
        detail_rows=detail_rows,
        md_name=md_name,
        md_email=md_email
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
            print(f"[ERROR] Supplier not found: {cpt_supplier}")
            return {"status": "error", "message": f"Supplier not found: {cpt_supplier}"}

        # Base query
        query = db.query(models.FirstLotRequest).filter(
            models.FirstLotRequest.cpt_supplier == cpt_supplier,
            models.FirstLotRequest.sample_type.ilike('%1st lot%')
        )

        if mode == 'pending':
            # Rows NOT yet sent
            rows = query.filter(
                (models.FirstLotRequest.email_status != 'SENT') | (models.FirstLotRequest.email_status == None)
            ).all()
        elif mode == 'timeout':
            # Rows already SENT but timed out, or overdue based on expected_arrival_date
            all_rows = query.all()
            rows = []
            for r in all_rows:
                # Check validity from Master
                master = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == r.item_code).first()
                validity_status = "Chưa có"
                if master and master.received_date:
                    expiration_date = master.received_date + relativedelta(years=master.using_time_years or 2)
                    if today <= expiration_date:
                        validity_status = "Còn hiệu lực"
                
                if validity_status == "Còn hiệu lực":
                    continue
                
                is_timeout = False
                if r.email_status == "SENT" and r.email_sent_at and today > (r.email_sent_at.date() + timedelta(days=7)):
                    is_timeout = True
                
                if r.expected_arrival_date and today >= r.expected_arrival_date:
                    is_timeout = True
                
                if is_timeout:
                    rows.append(r)
        else:
            return {"status": "error", "message": f"Invalid mode: {mode}"}

        if not rows:
            print(f"[ERROR] No 1st lot requests found for mode {mode}: {cpt_supplier}")
            return {"status": "error", "message": "No requests found"}

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
            md_email=md_email
        )

        # Send
        send_email(
            to_emails=supplier.email,
            subject=subject,
            html_body=html_body,
            cc_emails=cc_emails_str
        )

        # Mark as SENT in database (update timestamp)
        now = datetime.now()
        for r in rows:
            r.email_status = "SENT"
            r.email_sent_at = now
            if mode == 'timeout':
                r.resend_count = (r.resend_count or 0) + 1
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
            models.FirstLotRequest.sample_type.ilike('%1st lot%'),
            (models.FirstLotRequest.email_status != 'SENT') | (models.FirstLotRequest.email_status == None),
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
        sent_rows = db.query(models.FirstLotRequest).filter(
            models.FirstLotRequest.sample_type.ilike('%1st lot%'),
            models.FirstLotRequest.email_status == 'SENT',
            models.FirstLotRequest.cpt_supplier.isnot(None)
        ).all()
        
        timeout_suppliers = set()
        for r in sent_rows:
            if not r.email_sent_at: continue
            if today > (r.email_sent_at.date() + timedelta(days=7)):
                master = db.query(models.FirstLotMaster).filter(models.FirstLotMaster.item_code == r.item_code).first()
                is_timeout = False
                if not master or not master.received_date:
                    is_timeout = True
                else:
                    expiration = master.received_date + relativedelta(years=master.using_time_years or 2)
                    if today > expiration:
                        is_timeout = True
                
                if is_timeout:
                    timeout_suppliers.add(r.cpt_supplier)

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
