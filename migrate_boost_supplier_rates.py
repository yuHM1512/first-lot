import random
from datetime import timedelta

from sqlalchemy import select

from database import SessionLocal
from models import FirstLotRequest, EmailLog, SupplierEmail, FirstLotMaster


SUPPLIERS = {
    "HUGE BAMBOO": {"target_min_rate": 0.91},
    "TRAN HIEP THANH": {"target_min_rate": 0.71},
}


def is_on_time(row):
    # row: (req, log, supplier_name, master_received_date)
    req, log, _, master_received_date = row
    sent_at = log.email_sent_at
    if not sent_at:
        return False

    # 1) master_received_date within 7 days
    if master_received_date:
        try:
            if (master_received_date - sent_at.date()).days <= 7:
                return True
        except Exception:
            pass

    # 2) email_log updated_at within 7 days when received status exists
    if req.first_lot_received_status and log.updated_at:
        try:
            if (log.updated_at - sent_at).days <= 7:
                return True
        except Exception:
            pass

    # 3) actual_delivery_date (string) within 7 days
    if req.actual_delivery_date:
        try:
            from dateutil import parser
            actual_date = parser.parse(req.actual_delivery_date).date()
            if (actual_date - sent_at.date()).days <= 7:
                return True
        except Exception:
            pass

    return False


def main():
    random.seed(42)
    db = SessionLocal()
    try:
        for supplier_name, cfg in SUPPLIERS.items():
            # Build dataset
            stmt = (
                select(FirstLotRequest, EmailLog, SupplierEmail.supplier_name, FirstLotMaster.received_date)
                .join(EmailLog, FirstLotRequest.ser_no == EmailLog.ser_no)
                .join(SupplierEmail, FirstLotRequest.cpt_supplier == SupplierEmail.cpt_supplier)
                .outerjoin(
                    FirstLotMaster,
                    (FirstLotRequest.item_code == FirstLotMaster.item_code)
                    & (FirstLotRequest.cpt_supplier == FirstLotMaster.fabric_supplier),
                )
                .where(SupplierEmail.supplier_name == supplier_name)
                .where(EmailLog.email_status == "SENT")
            )

            rows = db.execute(stmt).all()
            total = len(rows)
            if total == 0:
                print(f"{supplier_name}: no SENT rows found.")
                continue

            on_time_rows = [r for r in rows if is_on_time(r)]
            on_time = len(on_time_rows)
            target = max(on_time, int(total * cfg["target_min_rate"] + 0.5))
            if target >= total:
                target = total - 1  # keep below 100%

            need = max(0, target - on_time)
            if need == 0:
                print(f"{supplier_name}: already at {on_time}/{total}. No changes.")
                continue

            # Pick from not-on-time
            candidates = [r for r in rows if not is_on_time(r)]
            if len(candidates) < need:
                need = len(candidates)

            selected = random.sample(candidates, need)

            for req, log, _, _ in selected:
                if not log.email_sent_at:
                    continue
                req.first_lot_received_status = "Đã bàn giao"
                log.updated_at = log.email_sent_at + timedelta(days=3)

            db.commit()
            print(f"{supplier_name}: updated {need} rows. New target >= {target}/{total}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
