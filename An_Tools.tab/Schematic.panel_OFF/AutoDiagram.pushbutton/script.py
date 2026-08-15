# -*- coding: utf-8 -*-
import os
import json
import traceback
import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System")

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Plumbing import *
from Autodesk.Revit.UI import *

from System.Windows import Controls, Thickness, VerticalAlignment, TextWrapping
from System.Windows.Markup import XamlReader
from System.IO import File

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

# ==========================================
# 1. CẤU HÌNH THÔNG SỐ
# ==========================================
X_STEP = 1500 # Khoảng cách phương ngang (mm)
Z_MULTIPLIER = 1.5 # HỆ SỐ NHÂN CAO ĐỘ (Tăng số này để các thiết bị cách xa nhau hơn theo chiều dọc)
PIPE_DETAIL_FAMILY_NAME = "Schematic_Pipe" 

# Đã bổ sung Fitting và Sprinkler để không bị mất đoạn ống
VALID_CATEGORIES = [
    int(BuiltInCategory.OST_MechanicalEquipment),
    int(BuiltInCategory.OST_PlumbingFixtures),
    int(BuiltInCategory.OST_GenericModel),
    int(BuiltInCategory.OST_PipeAccessory),
    int(BuiltInCategory.OST_PipeFitting),
    int(BuiltInCategory.OST_Sprinklers)
]

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mapping_config.json')

# ==========================================
# 2. HÀM BỔ TRỢ 
# ==========================================
def safe_get_name(elem):
    if not elem: return ""
    try:
        p = elem.get_Parameter(BuiltInParameter.ELEM_NAME_PARAM)
        if p and p.AsString(): return p.AsString()
        p2 = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p2 and p2.AsString(): return p2.AsString()
    except: pass
    return str(elem.Name) if hasattr(elem, "Name") else "Unknown"

def safe_get_family_name(symbol):
    if not symbol: return ""
    try:
        if hasattr(symbol, "FamilyName"): return symbol.FamilyName
        if hasattr(symbol, "Family") and symbol.Family:
            p = symbol.Family.get_Parameter(BuiltInParameter.ELEM_NAME_PARAM)
            if p and p.AsString(): return p.AsString()
            return symbol.Family.Name
    except: pass
    return "Unknown"

def get_elevation(elem):
    """Lấy tọa độ Z (cao độ) thực tế của đối tượng trong không gian 3D"""
    try:
        loc = elem.Location
        if isinstance(loc, LocationPoint):
            return loc.Point.Z
        elif isinstance(loc, LocationCurve):
            # Lấy trung điểm nếu là Curve (ống)
            return (loc.Curve.GetEndPoint(0).Z + loc.Curve.GetEndPoint(1).Z) / 2.0
    except: pass
    
    # Dự phòng dùng BoundingBox
    try:
        bbox = elem.get_BoundingBox(None)
        if bbox: return (bbox.Min.Z + bbox.Max.Z) / 2.0
    except: pass
    
    return 0.0

# ==========================================
# 3. THUẬT TOÁN DÒ ĐƯỜNG KẾT NỐI (BFS)
# ==========================================
def get_connected_elements(start_elem):
    """Tìm các đối tượng kết nối tiếp theo, KHÔNG nhảy cóc qua Fitting"""
    results = []
    visited_internal = set()
    visited_internal.add(start_elem.Id.IntegerValue)
    
    queue = []
    if hasattr(start_elem, "MEPModel") and start_elem.MEPModel:
        for conn in start_elem.MEPModel.ConnectorManager.Connectors:
            # Lấy mọi hướng để không bỏ sót nhánh
            queue.append((conn, None))
                
    while queue:
        curr_conn, incoming_pipe = queue.pop(0)
        
        for ref in curr_conn.AllRefs:
            if ref.ConnectorType == ConnectorType.Logical: continue
            if ref.Owner.Id.IntegerValue == curr_conn.Owner.Id.IntegerValue: continue
            
            neighbor = ref.Owner
            n_id = neighbor.Id.IntegerValue
            
            if n_id in visited_internal: continue
            visited_internal.add(n_id)
            
            cat_id = neighbor.Category.Id.IntegerValue if neighbor.Category else -1
            
            if isinstance(neighbor, Pipe):
                # Nếu là ống, đi tiếp qua đầu kia của ống
                for p_conn in neighbor.ConnectorManager.Connectors:
                    if p_conn.Id != ref.Id:
                        queue.append((p_conn, neighbor))
            elif cat_id in VALID_CATEGORIES:
                # Nếu gặp thiết bị, phụ kiện hoặc FITTING -> Ghi nhận làm Node
                results.append((neighbor, incoming_pipe))
                
    return results

