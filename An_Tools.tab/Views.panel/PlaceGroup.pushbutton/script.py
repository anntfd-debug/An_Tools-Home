# -*- coding: utf-8 -*-
"""
Place Group From Linked Families
IronPython 2.7 / pyRevit

Chức năng:
- Pick nhiều RevitLinkInstance trước khi mở giao diện.
- Chỉ xử lý Link và Family đang hiển thị trong Active View.
- Lọc FamilyInstance theo Family Name hoặc Type Name bằng ô nhập nhiều dòng.
- Đặt Model Group tại một trong bốn góc cục bộ:
  LEFT_FRONT, RIGHT_FRONT, LEFT_BACK, RIGHT_BACK.
- Xoay Group theo HandOrientation hoặc FacingOrientation.
- Chế độ kiểm tra chỉ tạo duy nhất một Group.
- Cache kết quả sau khi bấm Kiểm tra để không quét lại khi tạo Group.
- Progress visibility chỉ đếm các Family đã khớp từ khóa.
- Có progress bar và Cancel. Cancel trong lúc đặt sẽ rollback toàn bộ lần chạy.

Ghi chú về visibility:
- Nếu Link hiển thị By Linked View (Revit 2024+), tool dùng linked view để
  thu hẹp tập ứng viên nhưng vẫn bắt buộc kiểm tra lại View Range của Active View
  trong host. Điều này ngăn Family ở tầng khác bị đặt Group trong mặt bằng hiện tại.
- Nếu Link hiển thị By Host View, tool lọc theo: Link hiện trong Active View,
  category không bị ẩn, Crop/Section Box và View Range của Active View.
"""

import math
import os
import re
import traceback

from pyrevit import revit, DB, UI, forms, script
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType


doc = revit.doc
uidoc = revit.uidoc
active_view = revit.active_view
output = script.get_output()

try:
    text_type = unicode
except NameError:
    text_type = str


startup_notices = []


# ============================================================
# TIỆN ÍCH CHUNG
# ============================================================

def safe_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        try:
            return text_type(str(value), "utf-8", "ignore")
        except Exception:
            return u""


def get_element_name(element):
    if element is None:
        return u""

    try:
        name = element.Name
        if name:
            return safe_text(name)
    except Exception:
        pass

    for bip in (
        DB.BuiltInParameter.SYMBOL_NAME_PARAM,
        DB.BuiltInParameter.ALL_MODEL_TYPE_NAME,
        DB.BuiltInParameter.ELEM_TYPE_PARAM
    ):
        try:
            parameter = element.get_Parameter(bip)
            if parameter:
                value = parameter.AsString()
                if value:
                    return safe_text(value)
        except Exception:
            pass

    return u""


def get_id_value(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def is_invalid_element_id(element_id):
    if element_id is None:
        return True
    try:
        return get_id_value(element_id) == get_id_value(DB.ElementId.InvalidElementId)
    except Exception:
        return True


def mm_to_internal(mm_value):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(
            float(mm_value),
            DB.UnitTypeId.Millimeters
        )
    except Exception:
        return DB.UnitUtils.ConvertToInternalUnits(
            float(mm_value),
            DB.DisplayUnitType.DUT_MILLIMETERS
        )


def internal_to_mm(internal_value):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(
            float(internal_value),
            DB.UnitTypeId.Millimeters
        )
    except Exception:
        return DB.UnitUtils.ConvertFromInternalUnits(
            float(internal_value),
            DB.DisplayUnitType.DUT_MILLIMETERS
        )


def format_mm(internal_value):
    try:
        return u"{0:.1f}".format(internal_to_mm(internal_value))
    except Exception:
        return u"-"


def format_angle_degrees(angle_radians):
    try:
        value = math.degrees(angle_radians)
        while value > 180.0:
            value -= 360.0
        while value <= -180.0:
            value += 360.0
        return u"{0:.2f}".format(value)
    except Exception:
        return u"-"


def show_output_report(
        status_title,
        summary_lines=None,
        notices=None,
        failed_items=None,
        created_records=None,
        traceback_text=None):
    """In toàn bộ thông báo vào cửa sổ output của pyRevit."""

    summary_lines = summary_lines or []
    notices = notices or []
    failed_items = failed_items or []
    created_records = created_records or []

    try:
        output.set_title(u"Place Group From Linked Families")
    except Exception:
        pass

    output.print_md(u"# Place Group From Linked Families")
    output.print_md(u"## {0}".format(safe_text(status_title)))

    if summary_lines:
        output.print_md(u"### Tóm tắt")
        for line in summary_lines:
            output.print_md(u"- {0}".format(safe_text(line)))

    if notices:
        output.print_md(u"### Thông báo và lưu ý")
        for notice in notices:
            output.print_md(u"- {0}".format(safe_text(notice)))

    if created_records:
        output.print_md(
            u"### Các Group đã tạo"
        )
        output.print_md(
            u"Bấm vào tên Group để chọn phần tử; bấm biểu tượng **Show** bên cạnh liên kết để Revit zoom tới vị trí Group."
        )

        table_data = []
        for index, record in enumerate(created_records):
            group_id = record.get("group_id")
            group_id_value = record.get("group_id_value", u"?")
            try:
                zoom_link = output.linkify(
                    group_id,
                    title=u"Group {0}".format(group_id_value)
                )
            except Exception:
                zoom_link = u"Group {0}".format(group_id_value)

            point = record.get("point")
            if point is not None:
                x_text = format_mm(point.X)
                y_text = format_mm(point.Y)
                z_text = format_mm(point.Z)
            else:
                x_text = y_text = z_text = u"-"

            geometry_source = u"Solid"
            if not record.get("used_solid", False):
                geometry_source = u"BoundingBox"

            mirror_text = u"Có" if record.get("mirrored", False) else u"Không"

            table_data.append([
                index + 1,
                zoom_link,
                safe_text(record.get("group_type_name", u"")),
                safe_text(record.get("source_family", u"")),
                safe_text(record.get("link_name", u"")),
                x_text,
                y_text,
                z_text,
                safe_text(record.get("angle_deg", u"-")),
                safe_text(record.get("anchor_mode", u"")),
                geometry_source,
                mirror_text
            ])

        columns = [
            u"STT",
            u"Chọn / Zoom",
            u"Group Type",
            u"Family nguồn",
            u"Revit Link",
            u"X (mm)",
            u"Y (mm)",
            u"Z (mm)",
            u"Góc (°)",
            u"Điểm đặt",
            u"Hình học",
            u"Family Mirror"
        ]

        try:
            output.print_table(
                table_data=table_data,
                columns=columns
            )
        except Exception:
            for row in table_data:
                output.print_md(
                    u"{0}. {1} | {2} | {3} | X={5} mm; Y={6} mm; Z={7} mm; Góc={8}°".format(
                        *row
                    )
                )

    if failed_items:
        output.print_md(u"### Các Family không xử lý được")
        output.print_md(
            u"Tổng số lỗi: **{0}**".format(len(failed_items))
        )
        for item in failed_items:
            output.print_md(u"- {0}".format(safe_text(item)))

    if traceback_text:
        output.print_md(u"### Chi tiết kỹ thuật")
        try:
            output.print_code(safe_text(traceback_text))
        except Exception:
            output.print_md(u"```\n{0}\n```".format(safe_text(traceback_text)))

    output.print_md(u"---")
    output.print_md(
        u"Active View: **{0}**".format(
            get_element_name(active_view) if active_view is not None else u"Không xác định"
        )
    )


def parse_number(raw_value, field_name, minimum=None):
    text = safe_text(raw_value).strip().replace(u",", u".")
    if not text:
        value = 0.0
    else:
        try:
            value = float(text)
        except Exception:
            raise ValueError(u"'{0}' phải là một số hợp lệ.".format(field_name))

    if minimum is not None and value < minimum:
        raise ValueError(
            u"'{0}' phải lớn hơn hoặc bằng {1}.".format(field_name, minimum)
        )
    return value


def normalize_xy(vector):
    if vector is None:
        return None

    vector_xy = DB.XYZ(vector.X, vector.Y, 0.0)
    if vector_xy.GetLength() < 0.0000001:
        return None

    return vector_xy.Normalize()


def vector_angle_xy(vector):
    vector_xy = normalize_xy(vector)
    if vector_xy is None:
        return 0.0
    return math.atan2(vector_xy.Y, vector_xy.X)


def points_distance_3d(point_a, point_b):
    return (point_a - point_b).GetLength()


def bbox_local_corners(bbox):
    if bbox is None:
        return []

    min_pt = bbox.Min
    max_pt = bbox.Max

    return [
        DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z),
        DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z),
        DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z),
        DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z),
        DB.XYZ(min_pt.X, min_pt.Y, max_pt.Z),
        DB.XYZ(max_pt.X, min_pt.Y, max_pt.Z),
        DB.XYZ(min_pt.X, max_pt.Y, max_pt.Z),
        DB.XYZ(max_pt.X, max_pt.Y, max_pt.Z)
    ]


