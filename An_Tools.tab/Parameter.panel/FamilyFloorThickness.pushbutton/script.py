# -*- coding: utf-8 -*-
"""
Family Floor Data
-----------------
pyRevit tool (IronPython 2.7 / CPython-compatible style)

Workflow:
1. Pick one FamilyInstance.
2. Select the Family Types to process.
3. Enable either or both operations:
   - Ray-cast upward and write the floor thickness at each family location.
   - Read each instance's existing Elevation from Level and copy it to a
     selected writable instance Length parameter.
4. Show a pyRevit progress bar while processing and writing values.

Supported floor sources:
- Floors in the current model.
- Floors in loaded Revit links (optional).

Recommended bundle structure:
FamilyFloorThickness.pushbutton/
    script.py
    ui.xaml
"""

import os
import traceback

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import Thickness
from System.Windows.Controls import CheckBox

from pyrevit import revit, DB, UI, forms, script


doc = revit.doc
uidoc = revit.uidoc
active_view = revit.active_view
output = script.get_output()
config = script.get_config()

TOOL_TITLE = u"Cập nhật dữ liệu sàn cho Family"
MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8
DISTANCE_TOL = 1.0e-7


# -----------------------------------------------------------------------------
# Compatibility helpers
# -----------------------------------------------------------------------------

def id_value(element_id):
    """Return ElementId value for Revit versions using IntegerValue or Value."""
    if element_id is None:
        return -1
    try:
        return int(element_id.Value)
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return -1


def is_valid_id(element_id):
    return id_value(element_id) != -1


def get_config_value(name, default_value):
    try:
        return getattr(config, name)
    except Exception:
        return default_value


def get_element_name(element):
    if element is None:
        return u""
    try:
        return element.Name
    except Exception:
        pass
    try:
        parameter = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if parameter:
            return parameter.AsString() or u""
    except Exception:
        pass
    return u"Element {0}".format(id_value(element.Id))


def mm_to_internal(value_mm):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(
            float(value_mm), DB.UnitTypeId.Millimeters
        )
    except Exception:
        try:
            return DB.UnitUtils.ConvertToInternalUnits(
                float(value_mm), DB.DisplayUnitType.DUT_MILLIMETERS
            )
        except Exception:
            return float(value_mm) * MM_TO_FT


def internal_to_mm(value_internal):
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(
            float(value_internal), DB.UnitTypeId.Millimeters
        )
    except Exception:
        try:
            return DB.UnitUtils.ConvertFromInternalUnits(
                float(value_internal), DB.DisplayUnitType.DUT_MILLIMETERS
            )
        except Exception:
            return float(value_internal) * FT_TO_MM


def is_length_parameter(parameter):
    """True only for writable instance parameters storing a Length value."""
    if parameter is None:
        return False
    try:
        if parameter.IsReadOnly:
            return False
    except Exception:
        return False
    if parameter.StorageType != DB.StorageType.Double:
        return False

    # Revit 2022+
    try:
        data_type = parameter.Definition.GetDataType()
        return data_type == DB.SpecTypeId.Length
    except Exception:
        pass

    # Revit 2021 and earlier
    try:
        return parameter.Definition.ParameterType == DB.ParameterType.Length
    except Exception:
        return False


def get_parameter_id(parameter):
    try:
        return id_value(parameter.Id)
    except Exception:
        return -1


def find_parameter_by_id(element, parameter_id_value):
    try:
        for parameter in element.Parameters:
            if get_parameter_id(parameter) == parameter_id_value:
                return parameter
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------------
# Selection and data collection
# -----------------------------------------------------------------------------

class FamilyInstanceSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.FamilyInstance)

    def AllowReference(self, reference, point):
        return False


def pick_family_instance():
    reference = uidoc.Selection.PickObject(
        UI.Selection.ObjectType.Element,
        FamilyInstanceSelectionFilter(),
        u"Chọn một Family Instance làm mẫu"
    )
    element = doc.GetElement(reference.ElementId)
    if not isinstance(element, DB.FamilyInstance):
        return None
    return element


