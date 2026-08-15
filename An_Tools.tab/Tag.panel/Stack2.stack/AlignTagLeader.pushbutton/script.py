# -*- coding: utf-8 -*-
__title__ = "Align Leader\nFree End"
__doc__ = "Căn chỉnh góc nghiêng Leader song song với mẫu bằng cách dời nếp gấp theo trục X."

from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.Exceptions import OperationCanceledException

doc = revit.doc
uidoc = revit.uidoc

class AnnotationSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, elem):
        return isinstance(elem, DB.IndependentTag) or isinstance(elem, DB.TextNote)
    def AllowReference(self, ref, pos):
        return False

def set_free_end(elem):
    """Ép Tag về dạng Free End"""
    if isinstance(elem, DB.IndependentTag):
        try:
            if not elem.HasLeader: elem.HasLeader = True
        except: pass
        if hasattr(elem, "GetTaggedReferences"):
            try:
                refs = elem.GetTaggedReferences()
                if refs:
                    for ref in refs:
                        if hasattr(elem, "SetLeaderEndCondition"):
                            elem.SetLeaderEndCondition(ref, DB.LeaderEndCondition.Free)
            except: pass
        try: elem.LeaderEndCondition = DB.LeaderEndCondition.Free
        except: pass

def get_leader_data(elem):
    if isinstance(elem, DB.IndependentTag):
        tag = elem
        if not tag.HasLeader:
            try: tag.HasLeader = True
            except: pass

        ref = None
        if hasattr(tag, "GetTaggedReferences"):
            try:
                refs = tag.GetTaggedReferences()
                if refs and len(refs) > 0: ref = refs[0]
            except: pass

        elbow, end = None, None
        head_pos = tag.TagHeadPosition

        if ref and hasattr(tag, "GetLeaderEnd"):
            try:
                end = tag.GetLeaderEnd(ref)
                if hasattr(tag, "HasLeaderElbow") and tag.HasLeaderElbow(ref):
                    elbow = tag.GetLeaderElbow(ref)
                else:
                    elbow = head_pos
                return ref, elbow, end, head_pos
            except: pass
                
        try:
            end = tag.LeaderEnd
            has_elbow = False
            try: has_elbow = tag.HasLeaderElbow
            except: has_elbow = True 
                
            if has_elbow:
                try: elbow = tag.LeaderElbow
                except: elbow = head_pos
            else:
                elbow = head_pos
            return ref, elbow, end, head_pos
        except: pass

    elif isinstance(elem, DB.TextNote):
        text_note = elem
        leaders = text_note.GetLeaders()
        if leaders and len(leaders) > 0:
            leader = leaders[0]
            return leader, leader.Elbow, leader.End, text_note.Coord
            
    return None, None, None, None

def set_leader_points(elem, ref_or_leader, new_elbow, new_end):
    if isinstance(elem, DB.IndependentTag):
        tag = elem
        ref = ref_or_leader
        success = False
        if hasattr(tag, "SetLeaderElbow") and ref:
            try:
                tag.SetLeaderElbow(ref, new_elbow)
                tag.SetLeaderEnd(ref, new_end)
                success = True
            except: pass
                
        if not success:
            try:
                tag.LeaderElbow = new_elbow
                tag.LeaderEnd = new_end
            except: pass

    elif isinstance(elem, DB.TextNote):
        leader = ref_or_leader
        try:
            leader.Elbow = new_elbow
            leader.End = new_end
        except: pass

def main():
    try:
        target_refs = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            AnnotationSelectionFilter(),
            "1. Chọn các Tag / Text Note cần align (Bấm Finish khi xong)"
        )
        targets = [doc.GetElement(ref) for ref in target_refs]
    except OperationCanceledException:
        return

    if not targets: return

    try:
        source_ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            AnnotationSelectionFilter(),
            "2. Chọn Tag / Text Note mẫu"
        )
        source_elem = doc.GetElement(source_ref)
    except OperationCanceledException:
        return

    view = doc.ActiveView
    right_dir = view.RightDirection 
    up_dir = view.UpDirection       

    def get_uv(xyz_point):
        return xyz_point.DotProduct(right_dir), xyz_point.DotProduct(up_dir)

    with revit.Transaction("Align Leader Angle (Move Elbow X)"):
        # 1. Chuyển về Free End
        set_free_end(source_elem)
        for elem in targets:
            set_free_end(elem)
            
        doc.Regenerate()
        
        # 2. Lấy vector góc từ Tag mẫu
        src_ref, src_elbow, src_end, src_head = get_leader_data(source_elem)
        if not src_elbow or not src_end:
            forms.alert("Lỗi! Đảm bảo đối tượng mẫu đã có Leader.", title="Lỗi Dữ Liệu")
            return
            
        src_elbow_u, src_elbow_v = get_uv(src_elbow)
        src_end_u, src_end_v = get_uv(src_end)
        
        src_du = src_elbow_u - src_end_u
        src_dv = src_elbow_v - src_end_v

        # 3. Tính toán và dịch chuyển Nếp gấp (Elbow) theo trục X cho Tag đích
        for elem in targets:
            if elem.Id == source_elem.Id: continue
                
            tgt_ref, tgt_elbow, tgt_end, tgt_head = get_leader_data(elem)
            if not tgt_elbow or not tgt_end: continue
            
            tgt_elbow_u, tgt_elbow_v = get_uv(tgt_elbow)
            tgt_end_u, tgt_end_v = get_uv(tgt_end)
            
            # Chỉ tịnh tiến khi mẫu có độ chênh lệch theo trục Y (để tránh lỗi chia cho 0)
            if abs(src_dv) > 0.00001:
                # Tính vị trí X mới của Nếp gấp dựa trên tỷ lệ góc của Tag mẫu
                ratio = src_du / src_dv
                new_elbow_u = tgt_end_u + (tgt_elbow_v - tgt_end_v) * ratio
                
                # Vector trượt ngang
                shift_u = new_elbow_u - tgt_elbow_u
                new_elbow = tgt_elbow + right_dir * shift_u
                
                set_leader_points(elem, tgt_ref, new_elbow, tgt_end)
            else:
                # Nếu đường dẫn mẫu nằm ngang hoàn toàn
                new_elbow = tgt_elbow + up_dir * (tgt_end_v - tgt_elbow_v)
                set_leader_points(elem, tgt_ref, new_elbow, tgt_end)

if __name__ == '__main__':
    main()