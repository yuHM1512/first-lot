"""Tạo bảng rank2_* trên Postgres và sync data lần đầu từ Google Sheets.

Chạy: python migrate_rank2_to_db.py
"""
import models
from database import engine
from import_data import sync_rank2_from_sheets


def main():
    print("Tạo bảng rank2_* (nếu chưa có)...")
    models.Rank2Threshold.__table__.create(bind=engine, checkfirst=True)
    models.Rank2YearMeta.__table__.create(bind=engine, checkfirst=True)
    models.Rank2SupplierScore.__table__.create(bind=engine, checkfirst=True)
    print("Đã tạo xong.\n")

    print("Đang kéo dữ liệu từ Google Sheets...")
    result = sync_rank2_from_sheets(triggered_by="migrate_script")
    if result.get("status") == "success":
        print(f"✓ Sync thành công lúc {result['synced_at']}")
        print(f"  - Năm: {result['year_count']}")
        print(f"  - Dòng NCC: {result['row_count']}")
        print(f"  - Threshold: {result['threshold_count']}")
    else:
        print(f"✗ Lỗi: {result.get('message')}")


if __name__ == "__main__":
    main()