def get_family_types(family):
    items = []
    for symbol_id in family.GetFamilySymbolIds():
        symbol = doc.GetElement(symbol_id)
        if symbol is None:
            continue
        items.append({
            "id": id_value(symbol.Id),
            "name": get_element_name(symbol),
            "element": symbol
        })
    items.sort(key=lambda x: x["name"].lower())
    return items


def get_writable_instance_length_parameters(instance):
    raw_items = []
    seen_ids = set()

    for parameter in instance.Parameters:
        if not is_length_parameter(parameter):
            continue
        pid = get_parameter_id(parameter)
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        try:
            name = parameter.Definition.Name
        except Exception:
            name = u"Parameter {0}".format(pid)
        raw_items.append({
            "id": pid,
            "name": name
        })

    raw_items.sort(key=lambda x: x["name"].lower())

    # Make duplicate display names distinguishable without changing the actual name.
    name_counts = {}
    for item in raw_items:
        key = item["name"].lower()
        name_counts[key] = name_counts.get(key, 0) + 1

    for item in raw_items:
        if name_counts[item["name"].lower()] > 1:
            item["display"] = u"{0}  [ID {1}]".format(item["name"], item["id"])
        else:
            item["display"] = item["name"]

    return raw_items


def collect_family_instances(family_id_value, selected_type_ids, scope):
    selected_type_ids = set(selected_type_ids)

    if scope == "active_view":
        try:
            collector = DB.FilteredElementCollector(doc, active_view.Id)
        except Exception:
            collector = DB.FilteredElementCollector(doc)
    else:
        collector = DB.FilteredElementCollector(doc)

    result = []
    for element in collector.OfClass(DB.FamilyInstance).WhereElementIsNotElementType():
        try:
            symbol = element.Symbol
            if symbol is None or symbol.Family is None:
                continue
            if id_value(symbol.Family.Id) != family_id_value:
                continue
            if id_value(symbol.Id) not in selected_type_ids:
                continue
            result.append(element)
        except Exception:
            continue

    return result



# -----------------------------------------------------------------------------
# WPF UI
# -----------------------------------------------------------------------------

