# -*- coding: utf-8 -*-
from Autodesk.Revit.DB import *
from pyrevit import revit

def align_n_tags_horizontal(tag_list, spacing_mm):
    doc = revit.doc
    view = doc.ActiveView
    
    # Lấy tỉ lệ từ view hiện tại
    scale = float(view.Scale)
    
    # Quy đổi khoảng cách mm trên bản vẽ ra đơn vị feet trong mô hình
    spacing_feet = (spacing_mm * scale) / 304.8
    
    # Sắp xếp các tag từ trái sang phải dựa trên tọa độ X
    sorted_tags = sorted(tag_list, key=lambda t: t.TagHeadPosition.X)
    
    # Lấy tag gốc (tag ngoài cùng bên trái) làm mốc
    base_tag = sorted_tags[0]
    base_pos = base_tag.TagHeadPosition
    
    # Lặp qua các tag còn lại để căn chỉnh
    for i in range(1, len(sorted_tags)):
        tag = sorted_tags[i]
        
        # Chuyển trạng thái sang Free để cho phép di chuyển
        if tag.LeaderEndCondition != LeaderEndCondition.Free:
            tag.LeaderEndCondition = LeaderEndCondition.Free
        
        # Lấy thông tin leader cũ để giữ nguyên vị trí chân trỏ
        refs = list(tag.GetTaggedReferences())
        if not refs: continue
        ref_to_use = refs[0]
        old_leader_end = tag.GetLeaderEnd(ref_to_use)
        
        # Tính toán vị trí mới
        new_head_pos = XYZ(base_pos.X + (i * spacing_feet), base_pos.Y, base_pos.Z)
        
        # Áp dụng
        tag.TagHeadPosition = new_head_pos
        tag.SetLeaderEnd(ref_to_use, old_leader_end)

# --- CẤU HÌNH ---
SPACING_ON_SHEET_MM = 4.0 
# ----------------

selection = revit.get_selection()
tags = [x for x in selection if isinstance(x, IndependentTag)]

if len(tags) >= 2:
    with revit.Transaction("Align N Tags Horizontally"):
        align_n_tags_horizontal(tags, SPACING_ON_SHEET_MM)