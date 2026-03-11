import os
from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import Base, SupplierEmail

# Ensure tables are created
Base.metadata.create_all(bind=engine)

suppliers_data = [
    {"supplier_name": "TRAN HIEP THANH", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -Tran Hiep Thanh Textile", "email": "nhut.lm@thttextile.com.vn, lam.ttt@thttextile.com.vn"},
    {"supplier_name": "DALUEN", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -DA LUEN", "email": "84105@da-luen.com"},
    {"supplier_name": "HUNG YEN", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -HUNG YEN", "email": "alice@hungyen-kd.com"},
    {"supplier_name": "JDT", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -JDT", "email": "phung1071@dy-fz.vn, sales1@dy-fz.vn"},
    {"supplier_name": "DELICACY", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -DELICACY VIETNAM", "email": "Vivi28@delicacy.com.tw, rogersie@delicacy.com, xuanloc@delicacy.com.vn, huetam@delicacy.com.vn"},
    {"supplier_name": "HUGE BAMBOO", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -HUGE BAMBOO", "email": "atran01@hugebamboo.com, tnguyen02@hugebamboo.com, atran@hugebamboo.com"},
    {"supplier_name": "NEW WIDE", "cpt_supplier": "DP Viet Nam (Ho Chi Minh) -NEW WIDE", "email": "amanda.lin@newwide.com"},
    {"supplier_name": "TAIHUA", "cpt_supplier": "TAIHUA HIGH-TECH DYEING & FINISHING CO.,LTD(台华高新染整(嘉兴)有限公司)", "email": "lly15167704888@163.com"},
    {"supplier_name": "HUATEX", "cpt_supplier": "HUATEX VIETNAM CO., LT (X330)", "email": "soda@huafeng-cn.com, vernier.hsieh@huafeng-cn.com, bill@huafeng-cn.com"},
    {"supplier_name": "TEXWELL", "cpt_supplier": "ZHEJIANG TEXWELL TEXTILE CO.,LTD(浙江得伟纺织科技有限公司)", "email": "yy_yang@texwell.com.cn, ss_yang@texwell.com.cn, wq_dai@texwell.com.cn"},
    {"supplier_name": "FUJIAN SUNTION", "cpt_supplier": "FUJIAN SUNTION TEXTILE TECHNOLOGY CO.,LTD(福建省向兴纺织科技有限公司)", "email": "sales103@cnsuntion.com"},
    {"supplier_name": "SHANDONG HENGLI", "cpt_supplier": "Shandong Hengli Textile Technology Co., Ltd.", "email": "liuxia@zibotex.com, wangjiahui@zibotex.com, wangjinye@zibotex.com"},
    {"supplier_name": "FOSHAN MINGZHOU", "cpt_supplier": "FO SHAN MINGZHOU TEXTILE LIMIT(名洲纺织有限公司(MINGZHOU))", "email": "tanxueqin@fs-mzfz.com"},
    {"supplier_name": "EVEREST TH", "cpt_supplier": "EVEREST TEXTILE (THAILAND)", "email": "wanida_p@everest.co.th"},
    {"supplier_name": "EVEREST SH", "cpt_supplier": "EVEREST TEXTILE SHANGHAI LTD(宏远发展(上海)有限公司)", "email": "section205@everest-sh.com.cn"},
    {"supplier_name": "GRAND GREAT", "cpt_supplier": "Grand and Great textile company limited", "email": "helen_huang@daihaotextile.com, tina_li@daihaotextile.com,harry_ruan@daihaotextile.com, alina_pan@daihaotextile.com"},
    {"supplier_name": "DERUN", "cpt_supplier": "GUANGDONG DERUN TEXTILE CO.,LT(德润纺织有限公司)", "email": "Carrie@deruntex.com"},
    {"supplier_name": "XIMEN", "cpt_supplier": "JIAXING XIMEN ARTIFICIAL FUR & G(嘉兴西猛人造毛服装有限公司)", "email": "15257399970@126.com"},
    {"supplier_name": "FAR EASTERN", "cpt_supplier": "ORIENTAL INDUSTRIES (SUZHOU) LTD.", "email": "xiaoyu.chen@feg.cn, liuchangmiao@feg.cn"},
    {"supplier_name": "FUJIAN HUAFENG", "cpt_supplier": "FUJIAN HUAJIN INDUSTRIAL.CO.,L(福建华峰)", "email": "adele.lin@huafeng-cn.com, tanya@huafeng-cn.com, yamei.cai@huafeng-cn.com, jingjing.cai@huafeng-cn.com"},
    {"supplier_name": "DEYONG SH", "cpt_supplier": "JIAXING DEYONG TEXTILES CO.,LTD(嘉兴德永纺织品有限公司)", "email": "shengxiaolei@dy-fz.com"},
    {"supplier_name": "DEJUN", "cpt_supplier": "ZHEJIANG DEJUN NEW MATERIAL(浙江德俊)", "email": "Allengu@dejuntextile.com, kaychen@dejuntextile.com"},
    {"supplier_name": "HONTEX", "cpt_supplier": "FUQING HONG LIONG TEXTILE TECH(福清洪良染织科技有限公司(HONTEX))", "email": "jimmy@hontex.cn, calvin@hontex.cn"},
    {"supplier_name": "LIPENG", "cpt_supplier": "Li Peng", "email": "martina9@lipeng.com.tw"},
    {"supplier_name": "JUJIE", "cpt_supplier": "WUJIANG JUJIE MICROFIBERS DYEING CO., LTD", "email": "jujiedkl@jujie.com"}
]

def seed_data():
    db = SessionLocal()
    try:
        count = 0
        for item in suppliers_data:
            # Check if exists
            existing = db.query(SupplierEmail).filter(SupplierEmail.cpt_supplier == item["cpt_supplier"]).first()
            if existing:
                # Update email just in case
                existing.email = item["email"]
                existing.supplier_name = item["supplier_name"]
            else:
                new_supplier = SupplierEmail(**item)
                db.add(new_supplier)
                count += 1
        db.commit()
        print(f"Successfully seeded {count} new suppliers, total {len(suppliers_data)} processed.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()