def trace_path(current_elem, current_logical_x, branch_level, visited_ids, valid_view_ids, incoming_pipe=None, parent_id=None, current_depth=0):
    if current_depth > 150: return []
    if current_elem.Id.IntegerValue in visited_ids: return []
    visited_ids.add(current_elem.Id.IntegerValue)
    
    nodes = [{
        'element': current_elem, 
        'logical_x': current_logical_x, 
        'branch_level': branch_level, 
        'incoming_pipe': incoming_pipe,
        'parent_id': parent_id 
    }]
    
    next_data = get_connected_elements(current_elem)
    # Lọc các đối tượng có trong vùng nhìn/được chọn
    valid_next_data = [(el, pipe) for el, pipe in next_data if el.Id in valid_view_ids]
    
    for i, (next_el, connecting_pipe) in enumerate(valid_next_data):
        nodes.extend(trace_path(next_el, current_logical_x + 1, branch_level + i, visited_ids, valid_view_ids, connecting_pipe, current_elem.Id.IntegerValue, current_depth + 1))
    return nodes

# ==========================================
# 4. CHƯƠNG TRÌNH CHÍNH
# ==========================================
def main():
    try:
        active_view_elements = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
        valid_view_ids = [e.Id for e in active_view_elements]

        selection = [doc.GetElement(id) for id in uidoc.Selection.GetElementIds()]
        if not selection:
            TaskDialog.Show("Lỗi", "Vui lòng chọn đối tượng đầu nguồn.")
            return
            
        start_element = selection[0]
        start_elevation = get_elevation(start_element)
        
        visited = set()
        schematic_nodes = trace_path(start_element, 0, 0, visited, valid_view_ids)
        
        if len(schematic_nodes) < 2:
            TaskDialog.Show("Cảnh báo", "Mạch hở hoặc thiết bị kết nối không nằm trong View hiện tại.")
            return

        unique_3d_fams = []
        for node in schematic_nodes:
            el = node['element']
            cat_id = el.Category.Id.IntegerValue if el.Category else -1
            
            # Bỏ qua không đòi hỏi Map Detail Component cho Fitting
            if cat_id == int(BuiltInCategory.OST_PipeFitting):
                continue
                
            param = el.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM)
            fam_name = param.AsValueString() if param else safe_get_name(el)
            unique_3d_fams.append(fam_name)
            
        unique_3d_fams = sorted(list(set(unique_3d_fams)))
        
        detail_collector = FilteredElementCollector(doc).OfClass(FamilySymbol).OfCategory(BuiltInCategory.OST_DetailComponents).ToElements()
        all_detail_items = []
        for s in detail_collector:
            f_name = safe_get_family_name(s)
            if f_name and f_name != "Unknown":
                all_detail_items.append(f_name)
        all_detail_items = sorted(list(set(all_detail_items)))

        saved_mapping = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    saved_mapping = json.load(f)
            except: pass

        xaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui.xaml')
        if not os.path.exists(xaml_path):
            TaskDialog.Show("Lỗi", "Không tìm thấy file ui.xaml trong thư mục script.")
            return

        try:
            xaml_str = File.ReadAllText(xaml_path)
            window = XamlReader.Parse(xaml_str)
        except Exception as ex:
            TaskDialog.Show("Lỗi nạp XAML", "Không thể biên dịch file ui.xaml:\n" + str(ex))
            return

        mapping_panel = window.FindName("MappingPanel")
        btn_draw = window.FindName("BtnDraw")
        btn_cancel = window.FindName("BtnCancel")
        
        mapping_result = {}
        ui_state = {"is_ok": False}
        comboboxes = {}
        
        for fam_name in unique_3d_fams:
            row = Controls.Grid()
            row.ColumnDefinitions.Add(Controls.ColumnDefinition())
            row.ColumnDefinitions.Add(Controls.ColumnDefinition())
            row.Margin = Thickness(0, 5, 0, 5)
            
            txt = Controls.TextBlock()
            txt.Text = fam_name
            txt.VerticalAlignment = VerticalAlignment.Center
            txt.TextWrapping = TextWrapping.Wrap 
            txt.Padding = Thickness(5, 0, 10, 0)
            Controls.Grid.SetColumn(txt, 0)
            
            cb = Controls.ComboBox()
            cb.Items.Add("-- Bỏ qua (Không đặt) --")
            cb.SelectedIndex = 0
            
            match_index = 0
            saved_val = saved_mapping.get(fam_name)
            
            if saved_val and saved_val in all_detail_items:
                match_index = all_detail_items.index(saved_val) + 1
            else:
                for i, detail_name in enumerate(all_detail_items):
                    if detail_name == fam_name: 
                        match_index = i + 1
                        break
            
            for detail_name in all_detail_items:
                cb.Items.Add(detail_name)
                
            cb.SelectedIndex = match_index
            cb.VerticalAlignment = VerticalAlignment.Center
            Controls.Grid.SetColumn(cb, 1)
            
            row.Children.Add(txt)
            row.Children.Add(cb)
            mapping_panel.Children.Add(row)
            
            comboboxes[fam_name] = cb

        def on_draw_click(sender, e):
            for f_n, combob in comboboxes.items():
                if combob.SelectedIndex > 0:
                    mapping_result[f_n] = combob.SelectedItem
                else:
                    mapping_result[f_n] = None
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(mapping_result, f)
            except: pass

            ui_state["is_ok"] = True
            window.Close()

        def on_cancel_click(sender, e):
            window.Close()

        btn_draw.Click += on_draw_click
        btn_cancel.Click += on_cancel_click
        
        if unique_3d_fams:
            window.ShowDialog()
        else:
            ui_state["is_ok"] = True
        
        if not ui_state["is_ok"]: return

        drafting_view = None
        for v in FilteredElementCollector(doc).OfClass(ViewDrafting):
            if safe_get_name(v) == "Schematic_Output": drafting_view = v; break
            
        if not drafting_view:
            TaskDialog.Show("Lỗi", "Vui lòng tạo một Drafting View có tên là 'Schematic_Output' trước khi chạy Tool.")
            return

        if doc.ActiveView.Id != drafting_view.Id:
            uidoc.ActiveView = drafting_view
        
        base_point = XYZ(0, 0, 0)
        try:
            base_point = uidoc.Selection.PickPoint("Click vào màn hình để chọn điểm đặt sơ đồ")
        except:
            return 

        t = Transaction(doc, "Vẽ Schematic Đứng")
        t.Start()
        
        node_coordinates = {}
        
        for node in schematic_nodes:
            el = node['element']
            x_pos = node['logical_x'] * X_STEP
            
            # Lấy cao độ Z và nhân với hệ số giãn
            el_elevation = get_elevation(el)
            delta_z = el_elevation - start_elevation
            final_y = delta_z * Z_MULTIPLIER
            
            insert_pt = XYZ(base_point.X + (x_pos / 304.8), base_point.Y + final_y, 0)
            node_coordinates[el.Id.IntegerValue] = insert_pt
            
            param = el.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM)
            fam_name_3d = param.AsValueString() if param else safe_get_name(el)
            
            mapped_name = mapping_result.get(fam_name_3d)
            if mapped_name:
                symbol = None
                for s in detail_collector:
                    if safe_get_family_name(s) == mapped_name:
                        symbol = s; break
                if symbol:
                    if not symbol.IsActive: symbol.Activate()
                    try:
                        f_placement = symbol.Family.FamilyPlacementType
                        if f_placement == FamilyPlacementType.ViewBased:
                            doc.Create.NewFamilyInstance(insert_pt, symbol, drafting_view)
                        elif f_placement in [FamilyPlacementType.CurveBased, FamilyPlacementType.CurveBasedDetail]:
                            end_pt = XYZ(insert_pt.X + 0.1, insert_pt.Y, 0)
                            tiny_line = Line.CreateBound(insert_pt, end_pt)
                            doc.Create.NewFamilyInstance(tiny_line, symbol, drafting_view)
                        else:
                            doc.Create.NewFamilyInstance(insert_pt, symbol, drafting_view)
                    except:
                        pass 

        # VẼ ỐNG GIỮA CÁC ĐIỂM
        for node in schematic_nodes:
            parent_id = node['parent_id']
            if not parent_id or parent_id not in node_coordinates: continue
                
            el_current = node['element']
            pt_end_raw = node_coordinates[el_current.Id.IntegerValue]
            pt_start_raw = node_coordinates[parent_id]
            
            pt_start = XYZ(pt_start_raw.X, pt_start_raw.Y, 0)
            pt_end = XYZ(pt_end_raw.X, pt_end_raw.Y, 0)
            
            if pt_start.DistanceTo(pt_end) < 0.01: continue 
            
            pipe_3d = node.get('incoming_pipe')
            sys_name = ""
            pipe_size = ""
            
            if pipe_3d:
                sys_type_param = pipe_3d.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
                if sys_type_param and sys_type_param.AsElementId().IntegerValue != -1:
                    sys_type_elem = doc.GetElement(sys_type_param.AsElementId())
                    if sys_type_elem: sys_name = safe_get_name(sys_type_elem)
                
                size_param = pipe_3d.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                pipe_size = size_param.AsValueString() if size_param else ""
            
            pipe_symbol = None
            fallback_symbol = None
            
            for s in detail_collector:
                if safe_get_family_name(s) == PIPE_DETAIL_FAMILY_NAME:
                    if not fallback_symbol: fallback_symbol = s
                    type_name_2d = safe_get_name(s)
                    if sys_name and sys_name.strip().lower() == type_name_2d.strip().lower():
                        pipe_symbol = s; break
            
            if not pipe_symbol: pipe_symbol = fallback_symbol
            
            if pipe_symbol:
                if not pipe_symbol.IsActive: pipe_symbol.Activate()
                try:
                    p_placement = pipe_symbol.Family.FamilyPlacementType
                    pipe_inst = None
                    
                    if p_placement in [FamilyPlacementType.CurveBased, FamilyPlacementType.CurveBasedDetail]:
                        line_geom = Line.CreateBound(pt_start, pt_end)
                        pipe_inst = doc.Create.NewFamilyInstance(line_geom, pipe_symbol, drafting_view)
                    else:
                        pipe_inst = doc.Create.NewFamilyInstance(pt_start, pipe_symbol, drafting_view)
                    
                    if pipe_size and pipe_inst:
                        p_size = pipe_inst.LookupParameter("Size")
                        if p_size and not p_size.IsReadOnly: p_size.Set(pipe_size)
                except Exception as ex:
                    print("Lỗi tạo đoạn ống: " + str(ex))
                        
        t.Commit()
        
    except Exception as e:
        if 't' in locals() and t.HasStarted(): t.RollBack()
        TaskDialog.Show("Lỗi Hệ Thống", "Gặp sự cố:\n\n" + traceback.format_exc())

if __name__ == "__main__":
    main()