def bbox_model_corners(bbox):
    if bbox is None:
        return []

    try:
        transform = bbox.Transform
    except Exception:
        transform = DB.Transform.Identity

    result = []
    for point in bbox_local_corners(bbox):
        try:
            result.append(transform.OfPoint(point))
        except Exception:
            result.append(point)
    return result


def ranges_overlap(min_a, max_a, min_b, max_b, tolerance=0.000001):
    return not (
        max_a < min_b - tolerance or
        min_a > max_b + tolerance
    )


# ============================================================
# PICK NHIỀU REVIT LINK
# ============================================================

class RevitLinkSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        try:
            return isinstance(element, DB.RevitLinkInstance)
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return False


class LinkItem(object):
    def __init__(self, link_instance):
        self.element = link_instance
        self.link_doc = link_instance.GetLinkDocument()
        self.uid = safe_text(link_instance.UniqueId)
        self.transform = link_instance.GetTotalTransform()

        instance_name = get_element_name(link_instance)
        document_name = u""
        try:
            document_name = safe_text(self.link_doc.Title)
        except Exception:
            pass

        if document_name and document_name.lower() not in instance_name.lower():
            self.display_name = u"{0}  |  {1}".format(instance_name, document_name)
        else:
            self.display_name = instance_name or document_name or u"Revit Link"


class GroupTypeItem(object):
    def __init__(self, group_type):
        self.element = group_type
        self.uid = safe_text(group_type.UniqueId)
        self.display_name = get_element_name(group_type) or u"Unnamed Group Type"


class FamilyWorkItem(object):
    def __init__(self, link_item, family_instance):
        self.link_item = link_item
        self.family = family_instance


def pick_link_items():
    try:
        references = uidoc.Selection.PickObjects(
            ObjectType.Element,
            RevitLinkSelectionFilter(),
            u"Chọn một hoặc nhiều Revit Link, sau đó bấm Finish"
        )
    except UI.Exceptions.OperationCanceledException:
        return []

    result = []
    used_ids = set()

    for reference in references:
        try:
            link_instance = doc.GetElement(reference.ElementId)
            if not isinstance(link_instance, DB.RevitLinkInstance):
                continue
            if link_instance.GetLinkDocument() is None:
                continue

            id_value = get_id_value(link_instance.Id)
            if id_value in used_ids:
                continue

            used_ids.add(id_value)
            result.append(LinkItem(link_instance))
        except Exception:
            pass

    return result


def is_model_group_type(group_type):
    try:
        category = group_type.Category
        if category is None:
            return False
        return get_id_value(category.Id) == int(DB.BuiltInCategory.OST_IOSModelGroups)
    except Exception:
        return False


def collect_model_group_types():
    items = []
    collector = DB.FilteredElementCollector(doc).OfClass(DB.GroupType)

    for group_type in collector:
        if is_model_group_type(group_type):
            items.append(GroupTypeItem(group_type))

    items.sort(key=lambda item: item.display_name.lower())
    return items


def get_link_ids_visible_in_active_view(view):
    result = set()
    try:
        collector = (
            DB.FilteredElementCollector(doc, view.Id)
            .OfClass(DB.RevitLinkInstance)
            .WhereElementIsNotElementType()
        )
        for link_instance in collector:
            result.add(get_id_value(link_instance.Id))
    except Exception:
        # Nếu view không hỗ trợ view-specific collector, giữ tập rỗng để caller
        # báo rõ thay vì quét nhầm toàn bộ model.
        pass
    return result


def keep_only_links_visible_in_active_view(link_items, view):
    visible_ids = get_link_ids_visible_in_active_view(view)
    visible_items = []
    hidden_items = []

    for item in link_items:
        if get_id_value(item.element.Id) in visible_ids:
            visible_items.append(item)
        else:
            hidden_items.append(item)

    return visible_items, hidden_items


# ============================================================
# LỌC FAMILY THEO TEXT
# ============================================================

def split_keywords(raw_text):
    text = safe_text(raw_text).strip().lower()
    if not text:
        return []

    parts = re.split(u"[;,\r\n]+", text)
    keywords = []
    for part in parts:
        keyword = safe_text(part).strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def family_identity_text(family_instance):
    family_name = u""
    type_name = u""

    try:
        family_name = safe_text(family_instance.Symbol.Family.Name)
    except Exception:
        pass

    try:
        type_name = get_element_name(family_instance.Symbol)
    except Exception:
        pass

    return u"{0} {1}".format(family_name, type_name).lower()


