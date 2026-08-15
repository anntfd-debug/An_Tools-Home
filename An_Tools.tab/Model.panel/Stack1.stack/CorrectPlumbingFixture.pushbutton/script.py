# -*- coding: utf-8 -*-
__title__ = "L0.0 To Top"
__doc__ = "Align cao độ 0.0 của các family được chọn vào đỉnh hình học của link được chọn trong active view"
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
        # Bước 1: Cho phép quét chọn nhiều Family Instance mẫu
        ref_fams = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element, 
            FamilyInstanceFilter(), 
            "1/2: Chọn các Family mẫu (có thể quét chọn) -> Nhấn nút [FINISH] góc trên bên trái"
        )
        
        # Lọc ra danh sách các Family ID duy nhất
        target_family_ids = set()
        for ref in ref_fams:
            fam_inst = doc.GetElement(ref)
            if hasattr(fam_inst, "Symbol") and hasattr(fam_inst.Symbol, "Family"):
                target_family_ids.add(fam_inst.Symbol.Family.Id)
                
        if not target_family_ids:
            forms.alert("Không lấy được thông tin Family từ các đối tượng đã chọn.", title="Lỗi")
            return
            
        # Bước 2: Chọn Revit Link làm đối tượng đích
        ref_link = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element, 
            RevitLinkFilter(), 
            "2/2: Vui lòng chọn Revit Link (Mô hình liên kết) để lấy bề mặt đỉnh"
        )
        link_inst = doc.GetElement(ref_link)
        
    except Exceptions.OperationCanceledException:
        forms.alert("Đã hủy lệnh thao tác.", title="Thông báo")
        return

    # --------------------------------------------------
    # 2. LẤY TẤT CẢ INSTANCE CỦA CÁC FAMILY TRONG ACTIVE VIEW
    # --------------------------------------------------
    active_view = doc.ActiveView
    all_instances = DB.FilteredElementCollector(doc, active_view.Id)\
                      .OfClass(DB.FamilyInstance)\
                      .ToElements()
                      
    instances_to_align = [inst for inst in all_instances if inst.Symbol.Family.Id in target_family_ids]
    
    if not instances_to_align:
        forms.alert("Không tìm thấy Family nào tương ứng trong Active View.", title="Lỗi")
        return

    # --------------------------------------------------
    # 3. THỰC THI ALIGN ĐIỂM ĐẶT VÀO ĐỈNH LINK (TRANSACTION)
    # --------------------------------------------------
    with revit.Transaction("Align Family Origin to Link Top"):
        # Tạo 3D View tạm thời phục vụ tính năng Raycast
        vft_col = DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType).ToElements()
        vft_3d = next((vft for vft in vft_col if vft.ViewFamily == DB.ViewFamily.ThreeDimensional), None)
        
        temp_view = DB.View3D.CreateIsometric(doc, vft_3d.Id)
        doc.Regenerate()
        
        # Thiết lập bộ dò tia ReferenceIntersector
        intersector = DB.ReferenceIntersector(temp_view)
        intersector.FindReferencesInRevitLinks = True
        
        success_count = 0
        skip_count = 0
        
        for inst in instances_to_align:
            loc = inst.Location
            if not isinstance(loc, DB.LocationPoint):
                continue
                
            # Tọa độ điểm đặt hiện tại (Elevation 0.0 nội bộ của Family)
            pt = loc.Point
            current_origin_z = pt.Z
            
            # --- LOGIC BẮN TIA TỪ TRÊN XUỐNG ---
            # Xuất phát từ cao độ điểm đặt cộng thêm 30 feet (~9 mét) để vượt qua mái/sàn của Link
            origin = DB.XYZ(pt.X, pt.Y, current_origin_z + 30.0)
            direction = DB.XYZ(0, 0, -1) # Hướng bắn thẳng xuống đất
            
            hits = intersector.Find(origin, direction)
            
            valid_hits = []
            for hit in hits:
                ref = hit.GetReference()
                if ref.ElementId == link_inst.Id:
                    valid_hits.append(ref.GlobalPoint.Z)
            
            if valid_hits:
                # Đỉnh hình học của Link tại tọa độ này chính là điểm va chạm cao nhất (Max Z)
                target_top_z = max(valid_hits)
                
                # Tính khoảng cách dịch chuyển dựa trên điểm đặt Origin Z
                delta_z = target_top_z - current_origin_z
                translation_vec = DB.XYZ(0, 0, delta_z)
                
                # Di chuyển đối tượng
                DB.ElementTransformUtils.MoveElement(doc, inst.Id, translation_vec)
                success_count += 1
            else:
                skip_count += 1
                
        # Xóa View 3D tạm sau khi xử lý xong
        doc.Delete(temp_view.Id)
        
    # --------------------------------------------------
    # 4. BÁO CÁO KẾT QUẢ
    # --------------------------------------------------
    msg = "Đã Align thành công: {} family (Tính theo điểm đặt 0.0 vào Đỉnh Link).\n".format(success_count)
    if skip_count > 0:
        msg += "\nBỏ qua: {} family (Do tọa độ đứng không cắt qua khối hình học nào của Link).".format(skip_count)
        
    forms.alert(msg, title="Kết quả Align")

if __name__ == '__main__':
    main()