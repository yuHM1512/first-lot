import argparse
import random
from datetime import datetime, timedelta, date
import os
import sys

from dotenv import load_dotenv
from dateutil.relativedelta import relativedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import models
from database import SessionLocal


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _is_first_lot(sample_type: str) -> bool:
    st = (sample_type or "").lower()
    return "1st lot" in st or "1st" in st


def _random_sent_at(max_days: int) -> datetime:
    days = random.randint(1, max(1, max_days))
    hours = random.randint(0, 23)
    minutes = random.randint(0, 59)
    return datetime.now() - timedelta(days=days, hours=hours, minutes=minutes)


def _build_history_map(db):
    history_rows = (
        db.query(models.FirstLotHistory)
        .filter(models.FirstLotHistory.change_type == "received_date")
        .all()
    )
    latest = {}
    for h in history_rows:
        if not h.new_date:
            continue
        key = (_norm(h.item_code), _norm(h.fabric_supplier))
        existing = latest.get(key)
        if not existing or h.new_date > existing:
            latest[key] = h.new_date
    return latest


def _build_master_years_map(db):
    masters = db.query(models.FirstLotMaster).all()
    latest = {}
    for m in masters:
        key = (_norm(m.item_code), _norm(m.fabric_supplier))
        existing = latest.get(key)
        current_date = m.received_date or date.min
        if not existing or current_date > existing["received_date"]:
            latest[key] = {
                "years": m.using_time_years or 3,
                "received_date": current_date,
            }
    return latest


def main():
    parser = argparse.ArgumentParser(
        description="Reset email log + fake SENT status and validity-driven email display."
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--max-sent-days",
        type=int,
        default=6,
        help="Max days back for email_sent_at (kept within 7 days).",
    )
    parser.add_argument(
        "--validity-default-years",
        type=int,
        default=3,
        help="Fallback using_time_years when master data is missing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only without writing to DB.",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    load_dotenv()

    db = SessionLocal()
    try:
        # Step 1: delete SENT email logs, reset resend_count for remaining
        deleted = (
            db.query(models.EmailLog)
            .filter(models.EmailLog.email_status == "SENT")
            .delete(synchronize_session=False)
        )
        reset_remaining = (
            db.query(models.EmailLog)
            .filter(models.EmailLog.resend_count.isnot(None))
            .update({models.EmailLog.resend_count: 0}, synchronize_session=False)
        )

        supplier_name_map = {
            _norm(s.cpt_supplier): (s.supplier_name or "").strip()
            for s in db.query(models.SupplierEmail).all()
        }
        history_map = _build_history_map(db)
        years_map = _build_master_years_map(db)

        requests = db.query(models.FirstLotRequest).all()
        updated_logs = 0
        updated_requests = 0
        valid_count = 0
        invalid_count = 0

        today = date.today()

        for r in requests:
            if not r.ser_no:
                continue

            cpt_supplier = (r.cpt_supplier or "").strip()
            supplier_name = supplier_name_map.get(_norm(cpt_supplier), "")

            history_key = (_norm(r.item_code), _norm(supplier_name))
            if history_key not in history_map and cpt_supplier:
                history_key = (_norm(r.item_code), _norm(cpt_supplier))
            history_date = history_map.get(history_key)

            years_key = (_norm(r.item_code), _norm(supplier_name))
            years_entry = years_map.get(years_key)
            if not years_entry and cpt_supplier:
                years_entry = years_map.get((_norm(r.item_code), _norm(cpt_supplier)))
            using_years = (
                years_entry["years"] if years_entry else args.validity_default_years
            )

            is_valid = False
            if history_date:
                expiration_date = history_date + relativedelta(years=using_years)
                is_valid = today <= expiration_date

            if _is_first_lot(r.sample_type):
                if is_valid:
                    valid_count += 1
                    r.expected_arrival_date = today + timedelta(
                        days=random.randint(1, 10)
                    )
                else:
                    invalid_count += 1
                    r.expected_arrival_date = today - timedelta(
                        days=random.randint(1, 10)
                    )
                updated_requests += 1

            log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
            sent_at = _random_sent_at(args.max_sent_days)
            if log:
                log.email_status = "SENT"
                log.email_sent_at = sent_at
                log.fabric_supplier = cpt_supplier
                log.supplier_name = supplier_name or log.supplier_name
                log.resend_count = 0
            else:
                db.add(
                    models.EmailLog(
                        ser_no=r.ser_no,
                        item=r.item,
                        season=r.season,
                        fabric_supplier=cpt_supplier,
                        supplier_name=supplier_name,
                        email_status="SENT",
                        email_sent_at=sent_at,
                        resend_count=0,
                    )
                )
            updated_logs += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        print(
            "[OK] Deleted SENT logs: "
            f"{deleted} | Reset resend_count: {reset_remaining} | "
            f"EmailLog upserted: {updated_logs} | "
            f"Requests updated: {updated_requests} | "
            f"Valid 1st lot: {valid_count} | "
            f"Invalid 1st lot: {invalid_count}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