def family_matches(family_instance, keywords):
    if not keywords:
        return True

    search_text = family_identity_text(family_instance)
    for keyword in keywords:
        if keyword in search_text:
            return True
    return False


def make_scan_signature(link_items, host_view, raw_filter_text):
    """
    Tạo chữ ký cho cache kết quả quét.

    Cache chỉ còn hợp lệ khi đồng thời không đổi:
    - Active View.
    - Danh sách Revit Link đã pick.
    - Nội dung ô lọc Family.
    """

    try:
        view_id = get_id_value(host_view.Id)
    except Exception:
        view_id = -1

    link_keys = []
    for link_item in link_items:
        try:
            link_id = get_id_value(link_item.element.Id)
        except Exception:
            link_id = -1

        try:
            link_uid = safe_text(link_item.uid)
        except Exception:
            link_uid = u""

        link_keys.append(u"{0}|{1}".format(link_id, link_uid))

    link_keys.sort()

    # Chuẩn hóa theo đúng cách tool tách keyword, để thay đổi khoảng trắng
    # không cần thiết không làm mất cache.
    normalized_filter = u"|".join(split_keywords(raw_filter_text))

    return u"VIEW={0};LINKS={1};FILTER={2}".format(
        view_id,
        u";".join(link_keys),
        normalized_filter
    )


def describe_family(family_instance):
    family_name = u"Unknown Family"
    type_name = u"Unknown Type"

    try:
        family_name = safe_text(family_instance.Symbol.Family.Name)
    except Exception:
        pass

    try:
        type_name = get_element_name(family_instance.Symbol)
    except Exception:
        pass

    try:
        element_id = get_id_value(family_instance.Id)
    except Exception:
        element_id = u"?"

    return u"{0} : {1} [ID {2}]".format(family_name, type_name, element_id)


# ============================================================
# NHẬN DIỆN FAMILY HIỂN THỊ TRONG ACTIVE VIEW
# ============================================================

def get_link_graphics_settings(view, link_instance):
    """Revit 2024+. Trả về None trên phiên bản cũ hoặc view không hỗ trợ."""
    try:
        settings = view.GetLinkOverrides(link_instance.Id)
        if settings is not None:
            return settings
    except Exception:
        pass

    try:
        type_id = link_instance.GetTypeId()
        settings = view.GetLinkOverrides(type_id)
        if settings is not None:
            return settings
    except Exception:
        pass

    return None


def get_linked_view(link_item, host_view):
    settings = get_link_graphics_settings(host_view, link_item.element)
    if settings is None:
        return None

    try:
        visibility_type = safe_text(settings.LinkVisibilityType).lower()
        linked_view_id = settings.LinkedViewId

        if "bylinkview" not in visibility_type and "by link view" not in visibility_type:
            return None
        if is_invalid_element_id(linked_view_id):
            return None

        linked_view = link_item.link_doc.GetElement(linked_view_id)
        if isinstance(linked_view, DB.View) and not linked_view.IsTemplate:
            return linked_view
    except Exception:
        pass

    return None


def category_is_visible_by_host_view(family_instance, host_view):
    try:
        category = family_instance.Category
        if category is None:
            return False

        # Built-in category ids có cùng giá trị giữa host và link.
        if host_view.GetCategoryHidden(category.Id):
            return False
    except Exception:
        # Một số category/link setting không cho query; không loại nhầm.
        pass

    return True


def get_plan_view_z_range(view_plan):
    """
    Trả về khoảng Z host theo View Range. Bao gồm View Depth để các phần tử
    nhìn xuống dưới Bottom Clip vẫn được giữ lại.
    """
    try:
        view_range = view_plan.GetViewRange()
    except Exception:
        return None

    elevations = []
    plane_names = [
        "TopClipPlane",
        "CutPlane",
        "BottomClipPlane",
        "ViewDepthPlane"
    ]

    for plane_name in plane_names:
        try:
            plane = getattr(DB.PlanViewPlane, plane_name)
            level_id = view_range.GetLevelId(plane)
            offset = view_range.GetOffset(plane)

            if is_invalid_element_id(level_id):
                continue

            level = doc.GetElement(level_id)
            if isinstance(level, DB.Level):
                elevations.append(level.Elevation + offset)
        except Exception:
            pass

    if not elevations:
        return None

    return min(elevations), max(elevations)


def get_active_view_clip_box(view):
    """
    Trả về (BoundingBoxXYZ, check_z).
    - Plan: CropBox chỉ kiểm XY; Z dùng View Range riêng.
    - Section/Elevation/3D: Crop/SectionBox kiểm cả XYZ.
    """
    try:
        if isinstance(view, DB.View3D) and view.IsSectionBoxActive:
            return view.GetSectionBox(), True
    except Exception:
        pass

    try:
        if view.CropBoxActive:
            check_z = not isinstance(view, DB.ViewPlan)
            return view.CropBox, check_z
    except Exception:
        pass

    return None, False


def points_intersect_oriented_box(host_points, oriented_box, check_z):
    if not host_points or oriented_box is None:
        return True

    try:
        inverse = oriented_box.Transform.Inverse
    except Exception:
        inverse = DB.Transform.Identity

    local_points = []
    for point in host_points:
        try:
            local_points.append(inverse.OfPoint(point))
        except Exception:
            local_points.append(point)

    xs = [point.X for point in local_points]
    ys = [point.Y for point in local_points]
    zs = [point.Z for point in local_points]

    min_pt = oriented_box.Min
    max_pt = oriented_box.Max

    if not ranges_overlap(min(xs), max(xs), min_pt.X, max_pt.X):
        return False
    if not ranges_overlap(min(ys), max(ys), min_pt.Y, max_pt.Y):
        return False
    if check_z and not ranges_overlap(min(zs), max(zs), min_pt.Z, max_pt.Z):
        return False

    return True


def get_family_host_bbox_points(family_instance, link_item, linked_view=None):
    bbox = None

    if linked_view is not None:
        try:
            bbox = family_instance.get_BoundingBox(linked_view)
        except Exception:
            bbox = None

    if bbox is None:
        try:
            bbox = family_instance.get_BoundingBox(None)
        except Exception:
            bbox = None

    if bbox is None:
        return []

    result = []
    for link_point in bbox_model_corners(bbox):
        try:
            result.append(link_item.transform.OfPoint(link_point))
        except Exception:
            pass
    return result


