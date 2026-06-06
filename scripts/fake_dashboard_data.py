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
import crud
from database import SessionLocal


def random_datetime_within_days(days_back: int) -> datetime:
    now = datetime.now()
    delta_days = random.randint(1, max(1, days_back))
    delta_hours = random.randint(0, 23)
    delta_minutes = random.randint(0, 59)
    return now - timedelta(days=delta_days, hours=delta_hours, minutes=delta_minutes)


def random_datetime_between_days(min_days: int, max_days: int) -> datetime:
    now = datetime.now()
    min_days = max(1, min_days)
    max_days = max(min_days, max_days)
    delta_days = random.randint(min_days, max_days)
    delta_hours = random.randint(0, 23)
    delta_minutes = random.randint(0, 59)
    return now - timedelta(days=delta_days, hours=delta_hours, minutes=delta_minutes)


def random_date_between(start: date, end: date) -> date:
    if end < start:
        start, end = end, start
    span = (end - start).days or 1
    return start + timedelta(days=random.randint(0, span))


def choose_reason(prefix: str) -> str:
    samples = [
        "Thiếu biên bản",
        "Chậm xác nhận",
        "Sai thông tin lô",
        "Chưa đủ tài liệu",
        "Đang bổ sung hồ sơ",
    ]
    return f"{prefix}: {random.choice(samples)}"


def main():
    parser = argparse.ArgumentParser(description="Fake data for dashboard testing.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--sent-days-back", type=int, default=30, help="Random email_sent_at within N days.")
    parser.add_argument("--late-rate", type=float, default=0.35, help="Ratio of late rows for dashboard diversity.")
    parser.add_argument("--late-sent-min-days", type=int, default=10, help="Min days back for late sent_at.")
    parser.add_argument("--late-sent-max-days", type=int, default=30, help="Max days back for late sent_at.")
    parser.add_argument("--max-master-updates", type=int, default=120, help="Cap master updates.")
    parser.add_argument("--master-update-ratio", type=float, default=0.7, help="Ratio of eligible rows to update master.")
    args = parser.parse_args()

    random.seed(args.seed)
    load_dotenv()

    db = SessionLocal()
    try:
        query = db.query(
            models.FirstLotRequest,
            models.EmailLog.email_status.label("log_email_status"),
            models.EmailLog.email_sent_at.label("log_email_sent_at"),
            models.SupplierEmail.supplier_name.label("supplier_lookup_name"),
        ).outerjoin(
            models.SupplierEmail,
            models.FirstLotRequest.cpt_supplier == models.SupplierEmail.cpt_supplier,
        ).outerjoin(
            models.EmailLog,
            models.FirstLotRequest.ser_no == models.EmailLog.ser_no,
        )

        results = query.all()

        updated_email = 0
        updated_master = 0
        updated_requests = 0

        for res in results:
            r = res[0]
            log_email_status = res[1]
            supplier_lookup_name = res[3]

            if not r.cpt_supplier or not r.ser_no:
                continue

            sample_type = (r.sample_type or "").strip()
            is_1st_lot = sample_type.lower() == "cpt 1st lot &pps".lower()

            needs_send = False
            if is_1st_lot:
                validity = crud.check_first_lot_status(db, r.item_code, supplier_lookup_name)
                if validity != "OK":
                    needs_send = True
            else:
                if log_email_status != "SENT":
                    needs_send = True

            if not needs_send:
                continue

            mark_late = random.random() < args.late_rate
            if mark_late:
                sent_at = random_datetime_between_days(args.late_sent_min_days, args.late_sent_max_days)
            else:
                sent_at = random_datetime_within_days(args.sent_days_back)

            log = db.query(models.EmailLog).filter_by(ser_no=r.ser_no).first()
            if log:
                log.email_status = "SENT"
                log.email_sent_at = sent_at
                log.supplier_name = supplier_lookup_name
                log.fabric_supplier = r.cpt_supplier
            else:
                db.add(
                    models.EmailLog(
                        ser_no=r.ser_no,
                        item=r.item,
                        season=r.season,
                        fabric_supplier=r.cpt_supplier,
                        supplier_name=supplier_lookup_name,
                        email_status="SENT",
                        email_sent_at=sent_at,
                        resend_count=0,
                    )
                )
            updated_email += 1

            # Randomly update popup (master) data for 1st lot rows
            if (
                is_1st_lot
                and updated_master < args.max_master_updates
                and random.random() <= args.master_update_ratio
            ):
                master = (
                    db.query(models.FirstLotMaster)
                    .filter(
                        models.FirstLotMaster.item_code == r.item_code,
                        models.FirstLotMaster.fabric_supplier == r.cpt_supplier,
                    )
                    .first()
                )
                if not master:
                    master = models.FirstLotMaster(
                        item_code=r.item_code,
                        fabric_supplier=r.cpt_supplier,
                        fabric_name=r.item_description or r.item,
                        model_code=r.model_no,
                        unit=r.unit,
                        color=r.color,
                        description=r.item_description,
                    )
                    db.add(master)

                if mark_late:
                    # Make on-time FALSE but still "expired" at sent_at by using negative years.
                    using_years = random.choice([-1, -2])
                    received_date = sent_at.date() + timedelta(days=random.randint(8, 20))
                else:
                    using_years = random.choice([1, 2, 3])
                    expiration_date = sent_at.date() - timedelta(days=random.randint(1, 60))
                    received_date = expiration_date - relativedelta(years=using_years)

                master.using_time_years = using_years
                master.received_date = received_date
                master.color_test_report_received_date = received_date + timedelta(days=random.randint(5, 90))
                master.mtsr_received_date = received_date + timedelta(days=random.randint(10, 120))

                if random.random() < 0.25:
                    master.lot_info_status = "NOK"
                    master.lot_info_reason = choose_reason("Info")
                else:
                    master.lot_info_status = "OK"
                    master.lot_info_reason = None

                if random.random() < 0.25:
                    master.lot_quality_status = "NOK"
                    master.lot_quality_reason = choose_reason("Quality")
                else:
                    master.lot_quality_status = "OK"
                    master.lot_quality_reason = None

                master.quality_checked_at = datetime.now() - timedelta(days=random.randint(0, 30))
                master.quality_checked_by = random.choice(["FAKE_QA_01", "FAKE_QA_02", "FAKE_QA_03"])

                updated_master += 1

            # Update request popup fields (received status + remark) randomly
            if random.random() < 0.35:
                r.first_lot_received_status = random.choice(
                    ["Đã tiếp nhận", "Đã bàn giao", None]
                )
                r.remark = random.choice(
                    [
                        "Fake remark: kiểm tra nhanh",
                        "Fake remark: bổ sung thông tin",
                        "Fake remark: chờ xác nhận",
                        None,
                    ]
                )
                if sent_at:
                    if mark_late:
                        r.actual_delivery_date = random_date_between(
                            sent_at.date() + timedelta(days=9),
                            sent_at.date() + timedelta(days=20),
                        ).isoformat()
                    else:
                        r.actual_delivery_date = random_date_between(
                            sent_at.date() + timedelta(days=1),
                            sent_at.date() + timedelta(days=6),
                        ).isoformat()
                updated_requests += 1

        db.commit()

        print(
            f"[OK] Updated EmailLog SENT: {updated_email} rows | "
            f"Master updated: {updated_master} | Requests updated: {updated_requests}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
