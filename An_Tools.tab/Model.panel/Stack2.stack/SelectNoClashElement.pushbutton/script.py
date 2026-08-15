# -*- coding: utf-8 -*-
__title__ = "Find Pipe, PA, PF \nNo Sleeve"
__doc__ = "Tối ưu hóa hiệu suất, ProgressBar và chống đơ máy bằng Isolate."

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import *
from System.Collections.Generic import List
from pyrevit import forms, revit, script

uidoc = revit.uidoc
doc = revit.doc
active_view = doc.ActiveView

def main():
    # 1. CHỌN MẪU FAMILY SLEEVE
    try:
        picked = uidoc.Selection.PickObject(ObjectType.Element, "Chọn Family Sleeve mẫu...")
        sample = doc.GetElement(picked)
        if not isinstance(sample, FamilyInstance):
            forms.alert("Vui lòng chọn một đối tượng là Family (Sleeve)!")
            return
        target_type_ids = set(sample.Symbol.Family.GetFamilySymbolIds())
    except:
        return

    # 2. THU THẬP DỮ LIỆU
    cats = List[BuiltInCategory]([BuiltInCategory.OST_PipeCurves, 
                                  BuiltInCategory.OST_PipeAccessory, 
                                  BuiltInCategory.OST_PlumbingFixtures])
    
    all_targets = FilteredElementCollector(doc, active_view.Id) \
        .WherePasses(ElementMulticategoryFilter(cats)) \
        .WhereElementIsNotElementType() \
        .ToElements()

    if not all_targets:
        forms.alert("Không có ống/phụ kiện nào trong View này.")
        return

    all_sleeves = FilteredElementCollector(doc, active_view.Id) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType() \
        .ToElements()
    
    sleeves_to_check = [s for s in all_sleeves if s.GetTypeId() in target_type_ids]

    if not sleeves_to_check:
        forms.alert("Không tìm thấy Family Sleeve nào trong View!")
        return

    # 3. THUẬT TOÁN LỌC + PROGRESS BAR
    clashed_ids = set()
    total_sleeves = len(sleeves_to_check)
    
    with forms.ProgressBar(title='Đang quét va chạm...', cancellable=True) as pb:
        for index, sleeve in enumerate(sleeves_to_check):
            
            if pb.cancelled:
                forms.alert("Đã hủy quá trình quét!")
                script.exit() 
            
            bbox = sleeve.get_BoundingBox(active_view)
            if bbox:
                out = Outline(bbox.Min, bbox.Max)
                bbox_filter = BoundingBoxIntersectsFilter(out)
                
                clashes = FilteredElementCollector(doc, active_view.Id) \
                    .WherePasses(ElementMulticategoryFilter(cats)) \
                    .WherePasses(bbox_filter) \
                    .ToElementIds()
                
                clashed_ids.update(clashes)
            
            pb.update_progress(index + 1, total_sleeves)

    # 4. TRẢ VỀ KẾT QUẢ (SỬA LỖI TRANSACTION TẠI ĐÂY)
    non_clashed = [t.Id for t in all_targets if t.Id not in clashed_ids]

    if non_clashed:
        # Thay đổi từ Transaction(doc,...) sang revit.Transaction(...) của pyRevit
        with revit.Transaction("Cách ly đối tượng thiếu Sleeve"):
            active_view.IsolateElementsTemporary(List[ElementId](non_clashed))
            
        uidoc.RefreshActiveView()
        forms.toast("Xong! Đã hiển thị độc lập {} đối tượng không vướng Sleeve.\n(Gõ HR để trở lại bình thường)".format(len(non_clashed)))
    else:
        forms.alert("Tuyệt vời! Tất cả Pipe/PA/PF đều đã có Sleeve hợp lệ.")

if __name__ == "__main__":
    main()