def get_family_host_reference_point(family_instance, link_item, host_points=None):
    """
    Lấy cao độ đại diện của Family trong hệ tọa độ host.

    Ưu tiên LocationPoint/LocationCurve vì đây là cao độ đặt thật của Family.
    BoundingBox center chỉ là fallback. Việc kiểm tra thêm điểm đại diện giúp
    ngăn Family ở tầng trên lọt qua chỉ vì BoundingBox hoặc linked view rộng.
    """
    link_point = None

    try:
        location = family_instance.Location

        if isinstance(location, DB.LocationPoint):
            link_point = location.Point

        elif isinstance(location, DB.LocationCurve):
            curve = location.Curve
            if curve is not None:
                link_point = curve.Evaluate(0.5, True)
    except Exception:
        link_point = None

    if link_point is not None:
        try:
            return link_item.transform.OfPoint(link_point)
        except Exception:
            pass

    if host_points:
        try:
            count = float(len(host_points))
            return DB.XYZ(
                sum(point.X for point in host_points) / count,
                sum(point.Y for point in host_points) / count,
                sum(point.Z for point in host_points) / count
            )
        except Exception:
            pass

    return None


def family_intersects_active_view(family_instance, link_item, host_view, linked_view):
    host_points = get_family_host_bbox_points(
        family_instance,
        link_item,
        linked_view
    )
    if not host_points:
        return False

    clip_box, check_z = get_active_view_clip_box(host_view)
    if clip_box is not None:
        if not points_intersect_oriented_box(host_points, clip_box, check_z):
            return False

    # Luôn kiểm tra View Range của Active View trong host, kể cả Link đang dùng
    # By Linked View. Collector của linked view chỉ cho biết Family hiện trong
    # linked view đó, không đảm bảo Family nằm trong tầng của host active view.
    if isinstance(host_view, DB.ViewPlan):
        z_range = get_plan_view_z_range(host_view)
        if z_range is not None:
            z_min = z_range[0]
            z_max = z_range[1]
            z_values = [point.Z for point in host_points]

            # Bước 1: hình học Family phải giao với View Range của host.
            if not ranges_overlap(
                min(z_values),
                max(z_values),
                z_min,
                z_max
            ):
                return False

            # Bước 2: điểm đặt đại diện cũng phải nằm trong View Range. Bước này
            # loại Family ở tầng trên/dưới có BoundingBox bất thường hoặc family
            # cao nhiều tầng nhưng điểm đặt không thuộc tầng đang mở.
            reference_point = get_family_host_reference_point(
                family_instance,
                link_item,
                host_points
            )

            if reference_point is not None:
                z_tolerance = mm_to_internal(5.0)
                if (
                    reference_point.Z < z_min - z_tolerance
                    or reference_point.Z > z_max + z_tolerance
                ):
                    return False

    return True


def collect_link_candidate_families(link_item, host_view):
    linked_view = get_linked_view(link_item, host_view)

    if linked_view is not None:
        try:
            collector = (
                DB.FilteredElementCollector(link_item.link_doc, linked_view.Id)
                .OfClass(DB.FamilyInstance)
                .WhereElementIsNotElementType()
            )
            return list(collector), linked_view
        except Exception:
            # Nếu view-specific collector không dùng được, fallback toàn link.
            pass

    collector = (
        DB.FilteredElementCollector(link_item.link_doc)
        .OfClass(DB.FamilyInstance)
        .WhereElementIsNotElementType()
    )
    return list(collector), None


def collect_visible_matching_work_items(link_items, host_view, keywords, progress_enabled):
    """
    Quét theo hai giai đoạn:

    1. Thu thập FamilyInstance và lọc rất nhanh theo Family/Type text.
       Giai đoạn này không mở progress bar, nên progress không còn hiển thị
       hàng nghìn Family không liên quan.
    2. Chỉ kiểm tra visibility cho các Family đã khớp text. Progress bar
       dùng đúng số lượng Family của giai đoạn 1 làm max_value.
    """

    matched_candidates = []

    for link_item in link_items:
        candidates, linked_view = collect_link_candidate_families(
            link_item,
            host_view
        )

        for family_instance in candidates:
            try:
                if family_matches(family_instance, keywords):
                    matched_candidates.append((
                        link_item,
                        family_instance,
                        linked_view
                    ))
            except Exception:
                pass

    result = []
    cancelled = False
    total_matched = len(matched_candidates)

    def process_visibility(link_item, family_instance, linked_view):
        if linked_view is None and not category_is_visible_by_host_view(
            family_instance,
            host_view
        ):
            return

        if not family_intersects_active_view(
            family_instance,
            link_item,
            host_view,
            linked_view
        ):
            return

        result.append(FamilyWorkItem(link_item, family_instance))

    if progress_enabled and total_matched > 0:
        with forms.ProgressBar(
            title=(
                u"Đang kiểm tra hiển thị các Family đã khớp... "
                u"{value} / {max_value}"
            ),
            cancellable=True,
            step=1
        ) as progress:
            for index, candidate in enumerate(matched_candidates):
                if progress.cancelled:
                    cancelled = True
                    break

                link_item, family_instance, linked_view = candidate

                try:
                    process_visibility(
                        link_item,
                        family_instance,
                        linked_view
                    )
                except Exception:
                    pass

                progress.update_progress(index + 1, total_matched)

            if progress.cancelled:
                cancelled = True
    else:
        for link_item, family_instance, linked_view in matched_candidates:
            try:
                process_visibility(
                    link_item,
                    family_instance,
                    linked_view
                )
            except Exception:
                pass

    result.sort(
        key=lambda item: (
            item.link_item.display_name.lower(),
            get_id_value(item.family.Id)
        )
    )

    return result, cancelled


# ============================================================
# HÌNH HỌC FAMILY TRONG LINK
# ============================================================

def get_family_origin(family_instance):
    location = family_instance.Location

    if isinstance(location, DB.LocationPoint):
        return location.Point

    if isinstance(location, DB.LocationCurve):
        curve = location.Curve
        if curve is not None:
            return curve.Evaluate(0.5, True)

    try:
        transform = family_instance.GetTransform()
        if transform is not None:
            return transform.Origin
    except Exception:
        pass

    bbox = family_instance.get_BoundingBox(None)
    if bbox is not None:
        points = bbox_model_corners(bbox)
        if points:
            return DB.XYZ(
                sum(point.X for point in points) / len(points),
                sum(point.Y for point in points) / len(points),
                sum(point.Z for point in points) / len(points)
            )

    return None


