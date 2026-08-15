# -*- coding: utf-8 -*-
import clr
import traceback
from pyrevit import revit, DB, forms, script

doc = revit.doc
if not doc.IsFamilyDocument:
    forms.alert("Tool này chỉ chạy được trong môi trường chỉnh sửa Family (.rfa)!", exitscript=True)

fm = doc.FamilyManager
view = revit.active_view

class FamilyOptionLoad(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True
    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

def get_forge_type_ids():
    try:
        return DB.SpecTypeId.Boolean.YesNo, DB.GroupTypeId.Graphics
    except AttributeError:
        return DB.ParameterType.YesNo, DB.BuiltInParameterGroup.PG_GRAPHICS

def update_nested_family(doc, jump_family, nested_param_name, selected_subcat):
    """Hàm xử lý Family con: CHỈ TÁC ĐỘNG ĐẾN ĐƯỜNG CONG (ARC)"""
    nested_updated = False
    nested_doc = doc.EditFamily(jump_family)
    if not nested_doc:
        return False
        
    nt = DB.Transaction(nested_doc, "Auto Config Nested Family - Arc Only")
    nt.Start()
    try:
        nfm = nested_doc.FamilyManager
        param_type, group_type = get_forge_type_ids()
        
        # 1. Khởi tạo biến Instance hiển thị
        nested_param = next((p for p in nfm.Parameters if p.Definition.Name == nested_param_name), None)
        if not nested_param:
            nested_param = nfm.AddParameter(nested_param_name, group_type, param_type, True)
            
        # 2. Đồng bộ Subcategory
        nested_main_cat = nested_doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_DetailComponents)
        if not nested_main_cat.SubCategories.Contains(selected_subcat):
            nested_subcat = nested_doc.Settings.Categories.NewSubcategory(nested_main_cat, selected_subcat)
        else:
            nested_subcat = nested_main_cat.SubCategories.get_Item(selected_subcat)
            
        # Tìm mặt bằng làm việc hợp lệ
        plan_view = None
        for v in DB.FilteredElementCollector(nested_doc).OfClass(DB.View).ToElements():
            if v.ViewType in [DB.ViewType.FloorPlan, DB.ViewType.EngineeringPlan, DB.ViewType.CeilingPlan] and not v.IsTemplate:
                plan_view = v
                break
                
        # 3. Lọc CHÍNH XÁC CÁC NÉT LÀ ĐƯỜNG CONG (ARC)
        all_curves = DB.FilteredElementCollector(nested_doc).OfClass(DB.CurveElement).ToElements()
        arc_curves = []
        
        for c in all_curves:
            if isinstance(c, (DB.DetailCurve, DB.SymbolicCurve)):
                # Chỉ lấy nét nếu hình học của nó là cung tròn (DB.Arc)
                if isinstance(c.GeometryCurve, DB.Arc):
                    arc_curves.append(c)
                    
        target_arc = None
        
        # Kiểm tra xem đã có cung tròn nào mang Subcategory này chưa
        for arc in arc_curves:
            if arc.LineStyle and arc.LineStyle.Name == selected_subcat:
                target_arc = arc
                break
                
        # Nếu chưa có, tiến hành mượn 1 cung tròn gốc để vẽ lại cung mới
        if not target_arc and arc_curves and plan_view:
            template_arc = arc_curves[0]
            try:
                geom_curve = template_arc.GeometryCurve
                # Vẽ lại nét mới thuần túy
                if isinstance(template_arc, DB.SymbolicCurve):
                    target_arc = nested_doc.FamilyCreate.NewSymbolicCurve(geom_curve, plan_view)
                else:
                    target_arc = nested_doc.FamilyCreate.NewDetailCurve(plan_view, geom_curve)
            except Exception as e:
                print("Lỗi tái tạo Arc: {}".format(e))
                
        # 4. Gán Subcategory và gán biến liên kết
        if target_arc:
            if nested_subcat:
                target_arc.LineStyle = nested_subcat.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            if nested_param:
                # Sử dụng IS_VISIBLE_PARAM chuẩn xác theo API
                vis_param = target_arc.get_Parameter(DB.BuiltInParameter.IS_VISIBLE_PARAM)
                if vis_param:
                    try:
                        nfm.AssociateElementParameterToFamilyParameter(vis_param, nested_param)
                    except Exception:
                        pass
                        
        nt.Commit()
        nested_doc.LoadFamily(doc, FamilyOptionLoad())
        nested_updated = True
    except Exception as ex:
        nt.RollBack()
        print("Lỗi hệ thống khi xử lý Family con: {}".format(ex))
    finally:
        # Bắt buộc đóng Document ngầm để không bị lỗi Transaction lơ lửng
        nested_doc.Close(False) 
        
    return nested_updated

