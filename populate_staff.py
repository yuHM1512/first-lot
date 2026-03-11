
from database import SessionLocal
import models

def populate_internal_emails():
    db = SessionLocal()
    try:
        # Clear existing staff emails if any (or just add new ones)
        # User wants to "hoàn thiện logic", so let's populate exactly what was requested.
        db.query(models.StaffEmail).delete()
        
        data = [
            # KT (KTDH)
            ("KT", "DIEU", "dieuktdm@hachiba.com.vn"),
            ("KT", "HIEN", "hiendang@hachiba.com.vn"),
            ("KT", "VUI", "vui@hachiba.com.vn"),
            ("KT", "CHAU", "phuongchau@hachiba.com.vn"),
            ("KT", "HUONG", "huongkt@hachiba.com.vn"),
            ("KT", "HOA", "hoasd@hachiba.com.vn"),
            # MD
            ("MD", "MS. LINH", "hoailinh@hachiba.com.vn"),
            ("MD", "MS. THI", "quynhthi@hachiba.com.vn"),
            ("MD", "MR. HUY", "duchuy@hachiba.com.vn"),
            # MAU
            ("MAU", "TOMAU", "tomaymau@hachiba.com.vn, thuvan@hachiba.com.vn"),
            # QA
            ("QA", "DUNG", "dung-fabric@hachiba.com.vn"),
            ("QA", "THAO", "thao-fabric@hachiba.com.vn"),
        ]
        
        for role, name, email in data:
            staff = models.StaffEmail(role=role, name=name, email=email)
            db.add(staff)
        
        db.commit()
        print(f"Successfully populated {len(data)} internal emails.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    populate_internal_emails()
