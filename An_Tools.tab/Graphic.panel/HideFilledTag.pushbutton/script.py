# -*- coding: utf-8 -*-
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from System.Collections.Generic import List

from pyrevit import revit, DB, UI, forms

doc = revit.doc

def get_uv(point, view):
    """
    Hàm phụ trợ: Chuyển đổi tọa độ 3D của một điểm về mặt phẳng 2D của View hiện tại.
    """
    u = point.DotProduct(view.RightDirection)
    v = point.DotProduct(view.UpDirection)
    return u, v

def is_point_in_polygon_view(point, curve_loops, view):
    """
    Thuật toán Ray-Casting đã được tối ưu cho mọi loại View (Plan, Section, Elevation).
    """
    inside = False
    pt_u, pt_v = get_uv(point, view)
    
    for loop in curve_loops:
        for curve in loop:
            pts = curve.Tessellate()
            for i in range(len(pts) - 1):
                p1_u, p1_v = get_uv(pts[i], view)
                p2_u, p2_v = get_uv(pts[i+1], view)
                
                if (p1_v > pt_v) != (p2_v > pt_v):
                    intersect_u = (p2_u - p1_u) * (pt_v - p1_v) / (p2_v - p1_v) + p1_u
                    if pt_u < intersect_u:
                        inside = not inside
    return inside

# Class định dạng cách hiển thị View trong bảng chọn (List Window)
class ViewOption(forms.TemplateListItem):
    @property
    def name(self):
        return "[{}] {}".format(self.item.ViewType, self.item.Name)

def main():
    # 1. Bảng tùy chọn chế độ chạy
    options = {
        "Chỉ chạy trên View hiện tại (Active View)": "active",
        "Mở bảng chọn nhiều View (Có Filter)": "multi"
    }
    
    selected_option = forms.CommandSwitchWindow.show(
        options.keys(), 
        message="Bạn muốn ẩn Tag dưới Filled Region ở đâu?"
    )
    
    if not selected_option:
        return # Người dùng tắt bảng
        
    mode = options[selected_option]
    selected_views = []
    
    valid_view_types = [
        ViewType.FloorPlan, ViewType.CeilingPlan, ViewType.EngineeringPlan, 
        ViewType.Section, ViewType.Elevation, ViewType.Detail, ViewType.DraftingView
    ]

    # 2. Xử lý danh sách View theo chế độ đã chọn
    if mode == "active":
        active_view = doc.ActiveView
        if active_view.ViewType not in valid_view_types:
            forms.alert("View hiện hành không hỗ trợ Filled Region / Tag 2D.", title="Thông báo")
            return
        selected_views.append(active_view)
        
    elif mode == "multi":
        all_views = FilteredElementCollector(doc)\
            .OfCategory(BuiltInCategory.OST_Views)\
            .WhereElementIsNotElementType()\
            .ToElements()
            
        selectable_views = []
        for v in all_views:
            if not v.IsTemplate and v.ViewType in valid_view_types:
                selectable_views.append(v)
                
        view_options = [ViewOption(v) for v in selectable_views]
        view_options.sort(key=lambda x: x.name)
        
        selected_multi_views = forms.SelectFromList.show(
            view_options,
            title="Chọn View để ẩn Tag (Nhập chữ để Filter)",
            multiselect=True,
            button_name="Thực thi ẩn Tag"
        )
        
        if not selected_multi_views:
            return
        selected_views = selected_multi_views

    # 3. Tiến hành xử lý trên danh sách View đã chốt
    total_hidden = 0
    views_processed = 0
    
    with revit.Transaction("Hide Tags under Filled Regions"):
        for view in selected_views:
            filled_regions = FilteredElementCollector(doc, view.Id)\
                .OfClass(FilledRegion)\
                .ToElements()
                
            if not filled_regions: 
                continue
            
            tags = FilteredElementCollector(doc, view.Id).OfClass(IndependentTag).ToElements()
            if not tags: 
                continue
            
            tags_to_hide = []
            for tag in tags:
                if tag.IsHidden(view): 
                    continue
                
                tag_pt = None
                try:
                    if tag.HasTagHeadPosition: 
                        tag_pt = tag.TagHeadPosition
                except: 
                    pass
                    
                if not tag_pt:
                    bbox = tag.get_BoundingBox(view)
                    if bbox: 
                        tag_pt = (bbox.Min + bbox.Max) / 2.0
                    else: 
                        continue
                    
                for fr in filled_regions:
                    loops = fr.GetBoundaries()
                    if is_point_in_polygon_view(tag_pt, loops, view):
                        tags_to_hide.append(tag.Id)
                        break 
                        
            if tags_to_hide:
                view.HideElements(List[ElementId](tags_to_hide))
                total_hidden += len(tags_to_hide)
                views_processed += 1
                
    # 4. Báo cáo kết quả
    if total_hidden > 0:
        result_msg = "Đã hoàn tất!\n\n" \
                     "👉 Ẩn tổng cộng: **{}** Tag.\n" \
                     "👉 Trải dài trên: **{}/{}** View.".format(
                         total_hidden, views_processed, len(selected_views))
        forms.alert(result_msg, title="Thành công")
    else:
        forms.alert("Không tìm thấy Tag nào bị Filled Region che khuất trong (các) View đã chọn.", title="Kết quả")

if __name__ == '__main__':
    main()