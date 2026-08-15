# -*- coding: utf-8 -*-
import math
import clr
from pyrevit import revit, DB, UI
from pyrevit import forms

doc = revit.doc
uidoc = revit.uidoc

class PipeSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        # Chỉ cho phép chọn ống (Pipe)
        return isinstance(elem, DB.Plumbing.Pipe)
    def AllowReference(self, ref, pos):
        return True

def get_closest_connector(element, pick_point):
    """Tìm connector gần với điểm người dùng click chuột nhất"""
    min_dist = float('inf')
    closest_conn = None
    for conn in element.ConnectorManager.Connectors:
        dist = conn.Origin.DistanceTo(pick_point)
        if dist < min_dist:
            min_dist = dist
            closest_conn = conn
    return closest_conn

def main():
    try:
        # Yêu cầu người dùng chọn ống và lấy vị trí click chuột
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            PipeSelectionFilter(),
            "Chọn một điểm gần đầu ống để tạo Co 45 độ"
        )
        pipe = doc.GetElement(ref)
        pick_point = ref.GlobalPoint
        
    except Autodesk.Revit.Exceptions.OperationCanceledException:
        # Hủy lệnh khi nhấn ESC
        return

    # Lấy connector hở tại vị trí vừa chọn
    conn1 = get_closest_connector(pipe, pick_point)

    if not conn1:
        forms.alert("Không tìm thấy connector trên ống này.", exitscript=True)

    if conn1.IsConnected:
        forms.alert("Đầu ống này đã được kết nối. Vui lòng chọn một đầu ống hở.", exitscript=True)

    with revit.Transaction("Tạo Elbow 45 độ và giữ nguyên ống"):
        # 1. Lấy vector hướng thẳng ra từ đầu ống hiện tại
        v_out = conn1.CoordinateSystem.BasisZ

        # 2. Xoay vector 45 độ sang TRÁI (ngược chiều kim đồng hồ quanh trục Z)
        # Việc xoay quanh trục Z giúp giữ nguyên thành phần Z của vector, bảo toàn tuyệt đối độ dốc (slope)
        angle_45_left = math.pi / 4.0 
        rot_transform = DB.Transform.CreateRotation(DB.XYZ.BasisZ, angle_45_left)
        v_new = rot_transform.OfVector(v_out)

        # 3. Khởi tạo đoạn ống mới theo hướng rẽ trái 45 độ
        p_start = conn1.Origin
        length = 2.0 # Chiều dài đoạn ống mới (2 feet ~ 600mm), anh có thể tùy chỉnh lại độ dài này nếu cần
        p_end = p_start + (v_new * length)

        system_type_param = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM)
        system_type_id = system_type_param.AsElementId() if system_type_param else DB.ElementId.InvalidElementId
        pipe_type_id = pipe.GetTypeId()
        level_id = pipe.ReferenceLevel.Id

        # Tạo ống mới
        new_pipe = DB.Plumbing.Pipe.Create(doc, system_type_id, pipe_type_id, level_id, p_start, p_end)

        # Đồng bộ đường kính ống mới với ống gốc để tránh phát sinh Reducer (côn thu)
        diameter = pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM).AsDouble()
        new_pipe.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM).Set(diameter)

        # 4. Tìm connector trên ống mới (điểm bắt đầu nối với ống gốc)
        conn2 = get_closest_connector(new_pipe, p_start)

        # 5. Sinh ra fitting Elbow nối giữa 2 đoạn ống
        try:
            elbow = doc.Create.NewElbowFitting(conn1, conn2)
        except Exception as e:
            forms.alert("Không thể tạo fitting. Vui lòng kiểm tra lại cấu hình Routing Preferences của loại ống.\nChi tiết lỗi: {}".format(e))
            return

        # Đoạn code xóa ống tạm cũ (doc.Delete) đã được gỡ bỏ theo yêu cầu.

if __name__ == '__main__':
    main()