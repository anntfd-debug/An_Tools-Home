# -*- coding: utf-8 -*-
import clr
import math
from pyrevit import revit, DB, forms, script

doc = revit.doc
active_view = doc.ActiveView

# ----------------------------------------------------
# CẤU HÌNH TÊN FAMILY
# ----------------------------------------------------
LINE_FAMILY_NAME = "MEP_Line_Based_Pipe"
MIN_LENGTH = 0.0026 

def is_vertical(curve):
    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)
    return abs(p1.X - p2.X) < 0.001

def is_horizontal(curve):
    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)
    return abs(p1.Y - p2.Y) < 0.001

def copy_instance_parameters(source_el, target_el, skip_params=["Jump_Position"]):
    """Hàm sao chép toàn bộ tham số instance từ phần tử gốc sang phần tử mới"""
    for param in source_el.Parameters:
        # Bỏ qua các tham số chỉ đọc hoặc nằm trong danh sách cần bỏ qua
        if not param.IsReadOnly and param.Definition.Name not in skip_params:
            target_param = target_el.LookupParameter(param.Definition.Name)
            if target_param and not target_param.IsReadOnly:
                try:
                    if param.StorageType == DB.StorageType.String:
                        val = param.AsString()
                        target_param.Set(val if val else "")
                    elif param.StorageType == DB.StorageType.Integer:
                        target_param.Set(param.AsInteger())
                    elif param.StorageType == DB.StorageType.Double:
                        target_param.Set(param.AsDouble())
                    elif param.StorageType == DB.StorageType.ElementId:
                        target_param.Set(param.AsElementId())
                except Exception:
                    pass # Bỏ qua an toàn nếu gặp lỗi gán tham số hệ thống đặc thù