def get_family_local_axes(family_instance):
    local_x = None
    local_y = None

    try:
        local_x = normalize_xy(family_instance.HandOrientation)
    except Exception:
        pass

    try:
        local_y = normalize_xy(family_instance.FacingOrientation)
    except Exception:
        pass

    try:
        instance_transform = family_instance.GetTransform()
    except Exception:
        instance_transform = None

    if local_x is None and instance_transform is not None:
        local_x = normalize_xy(instance_transform.BasisX)

    if local_y is None and instance_transform is not None:
        local_y = normalize_xy(instance_transform.BasisY)

    if local_x is None:
        local_x = DB.XYZ.BasisX

    if local_y is None:
        local_y = DB.XYZ.BasisY

    if abs(local_x.DotProduct(local_y)) > 0.999:
        local_y = DB.XYZ(-local_x.Y, local_x.X, 0.0)

    return local_x, local_y


def transformed_point(transform, point):
    if transform is None:
        return point
    return transform.OfPoint(point)


def collect_geometry_points(geometry_element, accumulated_transform, points, depth):
    if geometry_element is None or depth > 12:
        return

    for geometry_object in geometry_element:
        try:
            if isinstance(geometry_object, DB.GeometryInstance):
                nested_transform = accumulated_transform.Multiply(
                    geometry_object.Transform
                )
                symbol_geometry = geometry_object.GetSymbolGeometry()
                collect_geometry_points(
                    symbol_geometry,
                    nested_transform,
                    points,
                    depth + 1
                )

            elif isinstance(geometry_object, DB.Solid):
                if geometry_object.Faces.Size == 0 or geometry_object.Edges.Size == 0:
                    continue

                for edge in geometry_object.Edges:
                    try:
                        for point in edge.Tessellate():
                            points.append(
                                transformed_point(accumulated_transform, point)
                            )
                    except Exception:
                        try:
                            curve = edge.AsCurve()
                            points.append(
                                transformed_point(
                                    accumulated_transform,
                                    curve.GetEndPoint(0)
                                )
                            )
                            points.append(
                                transformed_point(
                                    accumulated_transform,
                                    curve.GetEndPoint(1)
                                )
                            )
                        except Exception:
                            pass

            elif isinstance(geometry_object, DB.Mesh):
                try:
                    for point in geometry_object.Vertices:
                        points.append(
                            transformed_point(accumulated_transform, point)
                        )
                except Exception:
                    pass

        except Exception:
            continue


def get_solid_geometry_points(family_instance):
    options = DB.Options()
    options.ComputeReferences = False
    options.IncludeNonVisibleObjects = False
    options.DetailLevel = DB.ViewDetailLevel.Fine

    geometry = family_instance.get_Geometry(options)
    points = []

    collect_geometry_points(
        geometry,
        DB.Transform.Identity,
        points,
        0
    )

    return points


def get_bbox_points(family_instance):
    bbox = family_instance.get_BoundingBox(None)
    return bbox_model_corners(bbox)


def get_family_extents(family_instance, use_solid):
    origin = get_family_origin(family_instance)
    if origin is None:
        return None

    local_x, local_y = get_family_local_axes(family_instance)

    points = []
    used_solid = False

    if use_solid:
        try:
            points = get_solid_geometry_points(family_instance)
            used_solid = len(points) > 0
        except Exception:
            points = []

    if not points:
        points = get_bbox_points(family_instance)

    if not points:
        return None

    x_values = []
    y_values = []

    for point in points:
        relative = point - origin
        x_values.append(relative.DotProduct(local_x))
        y_values.append(relative.DotProduct(local_y))

    return {
        "origin": origin,
        "local_x": local_x,
        "local_y": local_y,
        "min_x": min(x_values),
        "max_x": max(x_values),
        "min_y": min(y_values),
        "max_y": max(y_values),
        "used_solid": used_solid
    }


def get_anchor_point(family_instance, anchor_mode, offset_x, offset_y, use_solid):
    extents = get_family_extents(family_instance, use_solid)
    if extents is None:
        return None, False

    mode = safe_text(anchor_mode).upper().strip()

    if mode.startswith("LEFT_"):
        x_value = extents["min_x"] - offset_x
    elif mode.startswith("RIGHT_"):
        x_value = extents["max_x"] + offset_x
    else:
        raise ValueError(u"Anchor mode không hợp lệ: {0}".format(mode))

    if mode.endswith("_FRONT"):
        y_value = extents["max_y"] + offset_y
    elif mode.endswith("_BACK"):
        y_value = extents["min_y"] - offset_y
    else:
        raise ValueError(u"Anchor mode không hợp lệ: {0}".format(mode))

    origin = extents["origin"]
    local_x = extents["local_x"]
    local_y = extents["local_y"]

    target = (
        origin
        + local_x.Multiply(x_value)
        + local_y.Multiply(y_value)
    )

    target = DB.XYZ(target.X, target.Y, origin.Z)
    return target, extents["used_solid"]


def get_rotation_direction(family_instance, rotation_basis):
    basis = safe_text(rotation_basis).upper().strip()

    if basis == "FACING":
        try:
            direction = normalize_xy(family_instance.FacingOrientation)
            if direction is not None:
                return direction
        except Exception:
            pass
    else:
        try:
            direction = normalize_xy(family_instance.HandOrientation)
            if direction is not None:
                return direction
        except Exception:
            pass

    local_x, local_y = get_family_local_axes(family_instance)
    if basis == "FACING":
        return local_y
    return local_x


# ============================================================
# KIỂM TRA GROUP TRÙNG
# ============================================================

def get_group_location(group):
    try:
        location = group.Location
        if isinstance(location, DB.LocationPoint):
            return location.Point
    except Exception:
        pass

    try:
        bbox = group.get_BoundingBox(None)
        if bbox is not None:
            points = bbox_model_corners(bbox)
            if points:
                return DB.XYZ(
                    sum(point.X for point in points) / len(points),
                    sum(point.Y for point in points) / len(points),
                    sum(point.Z for point in points) / len(points)
                )
    except Exception:
        pass

    return None


def collect_existing_group_points(group_type):
    result = []
    collector = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.Group)
        .WhereElementIsNotElementType()
    )

    target_type_id = get_id_value(group_type.Id)

    for group in collector:
        try:
            if get_id_value(group.GroupType.Id) != target_type_id:
                continue
            point = get_group_location(group)
            if point is not None:
                result.append(point)
        except Exception:
            pass

    return result


def has_nearby_point(target_point, existing_points, tolerance):
    if tolerance <= 0.0:
        return False

    for point in existing_points:
        try:
            if points_distance_3d(target_point, point) <= tolerance:
                return True
        except Exception:
            pass

    return False


# ============================================================
# GIAO DIỆN NGƯỜI DÙNG
# ============================================================

