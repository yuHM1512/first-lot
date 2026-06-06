import os
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import models
from database import SessionLocal


SEED_ROWS = [
    {
        "role": "KTDH",
        "name": "DIEU",
        "email": "dieuktdm@hachiba.com.vn",
        "employee_code": "D0784",
        "department": "P.KTCN",
    },
    {
        "role": "KTDH",
        "name": "HIEN",
        "email": "hiendang@hachiba.com.vn",
        "employee_code": "H3795",
        "department": "P.KTCN",
    },
    {
        "role": "KTDH",
        "name": "CHAU",
        "email": "phuongchau@hachiba.com.vn",
        "employee_code": "C0697",
        "department": "P.KTCN",
    },
    {
        "role": "KTDH",
        "name": "HUONG",
        "email": "huongkt@hachiba.com.vn",
        "employee_code": "H1436",
        "department": "P.KTCN",
    },
    {
        "role": "KTDH",
        "name": "HOA",
        "email": "hoasd@hachiba.com.vn",
        "employee_code": "H3113",
        "department": "P.KTCN",
    },
    {
        "role": "MD",
        "name": "MS. LINH",
        "email": "hoailinh@hachiba.com.vn",
        "employee_code": "L1835",
        "department": "P.KDXNK",
    },
    {
        "role": "MD",
        "name": "MS. THI",
        "email": "quynhthi@hachiba.com.vn",
        "employee_code": "T4762",
        "department": "P.KDXNK",
    },
    {
        "role": "MD",
        "name": "MR. HUY",
        "email": "duchuy@hachiba.com.vn",
        "employee_code": "H3716",
        "department": "P.KDXNK",
    },
    {
        "role": "MAU",
        "name": "TOMAU",
        "email": "tomaymau@hachiba.com.vn",
        "employee_code": "V0064",
        "department": "P.KTCN",
    },
    {
        "role": "QA",
        "name": "DUNG",
        "email": "dung-fabric@hachiba.com.vn",
        "employee_code": "D0167",
        "department": "P.QLCL",
    },
    {
        "role": "QA",
        "name": "THAO",
        "email": "thao-fabric@hachiba.com.vn",
        "employee_code": "T0154",
        "department": "P.QLCL",
    },
]


def main():
    load_dotenv()
    db = SessionLocal()
    try:
        inserted = 0
        updated = 0
        for row in SEED_ROWS:
            existing = (
                db.query(models.StaffEmail)
                .filter(models.StaffEmail.employee_code == row["employee_code"])
                .first()
            )
            if existing:
                existing.role = row["role"]
                existing.name = row["name"]
                existing.email = row["email"]
                existing.department = row["department"]
                updated += 1
            else:
                db.add(models.StaffEmail(**row))
                inserted += 1
        db.commit()
        print(f"[OK] staff_emails inserted: {inserted}, updated: {updated}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
