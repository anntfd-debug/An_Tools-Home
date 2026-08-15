# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = u'Tag Pipes Pro'
__doc__ = u'Chọn loại Pipe Tag, lọc chiều dài/kích thước/hướng ống, lọc theo View Range, chặn chọn ống ngoài View Range và tự lưu cấu hình lần chạy trước.'

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.UI import TaskDialog
from pyrevit import revit, UI, script, forms
import os
import json

# -----------------------------------------------------------------------------
# UNICODE / TIẾNG VIỆT HELPERS
# -----------------------------------------------------------------------------
# Lưu ý quan trọng:
# - Một số bản pyRevit bị lỗi mã hóa khi dùng forms.toast/forms.alert với tiếng Việt.
# - Dấu tiếng Việt bị biến thành dạng mojibake như: "ÄÃ£...".
# - Đây không phải lỗi font của Windows, mà là lỗi đường truyền encoding của pyRevit UI.
# - Cách ổn định nhất là KHÔNG dùng forms.toast/forms.alert cho tiếng Việt;
#   chuyển toàn bộ thông báo sang Revit native TaskDialog.
try:
    unicode
except NameError:
    unicode = str


TASK_DIALOG_TITLE = u'Tag Pipes Pro'


def ensure_unicode(value):
    if value is None:
        return u''
    if isinstance(value, unicode):
        return value
    try:
        return value.decode('utf-8')
    except:
        try:
            return unicode(value)
        except:
            return str(value)


def revit_message(message, title=TASK_DIALOG_TITLE):
    """Hiển thị thông báo Unicode bằng TaskDialog của Revit.

    Không dùng pyrevit.forms.toast/forms.alert vì một số máy sẽ lỗi dấu tiếng Việt.
    """
    msg = ensure_unicode(message)
    ttl = ensure_unicode(title)
    try:
        TaskDialog.Show(ttl, msg)
    except:
        try:
            # Fallback cuối cùng: in ra pyRevit output.
            print(msg.encode('utf-8') if hasattr(msg, 'encode') else msg)
        except:
            pass


def alert_msg(message):
    revit_message(message)


def toast_msg(message):
    # Giữ tên hàm để không phải đổi logic bên dưới, nhưng thực tế dùng TaskDialog.
    revit_message(message)


# -----------------------------------------------------------------------------
# FILE PATHS
# -----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(__file__)
xaml_file = os.path.join(SCRIPT_DIR, 'ui_definition.xaml')
CONFIG_FILENAME = 'tag_parallel_pipes_settings.json'
CONFIG_FILE = os.path.join(SCRIPT_DIR, CONFIG_FILENAME)


def get_fallback_config_file():
    """Fallback lưu setting vào AppData nếu thư mục bundle không cho ghi file."""
    appdata = os.getenv('APPDATA')
    if appdata:
        folder = os.path.join(appdata, 'pyRevit_TagPipesPro')
    else:
        folder = os.path.join(os.path.expanduser('~'), '.pyRevit_TagPipesPro')

    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
    except:
        pass

    return os.path.join(folder, CONFIG_FILENAME)


FALLBACK_CONFIG_FILE = get_fallback_config_file()

DEFAULT_SETTINGS = {
    'spacing_mm': '5.0',
    'min_length_mm': '500',
    'min_size_mm': '15',
    'max_size_mm': '1000',
    'scope': 'selection',        # selection | view
    'orientation': 'all',        # all | horizontal | vertical
    'tag_condition': 'all',      # all | untagged
    'tag_side': 'left',           # left | right
    'view_range_mode': 'none',    # none | manual | active
    'view_range_bottom_mm': '-1000',
    'view_range_top_mm': '3000',
    'tag_type': ''
}


def to_text(value):
    """Chuyển giá trị sang Unicode text, tương thích IronPython/Python."""
    return ensure_unicode(value)


def load_settings():
    settings = DEFAULT_SETTINGS.copy()

    for path in [CONFIG_FILE, FALLBACK_CONFIG_FILE]:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, 'r') as fp:
                saved_data = json.load(fp)
            if isinstance(saved_data, dict):
                settings.update(saved_data)
            break
        except:
            pass

    return settings