class MainWindow(forms.WPFWindow):
    def __init__(self, xaml_file, family_name, type_items, parameter_items):
        forms.WPFWindow.__init__(self, xaml_file)

        self.type_items = type_items
        self.parameter_items = parameter_items
        self.accepted = False
        self.selected_type_ids = []

        self.run_ray_cast = True
        self.run_elevation_copy = True

        self.floor_parameter_id = None
        self.floor_parameter_name = None
        self.elevation_parameter_id = None
        self.elevation_parameter_name = None

        self.scope = "entire_model"
        self.include_linked_floors = True
        self.ray_offset_mm = 5.0
        self.max_ray_distance_mm = 30000.0

        self.txt_family_name.Text = family_name
        self.txt_type_count.Text = u"{0} type".format(len(type_items))

        for item in type_items:
            checkbox = CheckBox()
            checkbox.Content = item["name"]
            checkbox.Tag = str(item["id"])
            checkbox.IsChecked = True
            checkbox.Margin = Thickness(0, 3, 0, 3)
            self.panel_types.Children.Add(checkbox)

        for item in parameter_items:
            self.cmb_floor_parameter.Items.Add(item["display"])
            self.cmb_elevation_parameter.Items.Add(item["display"])

        old_saved_parameter = get_config_value("parameter_name", u"")
        saved_floor_parameter = get_config_value(
            "floor_parameter_name", old_saved_parameter
        )
        saved_elevation_parameter = get_config_value(
            "elevation_parameter_name", u""
        )

        floor_index = self._find_parameter_index(
            saved_floor_parameter, preferred_name=u"Floor Thickness"
        )
        elevation_index = self._find_parameter_index(
            saved_elevation_parameter, preferred_name=u""
        )

        if parameter_items:
            self.cmb_floor_parameter.SelectedIndex = floor_index

            # Default the second combo to a different target to avoid accidental
            # overwrite when both operations are enabled.
            if elevation_index == floor_index and len(parameter_items) > 1:
                elevation_index = 1 if floor_index == 0 else 0
            self.cmb_elevation_parameter.SelectedIndex = elevation_index

        self.chk_run_ray.IsChecked = bool(
            get_config_value("run_ray_cast", True)
        )
        self.chk_run_elevation.IsChecked = bool(
            get_config_value("run_elevation_copy", True)
        )

        saved_scope = get_config_value("scope", "entire_model")
        if saved_scope == "active_view":
            self.rb_active_view.IsChecked = True
        else:
            self.rb_entire_model.IsChecked = True

        self.chk_linked_floors.IsChecked = bool(
            get_config_value("include_linked_floors", True)
        )
        self.txt_ray_offset.Text = str(get_config_value("ray_offset_mm", 5.0))
        self.txt_max_distance.Text = str(
            get_config_value("max_ray_distance_mm", 30000.0)
        )

        self.btn_select_all.Click += self.select_all_clicked
        self.btn_select_none.Click += self.select_none_clicked
        self.btn_run.Click += self.run_clicked
        self.btn_cancel.Click += self.cancel_clicked
        self.chk_run_ray.Checked += self.option_changed
        self.chk_run_ray.Unchecked += self.option_changed
        self.chk_run_elevation.Checked += self.option_changed
        self.chk_run_elevation.Unchecked += self.option_changed

        self.update_option_states()

    def _find_parameter_index(self, saved_name, preferred_name=u""):
        if not self.parameter_items:
            return -1

        if saved_name:
            for index, item in enumerate(self.parameter_items):
                if item["name"] == saved_name:
                    return index

        if preferred_name:
            for index, item in enumerate(self.parameter_items):
                if item["name"].lower() == preferred_name.lower():
                    return index

        return 0

    def option_changed(self, sender, args):
        self.update_option_states()

    def update_option_states(self):
        ray_enabled = bool(self.chk_run_ray.IsChecked)
        elevation_enabled = bool(self.chk_run_elevation.IsChecked)

        self.cmb_floor_parameter.IsEnabled = ray_enabled
        self.grp_ray_settings.IsEnabled = ray_enabled
        self.cmb_elevation_parameter.IsEnabled = elevation_enabled

    def select_all_clicked(self, sender, args):
        for child in self.panel_types.Children:
            if isinstance(child, CheckBox):
                child.IsChecked = True

    def select_none_clicked(self, sender, args):
        for child in self.panel_types.Children:
            if isinstance(child, CheckBox):
                child.IsChecked = False

    def run_clicked(self, sender, args):
        selected_type_ids = []
        for child in self.panel_types.Children:
            if isinstance(child, CheckBox) and child.IsChecked:
                try:
                    selected_type_ids.append(int(str(child.Tag)))
                except Exception:
                    pass

        if not selected_type_ids:
            forms.alert(
                u"Hãy chọn ít nhất một Family Type.",
                title=TOOL_TITLE,
                warn_icon=True
            )
            return

        run_ray_cast = bool(self.chk_run_ray.IsChecked)
        run_elevation_copy = bool(self.chk_run_elevation.IsChecked)

        if not run_ray_cast and not run_elevation_copy:
            forms.alert(
                u"Hãy bật ít nhất một chức năng:\n"
                u"- Ray-cast lên trên\n"
                u"- Đọc Elevation from Level",
                title=TOOL_TITLE,
                warn_icon=True
            )
            return

        floor_parameter = None
        elevation_parameter = None

        if run_ray_cast:
            floor_index = self.cmb_floor_parameter.SelectedIndex
            if floor_index < 0 or floor_index >= len(self.parameter_items):
                forms.alert(
                    u"Hãy chọn parameter nhận chiều dày sàn.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return
            floor_parameter = self.parameter_items[floor_index]

        if run_elevation_copy:
            elevation_index = self.cmb_elevation_parameter.SelectedIndex
            if elevation_index < 0 or elevation_index >= len(self.parameter_items):
                forms.alert(
                    u"Hãy chọn parameter nhận giá trị Elevation from Level.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return
            elevation_parameter = self.parameter_items[elevation_index]

            source_parameter_id = id_value(
                DB.ElementId(DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM)
            )
            if elevation_parameter["id"] == source_parameter_id:
                forms.alert(
                    u"Parameter nhận cao độ không được là chính parameter "
                    u"Elevation from Level. Hãy chọn một instance parameter khác.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return

        if (
            run_ray_cast
            and run_elevation_copy
            and floor_parameter["id"] == elevation_parameter["id"]
        ):
            forms.alert(
                u"Parameter nhận chiều dày sàn và parameter nhận cao độ "
                u"không được trùng nhau, vì giá trị ghi sau sẽ ghi đè giá trị trước.",
                title=TOOL_TITLE,
                warn_icon=True
            )
            return

        ray_offset_mm = self.ray_offset_mm
        max_distance_mm = self.max_ray_distance_mm

        if run_ray_cast:
            try:
                ray_offset_mm = float(
                    str(self.txt_ray_offset.Text).replace(",", ".")
                )
                max_distance_mm = float(
                    str(self.txt_max_distance.Text).replace(",", ".")
                )
            except Exception:
                forms.alert(
                    u"Khoảng lùi tia và khoảng cách tìm tối đa phải là số.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return

            if ray_offset_mm < 0:
                forms.alert(
                    u"Khoảng lùi tia không được nhỏ hơn 0 mm.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return

            if max_distance_mm <= 0:
                forms.alert(
                    u"Khoảng cách tìm tối đa phải lớn hơn 0 mm.",
                    title=TOOL_TITLE,
                    warn_icon=True
                )
                return

        self.selected_type_ids = selected_type_ids
        self.run_ray_cast = run_ray_cast
        self.run_elevation_copy = run_elevation_copy

        if floor_parameter is not None:
            self.floor_parameter_id = floor_parameter["id"]
            self.floor_parameter_name = floor_parameter["name"]

        if elevation_parameter is not None:
            self.elevation_parameter_id = elevation_parameter["id"]
            self.elevation_parameter_name = elevation_parameter["name"]

        self.scope = (
            "active_view" if self.rb_active_view.IsChecked else "entire_model"
        )
        self.include_linked_floors = bool(self.chk_linked_floors.IsChecked)
        self.ray_offset_mm = ray_offset_mm
        self.max_ray_distance_mm = max_distance_mm
        self.accepted = True
        self.Close()

    def cancel_clicked(self, sender, args):
        self.accepted = False
        self.Close()

# -----------------------------------------------------------------------------
# Geometry and ray casting
# -----------------------------------------------------------------------------

def get_instance_point(instance):
    """Get one representative model point for point-, line-, or hosted families."""
    location = instance.Location

    if isinstance(location, DB.LocationPoint):
        return location.Point

    if isinstance(location, DB.LocationCurve):
        curve = location.Curve
        if curve is not None:
            try:
                return curve.Evaluate(0.5, True)
            except Exception:
                return curve.GetEndPoint(0).Add(curve.GetEndPoint(1)).Multiply(0.5)

    try:
        transform = instance.GetTransform()
        if transform is not None:
            return transform.Origin
    except Exception:
        pass

    bbox = instance.get_BoundingBox(None)
    if bbox is not None:
        return bbox.Min.Add(bbox.Max).Multiply(0.5)

    return None


def create_temporary_3d_view():
    view_family_type = None
    for candidate in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if candidate.ViewFamily == DB.ViewFamily.ThreeDimensional:
            view_family_type = candidate
            break

    if view_family_type is None:
        raise Exception(u"Không tìm thấy ViewFamilyType 3D để tạo view ray-cast.")

    view3d = DB.View3D.CreateIsometric(doc, view_family_type.Id)
    try:
        view3d.Name = u"__pyRevit_FloorThickness_Ray__"
    except Exception:
        pass
    try:
        view3d.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    return view3d


def is_floor_element(element):
    if element is None or element.Category is None:
        return False
    try:
        floor_category_id = id_value(
            DB.ElementId(DB.BuiltInCategory.OST_Floors)
        )
        return id_value(element.Category.Id) == floor_category_id
    except Exception:
        return isinstance(element, DB.Floor)


def resolve_floor_reference(reference):
    """
    Return (floor, key, source_label) for host or linked floor reference.
    The key uniquely identifies the floor and its link instance.
    """
    linked_id = None
    try:
        linked_id = reference.LinkedElementId
    except Exception:
        linked_id = None

    if linked_id is not None and is_valid_id(linked_id):
        link_instance = doc.GetElement(reference.ElementId)
        if not isinstance(link_instance, DB.RevitLinkInstance):
            return None
        link_doc = link_instance.GetLinkDocument()
        if link_doc is None:
            return None
        floor = link_doc.GetElement(linked_id)
        if not is_floor_element(floor):
            return None
        key = (
            "LINK",
            id_value(link_instance.Id),
            id_value(linked_id)
        )
        source_label = u"Link: {0}".format(get_element_name(link_instance))
        return floor, key, source_label

    floor = doc.GetElement(reference.ElementId)
    if not is_floor_element(floor):
        return None
    key = ("HOST", id_value(floor.Id))
    return floor, key, u"Current model"


def get_nominal_floor_thickness(floor):
    """Fallback: total compound structure width / built-in floor thickness."""
    if floor is None:
        return None

    floor_doc = floor.Document
    floor_type = None
    try:
        floor_type = floor_doc.GetElement(floor.GetTypeId())
    except Exception:
        floor_type = None

    if floor_type is not None:
        try:
            compound = floor_type.GetCompoundStructure()
            if compound is not None:
                width = compound.GetWidth()
                if width and width > DISTANCE_TOL:
                    return width
        except Exception:
            pass

    elements_to_check = [floor, floor_type]
    parameter_names = [
        "FLOOR_ATTR_THICKNESS_PARAM",
        "HOST_ATTR_DEFAULT_THICKNESS_PARAM"
    ]

    for element in elements_to_check:
        if element is None:
            continue
        for bip_name in parameter_names:
            try:
                bip = getattr(DB.BuiltInParameter, bip_name)
                parameter = element.get_Parameter(bip)
                if parameter and parameter.StorageType == DB.StorageType.Double:
                    value = parameter.AsDouble()
                    if value and value > DISTANCE_TOL:
                        return value
            except Exception:
                continue

    return None


def get_floor_description(floor, source_label):
    floor_type_name = u""
    try:
        floor_type = floor.Document.GetElement(floor.GetTypeId())
        floor_type_name = get_element_name(floor_type)
    except Exception:
        floor_type_name = u""

    if floor_type_name:
        return u"{0} | {1} | ID {2}".format(
            source_label, floor_type_name, id_value(floor.Id)
        )
    return u"{0} | Floor ID {1}".format(source_label, id_value(floor.Id))


def find_floor_thickness(intersector, origin, max_distance_internal):
    """
    Find the nearest floor above origin.

    Preferred result:
      Difference between the first two unique face-hit distances belonging to
      the same nearest floor. This returns the vertical thickness at the exact
      family XY position.

    Fallback:
      Nominal FloorType compound structure width when only one face is returned.
    """
    hits = intersector.Find(origin, DB.XYZ.BasisZ)
    if hits is None:
        return None

    ordered_hits = sorted(list(hits), key=lambda x: x.Proximity)
    target_key = None
    target_floor = None
    target_source = None
    unique_proximities = []

    for hit in ordered_hits:
        proximity = float(hit.Proximity)
        if proximity < -DISTANCE_TOL:
            continue
        if proximity > max_distance_internal:
            break

        try:
            reference = hit.GetReference()
        except Exception:
            continue

        resolved = resolve_floor_reference(reference)
        if resolved is None:
            continue

        floor, floor_key, source_label = resolved

        if target_key is None:
            target_key = floor_key
            target_floor = floor
            target_source = source_label

        if floor_key != target_key:
            continue

        duplicate = False
        for old_value in unique_proximities:
            if abs(old_value - proximity) <= DISTANCE_TOL:
                duplicate = True
                break
        if duplicate:
            continue

        unique_proximities.append(proximity)
        if len(unique_proximities) >= 2:
            thickness = unique_proximities[1] - unique_proximities[0]
            if thickness > DISTANCE_TOL:
                return {
                    "thickness": thickness,
                    "floor": target_floor,
                    "floor_description": get_floor_description(
                        target_floor, target_source
                    ),
                    "method": u"Ray: 2 floor faces",
                    "distance": unique_proximities[0]
                }

    if target_floor is not None:
        nominal_thickness = get_nominal_floor_thickness(target_floor)
        if nominal_thickness is not None:
            first_distance = unique_proximities[0] if unique_proximities else None
            return {
                "thickness": nominal_thickness,
                "floor": target_floor,
                "floor_description": get_floor_description(
                    target_floor, target_source
                ),
                "method": u"Fallback: Floor Type thickness",
                "distance": first_distance
            }

    return None




def get_elevation_from_level(instance):
    """Read the built-in Elevation from Level value in Revit internal units."""
    try:
        parameter = instance.get_Parameter(
            DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM
        )
        if parameter is None:
            return None
        if parameter.StorageType != DB.StorageType.Double:
            return None
        return float(parameter.AsDouble())
    except Exception:
        return None


def validate_destination_parameter(instance, parameter_id_value):
    parameter = find_parameter_by_id(instance, parameter_id_value)
    if parameter is None:
        return None, u"Thiếu parameter"
    if not is_length_parameter(parameter):
        return None, u"Parameter không thể ghi hoặc không phải kiểu Length"
    return parameter, None

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------

def type_name_of_instance(instance):
    try:
        return get_element_name(instance.Symbol)
    except Exception:
        return u""


def print_issue_report(issue_rows, total_count):
    if not issue_rows:
        return

    output.print_md(u"## {0}".format(TOOL_TITLE))
    output.print_md(
        u"Đã ghi nhận **{0}** trường hợp cần kiểm tra trên tổng số **{1}** family."
        .format(len(issue_rows), total_count)
    )
    output.print_table(
        table_data=issue_rows[:200],
        columns=[u"Family ID", u"Type", u"Trạng thái", u"Thông tin"],
        formats=["", "", "", ""],
        title=u"Các family chưa cập nhật hoặc cần kiểm tra"
    )
    if len(issue_rows) > 200:
        output.print_md(
            u"Chỉ hiển thị 200/{0} dòng đầu tiên.".format(len(issue_rows))
        )


# -----------------------------------------------------------------------------
# Transaction helper
# -----------------------------------------------------------------------------

def execute_transaction(name, action):
    """Run one DB.Transaction and always close it on error."""
    transaction = DB.Transaction(doc, name)
    transaction.Start()
    try:
        result = action()
        transaction.Commit()
        return result
    except Exception:
        try:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
        except Exception:
            pass
        raise



# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    if doc.IsFamilyDocument:
        forms.alert(
            u"Tool này chỉ chạy trong Project, không chạy trong Family Editor.",
            title=TOOL_TITLE,
            exitscript=True
        )

    picked_instance = pick_family_instance()
    if picked_instance is None:
        return

    family = picked_instance.Symbol.Family
    family_id_value = id_value(family.Id)
    family_name = get_element_name(family)

    type_items = get_family_types(family)
    if not type_items:
        forms.alert(
            u"Không tìm thấy Family Type nào trong family đã chọn.",
            title=TOOL_TITLE,
            exitscript=True
        )

    parameter_items = get_writable_instance_length_parameters(picked_instance)
    if not parameter_items:
        forms.alert(
            u"Family đã chọn không có instance parameter kiểu Length có thể ghi.\n\n"
            u"Hãy tạo một Instance Parameter kiểu Length trong Family/Project Parameter.",
            title=TOOL_TITLE,
            exitscript=True
        )

    xaml_file = script.get_bundle_file("ui.xaml")
    if not xaml_file or not os.path.exists(xaml_file):
        forms.alert(
            u"Không tìm thấy file ui.xaml trong thư mục pushbutton.",
            title=TOOL_TITLE,
            exitscript=True
        )

    window = MainWindow(
        xaml_file,
        family_name,
        type_items,
        parameter_items
    )
    window.ShowDialog()

    if not window.accepted:
        return

    instances = collect_family_instances(
        family_id_value,
        window.selected_type_ids,
        window.scope
    )

    if not instances:
        forms.alert(
            u"Không tìm thấy Family Instance nào phù hợp với type và phạm vi đã chọn.",
            title=TOOL_TITLE,
            exitscript=True
        )

    # Save UI preferences only after valid confirmation.
    config.run_ray_cast = window.run_ray_cast
    config.run_elevation_copy = window.run_elevation_copy
    config.floor_parameter_name = window.floor_parameter_name or u""
    config.elevation_parameter_name = window.elevation_parameter_name or u""
    # Keep the old key for backward compatibility with earlier versions.
    config.parameter_name = window.floor_parameter_name or u""
    config.scope = window.scope
    config.include_linked_floors = window.include_linked_floors
    config.ray_offset_mm = window.ray_offset_mm
    config.max_ray_distance_mm = window.max_ray_distance_mm
    script.save_config()

    ray_offset_internal = None
    max_distance_internal = None
    if window.run_ray_cast:
        ray_offset_internal = mm_to_internal(window.ray_offset_mm)
        max_distance_internal = mm_to_internal(window.max_ray_distance_mm)

    transaction_group = DB.TransactionGroup(doc, TOOL_TITLE)
    transaction_group.Start()

    temp_view = None
    intersector = None
    prepared_rows = []
    issue_rows = []
    ray_updated_count = 0
    elevation_updated_count = 0
    fallback_count = 0

    # Two progress units per family: collect/read, then write.
    total_steps = max(1, len(instances) * 2)
    progress_step = 0

    try:
        if window.run_ray_cast:
            temp_view = execute_transaction(
                u"Tạo view 3D tạm cho ray-cast",
                create_temporary_3d_view
            )

            floor_filter = DB.ElementCategoryFilter(
                DB.BuiltInCategory.OST_Floors
            )
            intersector = DB.ReferenceIntersector(
                floor_filter,
                DB.FindReferenceTarget.Face,
                temp_view
            )
            intersector.FindReferencesInRevitLinks = (
                window.include_linked_floors
            )

        progress_title = u"Đang xử lý Family: {value}/{max_value}"
        with forms.ProgressBar(
            title=progress_title,
            step=1
        ) as progress_bar:
            for instance in instances:
                row = {
                    "instance": instance,
                    "floor_result": None,
                    "elevation_value": None
                }

                if window.run_ray_cast:
                    point = get_instance_point(instance)
                    if point is None:
                        issue_rows.append([
                            str(id_value(instance.Id)),
                            type_name_of_instance(instance),
                            u"Ray-cast: bỏ qua",
                            u"Không xác định được điểm đặt family"
                        ])
                    else:
                        # Move the ray origin slightly downward so a family
                        # origin on the underside of a floor catches both faces.
                        ray_origin = point - DB.XYZ.BasisZ.Multiply(
                            ray_offset_internal
                        )
                        floor_result = find_floor_thickness(
                            intersector,
                            ray_origin,
                            max_distance_internal
                        )
                        if floor_result is None:
                            issue_rows.append([
                                str(id_value(instance.Id)),
                                type_name_of_instance(instance),
                                u"Ray-cast: không tìm thấy sàn",
                                u"Không có Floor phía trên trong {0:.0f} mm"
                                .format(window.max_ray_distance_mm)
                            ])
                        else:
                            row["floor_result"] = floor_result

                if window.run_elevation_copy:
                    elevation_value = get_elevation_from_level(instance)
                    if elevation_value is None:
                        issue_rows.append([
                            str(id_value(instance.Id)),
                            type_name_of_instance(instance),
                            u"Elevation: không đọc được",
                            u"Instance không có giá trị Elevation from Level kiểu Length"
                        ])
                    else:
                        row["elevation_value"] = elevation_value

                prepared_rows.append(row)
                progress_step += 1
                progress_bar.update_progress(progress_step, total_steps)

            try:
                if intersector is not None:
                    intersector.Dispose()
            except Exception:
                pass
            intersector = None

            write_transaction = DB.Transaction(
                doc, u"Ghi dữ liệu Floor và Elevation vào Family"
            )
            write_transaction.Start()
            try:
                for row in prepared_rows:
                    instance = row["instance"]

                    if window.run_ray_cast and row["floor_result"] is not None:
                        parameter, parameter_error = validate_destination_parameter(
                            instance, window.floor_parameter_id
                        )
                        if parameter is None:
                            issue_rows.append([
                                str(id_value(instance.Id)),
                                type_name_of_instance(instance),
                                u"Ray-cast: không ghi được",
                                u"{0}: {1}".format(
                                    window.floor_parameter_name,
                                    parameter_error
                                )
                            ])
                        else:
                            try:
                                parameter.Set(
                                    float(row["floor_result"]["thickness"])
                                )
                                ray_updated_count += 1
                                if row["floor_result"]["method"].startswith(
                                    u"Fallback"
                                ):
                                    fallback_count += 1
                            except Exception as set_error:
                                issue_rows.append([
                                    str(id_value(instance.Id)),
                                    type_name_of_instance(instance),
                                    u"Ray-cast: không ghi được",
                                    u"{0}: {1}".format(
                                        window.floor_parameter_name,
                                        set_error
                                    )
                                ])

                    if (
                        window.run_elevation_copy
                        and row["elevation_value"] is not None
                    ):
                        parameter, parameter_error = validate_destination_parameter(
                            instance, window.elevation_parameter_id
                        )
                        if parameter is None:
                            issue_rows.append([
                                str(id_value(instance.Id)),
                                type_name_of_instance(instance),
                                u"Elevation: không ghi được",
                                u"{0}: {1}".format(
                                    window.elevation_parameter_name,
                                    parameter_error
                                )
                            ])
                        else:
                            try:
                                parameter.Set(float(row["elevation_value"]))
                                elevation_updated_count += 1
                            except Exception as set_error:
                                issue_rows.append([
                                    str(id_value(instance.Id)),
                                    type_name_of_instance(instance),
                                    u"Elevation: không ghi được",
                                    u"{0}: {1}".format(
                                        window.elevation_parameter_name,
                                        set_error
                                    )
                                ])

                    progress_step += 1
                    progress_bar.update_progress(progress_step, total_steps)

                write_transaction.Commit()
            except Exception:
                try:
                    if write_transaction.GetStatus() == DB.TransactionStatus.Started:
                        write_transaction.RollBack()
                except Exception:
                    pass
                raise

        if temp_view is not None:
            def delete_temp_view():
                if doc.GetElement(temp_view.Id) is not None:
                    doc.Delete(temp_view.Id)

            execute_transaction(u"Xóa view 3D tạm", delete_temp_view)
            temp_view = None

        transaction_group.Assimilate()

    except Exception:
        try:
            if intersector is not None:
                intersector.Dispose()
        except Exception:
            pass

        try:
            transaction_group.RollBack()
        except Exception:
            pass

        output.print_md(u"## {0} - Chi tiết lỗi".format(TOOL_TITLE))
        output.print_code(traceback.format_exc())
        forms.alert(
            u"Tool gặp lỗi và đã hoàn tác toàn bộ thay đổi.\n"
            u"Xem cửa sổ pyRevit Output để biết chi tiết.",
            title=TOOL_TITLE,
            warn_icon=True,
            exitscript=True
        )

    print_issue_report(issue_rows, len(instances))

    message_lines = [
        u"Đã xử lý: {0} family".format(len(instances))
    ]

    if window.run_ray_cast:
        message_lines.append(
            u"Ray-cast: {0}/{1} family → {2}".format(
                ray_updated_count,
                len(instances),
                window.floor_parameter_name
            )
        )
        if fallback_count > 0:
            message_lines.append(
                u"Dùng chiều dày Floor Type dự phòng: {0} family".format(
                    fallback_count
                )
            )

    if window.run_elevation_copy:
        message_lines.append(
            u"Elevation from Level: {0}/{1} family → {2}".format(
                elevation_updated_count,
                len(instances),
                window.elevation_parameter_name
            )
        )

    if issue_rows:
        message_lines.append(
            u"Cần kiểm tra: {0} trường hợp (xem pyRevit Output)".format(
                len(issue_rows)
            )
        )

    forms.alert(u"\n".join(message_lines), title=TOOL_TITLE)


if __name__ == "__main__":
    try:
        main()
    except UI.Exceptions.OperationCanceledException:
        pass