class SettingsWindow(forms.WPFWindow):
    def __init__(self, xaml_file, link_items, group_items, config, view):
        forms.WPFWindow.__init__(self, xaml_file)

        self.link_items = link_items
        self.group_items = group_items
        self.config = config
        self.host_view = view
        self.result = None

        # Cache chỉ tồn tại trong phiên mở giao diện hiện tại.
        # Không ghi Element reference vào config để tránh dùng dữ liệu cũ
        # sau khi model/link thay đổi.
        self.cached_matched_items = None
        self.cached_scan_signature = None

        self.active_view_text.Text = u"Active View: {0}".format(
            get_element_name(view) or safe_text(view.ViewType)
        )

        for item in link_items:
            self.links_list.Items.Add(item.display_name)

        for item in group_items:
            self.group_combo.Items.Add(item.display_name)

        self.rotation_basis_combo.Items.Add(u"Xoay theo HandOrientation")
        self.rotation_basis_combo.Items.Add(u"Xoay theo FacingOrientation")

        self.restore_config()

        self.scan_button.Click += self.scan_clicked
        self.run_button.Click += self.run_clicked
        self.cancel_button.Click += self.cancel_clicked

        # Bất kỳ thay đổi nào của text lọc đều làm cache cũ không còn hợp lệ.
        self.filter_text.TextChanged += self.filter_text_changed

    def restore_config(self):
        last_group_uid = safe_text(getattr(self.config, "last_group_uid", u""))
        self.group_combo.SelectedIndex = self.find_uid_index(
            self.group_items,
            last_group_uid
        )

        self.filter_text.Text = safe_text(
            getattr(self.config, "family_filter", u"")
        )
        self.offset_x_text.Text = safe_text(
            getattr(self.config, "offset_x_mm", u"0")
        )
        self.offset_y_text.Text = safe_text(
            getattr(self.config, "offset_y_mm", u"0")
        )
        self.rotation_offset_text.Text = safe_text(
            getattr(self.config, "rotation_offset_deg", u"0")
        )
        self.duplicate_tolerance_text.Text = safe_text(
            getattr(self.config, "duplicate_tolerance_mm", u"10")
        )

        self.skip_duplicate_checkbox.IsChecked = bool(
            getattr(self.config, "skip_duplicate", True)
        )
        self.use_solid_checkbox.IsChecked = bool(
            getattr(self.config, "use_solid", True)
        )
        self.test_one_checkbox.IsChecked = bool(
            getattr(self.config, "test_one", True)
        )

        rotation_basis = safe_text(
            getattr(self.config, "rotation_basis", u"HAND")
        ).upper()
        if rotation_basis == "FACING":
            self.rotation_basis_combo.SelectedIndex = 1
        else:
            self.rotation_basis_combo.SelectedIndex = 0

        anchor_mode = safe_text(
            getattr(self.config, "anchor_mode", u"RIGHT_FRONT")
        ).upper()
        anchor_controls = {
            "LEFT_FRONT": self.left_front_radio,
            "RIGHT_FRONT": self.right_front_radio,
            "LEFT_BACK": self.left_back_radio,
            "RIGHT_BACK": self.right_back_radio
        }

        if anchor_mode not in anchor_controls:
            anchor_mode = "RIGHT_FRONT"
        anchor_controls[anchor_mode].IsChecked = True

    def find_uid_index(self, items, uid):
        if uid:
            for index, item in enumerate(items):
                if item.uid == uid:
                    return index
        return 0 if items else -1

    def get_selected_group_item(self):
        index = self.group_combo.SelectedIndex
        if index < 0 or index >= len(self.group_items):
            return None
        return self.group_items[index]

    def get_anchor_mode(self):
        if self.left_front_radio.IsChecked:
            return "LEFT_FRONT"
        if self.right_front_radio.IsChecked:
            return "RIGHT_FRONT"
        if self.left_back_radio.IsChecked:
            return "LEFT_BACK"
        if self.right_back_radio.IsChecked:
            return "RIGHT_BACK"
        return "RIGHT_FRONT"

    def invalidate_scan_cache(self):
        self.cached_matched_items = None
        self.cached_scan_signature = None

    def filter_text_changed(self, sender, args):
        # Không đóng/mở progress ở đây. Chỉ đánh dấu để lần bấm Run tiếp theo
        # biết rằng phải quét lại nếu người dùng chưa bấm Kiểm tra.
        if self.cached_matched_items is not None:
            self.invalidate_scan_cache()
            self.match_count_text.Text = (
                u"Bộ lọc đã thay đổi. Bấm Kiểm tra để tạo cache mới, "
                u"hoặc bấm Tạo Group để tool tự quét lại."
            )

    def scan_clicked(self, sender, args):
        try:
            raw_filter_text = safe_text(self.filter_text.Text)
            keywords = split_keywords(raw_filter_text)
            matched, cancelled = collect_visible_matching_work_items(
                self.link_items,
                self.host_view,
                keywords,
                True
            )

            if cancelled:
                self.invalidate_scan_cache()
                self.match_count_text.Text = u"Đã hủy kiểm tra; chưa lưu cache."
            else:
                self.cached_matched_items = list(matched)
                self.cached_scan_signature = make_scan_signature(
                    self.link_items,
                    self.host_view,
                    raw_filter_text
                )
                self.match_count_text.Text = (
                    u"Tìm thấy {0} Family đang hiển thị. "
                    u"Kết quả đã được lưu cache."
                ).format(len(matched))
        except Exception as ex:
            self.invalidate_scan_cache()
            self.match_count_text.Text = u"Lỗi quét: {0}".format(safe_text(ex))

    def run_clicked(self, sender, args):
        group_item = self.get_selected_group_item()
        if group_item is None:
            self.match_count_text.Text = u"Hãy chọn Model Group Type cần đặt."
            return

        try:
            offset_x_mm = parse_number(
                self.offset_x_text.Text,
                u"Offset X",
                0.0
            )
            offset_y_mm = parse_number(
                self.offset_y_text.Text,
                u"Offset Y",
                0.0
            )
            rotation_offset_deg = parse_number(
                self.rotation_offset_text.Text,
                u"Hiệu chỉnh góc"
            )
            duplicate_tolerance_mm = parse_number(
                self.duplicate_tolerance_text.Text,
                u"Dung sai kiểm tra trùng",
                0.0
            )
        except ValueError as ex:
            self.match_count_text.Text = safe_text(ex)
            return

        rotation_basis = "HAND"
        if self.rotation_basis_combo.SelectedIndex == 1:
            rotation_basis = "FACING"

        raw_filter_text = safe_text(self.filter_text.Text)
        current_scan_signature = make_scan_signature(
            self.link_items,
            self.host_view,
            raw_filter_text
        )

        cached_items = None
        cache_used = False
        if (
            self.cached_matched_items is not None and
            self.cached_scan_signature == current_scan_signature
        ):
            cached_items = list(self.cached_matched_items)
            cache_used = True

        self.result = {
            "link_items": self.link_items,
            "group_item": group_item,
            "filter_text": raw_filter_text,
            "scan_signature": current_scan_signature,
            "cached_matched_items": cached_items,
            "cache_used": cache_used,
            "anchor_mode": self.get_anchor_mode(),
            "offset_x_mm": offset_x_mm,
            "offset_y_mm": offset_y_mm,
            "rotation_offset_deg": rotation_offset_deg,
            "rotation_basis": rotation_basis,
            "skip_duplicate": self.skip_duplicate_checkbox.IsChecked is True,
            "duplicate_tolerance_mm": duplicate_tolerance_mm,
            "use_solid": self.use_solid_checkbox.IsChecked is True,
            "test_one": self.test_one_checkbox.IsChecked is True
        }
        self.Close()

    def cancel_clicked(self, sender, args):
        self.result = None
        self.Close()