def write_settings_file(path, settings):
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)

    with open(path, 'w') as fp:
        json.dump(settings, fp, indent=2, sort_keys=True)


def save_settings(settings):
    """Ưu tiên lưu cùng thư mục script. Nếu không được thì lưu vào AppData."""
    try:
        write_settings_file(CONFIG_FILE, settings)
        return CONFIG_FILE
    except:
        try:
            write_settings_file(FALLBACK_CONFIG_FILE, settings)
            return FALLBACK_CONFIG_FILE
        except:
            return None


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
def mm_to_feet(mm, scale):
    return (mm * scale) / 304.8


def reverse_xyz(vec):
    """Đảo chiều vector, tương thích nhiều phiên bản Revit/IronPython."""
    try:
        return vec.Negate()
    except:
        return vec * -1.0


def are_pipes_parallel(p1, p2):
    d1 = (p1.Location.Curve.GetEndPoint(1) - p1.Location.Curve.GetEndPoint(0)).Normalize()
    d2 = (p2.Location.Curve.GetEndPoint(1) - p2.Location.Curve.GetEndPoint(0)).Normalize()
    return abs(d1.DotProduct(d2)) > 0.95


def get_perpendicular_distance(p1, p2):
    pt1 = p1.Location.Curve.Evaluate(0.5, True)
    pt2 = p2.Location.Curve.Evaluate(0.5, True)
    d1 = (p1.Location.Curve.GetEndPoint(1) - p1.Location.Curve.GetEndPoint(0)).Normalize()
    v = pt2 - pt1
    return (v - d1 * v.DotProduct(d1)).GetLength()


def parse_mm_value(text_value, field_name):
    """Đọc số mm từ TextBox, cho phép nhập dấu phẩy hoặc dấu chấm."""
    try:
        return float(to_text(text_value).replace(',', '.'))
    except:
        raise ValueError(u'{} phải là số hợp lệ.'.format(field_name))


def get_view_base_elevation(view):
    """Cao độ Level gắn với active view. Nếu không lấy được thì dùng 0."""
    try:
        level = view.GenLevel
        if level:
            return level.Elevation
    except:
        pass
    return 0.0


def is_valid_level_id(level_id):
    try:
        return level_id and level_id.IntegerValue != -1
    except:
        return False


def get_plan_view_plane_z(doc, view, plan_view_range, plane):
    """Trả về cao độ tuyệt đối của một mặt phẳng View Range."""
    try:
        level_id = plan_view_range.GetLevelId(plane)
        offset = plan_view_range.GetOffset(plane)

        level_elevation = get_view_base_elevation(view)
        if is_valid_level_id(level_id):
            level = doc.GetElement(level_id)
            if level and hasattr(level, 'Elevation'):
                level_elevation = level.Elevation

        return level_elevation + offset
    except:
        return None


def get_active_view_range_bounds(doc, view):
    """Lấy khoảng Z của View Range hiện hành trong active view.

    Dùng Top Clip làm giới hạn trên. Giới hạn dưới lấy thấp nhất giữa
    Bottom Clip và View Depth để không bỏ sót các phần tử đang được view hiển thị.
    """
    try:
        pvr = view.GetViewRange()
    except:
        return None

    top_z = get_plan_view_plane_z(doc, view, pvr, PlanViewPlane.TopClipPlane)
    bottom_z = get_plan_view_plane_z(doc, view, pvr, PlanViewPlane.BottomClipPlane)
    view_depth_z = get_plan_view_plane_z(doc, view, pvr, PlanViewPlane.ViewDepthPlane)

    values = []
    if top_z is not None:
        values.append(top_z)
    if bottom_z is not None:
        values.append(bottom_z)
    if view_depth_z is not None:
        values.append(view_depth_z)

    if len(values) < 2:
        return None

    upper = top_z if top_z is not None else max(values)
    lower_candidates = []
    if bottom_z is not None:
        lower_candidates.append(bottom_z)
    if view_depth_z is not None:
        lower_candidates.append(view_depth_z)
    lower = min(lower_candidates) if lower_candidates else min(values)

    if upper < lower:
        tmp = upper
        upper = lower
        lower = tmp

    return (lower, upper)


