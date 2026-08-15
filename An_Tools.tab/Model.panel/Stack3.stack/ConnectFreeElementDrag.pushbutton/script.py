# -*- coding: utf-8 -*-
__title__ = "Connect Free Element"
__doc__ = "Quét vùng để kết nối ống vào Fitting và Pipe Accessories không cần cắt ống"
from pyrevit import revit, DB, forms, script
from Autodesk.Revit.UI.Selection import ObjectType

doc = revit.doc
uidoc = revit.uidoc

MAX_DIST_MM = 2000 
MAX_DIST_FEET = MAX_DIST_MM / 304.8

def get_connectors(element):
    """Hàm lấy các Connector vật lý chuẩn cho cả Pipe, Accessory và Fitting."""
    connectors = []
    con_mgr = None
    if hasattr(element, "ConnectorManager") and element.ConnectorManager:
        con_mgr = element.ConnectorManager
    elif hasattr(element, "MEPModel") and element.MEPModel:
        con_mgr = element.MEPModel.ConnectorManager
                
    if con_mgr:
        for c in con_mgr.Connectors:
            if c.ConnectorType != DB.ConnectorType.Logical:
                connectors.append(c)
    return connectors

def main():
    # 1. Lấy danh sách đối tượng được chọn
    selection_ids = uidoc.Selection.GetElementIds()
    selected_elements = [doc.GetElement(eid) for eid in selection_ids]
    
    if not selected_elements:
        forms.toast("Vui lòng quét chọn vùng chứa Ống và Phụ kiện...", title="Hướng dẫn")
        try:
            refs = uidoc.Selection.PickObjects(ObjectType.Element, "Quét chọn vùng rồi nhấn FINISH trên thanh Ribbon")
            selected_elements = [doc.GetElement(r.ElementId) for r in refs]
        except Exception:
            script.exit()

    pipes = []
    unconnected_items = []
    
    acc_category_id = int(DB.BuiltInCategory.OST_PipeAccessory)
    fitting_category_id = int(DB.BuiltInCategory.OST_PipeFitting)
    fixture_category_id = int(DB.BuiltInCategory.OST_PlumbingFixtures)
    
    # 2. Phân loại đối tượng với bộ lọc thông minh hơn cho cấu kiện nhiều đầu (như Tê)
    for el in selected_elements:
        if not el: 
            continue
        if isinstance(el, DB.Plumbing.Pipe):
            pipes.append(el)
        elif isinstance(el, DB.FamilyInstance) and el.Category.Id.IntegerValue in [acc_category_id, fitting_category_id, fixture_category_id]:
            all_conns = get_connectors(el)
            # Lọc ra các đầu nối ĐANG HỞ (chưa kết nối với ai)
            open_conns = [c for c in all_conns if not c.IsConnected]
            
            # ĐIỀU KIỆN MỚI: Chỉ xử lý cấu kiện nếu nó có ĐÚNG 2 đầu nối đang hở
            # (Phù hợp cho van 2 đầu hở, HOẶC Tê 3 đầu nhưng đã nối 1 đầu rẽ, còn hở 2 đầu thẳng)
            if len(open_conns) == 2:
                unconnected_items.append(el)

    if not pipes or not unconnected_items:
        forms.alert("Không tìm thấy đủ cặp Ống và Phụ kiện thỏa mãn điều kiện hở 2 đầu để kết nối!", title="Thông báo")
        script.exit()

    success_count = 0
    
    # 3. Tiến hành thực hiện hàng loạt
    with revit.Transaction("Auto Connect Open Multi-Connectors"):
        for item in unconnected_items:
            all_conns = get_connectors(item)
            open_conns = [c for c in all_conns if not c.IsConnected]
            
            # Lấy trung điểm hình học của 2 đầu hở làm điểm định vị trung tâm để cắt ống
            item_pt = open_conns[0].Origin.Add(open_conns[1].Origin).Multiply(0.5)
            
            closest_pipe = None
            min_dist = float('inf')
            best_proj = None
            
            # Tìm đoạn ống chính nằm gần trục hở của phụ kiện này nhất
            for pipe in pipes:
                try:
                    pipe_curve = pipe.Location.Curve
                    proj = pipe_curve.Project(item_pt)
                    if proj:
                        dist = proj.Distance
                        if dist < min_dist:
                            min_dist = dist
                            closest_pipe = pipe
                            best_proj = proj
                except:
                    continue
            
            if closest_pipe and min_dist <= MAX_DIST_FEET:
                sub_trans = DB.SubTransaction(doc)
                sub_trans.Start()
                try:
                    # Bước 3.1: Dịch chuyển item về tim ống chính
                    split_pt = best_proj.XYZPoint
                    translation_vec = split_pt.Subtract(item_pt)
                    
                    if translation_vec.GetLength() > 0.001:
                        DB.ElementTransformUtils.MoveElement(doc, item.Id, translation_vec)
                        doc.Regenerate()
                    
                    # Bước 3.2: Tính điểm cắt chuẩn xác sau khi dịch chuyển
                    # Lấy lại vị trí 2 đầu hở sau dịch chuyển
                    all_conns = get_connectors(item)
                    open_conns = [c for c in all_conns if not c.IsConnected]
                    current_item_pt = open_conns[0].Origin.Add(open_conns[1].Origin).Multiply(0.5)
                    
                    current_curve = closest_pipe.Location.Curve
                    final_project = current_curve.Project(current_item_pt)
                    if not final_project:
                        sub_trans.RollBack()
                        continue
                    split_pt = final_project.XYZPoint
                    
                    if split_pt.DistanceTo(current_curve.GetEndPoint(0)) < 0.05 or \
                       split_pt.DistanceTo(current_curve.GetEndPoint(1)) < 0.05:
                        sub_trans.RollBack()
                        continue
                    
                    # Bước 3.3: Tiến hành cắt ống chính làm đôi
                    new_pipe_id = DB.Plumbing.PlumbingUtils.BreakCurve(doc, closest_pipe.Id, split_pt)
                    new_pipe = doc.GetElement(new_pipe_id)
                    doc.Regenerate()
                    
                    pipes.append(new_pipe)
                    
                    # Bước 3.4: Co ngắn ống khớp khít vào bề mặt 2 đầu nối đang hở
                    c1 = closest_pipe.Location.Curve
                    p1_far = c1.GetEndPoint(0) if c1.GetEndPoint(0).DistanceTo(split_pt) > c1.GetEndPoint(1).DistanceTo(split_pt) else c1.GetEndPoint(1)
                    
                    c2 = new_pipe.Location.Curve
                    p2_far = c2.GetEndPoint(0) if c2.GetEndPoint(0).DistanceTo(split_pt) > c2.GetEndPoint(1).DistanceTo(split_pt) else c2.GetEndPoint(1)
                    
                    # Phân loại đầu hở 1 và đầu hở 2 hướng về phía 2 ống tương ứng
                    if open_conns[0].Origin.DistanceTo(p1_far) < open_conns[1].Origin.DistanceTo(p1_far):
                        item_conn1, item_conn2 = open_conns[0], open_conns[1]
                    else:
                        item_conn1, item_conn2 = open_conns[1], open_conns[0]
                    
                    # Co ống 1
                    p1_start, p1_end = c1.GetEndPoint(0), c1.GetEndPoint(1)
                    if p1_start.DistanceTo(split_pt) < p1_end.DistanceTo(split_pt):
                        closest_pipe.Location.Curve = DB.Line.CreateBound(item_conn1.Origin, p1_end)
                    else:
                        closest_pipe.Location.Curve = DB.Line.CreateBound(p1_start, item_conn1.Origin)
                        
                    # Co ống 2
                    p2_start, p2_end = c2.GetEndPoint(0), c2.GetEndPoint(1)
                    if p2_start.DistanceTo(split_pt) < p2_end.DistanceTo(split_pt):
                        new_pipe.Location.Curve = DB.Line.CreateBound(item_conn2.Origin, p2_end)
                    else:
                        new_pipe.Location.Curve = DB.Line.CreateBound(p2_start, item_conn2.Origin)
                    
                    doc.Regenerate()
                    
                    # Bước 3.5: Thực hiện bập nối connector vật lý vào 2 đầu hở
                    pipe1_conns = get_connectors(closest_pipe)
                    pipe1_split_conn = min(pipe1_conns, key=lambda c: c.Origin.DistanceTo(item_conn1.Origin))
                    
                    pipe2_conns = get_connectors(new_pipe)
                    pipe2_split_conn = min(pipe2_conns, key=lambda c: c.Origin.DistanceTo(item_conn2.Origin))
                    
                    if not pipe1_split_conn.IsConnected:
                        pipe1_split_conn.ConnectTo(item_conn1)
                    if not pipe2_split_conn.IsConnected:
                        pipe2_split_conn.ConnectTo(item_conn2)
                    
                    sub_trans.Commit()
                    success_count += 1
                except Exception as ex:
                    # print("Lỗi chi tiết cấu kiện: ", ex)
                    sub_trans.RollBack()

    forms.alert("Đã hoàn thành quét chọn!\n\nKết nối thành công: {} cấu kiện.".format(success_count), 
                title="Hoàn thành", warn_icon=False)

if __name__ == '__main__':
    main()