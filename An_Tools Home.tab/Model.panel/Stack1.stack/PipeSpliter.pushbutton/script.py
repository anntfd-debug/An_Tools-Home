# -*- coding: utf-8 -*-
import clr
import os
import traceback
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.DB.Plumbing import Pipe
from pyrevit import revit, forms, script
from System.Windows.Controls import CheckBox
from System.Windows import Thickness

doc = revit.doc
uidoc = revit.uidoc

class SplitPipeWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.cfg = script.get_config()
        
        # Nạp dữ liệu cấu hình cũ từ lần chạy trước
        self.txt_length.Text = self.cfg.get_option("PipeSplitter:length", "6000")
        self.txt_wall_offset.Text = self.cfg.get_option("PipeSplitter:wall_offset", "500")
        self.txt_min_size.Text = self.cfg.get_option("PipeSplitter:min_size", "65")
        self.txt_max_size.Text = self.cfg.get_option("PipeSplitter:max_size", "300")
        
        if self.cfg.get_option("PipeSplitter:mode", "selection") == "all":
            self.rb_all.IsChecked = True
        else:
            self.rb_selection.IsChecked = True
            
        # Nạp trạng thái nút bỏ qua tối ưu tường
        self.cb_skip_wall.IsChecked = self.cfg.get_option("PipeSplitter:skip_wall", "false") == "true"
            
        # QUÉT VÀ NẠP CÁC FILE LINK VÀO LISTBOX
        self.link_checkboxes = []
        link_instances = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
        
        for link in link_instances:
            link_doc = link.GetLinkDocument()
            if link_doc: # Chỉ hiện các file link đang được Load
                cb = CheckBox()
                cb.Content = link.Name
                cb.Tag = link # Lưu trữ đối tượng link để lấy dữ liệu sau này
                cb.Margin = Thickness(2, 2, 2, 2)
                self.lb_links.Items.Add(cb)
                self.link_checkboxes.append(cb)
                
        self.is_submitted = False

    def btn_run_Click(self, sender, e):
        # Lưu cấu hình hệ thống
        self.cfg.set_option("PipeSplitter:length", self.txt_length.Text)
        self.cfg.set_option("PipeSplitter:wall_offset", self.txt_wall_offset.Text)
        self.cfg.set_option("PipeSplitter:min_size", self.txt_min_size.Text)
        self.cfg.set_option("PipeSplitter:max_size", self.txt_max_size.Text)
        self.cfg.set_option("PipeSplitter:mode", "all" if self.rb_all.IsChecked else "selection")
        self.cfg.set_option("PipeSplitter:skip_wall", "true" if self.cb_skip_wall.IsChecked else "false")
        script.save_config()
        
        self.is_submitted = True
        self.Close()

# Hàm lấy Connector tại một vị trí tọa độ
def get_connector_at_point(pipe, point, tolerance=0.001):
    for conn in pipe.ConnectorManager.Connectors:
        if conn.Origin.DistanceTo(point) < tolerance:
            return conn
    return None