def get_element_z_span(elem):
    """Lấy khoảng cao độ Z của element để so với View Range."""
    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            return (bb.Min.Z, bb.Max.Z)
    except:
        pass

    try:
        curve = elem.Location.Curve
        z0 = curve.GetEndPoint(0).Z
        z1 = curve.GetEndPoint(1).Z
        return (min(z0, z1), max(z0, z1))
    except:
        return None


def element_overlaps_z_range(elem, bottom_z, top_z):
    """True nếu element có phần giao với dải View Range."""
    span = get_element_z_span(elem)
    if not span:
        return False

    elem_min_z, elem_max_z = span
    return elem_max_z >= bottom_z and elem_min_z <= top_z


# BỘ LỌC CHỈ CHO PHÉP CHỌN ỐNG KHI THAO TÁC TRÊN MÀN HÌNH
class PipeSelectionFilter(ISelectionFilter):
    def __init__(self, view_range_bounds=None):
        # view_range_bounds = (bottom_z, top_z) theo đơn vị internal feet của Revit.
        # Khi có giá trị này, user chỉ quét/chọn được pipe có giao với dải View Range.
        self.view_range_bounds = view_range_bounds

    def AllowElement(self, elem):
        if not (elem and elem.Category and elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)):
            return False

        if self.view_range_bounds:
            try:
                return element_overlaps_z_range(elem, self.view_range_bounds[0], self.view_range_bounds[1])
            except:
                return False

        return True

    def AllowReference(self, reference, position):
        return False


