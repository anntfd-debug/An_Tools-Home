# -*- coding: utf-8 -*-
__title__ = "Find Orphaned Sleeve"
__doc__ = "Phiên bản Siêu Tốc (Turbo): Kết hợp BoundingBox, Solid Filter và Isolate chống đơ máy."

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *
from System.Collections.Generic import List
from pyrevit import forms, revit

uidoc = revit.uidoc
doc = revit.doc
active_view = doc.ActiveView

class FamilyInstanceSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, FamilyInstance)
    def AllowReference(self, ref, pos): return False

def get_element_solids(elem, options):
    """Trích xuất khối 3D thực tế (Bất kể đang ở View 2D hay 3D)"""
    solids = []
    geom_elem = elem.get_Geometry(options)
    if not geom_elem:
        return solids
    for geom_obj in geom_elem:
        if isinstance(geom_obj, Solid) and geom_obj.Volume > 0:
            solids.append(geom_obj)
        elif isinstance(geom_obj, GeometryInstance):
            for sub_obj in geom_obj.GetInstanceGeometry():
                if isinstance(sub_obj, Solid) and sub_obj.Volume > 0:
                    solids.append(sub_obj)
    return solids

def main():
    try:
        picked_ref = uidoc.Selection.PickObject(
            ObjectType.Element, 
            FamilyInstanceSelectionFilter(), 
            "Chọn thiết bị mẫu thuộc Family cần kiểm tra..."
        )
        sample_instance = doc.GetElement(picked_ref)
    except:
        return

    all_types_ids = sample_instance.Symbol.Family.GetFamilySymbolIds()

    # 1. Thu thập thiết bị trong View hiện hành
    all_instances = FilteredElementCollector(doc, active_view.Id) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType() \
        .ToElements()

    family_instances = [inst for inst in all_instances if inst.GetTypeId() in all_types_ids]

    if not family_instances:
        forms.alert("Không tìm thấy cấu kiện nào thuộc Family này!")
        return

    # 2. Tạo bộ lọc hệ thống ống trong View
    pipe_categories = List[BuiltInCategory]([
        BuiltInCategory.OST_PipeCurves,      
        BuiltInCategory.OST_PipeFitting,     
        BuiltInCategory.OST_PipeAccessory,
        BuiltInCategory.OST_PlumbingFixtures
    ])
    pipe_multi_filter = ElementMulticategoryFilter(pipe_categories)

    # TỐI ƯU GEOMETRY OPTIONS: Ép buộc lấy Model 3D ở mức độ chi tiết cao nhất
    geom_options = Options()
    geom_options.ComputeReferences = False
    geom_options.DetailLevel = ViewDetailLevel.Fine 
    # KHÔNG gán geom_options.View = active_view để tránh bị lỗi lấy nét ẩn 2D trên mặt bằng

    non_clashed_equipment_ids = []
    tolerance = 0.01 
    total_items = len(family_instances)

    # 3. QUÉT VA CHẠM KẾT HỢP PROGRESS BAR
    with forms.ProgressBar(title="Đang quét va chạm...", cancellable=True) as pb:
        for index, inst in enumerate(family_instances):
            
            if pb.cancelled:
                forms.toast("Đã hủy quá trình quét ngang chừng!")
                return 

            pb.update_progress(index + 1, total_items)

            # Lấy BoundingBox tổng thể không phụ thuộc vào góc nhìn View
            bbox = inst.get_BoundingBox(None)
            if not bbox:
                non_clashed_equipment_ids.append(inst.Id)
                continue

            # --- BƯỚC TỐI ƯU 1: LỌC SƠ LOẠI BẰNG BOUNDING BOX ---
            out = Outline(XYZ(bbox.Min.X - tolerance, bbox.Min.Y - tolerance, bbox.Min.Z - tolerance),
                          XYZ(bbox.Max.X + tolerance, bbox.Max.Y + tolerance, bbox.Max.Z + tolerance))
            bbox_filter = BoundingBoxIntersectsFilter(out)

            has_bbox_clash = FilteredElementCollector(doc, active_view.Id) \
                .WherePasses(pipe_multi_filter) \
                .WherePasses(bbox_filter) \
                .FirstElementId()

            if has_bbox_clash == ElementId.InvalidElementId:
                non_clashed_equipment_ids.append(inst.Id)
                continue

            # --- BƯỚC TỐI ƯU 2: CHỈ KIỂM TRA SOLID VỚI CÁC THIẾT BỊ BỊ NGHI NGỜ ---
            inst_solids = get_element_solids(inst, geom_options)
            if not inst_solids:
                non_clashed_equipment_ids.append(inst.Id)
                continue

            real_clash = False
            for solid in inst_solids:
                solid_filter = ElementIntersectsSolidFilter(solid)
                
                has_solid_clash = FilteredElementCollector(doc, active_view.Id) \
                    .WherePasses(pipe_multi_filter) \
                    .WherePasses(solid_filter) \
                    .FirstElementId()
                
                if has_solid_clash != ElementId.InvalidElementId:
                    real_clash = True
                    break 
            
            if not real_clash:
                non_clashed_equipment_ids.append(inst.Id)

    # 4. HIỂN THỊ KẾT QUẢ
    if non_clashed_equipment_ids:
        with revit.Transaction("Cách ly Sleeve trống"):
            active_view.IsolateElementsTemporary(List[ElementId](non_clashed_equipment_ids))
            
        uidoc.RefreshActiveView()
        forms.toast("Turbo Xong! Đã hiển thị độc lập {} Sleeve chưa có ống đi qua.\n(Gõ HR trên bàn phím để quay lại ban đầu)".format(len(non_clashed_equipment_ids)))
    else:
        forms.alert("Tất cả Sleeve đều ôm ống, không có cấu kiện nào bị thừa!")

if __name__ == "__main__":
    main()