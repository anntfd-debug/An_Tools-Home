# -*- coding: utf-8 -*-
__title__ = "Correct Pipe Fitting"
__doc__ = "Sửa ống và fitting về đúng loại của hệ thống được chọn"
import os
from pyrevit import revit, DB, UI
from pyrevit import forms

doc = revit.doc
uidoc = revit.uidoc

# ====================================================================
# CÁC HÀM PHỤ TRỢ (Giữ nguyên)
# ====================================================================
def get_real_name(elem):
    if not elem:
        return u"Không xác định"
    try:
        for param_id in [DB.BuiltInParameter.ALL_MODEL_TYPE_NAME,
                         DB.BuiltInParameter.SYMBOL_NAME_PARAM,
                         DB.BuiltInParameter.RBS_SYSTEM_NAME_PARAM]:
            param = elem.get_Parameter(param_id)
            if param and param.AsString():
                return param.AsString()
    except Exception:
        pass
    try:
        n = revit.query.get_name(elem)
        if n: return n
    except Exception:
        pass
    try:
        if hasattr(elem, "Name") and elem.Name:
            return elem.Name
    except Exception:
        pass
    return u"Đối tượng ID [{}]".format(elem.Id.ToString())

def get_real_family_name(elem):
    if not elem: return u"Family Không Xác Định"
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        if p and p.AsString(): return p.AsString()
    except Exception: pass
    try:
        if hasattr(elem, "FamilyName") and elem.FamilyName: return elem.FamilyName
    except Exception: pass
    try:
        if hasattr(elem, "Family") and elem.Family: return elem.Family.Name
    except Exception: pass
    return u"Family Không Xác Định"

def get_part_type(family_symbol):
    if not family_symbol: return None
    try:
        param = family_symbol.Family.get_Parameter(DB.BuiltInParameter.FAMILY_CONTENT_PART_TYPE)
        if param: return param.AsInteger()
    except Exception: pass
    return None

class ReplaceElementsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.setup_data()

    def setup_data(self):
        pipes = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeCurves).WhereElementIsNotElementType().ToElements()
        fittings = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeFitting).WhereElementIsNotElementType().ToElements()
        self.all_elements = list(pipes) + list(fittings)
        
        # 1. DỮ LIỆU SYSTEM
        used_system_ids = set()
        for elem in self.all_elements:
            sys_param = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
            if sys_param and sys_param.AsElementId() != DB.ElementId.InvalidElementId:
                used_system_ids.add(sys_param.AsElementId())
                
        self.system_map = {}
        for s_id in used_system_ids:
            sys_elem = doc.GetElement(s_id)
            if sys_elem:
                name = get_real_name(sys_elem)
                if name in self.system_map:
                    name = u"{} [{}]".format(name, sys_elem.Id.ToString())
                self.system_map[name] = sys_elem
        
        self.cmb_Systems.ItemsSource = sorted(self.system_map.keys())

        # 2. DỮ LIỆU PIPE TYPE
        pipe_types_elems = DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipeType).ToElements()
        self.pipe_map = {}
        for pt in pipe_types_elems:
            name = get_real_name(pt)
            if name in self.pipe_map:
                name = u"{} [{}]".format(name, pt.Id.ToString())
            self.pipe_map[name] = pt
            
        self.cmb_PipeTypes.ItemsSource = sorted(self.pipe_map.keys())

        # 3. DỮ LIỆU FITTING TYPE
        fitting_types_elems = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_PipeFitting).WhereElementIsElementType().ToElements()
        self.fitting_map = {}
        for f in fitting_types_elems:
            fam_name = get_real_family_name(f)
            t_name = get_real_name(f)
            display_name = u"{}: {}".format(fam_name, t_name)
            
            if display_name in self.fitting_map:
                display_name = u"{} [{}]".format(display_name, f.Id.ToString())
            self.fitting_map[display_name] = f
            
        self.lst_FittingTypes.ItemsSource = sorted(self.fitting_map.keys())

    def replace_click(self, sender, args):
        sel_system_str = self.cmb_Systems.SelectedItem
        sel_pipe_str = self.cmb_PipeTypes.SelectedItem
        sel_fitting_strs = self.lst_FittingTypes.SelectedItems 

        if not sel_system_str or not sel_pipe_str or sel_fitting_strs.Count == 0:
            forms.alert(u"Vui lòng chọn 1 System, 1 Pipe Type và ÍT NHẤT 1 Fitting Type!", title="Thiếu thông tin")
            return
            
        sel_system = self.system_map[sel_system_str]
        sel_pipe_type = self.pipe_map[sel_pipe_str]
        sel_fitting_types = [self.fitting_map[s] for s in sel_fitting_strs]

        target_fittings_map = {}
        for f_type in sel_fitting_types:
            pt = get_part_type(f_type)
            if pt is not None:
                target_fittings_map[pt] = f_type

        success_count = 0
        error_count = 0
        invalid_count = 0 # Đếm số đối tượng bị Revit tự động xóa

        with revit.Transaction("Thay the Pipes va Fittings"):
            for elem in self.all_elements:
                # ==========================================================
                # LỚP BẢO VỆ 1: Kiểm tra xem đối tượng có còn tồn tại không
                # ==========================================================
                if not elem.IsValidObject:
                    invalid_count += 1
                    continue
                
                # ==========================================================
                # LỚP BẢO VỆ 2: Đưa việc đọc dữ liệu vào khối Try-Except
                # ==========================================================
                try:
                    sys_param = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
                    
                    if sys_param and sys_param.AsElementId() == sel_system.Id:
                        if isinstance(elem, DB.Plumbing.Pipe):
                            diam_param = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                            old_diameter = diam_param.AsDouble() if diam_param else None
                            
                            elem.ChangeTypeId(sel_pipe_type.Id)
                            
                            if old_diameter is not None:
                                diam_param = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                                if diam_param and not diam_param.IsReadOnly:
                                    diam_param.Set(old_diameter)
                            success_count += 1
                            
                        elif isinstance(elem, DB.FamilyInstance):
                            old_pt = get_part_type(elem.Symbol)
                            
                            if old_pt is not None and old_pt in target_fittings_map:
                                elem.ChangeTypeId(target_fittings_map[old_pt].Id)
                                success_count += 1
                            else:
                                changed = False
                                for f_type in sel_fitting_types:
                                    try:
                                        elem.ChangeTypeId(f_type.Id)
                                        changed = True
                                        success_count += 1
                                        break
                                    except Exception:
                                        pass
                                if not changed:
                                    error_count += 1
                except Exception:
                    # Nếu có bất kỳ lỗi hình học/độ dốc nào khác, âm thầm bỏ qua để chạy tiếp
                    error_count += 1

        self.Close()
        
        msg = u"Đã thay đổi thành công: {} đối tượng.\n".format(success_count)
        if error_count > 0 or invalid_count > 0:
            msg += u"\n⚠️ CẢNH BÁO NHẸ:\n"
            if error_count > 0:
                msg += u"- {} đối tượng bị bỏ qua do sai kết nối/độ dốc.\n".format(error_count)
            if invalid_count > 0:
                msg += u"- {} đối tượng đã bị Revit tự động xóa trong quá trình cập nhật (thường là các fitting dính sát nhau).".format(invalid_count)
            forms.alert(msg, title="Kết quả", warn_icon=True)
        else:
            forms.alert(msg, title="Hoàn tất")

script_dir = os.path.dirname(__file__)
xaml_path = os.path.join(script_dir, 'ui.xaml')

if os.path.exists(xaml_path):
    ReplaceElementsWindow(xaml_path).show_dialog()
else:
    forms.alert(u"Không tìm thấy file giao diện ui.xaml!", title="Lỗi đường dẫn")