class TagParallelPipesWPF(forms.WPFWindow):
    def __init__(self, xaml_file_path):
        forms.WPFWindow.__init__(self, xaml_file_path)

        self.settings = load_settings()
        self.setup_tag_combobox()
        self.apply_saved_settings()

        self.btnExecute.Click += self.on_execute_click
        self.btnCancel.Click += self.on_close_click

    # -------------------------------------------------------------------------
    # UI SETTINGS
    # -------------------------------------------------------------------------
    def set_status(self, message):
        try:
            self.txtSettingsStatus.Text = message
        except:
            pass

    def set_radio_value(self, radio_map, saved_value, default_value):
        if saved_value not in radio_map:
            saved_value = default_value

        for key in radio_map:
            radio_map[key].IsChecked = (key == saved_value)

    def apply_saved_settings(self):
        self.txtSpacing.Text = to_text(self.settings.get('spacing_mm', DEFAULT_SETTINGS['spacing_mm']))
        self.txtMinLength.Text = to_text(self.settings.get('min_length_mm', DEFAULT_SETTINGS['min_length_mm']))
        self.txtMinSize.Text = to_text(self.settings.get('min_size_mm', DEFAULT_SETTINGS['min_size_mm']))
        self.txtMaxSize.Text = to_text(self.settings.get('max_size_mm', DEFAULT_SETTINGS['max_size_mm']))
        self.txtRangeBottom.Text = to_text(self.settings.get('view_range_bottom_mm', DEFAULT_SETTINGS['view_range_bottom_mm']))
        self.txtRangeTop.Text = to_text(self.settings.get('view_range_top_mm', DEFAULT_SETTINGS['view_range_top_mm']))

        self.set_radio_value({
            'view': self.rbView,
            'selection': self.rbSelection
        }, self.settings.get('scope', 'selection'), 'selection')

        self.set_radio_value({
            'all': self.rbOrientationAll,
            'horizontal': self.rbOrientationHorizontal,
            'vertical': self.rbOrientationVertical
        }, self.settings.get('orientation', 'all'), 'all')

        self.set_radio_value({
            'all': self.rbAllPipes,
            'untagged': self.rbUntaggedOnly
        }, self.settings.get('tag_condition', 'all'), 'all')

        self.set_radio_value({
            'left': self.rbTagSideLeft,
            'right': self.rbTagSideRight
        }, self.settings.get('tag_side', 'left'), 'left')

        self.set_radio_value({
            'none': self.rbViewRangeNone,
            'manual': self.rbViewRangeManual,
            'active': self.rbViewRangeActive
        }, self.settings.get('view_range_mode', 'none'), 'none')

        saved_tag = self.settings.get('tag_type', '')
        if saved_tag and saved_tag in self.tag_map:
            self.cbTagTypes.SelectedItem = saved_tag
        elif self.tag_map and self.cbTagTypes.SelectedIndex < 0:
            self.cbTagTypes.SelectedIndex = 0

        if os.path.exists(CONFIG_FILE) or os.path.exists(FALLBACK_CONFIG_FILE):
            self.set_status(u'Đã nạp cấu hình lần chạy trước.')
        else:
            self.set_status(u'Chưa có cấu hình đã lưu. Tool sẽ tự lưu sau khi bấm Đặt Tag.')

    def get_current_settings(self):
        if bool(self.rbSelection.IsChecked):
            scope = 'selection'
        else:
            scope = 'view'

        if bool(self.rbOrientationHorizontal.IsChecked):
            orientation = 'horizontal'
        elif bool(self.rbOrientationVertical.IsChecked):
            orientation = 'vertical'
        else:
            orientation = 'all'

        if bool(self.rbUntaggedOnly.IsChecked):
            tag_condition = 'untagged'
        else:
            tag_condition = 'all'

        if bool(self.rbTagSideRight.IsChecked):
            tag_side = 'right'
        else:
            tag_side = 'left'

        if bool(self.rbViewRangeManual.IsChecked):
            view_range_mode = 'manual'
        elif bool(self.rbViewRangeActive.IsChecked):
            view_range_mode = 'active'
        else:
            view_range_mode = 'none'

        return {
            'spacing_mm': to_text(self.txtSpacing.Text),
            'min_length_mm': to_text(self.txtMinLength.Text),
            'min_size_mm': to_text(self.txtMinSize.Text),
            'max_size_mm': to_text(self.txtMaxSize.Text),
            'scope': scope,
            'orientation': orientation,
            'tag_condition': tag_condition,
            'tag_side': tag_side,
            'view_range_mode': view_range_mode,
            'view_range_bottom_mm': to_text(self.txtRangeBottom.Text),
            'view_range_top_mm': to_text(self.txtRangeTop.Text),
            'tag_type': to_text(self.cbTagTypes.SelectedItem)
        }

    # -------------------------------------------------------------------------
    # TAG COMBOBOX
    # -------------------------------------------------------------------------
    def setup_tag_combobox(self):
        doc = revit.doc
        pipe_tag_symbols = FilteredElementCollector(doc)\
                            .OfClass(FamilySymbol)\
                            .OfCategory(BuiltInCategory.OST_PipeTags)\
                            .ToElements()

        self.tag_map = {}
        for t in pipe_tag_symbols:
            type_param = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            type_name = type_param.AsString() if type_param else 'Unknown Type'

            fam_param = t.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
            fam_name = fam_param.AsString() if fam_param else 'Unknown Family'

            if type_name != 'Unknown Type':
                display_name = '{} - {}'.format(fam_name, type_name)
                self.tag_map[display_name] = t

        self.cbTagTypes.ItemsSource = sorted(self.tag_map.keys())
        if self.tag_map:
            self.cbTagTypes.SelectedIndex = 0

    # -------------------------------------------------------------------------
    # MAIN COMMAND
    # -------------------------------------------------------------------------
    def on_execute_click(self, sender, args):
        scope_selection = bool(self.rbSelection.IsChecked)

        try:
            doc = revit.doc
            uidoc = revit.uidoc
            active_view = doc.ActiveView
            rvt_year = int(doc.Application.VersionNumber)

            # Đọc giá trị từ giao diện WPF
            spacing_mm = parse_mm_value(self.txtSpacing.Text, u'Khoảng cách xếp bậc')
            min_length_mm = parse_mm_value(self.txtMinLength.Text, u'Chiều dài ống tối thiểu')
            min_size_mm = parse_mm_value(self.txtMinSize.Text, u'Đường kính ống nhỏ nhất')
            max_size_mm = parse_mm_value(self.txtMaxSize.Text, u'Đường kính ống lớn nhất')

            first_offset_mm = 5.0  # Khoảng đẩy vuông góc ban đầu trên giấy

            view_scale = active_view.Scale
            view_right = active_view.RightDirection
            view_up = active_view.UpDirection

            model_first_offset = mm_to_feet(first_offset_mm, view_scale)
            model_spacing = mm_to_feet(spacing_mm, view_scale)

            # Đổi điều kiện từ mm sang Feet hệ thống
            min_length_ft = min_length_mm / 304.8
            min_size_ft = min_size_mm / 304.8
            max_size_ft = max_size_mm / 304.8

            # Đọc trạng thái các RadioButton từ UI
            scope_selection = bool(self.rbSelection.IsChecked)
            tag_all_pipes = bool(self.rbAllPipes.IsChecked)
            tag_side_left = bool(self.rbTagSideLeft.IsChecked)

            # Đọc trạng thái lọc hướng ống
            filter_horizontal = bool(self.rbOrientationHorizontal.IsChecked)
            filter_vertical = bool(self.rbOrientationVertical.IsChecked)

            # Đọc tùy chọn lọc theo View Range
            view_range_bounds = None
            view_range_mode_manual = bool(self.rbViewRangeManual.IsChecked)
            view_range_mode_active = bool(self.rbViewRangeActive.IsChecked)
            if view_range_mode_manual:
                range_bottom_mm = parse_mm_value(self.txtRangeBottom.Text, u'Cao độ dưới View Range')
                range_top_mm = parse_mm_value(self.txtRangeTop.Text, u'Cao độ trên View Range')
                if range_top_mm <= range_bottom_mm:
                    alert_msg(u'Cao độ trên View Range phải lớn hơn cao độ dưới.')
                    return

                base_z = get_view_base_elevation(active_view)
                view_range_bounds = (base_z + range_bottom_mm / 304.8,
                                     base_z + range_top_mm / 304.8)

            elif view_range_mode_active:
                view_range_bounds = get_active_view_range_bounds(doc, active_view)
                if not view_range_bounds:
                    alert_msg(u'Active view hiện tại không hỗ trợ View Range. Hãy chọn Plan View hoặc dùng chế độ nhập View Range thủ công.')
                    return

            selected_tag_name = self.cbTagTypes.SelectedItem
            if not selected_tag_name:
                alert_msg(u'Vui lòng chọn loại Tag trước khi thực hiện.')
                return

            # Lưu cấu hình ngay sau khi thông số nhập hợp lệ.
            # Nhờ vậy kể cả khi user hủy bước PickObjects, lần sau mở tool vẫn giữ option đã nhập.
            saved_path = save_settings(self.get_current_settings())
            if saved_path:
                self.set_status(u'Đã lưu cấu hình hiện tại.')

            tag_symbol = self.tag_map[selected_tag_name]

            # QUÉT TOÀN BỘ TAG ĐANG CÓ TRÊN VIEW
            existing_tags = FilteredElementCollector(doc, active_view.Id)\
                                .OfClass(IndependentTag)\
                                .ToElements()

            tagged_dict = {}
            for tag in existing_tags:
                if tag.Category and tag.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeTags):
                    try:
                        if rvt_year >= 2022:
                            for ref in tag.GetTaggedReferences():
                                pid = ref.ElementId
                                if pid not in tagged_dict:
                                    tagged_dict[pid] = []
                                tagged_dict[pid].append(tag.Id)
                        else:
                            pid = tag.TaggedLocalElementId
                            if pid not in tagged_dict:
                                tagged_dict[pid] = []
                            tagged_dict[pid].append(tag.Id)
                    except:
                        pass

            # THU THẬP ĐỐI TƯỢNG ỐNG
            raw_elements = []
            if scope_selection:
                self.Hide()
                try:
                    # Khi đang bật View Range, truyền giới hạn Z vào SelectionFilter.
                    # Với chế độ nhập thủ công, user sẽ không quét/chọn được pipe nằm ngoài dải đã nhập.
                    pipe_filter = PipeSelectionFilter(view_range_bounds)

                    pick_message = u'Hãy quét chọn/click chọn các ống cần đặt Tag -> Sau đó nhấn FINISH trên thanh Option Bar.'
                    if view_range_mode_manual:
                        pick_message = u'Chỉ chọn được các ống nằm trong View Range thủ công đã nhập -> Sau đó nhấn FINISH trên thanh Option Bar.'
                    elif view_range_mode_active:
                        pick_message = u'Chỉ chọn được các ống nằm trong View Range hiện hành -> Sau đó nhấn FINISH trên thanh Option Bar.'

                    selected_refs = uidoc.Selection.PickObjects(
                        ObjectType.Element,
                        pipe_filter,
                        pick_message
                    )
                    raw_elements = [doc.GetElement(ref.ElementId) for ref in selected_refs]
                except:
                    try:
                        self.ShowDialog()
                    except:
                        pass
                    return
            else:
                raw_elements = FilteredElementCollector(doc, active_view.Id)\
                                     .OfCategory(BuiltInCategory.OST_PipeCurves)\
                                     .WhereElementIsNotElementType()\
                                     .ToElements()

            # LỌC DANH SÁCH ỐNG
            pipes_to_process = []
            view_range_skipped = 0
            for el in raw_elements:
                if isinstance(el, Pipe) and el.Location and hasattr(el.Location, 'Curve') and el.Location.Curve:
                    if view_range_bounds:
                        if not element_overlaps_z_range(el, view_range_bounds[0], view_range_bounds[1]):
                            view_range_skipped += 1
                            continue

                    ep0 = el.Location.Curve.GetEndPoint(0)
                    ep1 = el.Location.Curve.GetEndPoint(1)
                    dir_vec = (ep1 - ep0).Normalize()

                    is_vertical = abs(dir_vec.Z) > 0.99
                    is_horizontal = not is_vertical

                    if filter_horizontal and not is_horizontal:
                        continue
                    if filter_vertical and not is_vertical:
                        continue

                    if not tag_all_pipes and el.Id in tagged_dict:
                        continue

                    length_param = el.get_Parameter(BuiltInParameter.CURVE_ELEM_LENGTH)
                    if length_param and length_param.AsDouble() < min_length_ft:
                        continue

                    size_param = el.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
                    if size_param:
                        pipe_size_ft = size_param.AsDouble()
                        if pipe_size_ft < min_size_ft or pipe_size_ft > max_size_ft:
                            continue

                    pipes_to_process.append(el)

            if not pipes_to_process:
                if view_range_bounds and view_range_skipped > 0:
                    alert_msg(u'Không tìm thấy đường ống nào thỏa mãn các điều kiện lọc. Có {} ống bị bỏ qua vì nằm ngoài View Range.'.format(view_range_skipped))
                else:
                    alert_msg(u'Không tìm thấy đường ống nào thỏa mãn các điều kiện lọc.')
                if scope_selection:
                    try:
                        self.ShowDialog()
                    except:
                        pass
                return

            # THUẬT TOÁN GOM CỤM SONG SONG
            clusters = []
            unclustered = list(pipes_to_process)
            while unclustered:
                current = unclustered.pop(0)
                cluster = [current]
                to_remove = []
                for p in unclustered:
                    if are_pipes_parallel(current, p) and get_perpendicular_distance(current, p) < 5.0:
                        cluster.append(p)
                        to_remove.append(p)
                for p in to_remove:
                    unclustered.remove(p)
                clusters.append(cluster)

            # TIẾN HÀNH TRANSACTION VÀ ĐẶT TAG
            success_count = 0
            deleted_count = 0

            with revit.Transaction('Tag Pipes Pro'):
                if not tag_symbol.IsActive:
                    tag_symbol.Activate()
                    doc.Regenerate()

                for cluster in clusters:
                    if not cluster:
                        continue

                    p_dir = (cluster[0].Location.Curve.GetEndPoint(1) - cluster[0].Location.Curve.GetEndPoint(0)).Normalize()

                    # Phân tách hướng đẩy vuông góc và hướng trượt song song
                    # tag_side_left = True:
                    # - Với ống đứng: đầu tag nằm bên trái ống theo hướng nhìn của view.
                    # - Với ống ngang/ống dốc: vẫn đẩy tag lên trên, nhưng xếp bậc trượt về bên trái.
                    if abs(p_dir.DotProduct(view_right)) > 0.707:
                        dir_perp = view_up       # Vuông góc với ống ngang -> Đẩy lên
                        dir_para = reverse_xyz(view_right) if tag_side_left else view_right
                        sorted_cluster = sorted(cluster, key=lambda p: p.Location.Curve.Evaluate(0.5, True).DotProduct(view_up))
                    else:
                        dir_perp = reverse_xyz(view_right) if tag_side_left else view_right
                        dir_para = view_up       # Song song với ống đứng -> Trượt dọc lên trên
                        sorted_cluster = sorted(cluster, key=lambda p: p.Location.Curve.Evaluate(0.5, True).DotProduct(view_right))

                    for index, pipe in enumerate(sorted_cluster):

                        # Xóa Tag cũ
                        if tag_all_pipes and pipe.Id in tagged_dict:
                            for old_tag_id in tagged_dict[pipe.Id]:
                                try:
                                    doc.Delete(old_tag_id)
                                    deleted_count += 1
                                except:
                                    pass

                        mid_point = pipe.Location.Curve.Evaluate(0.5, True)

                        try:
                            pipe_ref = Reference(pipe)

                            # 1. Đẩy ra xa ống một khoảng cố định để leader bằng nhau
                            vec_perp = dir_perp * model_first_offset

                            # 2. Trượt dọc theo thân ống một khoảng cộng dồn để xếp bậc
                            vec_para = dir_para * (index * model_spacing)

                            # Gộp tọa độ
                            head_pos = mid_point + vec_perp + vec_para

                            new_tag = IndependentTag.Create(
                                doc, tag_symbol.Id, active_view.Id,
                                pipe_ref, False, TagOrientation.Horizontal, head_pos
                            )

                            new_tag.HasLeader = True
                            new_tag.LeaderEndCondition = LeaderEndCondition.Attached

                            success_count += 1
                        except Exception as e:
                            print(ensure_unicode(u'Lỗi tại ống ID {}: {}'.format(pipe.Id, ensure_unicode(e))))

            extra_msg = u''
            if view_range_bounds and view_range_skipped > 0:
                extra_msg = u'\nBỏ qua {} ống nằm ngoài View Range.'.format(view_range_skipped)

            if tag_all_pipes and deleted_count > 0:
                toast_msg(u'Đã dọn dẹp mặt bằng!\nXóa {} tag cũ và tạo mới {} tag bậc.{}'.format(deleted_count, success_count, extra_msg))
            else:
                toast_msg(u'Đã đặt thành công {} Tag xếp bậc!{}'.format(success_count, extra_msg))

            self.Close()

        except ValueError:
            alert_msg(u'Vui lòng nhập số hợp lệ vào các ô thông số!')
            if scope_selection:
                try:
                    self.ShowDialog()
                except:
                    pass
        except Exception as ex:
            alert_msg(u'Lỗi hệ thống: {}'.format(ensure_unicode(ex)))
            if scope_selection:
                try:
                    self.ShowDialog()
                except:
                    pass

    def on_close_click(self, sender, args):
        self.Close()


if __name__ == '__main__':
    win = TagParallelPipesWPF(xaml_file)
    win.show_dialog()