def main():
    # Khởi chạy UI (Chạy lệnh trước)
    xaml_path = os.path.join(os.path.dirname(__file__), 'ui.xaml')
    if not os.path.exists(xaml_path):
        forms.alert(u"Không tìm thấy file ui.xaml nằm cùng thư mục với script.py.", exitscript=True)

    win = SplitPipeWindow(xaml_path)
    win.ShowDialog()

    if not win.is_submitted:
        script.exit()

    # Phân tách dữ liệu đầu vào từ giao diện
    try:
        split_length_feet = float(win.txt_length.Text) / 304.8
        wall_offset_feet = float(win.txt_wall_offset.Text) / 304.8
        min_size_mm = float(win.txt_min_size.Text)
        max_size_mm = float(win.txt_max_size.Text)
    except (ValueError, TypeError):
        forms.alert(u"Vui lòng nhập đúng định dạng số cho các thông số cấu hình!")
        script.exit()

    if split_length_feet <= 0:
        forms.alert(u"Chiều dài tiêu chuẩn phải lớn hơn 0 mm!")
        script.exit()
    if wall_offset_feet < 0:
        forms.alert(u"Khoảng cách tường không được nhỏ hơn 0 mm!")
        script.exit()
    if min_size_mm < 0 or max_size_mm < min_size_mm:
        forms.alert(u"Khoảng Size Min/Max không hợp lệ!")
        script.exit()

    # Thu thập trạng thái thiết lập từ UI
    selected_links = [cb.Tag for cb in win.link_checkboxes if cb.IsChecked]
    skip_wall = win.cb_skip_wall.IsChecked

    # THAO TÁC CHỌN ỐNG SAU KHI ĐÓNG UI
    if win.rb_all.IsChecked:
        collector = FilteredElementCollector(doc).OfClass(Pipe).WhereElementIsNotElementType()
        pipes = [p for p in collector]
    else:
        # Gọi chế độ PickObjects của Revit để người dùng quét chọn ống trên mặt bằng
        try:
            ref_pipes = uidoc.Selection.PickObjects(ObjectType.Element, u"Hãy quét chọn các ống cần cắt chia trên màn hình, sau đó nhấn Finish.")
            pipes = [doc.GetElement(ref.ElementId) for ref in ref_pipes if isinstance(doc.GetElement(ref.ElementId), Pipe)]
        except OperationCanceledException:
            script.exit()
        except Exception as ex:
            forms.alert(u"Không thể quét chọn ống.\n\n{0}".format(ex))
            script.exit()

    if not pipes:
        forms.alert(u"Không tìm thấy đoạn ống nào hợp lệ để xử lý!")
        script.exit()

    total_pipes = len(pipes)
    interrupted = False
    processed_count = 0
    split_count = 0
    error_messages = []

    # Thiết lập Thanh Progress Bar tích hợp nút Hủy lệnh (cancellable=True)
    with forms.ProgressBar(title=u"Đang tiến hành phân tách ống... (Nhấn ESC hoặc Cancel để dừng)", cancellable=True) as pb:
        with revit.Transaction(u"Advanced Auto Split Pipes Pro"):
            for idx, pipe in enumerate(pipes):
            
                # 1. KIỂM TRA XEM NGƯỜI DÙNG CÓ BẤM CANCEL KHÔNG (Sửa lại .cancelled)
                if pb.cancelled:
                    interrupted = True
                    break
                
                # 2. CẬP NHẬT TIẾN TRÌNH TRÊN PROGRESS BAR
                pb.update_progress(idx + 1, total_pipes)
            
                diameter_param = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                if not diameter_param:
                    continue
                diameter_mm = diameter_param.AsDouble() * 304.8
                if not (min_size_mm <= diameter_mm <= max_size_mm):
                    continue

                curve = pipe.Location.Curve
                if not isinstance(curve, Line):
                    continue
                
                processed_count += 1

                p_start = curve.GetEndPoint(0)
                p_end = curve.GetEndPoint(1)
            
                # Thiết lập mặc định ban đầu: Cắt từ đầu ống p_start đến p_end
                pt_origin, pt_target = p_start, p_end
            
                # Nếu KHÔNG tích chọn bỏ qua tường, tiến hành tính toán tối ưu theo khoảng cách tường
                if not skip_wall:
                    pipe_bb = pipe.get_BoundingBox(None)
                    nearby_walls_data = [] # Lưu trữ tuple dạng (Wall, Transform)
                
                    if pipe_bb:
                        # Tìm tường trong file Host gốc
                        outline_host = Outline(pipe_bb.Min - XYZ(5,5,5), pipe_bb.Max + XYZ(5,5,5))
                        filter_box_host = BoundingBoxIntersectsFilter(outline_host)
                        host_walls = FilteredElementCollector(doc).OfClass(Wall).WhereElementIsNotElementType().WherePasses(filter_box_host).ToElements()
                        for w in host_walls:
                            nearby_walls_data.append((w, None))
                    
                        # Tìm tường trong các file Link được tick chọn
                        for link_inst in selected_links:
                            link_doc = link_inst.GetLinkDocument()
                            if not link_doc:
                                continue
                        
                            transform = link_inst.GetTotalTransform()
                            inv_transform = transform.Inverse
                        
                            p1, p2 = pipe_bb.Min, pipe_bb.Max
                            corners = [
                                XYZ(p1.X, p1.Y, p1.Z), XYZ(p2.X, p1.Y, p1.Z), XYZ(p1.X, p2.Y, p1.Z), XYZ(p2.X, p2.Y, p1.Z),
                                XYZ(p1.X, p1.Y, p2.Z), XYZ(p2.X, p1.Y, p2.Z), XYZ(p1.X, p2.Y, p2.Z), XYZ(p2.X, p2.Y, p2.Z)
                            ]
                            local_pts = [inv_transform.OfPoint(p) for p in corners]
                            xs = [p.X for p in local_pts]
                            ys = [p.Y for p in local_pts]
                            zs = [p.Z for p in local_pts]
                        
                            local_outline = Outline(XYZ(min(xs)-5, min(ys)-5, min(zs)-5), XYZ(max(xs)+5, max(ys)+5, max(zs)+5))
                            filter_box_link = BoundingBoxIntersectsFilter(local_outline)
                        
                            link_walls = FilteredElementCollector(link_doc).OfClass(Wall).WhereElementIsNotElementType().WherePasses(filter_box_link).ToElements()
                            for w in link_walls:
                                nearby_walls_data.append((w, transform))

                    # Tính toán khoảng cách an toàn từ tường đến các đầu ống
                    min_d0, min_d1 = float('inf'), float('inf')
                    for wall, transform in nearby_walls_data:
                        w_curve = wall.Location.Curve
                        if w_curve:
                            if transform:
                                w_curve = w_curve.CreateTransformed(transform)
                            
                            proj0 = w_curve.Project(p_start)
                            d0 = proj0.Distance if proj0 else min(p_start.DistanceTo(w_curve.GetEndPoint(0)), p_start.DistanceTo(w_curve.GetEndPoint(1)))
                            proj1 = w_curve.Project(p_end)
                            d1 = proj1.Distance if proj1 else min(p_end.DistanceTo(w_curve.GetEndPoint(0)), p_end.DistanceTo(w_curve.GetEndPoint(1)))
                        
                            if d0 < min_d0: min_d0 = d0
                            if d1 < min_d1: min_d1 = d1

                    if min_d1 < min_d0:
                        pt_origin, pt_target = p_end, p_start
                    else:
                        pt_origin, pt_target = p_start, p_end
                
                direction = (pt_target - pt_origin).Normalize()

                conn_start = get_connector_at_point(pipe, p_start)
                conn_end = get_connector_at_point(pipe, p_end)
            
                connected_start_to = None
                if conn_start and conn_start.IsConnected:
                    for ref in conn_start.AllRefs:
                        if ref.Owner.Id != pipe.Id and ref.ConnectorType == ConnectorType.End:
                            connected_start_to = ref
                            break
                        
                connected_end_to = None
                if conn_end and conn_end.IsConnected:
                    for ref in conn_end.AllRefs:
                        if ref.Owner.Id != pipe.Id and ref.ConnectorType == ConnectorType.End:
                            connected_end_to = ref
                            break

                current_pipe = pipe
                is_first_cut = True
            
                while True:
                    cur_curve = current_pipe.Location.Curve
                    c_start = cur_curve.GetEndPoint(0)
                    c_end = cur_curve.GetEndPoint(1)
                
                    if (c_start - pt_origin).DotProduct(direction) < (c_end - pt_origin).DotProduct(direction):
                        working_start, working_end = c_start, c_end
                    else:
                        working_start, working_end = c_end, c_start
                    
                    remaining_len = working_start.DistanceTo(working_end)
                
                    step_len = wall_offset_feet if (is_first_cut and not skip_wall and wall_offset_feet > 0) else split_length_feet
                
                    if remaining_len <= step_len + 0.001:
                        break
                    
                    split_pt = working_start + direction * step_len
                
                    pipe_type_id = current_pipe.GetTypeId()
                    sys_type_id = current_pipe.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM).AsElementId()
                    level_id = current_pipe.LevelId
                    diameter = current_pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM).AsDouble()
                
                    try:
                        current_pipe.Location.Curve = Line.CreateBound(working_start, split_pt)
                    except Exception as ex:
                        error_messages.append(u"Pipe ID {0}: không thể rút ngắn ống - {1}".format(pipe.Id.IntegerValue, ex))
                        break
                    
                    try:
                        new_pipe = Pipe.Create(doc, sys_type_id, pipe_type_id, level_id, split_pt, working_end)
                        new_pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM).Set(diameter)
                        split_count += 1
                        doc.Regenerate()
                    except Exception as ex:
                        error_messages.append(u"Pipe ID {0}: không thể tạo đoạn ống mới - {1}".format(pipe.Id.IntegerValue, ex))
                        break
                
                    c1 = get_connector_at_point(current_pipe, split_pt)
                    c2 = get_connector_at_point(new_pipe, split_pt)
                    if c1 and c2:
                        try:
                            doc.Create.NewUnionFitting(c1, c2)
                        except Exception as ex:
                            error_messages.append(u"Pipe ID {0}: không tạo được Union - {1}".format(pipe.Id.IntegerValue, ex))
                        
                    current_pipe = new_pipe
                    is_first_cut = False

                if connected_start_to:
                    c_start_now = get_connector_at_point(pipe, p_start)
                    if c_start_now:
                        try:
                            c_start_now.ConnectTo(connected_start_to)
                        except Exception as ex:
                            error_messages.append(u"Pipe ID {0}: không nối lại được đầu ống thứ nhất - {1}".format(pipe.Id.IntegerValue, ex))
                    
                if connected_end_to:
                    c_end_now = get_connector_at_point(current_pipe, p_end)
                    if c_end_now:
                        try:
                            c_end_now.ConnectTo(connected_end_to)
                        except Exception as ex:
                            error_messages.append(u"Pipe ID {0}: không nối lại được đầu ống thứ hai - {1}".format(pipe.Id.IntegerValue, ex))

    # Hiển thị kết quả bằng hộp thoại, tránh cửa sổ output pyRevit trắng
    if interrupted:
        forms.alert(
            u"Quá trình đã dừng.\n\nỐng hợp lệ đã kiểm tra: {0}\nSố lần chia ống: {1}".format(
                processed_count, split_count
            )
        )
    elif split_count == 0:
        forms.alert(
            u"Tool đã chạy nhưng không tạo ra đoạn chia nào.\n\n"
            u"Ống được chọn/thu thập: {0}\n"
            u"Ống đạt bộ lọc và là ống thẳng: {1}\n\n"
            u"Hãy kiểm tra chiều dài ống, Size Min/Max và chiều dài chia.".format(
                total_pipes, processed_count
            )
        )
    else:
        message = u"Hoàn tất chia ống.\n\nỐng được thu thập: {0}\nỐng hợp lệ đã xử lý: {1}\nSố lần chia: {2}".format(
            total_pipes, processed_count, split_count
        )
        if error_messages:
            message += u"\n\nCó {0} lỗi cục bộ. Chi tiết nằm trong pyRevit Output.".format(len(error_messages))
            output = script.get_output()
            output.print_md(u"## Auto Split Pipes Pro - Chi tiết lỗi")
            for err in error_messages:
                output.print_md(u"- {0}".format(err))
        forms.alert(message)


try:
    main()
except Exception as ex:
    details = traceback.format_exc()
    output = script.get_output()
    output.print_md(u"## Auto Split Pipes Pro - Lỗi chưa xử lý")
    try:
        output.print_code(details)
    except Exception:
        output.print_md(u"```\n{0}\n```".format(details))
    forms.alert(u"Tool gặp lỗi và đã dừng. Chi tiết lỗi đã được ghi trong pyRevit Output.\n\n{0}".format(ex))