# ============================================================
# MAIN PROCESS
# ============================================================

def save_user_config(config, settings):
    config.last_group_uid = settings["group_item"].uid
    config.family_filter = settings["filter_text"]
    config.anchor_mode = settings["anchor_mode"]
    config.offset_x_mm = safe_text(settings["offset_x_mm"])
    config.offset_y_mm = safe_text(settings["offset_y_mm"])
    config.rotation_offset_deg = safe_text(settings["rotation_offset_deg"])
    config.rotation_basis = settings["rotation_basis"]
    config.skip_duplicate = settings["skip_duplicate"]
    config.duplicate_tolerance_mm = safe_text(
        settings["duplicate_tolerance_mm"]
    )
    config.use_solid = settings["use_solid"]
    config.test_one = settings["test_one"]
    script.save_config()


def run_tool(settings, initial_notices=None):
    group_type = settings["group_item"].element
    keywords = split_keywords(settings["filter_text"])
    report_notices = list(initial_notices or [])

    context_lines = [
        u"Active View: {0}".format(
            get_element_name(active_view) or safe_text(active_view.ViewType)
        ),
        u"Revit Link được xử lý: {0}".format(len(settings["link_items"])),
        u"Model Group Type: {0}".format(settings["group_item"].display_name),
        u"Điểm đặt: {0}".format(settings["anchor_mode"]),
        u"Offset X / Y: {0:.1f} / {1:.1f} mm".format(
            settings["offset_x_mm"],
            settings["offset_y_mm"]
        ),
        u"Hiệu chỉnh góc: {0:.2f}°; cơ sở xoay: {1}".format(
            settings["rotation_offset_deg"],
            settings["rotation_basis"]
        )
    ]

    filter_display = safe_text(settings["filter_text"]).strip()
    if filter_display:
        context_lines.append(
            u"Từ khóa lọc: {0}".format(
                filter_display.replace(u"\r", u" ").replace(u"\n", u"; ")
            )
        )
    else:
        context_lines.append(u"Từ khóa lọc: để trống, lấy tất cả Family phù hợp")

    matched_items = settings.get("cached_matched_items")
    scan_cancelled = False

    expected_signature = make_scan_signature(
        settings["link_items"],
        active_view,
        settings["filter_text"]
    )

    cache_is_valid = (
        matched_items is not None and
        settings.get("scan_signature") == expected_signature
    )

    if cache_is_valid:
        # Tạo list mới để tránh vô tình thay đổi cache gốc của giao diện.
        matched_items = list(matched_items)
        report_notices.append(
            u"Đã dùng kết quả cache từ nút Kiểm tra; không quét lại Family."
        )
    else:
        matched_items, scan_cancelled = collect_visible_matching_work_items(
            settings["link_items"],
            active_view,
            keywords,
            True
        )
        report_notices.append(
            u"Không có cache hợp lệ nên tool đã quét lại; progress chỉ đếm các Family đã khớp từ khóa."
        )

    if scan_cancelled:
        show_output_report(
            u"Đã hủy khi quét Family",
            summary_lines=context_lines + [
                u"Chưa có Group nào được tạo."
            ],
            notices=report_notices
        )
        return

    if not matched_items:
        show_output_report(
            u"Không tìm thấy Family phù hợp",
            summary_lines=context_lines + [
                u"Family phù hợp đang hiển thị: 0",
                u"Group đã tạo: 0"
            ],
            notices=report_notices + [
                u"Kiểm tra từ khóa, Crop/Section Box, View Range và Visibility/Graphics của Revit Link."
            ]
        )
        return

    offset_x = mm_to_internal(settings["offset_x_mm"])
    offset_y = mm_to_internal(settings["offset_y_mm"])
    duplicate_tolerance = mm_to_internal(
        settings["duplicate_tolerance_mm"]
    )
    rotation_offset = math.radians(settings["rotation_offset_deg"])

    if settings["skip_duplicate"]:
        existing_group_points = collect_existing_group_points(group_type)
    else:
        existing_group_points = []

    created_count = 0
    duplicate_count = 0
    fallback_bbox_count = 0
    mirrored_count = 0
    failed_items = []
    created_records = []
    cancelled = False

    transaction_name = u"Test Place One Group From Linked Family"
    if not settings["test_one"]:
        transaction_name = u"Place Groups From Linked Families"

    transaction = DB.Transaction(doc, transaction_name)

    try:
        transaction.Start()
        total = len(matched_items)

        with forms.ProgressBar(
            title=u"Đang đặt Group... {value} / {max_value}",
            cancellable=True,
            step=1
        ) as progress:

            for index, work_item in enumerate(matched_items):
                if progress.cancelled:
                    cancelled = True
                    break

                family_instance = work_item.family
                link_item = work_item.link_item

                sub_transaction = DB.SubTransaction(doc)
                sub_transaction.Start()

                try:
                    link_anchor, used_solid = get_anchor_point(
                        family_instance,
                        settings["anchor_mode"],
                        offset_x,
                        offset_y,
                        settings["use_solid"]
                    )

                    if link_anchor is None:
                        raise Exception(
                            u"Không xác định được điểm đặt của Family."
                        )

                    if settings["use_solid"] and not used_solid:
                        fallback_bbox_count += 1

                    host_anchor = link_item.transform.OfPoint(link_anchor)

                    if settings["skip_duplicate"] and has_nearby_point(
                        host_anchor,
                        existing_group_points,
                        duplicate_tolerance
                    ):
                        duplicate_count += 1
                        sub_transaction.RollBack()
                    else:
                        link_direction = get_rotation_direction(
                            family_instance,
                            settings["rotation_basis"]
                        )
                        host_direction = link_item.transform.OfVector(
                            link_direction
                        )
                        angle = vector_angle_xy(host_direction) + rotation_offset

                        new_group = doc.Create.PlaceGroup(
                            host_anchor,
                            group_type
                        )

                        if new_group is None:
                            raise Exception(u"Revit không tạo được Group.")

                        if abs(angle) > 0.0000001:
                            rotation_axis = DB.Line.CreateBound(
                                host_anchor,
                                host_anchor + DB.XYZ.BasisZ
                            )
                            DB.ElementTransformUtils.RotateElement(
                                doc,
                                new_group.Id,
                                rotation_axis,
                                angle
                            )

                        is_mirrored = False
                        try:
                            is_mirrored = family_instance.Mirrored is True
                            if is_mirrored:
                                mirrored_count += 1
                        except Exception:
                            pass

                        group_id = new_group.Id
                        group_id_value = get_id_value(group_id)

                        sub_transaction.Commit()

                        existing_group_points.append(host_anchor)
                        created_count += 1
                        created_records.append({
                            "group_id": group_id,
                            "group_id_value": group_id_value,
                            "group_type_name": settings["group_item"].display_name,
                            "source_family": describe_family(family_instance),
                            "link_name": link_item.display_name,
                            "point": host_anchor,
                            "angle_deg": format_angle_degrees(angle),
                            "anchor_mode": settings["anchor_mode"],
                            "used_solid": used_solid,
                            "mirrored": is_mirrored
                        })

                except Exception as item_error:
                    try:
                        if sub_transaction.GetStatus() == DB.TransactionStatus.Started:
                            sub_transaction.RollBack()
                    except Exception:
                        pass

                    failed_items.append(
                        u"{0} | {1}: {2}".format(
                            link_item.display_name,
                            describe_family(family_instance),
                            safe_text(item_error)
                        )
                    )

                progress.update_progress(index + 1, total)

                # Test mode: dừng ngay sau Group đầu tiên được tạo thành công.
                if settings["test_one"] and created_count >= 1:
                    break

            if progress.cancelled:
                cancelled = True

        if cancelled:
            transaction.RollBack()
            show_output_report(
                u"Đã hủy thao tác đặt Group",
                summary_lines=context_lines + [
                    u"Family phù hợp đang hiển thị: {0}".format(len(matched_items)),
                    u"Toàn bộ Group của lần chạy đã được hoàn tác."
                ],
                notices=report_notices,
                failed_items=failed_items
            )
            return

        transaction.Commit()

    except Exception:
        try:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass

        show_output_report(
            u"Tool không thể hoàn thành",
            summary_lines=context_lines + [
                u"Toàn bộ thay đổi của lần chạy đã được hoàn tác."
            ],
            notices=report_notices,
            failed_items=failed_items,
            traceback_text=traceback.format_exc()
        )
        return

    result_lines = context_lines + [
        u"Family phù hợp đang hiển thị: {0}".format(len(matched_items)),
        u"Group đã tạo: {0}".format(created_count),
        u"Bỏ qua do trùng: {0}".format(duplicate_count),
        u"Không xử lý được: {0}".format(len(failed_items))
    ]

    if settings["test_one"]:
        if created_count == 1:
            status_title = u"Đã tạo 1 Group kiểm tra"
            report_notices.extend([
                u"Kiểm tra vị trí và góc xoay bằng liên kết Zoom trong bảng bên dưới.",
                u"Khi kết quả đúng, chạy lại tool và bỏ chọn 'Chỉ tạo 1 Group để kiểm tra góc và vị trí'.",
                u"Giữ bật kiểm tra trùng để lần chạy hàng loạt tự bỏ qua Group thử."
            ])
        else:
            status_title = u"Không tạo được Group kiểm tra"
    else:
        status_title = u"Đã hoàn thành tạo Group hàng loạt"

    if fallback_bbox_count:
        report_notices.append(
            u"Có {0} Family không đọc được Solid và đã dùng BoundingBox dự phòng.".format(
                fallback_bbox_count
            )
        )

    if mirrored_count:
        report_notices.append(
            u"Có {0} Family nguồn bị Mirror. Tool đã xoay Group nhưng chưa mirror hình học Group.".format(
                mirrored_count
            )
        )

    show_output_report(
        status_title,
        summary_lines=result_lines,
        notices=report_notices,
        failed_items=failed_items,
        created_records=created_records
    )


