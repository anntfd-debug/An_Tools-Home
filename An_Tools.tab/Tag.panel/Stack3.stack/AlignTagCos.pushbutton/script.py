# -*- coding: utf-8 -*-
from pyrevit import forms, revit, DB
import os
import math

doc = revit.doc
uidoc = revit.uidoc

# Hàm chuyển đổi mm trên giấy sang Decimal Feet trong Model
def mm_to_feet_model(mm_val, view_scale):
    # 1 foot = 304.8 mm. Nhân với view_scale để ra khoảng cách thực tế trong mô hình.
    return (mm_val / 304.8) * view_scale

def calculate_new_positions(head, end, align_type, min_sh, min_el):
    """
    Tính toán vị trí mới của Head và Elbow dựa trên thuật toán hình học
    """
    head_x, head_y, z = head.X, head.Y, head.Z
    end_x, end_y = end.X, end.Y
    
    # Các biến lưu kết quả
    new_head = head
    new_elbow = head
    
    dx = end_x - head_x
    dy = end_y - head_y

    if align_type == "90_LeftRight":
        # Vai ngang (Shoulder horizontal), Cẳng dọc (Elbow to End vertical)
        elbow_x = end_x
        
        # 1. Kiểm tra min elbow (khoảng cách dọc)
        if abs(end_y - head_y) < min_el:
            dir_y = 1 if dy < 0 else -1 # Đẩy ngược lại hướng của End
            elbow_y = end_y + dir_y * min_el
        else:
            elbow_y = head_y
            
        head_y = elbow_y # Giữ vai ngang
        
        # 2. Kiểm tra min shoulder (khoảng cách ngang)
        if abs(elbow_x - head_x) < min_sh:
            dir_x = -1 if dx > 0 else 1
            head_x = elbow_x + dir_x * min_sh
            
        new_head = DB.XYZ(head_x, head_y, z)
        new_elbow = DB.XYZ(elbow_x, elbow_y, z)

    elif align_type == "90_UpDown":
        # Vai dọc (Shoulder vertical), Cẳng ngang (Elbow to End horizontal)
        elbow_y = end_y
        
        # 1. Kiểm tra min elbow (khoảng cách ngang)
        if abs(end_x - head_x) < min_el:
            dir_x = 1 if dx < 0 else -1
            elbow_x = end_x + dir_x * min_el
        else:
            elbow_x = head_x
            
        head_x = elbow_x # Giữ vai dọc
        
        # 2. Kiểm tra min shoulder (khoảng cách dọc)
        if abs(elbow_y - head_y) < min_sh:
            dir_y = -1 if dy > 0 else 1
            head_y = elbow_y + dir_y * min_sh
            
        new_head = DB.XYZ(head_x, head_y, z)
        new_elbow = DB.XYZ(elbow_x, elbow_y, z)

    elif align_type == "45_LeftRight":
        # Vai ngang, Cẳng xéo góc 45 độ (dx = dy)
        min_dy = min_el * 0.7071 # sin(45)
        
        # 1. Chỉnh Y để đảm bảo min elbow
        current_dy = abs(end_y - head_y)
        if current_dy < min_dy:
            dir_y = 1 if head_y > end_y else -1
            head_y = end_y + dir_y * min_dy
        
        elbow_y = head_y
        
        # 2. Tạo góc 45 độ (tính X của elbow dựa vào Y)
        dy_actual = abs(end_y - elbow_y)
        dir_x = 1 if head_x < end_x else -1
        elbow_x = end_x - dir_x * dy_actual
        
        # 3. Đảm bảo min shoulder
        if abs(elbow_x - head_x) < min_sh:
            head_x = elbow_x - dir_x * min_sh
            
        new_head = DB.XYZ(head_x, head_y, z)
        new_elbow = DB.XYZ(elbow_x, elbow_y, z)

    elif align_type == "Straight_LeftRight":
        # Chỉ có 1 đường thẳng ngang từ Head đến End
        elbow_y = end_y
        head_y = end_y
        
        if abs(end_x - head_x) < (min_sh + min_el):
            dir_x = -1 if dx > 0 else 1
            head_x = end_x + dir_x * (min_sh + min_el)
            
        elbow_x = end_x + (head_x - end_x) / 2 # Đặt elbow ở giữa
        
        new_head = DB.XYZ(head_x, head_y, z)
        new_elbow = DB.XYZ(elbow_x, elbow_y, z)

    # (Bạn có thể bổ sung các logic toán học tương tự cho 45_UpDown và Straight_UpDown)

    return new_head, new_elbow


class TagLeaderForm(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        xaml_path = os.path.join(os.path.dirname(__file__), xaml_file_name)
        forms.WPFWindow.__init__(self, xaml_path)
    
    def btnApply_Click(self, sender, e):
        # 1. Đọc giao diện
        try:
            shoulder_mm = float(self.txtShoulder.Text)
            elbow_mm = float(self.txtElbow.Text)
        except ValueError:
            forms.alert("Vui lòng nhập giá trị số hợp lệ.")
            return

        is_auto = self.radAuto.IsChecked
        align_type = None

        if not is_auto:
            # Xác định Option được chọn (Lấy option đầu tiên được check)
            if self.chk90LeftRight.IsChecked: align_type = "90_LeftRight"
            elif self.chk90UpDown.IsChecked: align_type = "90_UpDown"
            elif self.chk45LeftRight.IsChecked: align_type = "45_LeftRight"
            elif self.chkStraightLeftRight.IsChecked: align_type = "Straight_LeftRight"
            # Thêm các nhánh kiểm tra khác tương ứng với XAML của bạn
            else: align_type = "90_LeftRight" # Mặc định nếu người dùng không chọn

        # 2. Lấy Tags được chọn
        selection = revit.get_selection()
        tags = [t for t in selection.elements if isinstance(t, DB.IndependentTag)]
        
        if not tags:
            forms.alert("Vui lòng chọn ít nhất 1 Tag.")
            self.Close()
            return

        view_scale = doc.ActiveView.Scale
        min_sh_ft = mm_to_feet_model(shoulder_mm, view_scale)
        min_el_ft = mm_to_feet_model(elbow_mm, view_scale)

        # 3. Thực thi thay đổi
        with revit.Transaction("Align Tag Leaders"):
            for tag in tags:
                tag.HasLeader = True
                
                # API Revit 2022+ yêu cầu Reference để lấy Leader End
                try:
                    refs = tag.GetTaggedReferences()
                    if not refs: continue
                    
                    for ref in refs:
                        end_pos = tag.GetLeaderEnd(ref)
                        head_pos = tag.TagHeadPosition
                        
                        if is_auto:
                            # Tự động: Đưa về mặc định hoặc thuật toán auto của bạn
                            pass 
                        else:
                            # Tính toán tọa độ mới
                            new_head, new_elbow = calculate_new_positions(
                                head_pos, end_pos, align_type, min_sh_ft, min_el_ft
                            )
                            
                            # Áp dụng tọa độ mới
                            tag.TagHeadPosition = new_head
                            tag.SetLeaderElbow(ref, new_elbow)
                except Exception as ex:
                    print("Lỗi trên tag ID {}: {}".format(tag.Id, ex))

        self.Close()

if __name__ == '__main__':
    # Xóa chữ modal=False hoặc đổi thành modal=True
    TagLeaderForm('ui.xaml').show(modal=True)