def main():
    detail_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_DetailComponents)
    subcategories = [sub.Name for sub in detail_cat.SubCategories]

    type_name = forms.ask_for_string(
        title="Tạo Type Hệ Thống Mới",
        default="NUOC LANH",
        prompt="Nhập tên hệ thống ống (Ví dụ: NUOC LANH, NUOC NONG...):"
    )
    if not type_name:
        script.exit()
        
    param_name = "He_Thong_" + type_name
    nested_param_name = "Hien_Thi_" + type_name

    selected_subcat = forms.SelectFromList.show(
        subcategories,
        title="Chọn nét Line (Subcategory) tương ứng với Type này",
        default=type_name,
        multiselect=False
    )
    if not selected_subcat:
        script.exit()

    # =========================================================
    # 1. GỌI HÀM XỬ LÝ FAMILY CON (CHỈ VẼ THÊM JUMP ARC)
    # =========================================================
    jump_family = next((f for f in DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements() if f.Name == "Jump_Symbol"), None)
    nested_updated = False
    
    if jump_family:
        nested_updated = update_nested_family(doc, jump_family, nested_param_name, selected_subcat)

    # =========================================================
    # 2. XỬ LÝ KHỞI TẠO VÀ LIÊN KẾT TẠI FAMILY CHA
    # =========================================================
    t = DB.Transaction(doc, "Create Geometry, Align and Map Parameters")
    t.Start()
    
    try:
        doc.Regenerate()
        
        param_type, group_type = get_forge_type_ids()
        
        new_param = next((p for p in fm.Parameters if p.Definition.Name == param_name), None)
        if not new_param:
            new_param = fm.AddParameter(param_name, group_type, param_type, False) 

        try:
            fm.NewType(type_name)
        except Exception:
            pass

        # Chỉ lấy Line thẳng để vẽ cho hệ thống ở Family cha
        all_curves = DB.FilteredElementCollector(doc).OfClass(DB.CurveElement).ToElements()
        old_line = next((c for c in all_curves if isinstance(c, DB.DetailLine) and isinstance(c.GeometryCurve, DB.Line)), None)
        
        line_created = False
        line_aligned = False
        
        if old_line:
            geom_curve = old_line.GeometryCurve
            new_line = doc.FamilyCreate.NewDetailCurve(view, geom_curve)
            
            main_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_DetailComponents)
            subcat_obj = main_cat.SubCategories.get_Item(selected_subcat)
            new_line.LineStyle = subcat_obj.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            line_created = True
            
            ref_planes = DB.FilteredElementCollector(doc).OfClass(DB.ReferencePlane).ToElements()
            rp_left = next((rp for rp in ref_planes if rp.Name in ["Left", "Trái"]), None)
            rp_right = next((rp for rp in ref_planes if rp.Name in ["Right", "Phải"]), None)
            
            if rp_left and rp_right:
                try:
                    start_ref = new_line.GeometryCurve.GetEndPointReference(0)
                    end_ref = new_line.GeometryCurve.GetEndPointReference(1)
                    
                    doc.FamilyCreate.NewAlignment(view, rp_left.GetReference(), start_ref)
                    doc.FamilyCreate.NewAlignment(view, rp_right.GetReference(), end_ref)
                    line_aligned = True
                except Exception:
                    pass

            vis_param = new_line.get_Parameter(DB.BuiltInParameter.IS_VISIBLE_PARAM)
            if vis_param:
                fm.AssociateElementParameterToFamilyParameter(vis_param, new_param)

        all_instances = DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance).ToElements()
        nested_associated = False
        
        for inst in all_instances:
            if inst.Symbol.Family.Name == "Jump_Symbol": 
                nested_param = inst.LookupParameter(nested_param_name)
                if nested_param:
                    fm.AssociateElementParameterToFamilyParameter(nested_param, new_param)
                    nested_associated = True

        all_types = fm.Types
        orig_current_type = fm.CurrentType
        he_thong_params = [p for p in fm.Parameters if p.Definition.Name.startswith("He_Thong_")]

        for t_item in all_types:
            fm.CurrentType = t_item 
            for p in he_thong_params:
                if t_item.Name == type_name and p.Definition.Name == param_name:
                    fm.Set(p, True)  
                elif t_item.Name == type_name and p.Definition.Name != param_name:
                    fm.Set(p, False) 
                elif t_item.Name != type_name and p.Definition.Name == param_name:
                    fm.Set(p, False) 

        fm.CurrentType = orig_current_type
        t.Commit()

        print("--- BÁO CÁO ĐỒNG BỘ 2 CHIỀU ---")
        print("✔ Loại Hệ Thống (Type): [{}]".format(type_name))
        print("✔ Xử lý (chỉ vẽ cung tròn Arc) trong [Jump_Symbol]: {}".format("THÀNH CÔNG" if nested_updated else "THẤT BẠI"))
        print("✔ Tạo & khóa nét thẳng (Align) ở Family cha: {}".format("THÀNH CÔNG" if line_created and line_aligned else "KIỂM TRA LẠI MẶT PHẲNG"))
        print("✔ Liên kết map biến 2 chiều [{} -> {}]: {}".format(nested_param_name, param_name, "THÀNH CÔNG" if nested_associated else "THẤT BẠI"))

    except Exception as e:
        t.RollBack()
        forms.alert("Quá trình chạy gặp lỗi hệ thống. Đã rollback.\n\nChi tiết:\n{}".format(traceback.format_exc()))

if __name__ == '__main__':
    main()