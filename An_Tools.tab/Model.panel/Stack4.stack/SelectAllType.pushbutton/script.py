# -*- coding: utf-8 -*-
__title__ = "Select Family\nAll Type"
__doc__ = "Click chọn 1 đối tượng, tool sẽ tự động chọn TẤT CẢ các Type của Family đó trong TOÀN BỘ dự án."

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *
from System.Collections.Generic import List
from pyrevit import forms, revit

uidoc = revit.uidoc
doc = revit.doc

def main():
    # 1. Yêu cầu người dùng chọn 1 đối tượng mồi
    try:
        picked_ref = uidoc.Selection.PickObject(
            ObjectType.Element, 
            "Chọn 1 đối tượng thuộc Family bạn muốn quét toàn dự án..."
        )
        sample_elem = doc.GetElement(picked_ref)
    except:
        return

    # Kiểm tra xem đối tượng có phải là Family Instance không
    if not isinstance(sample_elem, FamilyInstance):
        forms.alert("Vui lòng chọn một Family Instance (Ví dụ: Van, Thiết bị, Cửa...).", title="Lỗi")
        return

    # 2. Lấy thông tin Family gốc
    target_family = sample_elem.Symbol.Family
    family_name = target_family.Name
    all_type_ids = target_family.GetFamilySymbolIds()

    # 3. Quét toàn bộ dự án (Không giới hạn ở ActiveView)
    collector = FilteredElementCollector(doc) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType()

    selected_ids = []
    
    # 4. Lọc ra những đối tượng thuộc Family đó
    for inst in collector:
        if inst.GetTypeId() in all_type_ids:
            selected_ids.append(inst.Id)

    # 5. Hiển thị kết quả lên màn hình
    if selected_ids:
        id_list = List[ElementId](selected_ids)
        uidoc.Selection.SetElementIds(id_list)
        forms.toast(
            "Đã chọn thành công {} đối tượng thuộc Family: {}".format(len(selected_ids), family_name),
            title="pyRevit Success"
        )
    else:
        forms.alert("Không tìm thấy đối tượng nào hợp lệ!", title="Thông báo")

if __name__ == "__main__":
    main()