# ============================================================
# KHỞI CHẠY
# ============================================================

if doc is None:
    show_output_report(
        u"Không thể chạy tool",
        summary_lines=[u"Không có Revit document đang mở."]
    )
    script.exit()

if doc.IsFamilyDocument:
    show_output_report(
        u"Không thể chạy tool",
        summary_lines=[
            u"Tool chỉ chạy trong Project Document, không chạy trong Family Editor."
        ]
    )
    script.exit()

if active_view is None or active_view.IsTemplate:
    show_output_report(
        u"Không thể chạy tool",
        summary_lines=[
            u"Hãy mở một model view hợp lệ trước khi chạy tool."
        ]
    )
    script.exit()

selected_links = pick_link_items()
if not selected_links:
    script.exit()

selected_links, hidden_links = keep_only_links_visible_in_active_view(
    selected_links,
    active_view
)

if not selected_links:
    hidden_names = u"; ".join(
        [item.display_name for item in hidden_links]
    )
    show_output_report(
        u"Không có Revit Link hợp lệ",
        summary_lines=[
            u"Các Revit Link đã pick không hiển thị trong Active View.",
            u"Link đã pick: {0}".format(hidden_names or u"Không xác định")
        ],
        notices=[
            u"Bật Revit Link trong Visibility/Graphics hoặc chọn Link khác."
        ]
    )
    script.exit()

if hidden_links:
    hidden_names = u"; ".join(
        [item.display_name for item in hidden_links]
    )
    startup_notices.append(
        u"Các Link không hiển thị trong Active View đã bị bỏ qua: {0}".format(
            hidden_names
        )
    )

group_types = collect_model_group_types()
if not group_types:
    show_output_report(
        u"Không có Model Group Type",
        summary_lines=[
            u"Model hiện tại chưa có Model Group Type."
        ],
        notices=[
            u"Tạo hoặc load Model Group trước khi chạy tool."
        ]
    )
    script.exit()

config = script.get_config()
xaml_path = script.get_bundle_file("ui.xaml")

if not xaml_path or not os.path.exists(xaml_path):
    show_output_report(
        u"Thiếu file giao diện",
        summary_lines=[
            u"Không tìm thấy file ui.xaml trong thư mục pushbutton."
        ]
    )
    script.exit()

window = SettingsWindow(
    xaml_path,
    selected_links,
    group_types,
    config,
    active_view
)
window.ShowDialog()

if window.result is None:
    script.exit()

save_user_config(config, window.result)
run_tool(window.result, startup_notices)

