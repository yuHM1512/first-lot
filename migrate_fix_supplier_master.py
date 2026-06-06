from datetime import date, datetime
from sqlalchemy import select

from database import SessionLocal
from models import FirstLotMaster, SupplierEmail


def is_empty(val):
    return val is None or (isinstance(val, str) and val.strip() == "")


def choose_keeper(rows):
    def key(r):
        rd = r.received_date or date.min
        ca = r.created_at or datetime.min
        return (rd, ca, r.id or 0)
    return max(rows, key=key)


def merge_into(keeper, other):
    # Prefer non-empty values, and prefer NOK over OK for status fields.
    text_fields = [
        "fabric_name", "model_code", "fabric_supplier", "usable_width", "unit",
        "color", "description", "provider", "lot_info_reason", "lot_quality_reason",
        "quality_checked_by"
    ]
    date_fields = [
        "received_date", "color_test_report_received_date", "mtsr_received_date",
        "quality_checked_at"
    ]
    status_fields = ["lot_info_status", "lot_quality_status"]
    int_fields = ["using_time_years"]

    for f in text_fields:
        if is_empty(getattr(keeper, f)) and not is_empty(getattr(other, f)):
            setattr(keeper, f, getattr(other, f))

    for f in date_fields:
        if getattr(keeper, f) is None and getattr(other, f) is not None:
            setattr(keeper, f, getattr(other, f))

    for f in status_fields:
        k = getattr(keeper, f)
        o = getattr(other, f)
        if k != "NOK" and o == "NOK":
            setattr(keeper, f, "NOK")
        elif is_empty(k) and not is_empty(o):
            setattr(keeper, f, o)

    for f in int_fields:
        if getattr(keeper, f) is None and getattr(other, f) is not None:
            setattr(keeper, f, getattr(other, f))


def main():
    db = SessionLocal()
    try:
        # Map CPT supplier -> supplier_name
        supplier_map = {
            s.cpt_supplier: s.supplier_name
            for s in db.execute(select(SupplierEmail)).scalars().all()
            if s.cpt_supplier and s.supplier_name
        }

        # Normalize fabric_supplier to supplier_name when possible
        masters = db.execute(select(FirstLotMaster)).scalars().all()
        changed = 0
        for m in masters:
            if m.fabric_supplier in supplier_map:
                m.fabric_supplier = supplier_map[m.fabric_supplier]
                changed += 1
        if changed:
            db.commit()

        # Deduplicate by (item_code, fabric_supplier)
        masters = db.execute(select(FirstLotMaster)).scalars().all()
        groups = {}
        for m in masters:
            key = (m.item_code or "", m.fabric_supplier or "")
            groups.setdefault(key, []).append(m)

        removed = 0
        for key, rows in groups.items():
            if len(rows) <= 1:
                continue
            keeper = choose_keeper(rows)
            for other in rows:
                if other.id == keeper.id:
                    continue
                merge_into(keeper, other)
                db.delete(other)
                removed += 1

        if removed or changed:
            db.commit()

        print(f"Updated supplier_name on {changed} rows.")
        print(f"Removed {removed} duplicate rows.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
