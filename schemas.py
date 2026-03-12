from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional

class FirstLotMasterBase(BaseModel):
    item_code: str
    fabric_name: Optional[str] = None
    model_code: Optional[str] = None
    fabric_supplier: Optional[str] = None
    usable_width: Optional[str] = None
    unit: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    received_date: Optional[date] = None
    using_time_years: int = 2
    color_test_report_received_date: Optional[date] = None
    mtsr_received_date: Optional[date] = None
    
    # 1st Lot Info and Quality
    lot_info_status: Optional[str] = "OK"
    lot_info_reason: Optional[str] = None
    lot_quality_status: Optional[str] = "OK"
    lot_quality_reason: Optional[str] = None

class FirstLotMasterCreate(FirstLotMasterBase):
    pass

class FirstLotMaster(FirstLotMasterBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class FirstLotRequestBase(BaseModel):
    item_code: str
    ser_no: Optional[str] = None
    item: Optional[str] = None
    item_description: Optional[str] = None
    model_no: Optional[str] = None
    model_description: Optional[str] = None
    fg_cc_code: Optional[str] = None
    season: Optional[str] = None
    passion_brand: Optional[str] = None
    match_contrast_info: Optional[str] = None
    pl_name: Optional[str] = None
    unit: Optional[str] = None
    fg_sample_pieces: Optional[str] = None
    cpt_supplier: Optional[str] = None
    remark: Optional[str] = None
    expected_arrival_date: Optional[date] = None
    pick_up: Optional[str] = None
    update_etd: Optional[str] = None
    courrier_number: Optional[str] = None
    creator: Optional[str] = None
    po_date: Optional[str] = None
    fg_supplier: Optional[str] = None
    sample_type: Optional[str] = None
    test: Optional[str] = None
    color: Optional[str] = None
    actual_delivery_qty: Optional[str] = None
    actual_delivery_date: Optional[str] = None
    supplier_code: Optional[str] = None
    supplier_dpp: Optional[str] = None
    supplier_process: Optional[str] = None
    address: Optional[str] = None
    kt: Optional[str] = None
    md: Optional[str] = None
    md: Optional[str] = None

class FirstLotRequestCreate(FirstLotRequestBase):
    pass

class FirstLotRequest(FirstLotRequestBase):
    id: int
    status: str
    created_at: datetime
    email_status: Optional[str] = None
    email_sent_at: Optional[datetime] = None
    master_received_date: Optional[date] = None
    master_using_time_years: Optional[int] = None
    first_lot_received_status: Optional[str] = None
    resend_count: Optional[int] = 0

    class Config:
        from_attributes = True
class FirstLotHistoryBase(BaseModel):
    item_code: str
    fabric_supplier: Optional[str] = None
    change_type: str
    old_date: Optional[date] = None
    new_date: Optional[date] = None

class FirstLotHistory(FirstLotHistoryBase):
    id: int
    changed_at: datetime

    class Config:
        from_attributes = True

class FirstLotDateUpdate(BaseModel):
    item_code: str
    fabric_name: Optional[str] = None
    model_code: Optional[str] = None
    fabric_supplier: Optional[str] = None
    usable_width: Optional[str] = None
    unit: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    
    received_date: Optional[date] = None
    color_test_report_received_date: Optional[date] = None
    mtsr_received_date: Optional[date] = None
    
    # New fields
    lot_info_status: Optional[str] = None
    lot_info_reason: Optional[str] = None
    lot_quality_status: Optional[str] = None
    lot_quality_reason: Optional[str] = None
    
    request_id: Optional[int] = None
    received_status: Optional[str] = None
    remark: Optional[str] = None

class SupplierEmailBase(BaseModel):
    supplier_name: Optional[str] = None
    cpt_supplier: str
    email: str

class SupplierEmailCreate(SupplierEmailBase):
    pass

class SupplierEmail(SupplierEmailBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
class StaffEmailBase(BaseModel):
    employee_code: Optional[str] = None
    department: Optional[str] = None
    role: str
    name: str
    email: str

class StaffEmailCreate(StaffEmailBase):
    pass

class StaffEmail(StaffEmailBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
