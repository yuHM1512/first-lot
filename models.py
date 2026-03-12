from sqlalchemy import Column, Integer, String, Date, Float, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from database import Base

class FirstLotMaster(Base):
    __tablename__ = "first_lot_master"

    id = Column(Integer, primary_key=True, index=True)
    item_code = Column(String, index=True, nullable=False)
    fabric_name = Column(String)
    model_code = Column(String, index=True)
    fabric_supplier = Column(String)
    usable_width = Column(String)
    unit = Column(String)
    color = Column(String)
    description = Column(Text)
    provider = Column(String)
    received_date = Column(Date)
    using_time_years = Column(Integer, default=2)
    color_test_report_received_date = Column(Date)
    mtsr_received_date = Column(Date)
    
    # 1st Lot Info and Quality
    lot_info_status = Column(String, default="OK") # "OK" or "NOK"
    lot_info_reason = Column(Text)
    lot_quality_status = Column(String, default="OK") # "OK" or "NOK"
    lot_quality_reason = Column(Text)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class FirstLotRequest(Base):
    __tablename__ = "first_lot_requests"

    id = Column(Integer, primary_key=True, index=True)
    ser_no = Column(String)
    item = Column(String)
    item_description = Column(Text)
    model_no = Column(String)
    model_description = Column(Text)
    fg_cc_code = Column(String)
    season = Column(String)
    passion_brand = Column(String)
    match_contrast_info = Column(Text)
    pl_name = Column(String)
    unit = Column(String)
    fg_sample_pieces = Column(String)
    cpt_supplier = Column(String)
    remark = Column(Text)
    expected_arrival_date = Column(Date)
    pick_up = Column(String)
    update_etd = Column(String)
    courrier_number = Column(String)
    creator = Column(String)
    po_date = Column(String)
    fg_supplier = Column(String)
    sample_type = Column(String)
    test = Column(String)
    color = Column(String)
    actual_delivery_qty = Column(String)
    actual_delivery_date = Column(String)
    supplier_code = Column(String)
    supplier_dpp = Column(String)
    supplier_process = Column(String)
    address = Column(Text)
    status = Column(String)  # This will store "OK" or "Xin mới"
    kt = Column(String)
    md = Column(String)
    
    item_code = Column(String, index=True) # Field to link with master data
    created_at = Column(DateTime(timezone=True), server_default=func.now())
class FirstLotHistory(Base):
    __tablename__ = "first_lot_history"

    id = Column(Integer, primary_key=True, index=True)
    item_code = Column(String, index=True, nullable=False)
    fabric_supplier = Column(String) # Link with master supplier
    change_type = Column(String) # 'received_date', 'color_test', 'mtsr'
    old_date = Column(Date)
    new_date = Column(Date)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

class SupplierEmail(Base):
    __tablename__ = "supplier_email"

    id = Column(Integer, primary_key=True, index=True)
    supplier_name = Column(String, index=True)
    cpt_supplier = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class StaffEmail(Base):
    __tablename__ = "staff_emails"
    id = Column(Integer, primary_key=True, index=True)
    employee_code = Column(String, unique=True, index=True)
    department = Column(String)
    role = Column(String)  # 'KT' or 'MD'
    name = Column(String)
    email = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class EmailLog(Base):
    """Persistent log of email and status per ser_no - survives first_lot_requests sync."""
    __tablename__ = "email_log"
    id = Column(Integer, primary_key=True, index=True)
    ser_no = Column(String, unique=True, index=True, nullable=False)
    item = Column(String)
    season = Column(String)
    fabric_supplier = Column(String)         # CPT Supplier (raw code/string)
    supplier_name = Column(String)           # Human-friendly name from supplier_email table
    email_status = Column(String)            # 'SENT'
    email_sent_at = Column(DateTime(timezone=True))
    first_lot_received_status = Column(String)
    resend_count = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
