# -*- coding: utf-8 -*-
__title__ = "Bottom To Bottom"
__doc__ = "Align đáy hình học của các family được chọn vào đáy hình học của link được chọn trong active view"
import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
import Autodesk.Revit.Exceptions as Exceptions

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc

# --------------------------------------------------
# 1. TẠO FILTER ĐỂ NGƯỜI DÙNG CHỌN ĐÚNG ĐỐI TƯỢNG
# --------------------------------------------------
class FamilyInstanceFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.FamilyInstance)
    def AllowReference(self, ref, pos):
        return True

class RevitLinkFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.RevitLinkInstance)
    def AllowReference(self, ref, pos):
        return True

def main():
    logger = script.get_logger()
    
    try:
        # Bước 1: Cho phép quét chọn nhiều Family Instance
        ref_fams = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element, 
            FamilyInstanceFilter(), 
            "1/2: Chọn các Family mẫu (có thể quét chọn) -> Nhấn nút [FINISH] trên thanh Options Bar dưới Ribbon"
        )
        
        # Lấy danh sách các Family ID duy nhất từ các đối tượng đã chọn
        target_family_ids = set()
        for ref in ref_fams:
            fam_inst = doc.GetElement(ref)
            if hasattr(fam_inst, "Symbol") and hasattr(fam_inst.Symbol, "Family"):
                target_family_ids.add(fam_inst.Symbol.Family.Id)
                
        if not target_family_ids:
            forms.alert("Không lấy được thông tin Family từ các đối tượng đã chọn.", title="Lỗi")
            return
            
        # Bước 2: Chọn Revit Link làm đích đến
        ref_link = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element, 
            RevitLinkFilter(), 
            "2/2: Vui lòng chọn Revit Link (Mô hình liên kết) để lấy đáy"
        )
        link_inst = doc.GetElement(ref_link)
        
    except Exceptions.OperationCanceledException:
        forms.alert("Đã hủy lệnh thao tác.", title="Thông báo")
        return

    # --------------------------------------------------
    # 2. LẤY TẤT CẢ INSTANCE CỦA CÁC FAMILY ĐÃ CHỌN TRONG ACTIVE VIEW
    # --------------------------------------------------
    active_view = doc.ActiveView
    all_instances = DB.FilteredElementCollector(doc, active_view.Id)\
                      .OfClass(DB.FamilyInstance)\
                      .ToElements()
                      
    # Lọc ra những instance thuộc BẤT KỲ Family nào trong danh sách đã chọn
    instances_to_align = [inst for inst in all_instances if inst.Symbol.Family.Id in target_family_ids]
    
    if not instances_to_align:
        forms.alert("Không tìm thấy Family nào tương ứng trong Active View.", title="Lỗi")
        return

    # --------------------------------------------------
    # 3. THỰC THI ALIGN BẰNG RAYCAST
    # --------------------------------------------------
    with revit.Transaction("Align Multiple Families to Link Bottom"):
        # Tạo 3D View tạm thời cho Raycast
        vft_col = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType).ToElements()
        vft_3d = next((vft for vft in vft_col if vft.ViewFamily == DB.ViewFamily.ThreeDimensional), None)
        
        temp_view = DB.View3D.CreateIsometric(doc, vft_3d.Id)
        doc.Regenerate()
        
        # Thiết lập Intersector
        intersector = DB.ReferenceIntersector(temp_view)
        intersector.FindReferencesInRevitLinks = True
        
        success_count = 0
        skip_count = 0
        
        for inst in instances_to_align:
            loc = inst.Location
            if not isinstance(loc, DB.LocationPoint):
                continue
                
            pt = loc.Point
            
            fam_bbox = inst.get_BoundingBox(None)
            if not fam_bbox:
                continue
            fam_bottom_z = fam_bbox.Min.Z
            
            # Bắn tia thẳng lên từ tọa độ thấp hơn đáy family hiện tại 10 feet (~3m)
            origin = DB.XYZ(pt.X, pt.Y, fam_bottom_z - 10.0)
            direction = DB.XYZ(0, 0, 1)
            
            hits = intersector.Find(origin, direction)
            
            valid_hits = []
            for hit in hits:
                ref = hit.GetReference()
                if ref.ElementId == link_inst.Id:
                    valid_hits.append(ref.GlobalPoint.Z)
            
            if valid_hits:
                # Lấy mặt phẳng thấp nhất nằm phía trên điểm xuất phát
                target_bottom_z = min(valid_hits)
                
                delta_z = target_bottom_z - fam_bottom_z
                translation_vec = DB.XYZ(0, 0, delta_z)
                
                DB.ElementTransformUtils.MoveElement(doc, inst.Id, translation_vec)
                success_count += 1
            else:
                skip_count += 1
                
        # Dọn dẹp: Xóa View 3D tạm
        doc.Delete(temp_view.Id)
        
    # --------------------------------------------------
    # 4. BÁO CÁO KẾT QUẢ
    # --------------------------------------------------
    msg = "Đã Align thành công: {} family.\n".format(success_count)
    if skip_count > 0:
        msg += "\nBỏ qua: {} family (Không tìm thấy hình học Link phù hợp phía trên tọa độ quét).".format(skip_count)
        
    forms.alert(msg, title="Kết quả Align")

if __name__ == '__main__':
    main()