def main():
    # 1. Hiển thị bảng chọn Option cho người dùng
    options = {
        "1. Xử lý toàn bộ View (Dùng khi khởi tạo)": "ALL",
        "2. Quét chọn ống đứng cần xử lý (Nhanh hơn)": "SELECT"
    }
    
    selected_option = forms.SelectFromList.show(
        options.keys(),
        title="Chọn phương thức xử lý giao cắt",
        button_name="Tiếp tục",
        multiselect=False
    )
    
    if not selected_option:
        return # Người dùng hủy lệnh

    mode = options[selected_option]

    # 2. Thu thập tất cả Detail Items trong View hiện tại
    collector = DB.FilteredElementCollector(doc, active_view.Id)\
                  .OfCategory(DB.BuiltInCategory.OST_DetailComponents)\
                  .WhereElementIsNotElementType()\
                  .ToElements()

    all_v_elements = []
    h_elements = [] # Ống ngang luôn lấy toàn bộ View để check giao cắt

    for item in collector:
        if item.Symbol.Family.Name == LINE_FAMILY_NAME:
            loc_curve = item.Location
            if isinstance(loc_curve, DB.LocationCurve):
                curve = loc_curve.Curve
                if is_horizontal(curve):
                    h_elements.append(item)
                elif is_vertical(curve):
                    all_v_elements.append(item)

    v_elements = []
    
    # 3. Phân loại theo Option đã chọn
    if mode == "ALL":
        v_elements = all_v_elements
    elif mode == "SELECT":
        # Ưu tiên lấy các đối tượng đã chọn sẵn trên màn hình
        selection = revit.get_selection()
        for el in selection:
            if el.Symbol.Family.Name == LINE_FAMILY_NAME and isinstance(el.Location, DB.LocationCurve) and is_vertical(el.Location.Curve):
                v_elements.append(el)
        
        # Nếu chưa chọn gì, yêu cầu người dùng quét chọn
        if not v_elements:
            try:
                with forms.WarningBar(title="Hãy quét chọn các ống ĐỨNG cần xử lý giao cắt"):
                    picked_elements = revit.pick_elements()
                    if picked_elements:
                        for el in picked_elements:
                            if el.Symbol.Family.Name == LINE_FAMILY_NAME and isinstance(el.Location, DB.LocationCurve) and is_vertical(el.Location.Curve):
                                v_elements.append(el)
            except Exception:
                pass # Bỏ qua nếu người dùng nhấn ESC
                
        if not v_elements:
            forms.alert("Không có ống đứng nào được chọn! Hủy lệnh.")
            return

    if not v_elements or not h_elements:
        print("Không tìm thấy đủ dữ liệu ống ngang/đứng để xử lý giao cắt.")
        return

    # 4. Thực thi giao dịch trong Revit
    t = DB.Transaction(doc, "Tự động nhảy bậc, xếp lớp và giữ tham số")
    t.Start()

    count_jumps = 0
    all_new_segments = []
    elements_to_delete = [] # Lưu danh sách ống cũ cần xóa

    for v_el in v_elements:
        v_curve = v_el.Location.Curve
        start_pt = v_curve.GetEndPoint(0)
        end_pt = v_curve.GetEndPoint(1)
        v_symbol = v_el.Symbol
        
        if start_pt.Y > end_pt.Y:
            start_pt, end_pt = end_pt, start_pt

        # Tìm giao điểm
        intersections = []
        for h_el in h_elements:
            h_curve = h_el.Location.Curve
            result_array = clr.Reference[DB.IntersectionResultArray]()
            intersect_status = v_curve.Intersect(h_curve, result_array)
            
            if intersect_status == DB.SetComparisonResult.Overlap:
                for res in result_array.Value:
                    pt = res.XYZPoint
                    if start_pt.DistanceTo(pt) > MIN_LENGTH and end_pt.DistanceTo(pt) > MIN_LENGTH:
                        intersections.append(pt)

        if intersections:
            intersections.sort(key=lambda p: start_pt.DistanceTo(p))
            all_points = [start_pt] + intersections + [end_pt]
            
            # Đánh dấu ống đứng cũ để xóa (Khoan xóa để còn copy parameters)
            elements_to_delete.append(v_el.Id)
            
            # Vẽ các phân đoạn mới
            for i in range(len(all_points) - 1):
                p_start = all_points[i]
                p_end = all_points[i+1]
                
                if p_start.DistanceTo(p_end) < MIN_LENGTH:
                    continue
                
                segment_line = DB.Line.CreateBound(p_start, p_end)
                new_seg = doc.Create.NewFamilyInstance(segment_line, v_symbol, active_view)
                
                # Copy toàn bộ tham số từ ống cũ sang ống mới (ngoại trừ Jump_Position)
                copy_instance_parameters(v_el, new_seg, skip_params=["Jump_Position"])
                
                all_new_segments.append(new_seg)
                
                # Set các tham số phục vụ cho Jump
                if i < len(intersections):
                    segment_length = p_start.DistanceTo(p_end)
                    p_visible = new_seg.LookupParameter("Jump_Visible")
                    p_pos = new_seg.LookupParameter("Jump_Position")
                    
                    if p_visible and p_pos:
                        p_visible.Set(1)               
                        p_pos.Set(segment_length)       
                        count_jumps += 1

    # Xóa toàn bộ các ống dọc cũ sau khi đã copy xong parameters
    for el_id in elements_to_delete:
        try:
            doc.Delete(el_id)
        except Exception:
            pass

    # ----------------------------------------------------
    # 5. ĐIỀU CHỈNH THỨ TỰ HIỂN THỊ (DRAW ORDER)
    # ----------------------------------------------------
    if all_new_segments:
        # Đưa toàn bộ ống đứng mới tạo ra SAU CÙNG (Back)
        for v_seg in all_new_segments:
            DB.DetailElementOrderUtils.SendToBack(doc, active_view, v_seg.Id)
            
        # Đưa toàn bộ ống ngang lên TRÊN CÙNG (Front)
        # Chỉ Front các ống ngang liên quan hoặc toàn bộ tùy nhu cầu, ở đây tuân thủ logic gốc
        for h_el in h_elements:
            DB.DetailElementOrderUtils.BringToFront(doc, active_view, h_el.Id)
                        
    t.Commit()
    print("Thành công! Chế độ: {}. Đã xử lý {} điểm giao cắt và đồng bộ lại thứ tự lớp hiển thị.".format(
        "Toàn bộ View" if mode == "ALL" else "Quét chọn", 
        count_jumps
    ))

if __name__ == '__main__':
    main()