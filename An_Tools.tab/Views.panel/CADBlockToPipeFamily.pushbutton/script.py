# -*- coding: utf-8 -*-
"""
CAD Block -> Family + Pipe Parameter Mapping
--------------------------------------------
pyRevit / IronPython 2.7
Designed for Revit 2020-2026.

Workflow:
1. Pick a visible line/point belonging to one CAD block inside an ImportInstance.
2. Find the nearest nested GeometryInstance and build its geometry signature.
3. Find all CAD block instances with the same signature inside the selected CAD import.
4. Let the user choose a level-based Family Type.
5. Let the user map:
      Pipe elevation source -> Family elevation target
      Pipe diameter source  -> Family diameter target
6. Cast a vertical +Z ray from every CAD block position.
7. Place the Family and assign mapped values.

Important:
- The selected Family should be a non-hosted, level-based model family.
- Family target parameters must be writable INSTANCE parameters.
- The ray searches Pipes in the current Revit model only, not linked models.
"""

import math
import traceback

from pyrevit import revit, DB, forms, script
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Windows import Visibility


doc = revit.doc
uidoc = revit.uidoc
active_view = revit.active_view
output = script.get_output()


# -----------------------------------------------------------------------------
# USER-EDITABLE DEFAULTS
# -----------------------------------------------------------------------------
MM_PER_FOOT = 304.8
DEFAULT_MAX_RAY_HEIGHT_MM = 30000.0
DEFAULT_DUPLICATE_TOLERANCE_MM = 2.0
DEFAULT_PIPE_SEARCH_TOLERANCE_MM = 100.0
GEOMETRY_ELEVATION_LABEL = (
    "<Tim ống tại XY đặt Family - khuyên dùng cho ống dốc>"
)
PIPE_SIZE_BUILTIN_LABEL = (
    "<Pipe Size | Built-in | lấy đường kính danh nghĩa>"
)
PIPE_DIAMETER_BUILTIN_LABEL = (
    "<Pipe Diameter | Built-in RBS_PIPE_DIAMETER_PARAM>"
)

# False: skip CAD positions where no Pipe is hit.
# True : place Family anyway, but do not write Pipe values.
PLACE_FAMILY_WHEN_NO_PIPE = False


# -----------------------------------------------------------------------------
# BASIC HELPERS
# -----------------------------------------------------------------------------
try:
    text_type = unicode
except NameError:
    text_type = str


def to_text(value):
    try:
        return text_type(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return ""


def eid_value(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.IntegerValue)
    except Exception:
        try:
            return int(element_id.Value)
        except Exception:
            try:
                return int(element_id)
            except Exception:
                return None


def mm_to_internal(mm_value):
    return float(mm_value) / MM_PER_FOOT


def internal_to_mm(internal_value):
    return float(internal_value) * MM_PER_FOOT


def xyz_xy_distance(point_a, point_b):
    dx = point_a.X - point_b.X
    dy = point_a.Y - point_b.Y
    return math.sqrt(dx * dx + dy * dy)


def vector_xy_length(vector):
    return math.sqrt(vector.X * vector.X + vector.Y * vector.Y)


def normalize_xy(vector):
    length = vector_xy_length(vector)
    if length <= 1e-12:
        return None
    return DB.XYZ(vector.X / length, vector.Y / length, 0.0)


def signed_xy_angle(vector_from, vector_to):
    """Signed angle from vector_from to vector_to around global +Z."""
    source = normalize_xy(vector_from)
    target = normalize_xy(vector_to)
    if source is None or target is None:
        return None
    cross_z = source.X * target.Y - source.Y * target.X
    dot = source.X * target.X + source.Y * target.Y
    return math.atan2(cross_z, dot)


def normalize_parallel_angle(angle):
    """Return the smallest rotation that makes two axes parallel."""
    if angle is None:
        return None
    half_pi = math.pi * 0.5
    while angle > half_pi:
        angle -= math.pi
    while angle < -half_pi:
        angle += math.pi
    return angle


def element_type_name(symbol):
    try:
        p = symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            name = p.AsString()
            if name:
                return name
    except Exception:
        pass
    try:
        return symbol.Name
    except Exception:
        return "Type {0}".format(eid_value(symbol.Id))


def category_name(element):
    try:
        if element.Category:
            return element.Category.Name
    except Exception:
        pass
    return "No Category"


def get_view_level(view):
    try:
        level = view.GenLevel
        if level:
            return level
    except Exception:
        pass

    levels = list(DB.FilteredElementCollector(doc)
                  .OfClass(DB.Level)
                  .WhereElementIsNotElementType())
    levels.sort(key=lambda x: x.Elevation)
    if not levels:
        return None

    option_map = {}
    options = []
    for level in levels:
        label = "{0} | Elev. {1:.1f} mm".format(
            level.Name,
            internal_to_mm(level.Elevation)
        )
        option_map[label] = level
        options.append(label)

    selected = forms.SelectFromList.show(
        options,
        title="Chọn Level đặt Family",
        button_name="Chọn Level",
        multiselect=False
    )
    if not selected:
        return None
    return option_map[selected]


# -----------------------------------------------------------------------------
# CAD IMPORT SELECTION
# -----------------------------------------------------------------------------
class ImportInstanceSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.ImportInstance)

    def AllowReference(self, reference, position):
        try:
            element = doc.GetElement(reference.ElementId)
            return isinstance(element, DB.ImportInstance)
        except Exception:
            return False


def pick_cad_symbol_point():
    """Pick a point/edge belonging to a nested CAD block."""
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.PointOnElement,
            ImportInstanceSelectionFilter(),
            "Pick một đường/điểm thuộc CAD block mẫu"
        )
    except OperationCanceledException:
        return None, None

    import_instance = doc.GetElement(reference.ElementId)
    picked_point = None
    try:
        picked_point = reference.GlobalPoint
    except Exception:
        picked_point = None

    if picked_point is None:
        try:
            picked_point = uidoc.Selection.PickPoint(
                "Click gần tâm CAD block mẫu"
            )
        except OperationCanceledException:
            return None, None

    return import_instance, picked_point


# -----------------------------------------------------------------------------
# GEOMETRY SIGNATURE
# -----------------------------------------------------------------------------
def add_point(points, transform, point):
    try:
        points.append(transform.OfPoint(point))
    except Exception:
        try:
            points.append(point)
        except Exception:
            pass


def add_bbox_points(points, transform, bbox):
    if bbox is None:
        return
    try:
        xs = [bbox.Min.X, bbox.Max.X]
        ys = [bbox.Min.Y, bbox.Max.Y]
        zs = [bbox.Min.Z, bbox.Max.Z]
        for x in xs:
            for y in ys:
                for z in zs:
                    add_point(points, transform, DB.XYZ(x, y, z))
    except Exception:
        pass


def collect_geometry_stats(geometry, transform, stats, points):
    if geometry is None:
        return

    for obj in geometry:
        if obj is None:
            continue

        type_name = "Unknown"
        try:
            type_name = obj.GetType().Name
        except Exception:
            pass

        stats["types"][type_name] = stats["types"].get(type_name, 0) + 1

        try:
            style_id = eid_value(obj.GraphicsStyleId)
            if style_id is not None:
                stats["styles"].add(style_id)
        except Exception:
            pass

        if isinstance(obj, DB.GeometryInstance):
            try:
                child_transform = transform.Multiply(obj.Transform)
            except Exception:
                child_transform = transform
            try:
                child_geometry = obj.GetSymbolGeometry()
                collect_geometry_stats(
                    child_geometry,
                    child_transform,
                    stats,
                    points
                )
            except Exception:
                pass
            continue

        if isinstance(obj, DB.PolyLine):
            try:
                coords = obj.GetCoordinates()
                stats["polyline_points"] += len(coords)
                previous = None
                for point in coords:
                    add_point(points, transform, point)
                    if previous is not None:
                        try:
                            stats["curve_length"] += previous.DistanceTo(point)
                        except Exception:
                            pass
                    previous = point
            except Exception:
                pass
            continue

        if isinstance(obj, DB.Curve):
            try:
                stats["curve_length"] += obj.Length
            except Exception:
                pass
            try:
                tess_points = obj.Tessellate()
                stats["curve_points"] += len(tess_points)
                for point in tess_points:
                    add_point(points, transform, point)
            except Exception:
                try:
                    add_point(points, transform, obj.GetEndPoint(0))
                    add_point(points, transform, obj.GetEndPoint(1))
                except Exception:
                    pass
            continue

        if isinstance(obj, DB.Solid):
            try:
                stats["solid_volume"] += obj.Volume
            except Exception:
                pass
            try:
                stats["solid_faces"] += obj.Faces.Size
            except Exception:
                pass
            try:
                stats["solid_edges"] += obj.Edges.Size
                for edge in obj.Edges:
                    try:
                        edge_curve = edge.AsCurve()
                        for point in edge_curve.Tessellate():
                            add_point(points, transform, point)
                    except Exception:
                        pass
            except Exception:
                pass
            continue

        if isinstance(obj, DB.Mesh):
            try:
                stats["mesh_triangles"] += obj.NumTriangles
            except Exception:
                pass
            try:
                for vertex in obj.Vertices:
                    add_point(points, transform, vertex)
            except Exception:
                pass
            continue

        try:
            if isinstance(obj, DB.Point):
                add_point(points, transform, obj.Coord)
                continue
        except Exception:
            pass

        try:
            add_bbox_points(points, transform, obj.GetBoundingBox())
        except Exception:
            pass


def points_bbox(points):
    if not points:
        zero = DB.XYZ(0, 0, 0)
        return zero, zero

    min_x = min([p.X for p in points])
    min_y = min([p.Y for p in points])
    min_z = min([p.Z for p in points])
    max_x = max([p.X for p in points])
    max_y = max([p.Y for p in points])
    max_z = max([p.Z for p in points])

    return DB.XYZ(min_x, min_y, min_z), DB.XYZ(max_x, max_y, max_z)


def geometry_instance_identifier(geometry_instance):
    """
    Revit 2023+ can expose SymbolGeometryId. It is a more exact way to
    determine whether two GeometryInstances use the same symbol geometry.
    Older versions fall back to the geometry signature below.
    """
    try:
        symbol_geometry_id = geometry_instance.GetSymbolGeometryId()
        try:
            return to_text(symbol_geometry_id.AsUniqueIdentifier())
        except Exception:
            try:
                return to_text(symbol_geometry_id.AsIdentifier(False))
            except Exception:
                return to_text(symbol_geometry_id.AsIdentifier(True))
    except Exception:
        return None


def analyze_symbol_geometry(geometry_instance, cache):
    """Return a transform-independent signature and local bounding box."""
    symbol_geometry = geometry_instance.GetSymbolGeometry()
    exact_identifier = geometry_instance_identifier(geometry_instance)

    cache_key = None
    if exact_identifier:
        cache_key = ("SYMBOL_GEOMETRY_ID", exact_identifier)
    else:
        try:
            cache_key = ("GEOMETRY_HASH", int(symbol_geometry.GetHashCode()))
        except Exception:
            cache_key = None

    if cache_key is not None and cache_key in cache:
        return cache[cache_key]

    stats = {
        "types": {},
        "styles": set(),
        "curve_length": 0.0,
        "curve_points": 0,
        "polyline_points": 0,
        "solid_volume": 0.0,
        "solid_faces": 0,
        "solid_edges": 0,
        "mesh_triangles": 0,
    }
    points = []

    collect_geometry_stats(
        symbol_geometry,
        DB.Transform.Identity,
        stats,
        points
    )

    bbox_min, bbox_max = points_bbox(points)
    size_x = bbox_max.X - bbox_min.X
    size_y = bbox_max.Y - bbox_min.Y
    size_z = bbox_max.Z - bbox_min.Z

    signature = (
        round(internal_to_mm(size_x), 1),
        round(internal_to_mm(size_y), 1),
        round(internal_to_mm(size_z), 1),
        round(internal_to_mm(stats["curve_length"]), 1),
        round(stats["solid_volume"], 8),
        stats["curve_points"],
        stats["polyline_points"],
        stats["solid_faces"],
        stats["solid_edges"],
        stats["mesh_triangles"],
        tuple(sorted(stats["types"].items())),
        tuple(sorted(stats["styles"])),
    )

    result = {
        "signature": signature,
        "exact_identifier": exact_identifier,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
    }

    if cache_key is not None:
        cache[cache_key] = result
    return result


def bbox_corners(bbox_min, bbox_max):
    corners = []
    for x in [bbox_min.X, bbox_max.X]:
        for y in [bbox_min.Y, bbox_max.Y]:
            for z in [bbox_min.Z, bbox_max.Z]:
                corners.append(DB.XYZ(x, y, z))
    return corners


def transform_bbox(bbox_min, bbox_max, transform):
    transformed = []
    for point in bbox_corners(bbox_min, bbox_max):
        transformed.append(transform.OfPoint(point))
    return points_bbox(transformed)


def bbox_center(bbox_min, bbox_max):
    return DB.XYZ(
        (bbox_min.X + bbox_max.X) * 0.5,
        (bbox_min.Y + bbox_max.Y) * 0.5,
        (bbox_min.Z + bbox_max.Z) * 0.5
    )


def point_to_bbox_xy_distance(point, bbox_min, bbox_max):
    if point.X < bbox_min.X:
        dx = bbox_min.X - point.X
    elif point.X > bbox_max.X:
        dx = point.X - bbox_max.X
    else:
        dx = 0.0

    if point.Y < bbox_min.Y:
        dy = bbox_min.Y - point.Y
    elif point.Y > bbox_max.Y:
        dy = point.Y - bbox_max.Y
    else:
        dy = 0.0

    return math.sqrt(dx * dx + dy * dy)


def bbox_to_bbox_xy_distance(min_a, max_a, min_b, max_b):
    """Minimum plan distance between two axis-aligned bounding boxes."""
    if max_a.X < min_b.X:
        dx = min_b.X - max_a.X
    elif max_b.X < min_a.X:
        dx = min_a.X - max_b.X
    else:
        dx = 0.0

    if max_a.Y < min_b.Y:
        dy = min_b.Y - max_a.Y
    elif max_b.Y < min_a.Y:
        dy = min_a.Y - max_b.Y
    else:
        dy = 0.0

    return math.sqrt(dx * dx + dy * dy)


def collect_cad_block_candidates(import_instance, view):
    options = DB.Options()
    options.IncludeNonVisibleObjects = False
    options.ComputeReferences = False
    try:
        options.View = view
    except Exception:
        pass

    geometry = import_instance.get_Geometry(options)
    if geometry is None:
        return []

    raw_instances = []

    def walk(current_geometry, parent_transform, depth):
        for obj in current_geometry:
            if not isinstance(obj, DB.GeometryInstance):
                continue

            try:
                total_transform = parent_transform.Multiply(obj.Transform)
            except Exception:
                total_transform = parent_transform

            raw_instances.append({
                "geometry_instance": obj,
                "transform": total_transform,
                "depth": depth,
            })

            try:
                walk(obj.GetSymbolGeometry(), total_transform, depth + 1)
            except Exception:
                pass

    walk(geometry, DB.Transform.Identity, 0)

    if not raw_instances:
        return []

    depth_zero = [x for x in raw_instances if x["depth"] == 0]
    depth_one = [x for x in raw_instances if x["depth"] == 1]

    # Typical DWG structure: one root GeometryInstance at depth 0,
    # and actual block references at depth 1.
    if len(depth_zero) == 1 and depth_one:
        selected_depth = 1
    else:
        selected_depth = min([x["depth"] for x in raw_instances])

    selected_instances = [
        x for x in raw_instances if x["depth"] == selected_depth
    ]

    cache = {}
    candidates = []

    for item in selected_instances:
        try:
            analyzed = analyze_symbol_geometry(
                item["geometry_instance"],
                cache
            )
            model_bbox_min, model_bbox_max = transform_bbox(
                analyzed["bbox_min"],
                analyzed["bbox_max"],
                item["transform"]
            )
            center = bbox_center(model_bbox_min, model_bbox_max)
            origin = item["transform"].Origin
            basis_x = item["transform"].BasisX
            rotation = math.atan2(basis_x.Y, basis_x.X)

            if analyzed.get("exact_identifier"):
                match_key = (
                    "SYMBOL_GEOMETRY_ID",
                    analyzed["exact_identifier"]
                )
            else:
                match_key = ("GEOMETRY_SIGNATURE", analyzed["signature"])

            candidates.append({
                "signature": analyzed["signature"],
                "match_key": match_key,
                "origin": origin,
                "center": center,
                "bbox_min": model_bbox_min,
                "bbox_max": model_bbox_max,
                "rotation": rotation,
                "depth": item["depth"],
            })
        except Exception:
            pass

    return candidates


def find_nearest_candidate(candidates, picked_point):
    if not candidates:
        return None

    def key_function(candidate):
        bbox_distance = point_to_bbox_xy_distance(
            picked_point,
            candidate["bbox_min"],
            candidate["bbox_max"]
        )
        center_distance = xyz_xy_distance(
            picked_point,
            candidate["center"]
        )
        bbox_size = (
            (candidate["bbox_max"].X - candidate["bbox_min"].X) *
            (candidate["bbox_max"].Y - candidate["bbox_min"].Y)
        )
        return bbox_distance, center_distance, bbox_size

    return sorted(candidates, key=key_function)[0]


# -----------------------------------------------------------------------------
# FAMILY AND PARAMETER SELECTION
# -----------------------------------------------------------------------------
def collect_family_symbols():
    symbols = list(DB.FilteredElementCollector(doc)
                   .OfClass(DB.FamilySymbol)
                   .WhereElementIsElementType())

    option_map = {}
    options = []

    for symbol in symbols:
        try:
            family_name = symbol.Family.Name
        except Exception:
            family_name = "Family"

        label = "[{0}] {1} : {2} | ID {3}".format(
            category_name(symbol),
            family_name,
            element_type_name(symbol),
            eid_value(symbol.Id)
        )
        option_map[label] = symbol
        options.append(label)

    options.sort()
    return options, option_map


def parameter_spec_label(parameter):
    try:
        definition = parameter.Definition
        if hasattr(definition, "GetDataType"):
            data_type = definition.GetDataType()
            try:
                label = DB.LabelUtils.GetLabelForSpec(data_type)
                if label:
                    return label
            except Exception:
                return to_text(data_type.TypeId)
        else:
            return to_text(definition.ParameterType)
    except Exception:
        return "Double"


def parameter_id(parameter):
    try:
        return eid_value(parameter.Id)
    except Exception:
        return None


def make_parameter_descriptor(parameter):
    name = "Unnamed"
    try:
        name = parameter.Definition.Name
    except Exception:
        pass

    pid = parameter_id(parameter)
    spec = parameter_spec_label(parameter)
    display = "{0} | {1} | ID {2}".format(name, spec, pid)

    return {
        "name": name,
        "id": pid,
        "spec": spec,
        "display": display,
    }


def is_length_parameter(parameter):
    try:
        definition = parameter.Definition
        if hasattr(definition, "GetDataType"):
            data_type = definition.GetDataType()
            try:
                return data_type == DB.SpecTypeId.Length
            except Exception:
                type_id = to_text(data_type.TypeId).lower()
                return "length" in type_id
        try:
            return definition.ParameterType == DB.ParameterType.Length
        except Exception:
            return True
    except Exception:
        return False


def collect_double_parameter_descriptors(element, writable_only, length_only=True):
    descriptors = []
    seen = set()

    for parameter in element.Parameters:
        try:
            if parameter.StorageType != DB.StorageType.Double:
                continue
            if writable_only and parameter.IsReadOnly:
                continue
            if length_only and not is_length_parameter(parameter):
                continue

            descriptor = make_parameter_descriptor(parameter)
            unique_key = (descriptor["id"], descriptor["name"])
            if unique_key in seen:
                continue
            seen.add(unique_key)
            descriptors.append(descriptor)
        except Exception:
            pass

    return descriptors


def find_parameter_by_descriptor(element, descriptor):
    for parameter in element.Parameters:
        try:
            if parameter.StorageType != DB.StorageType.Double:
                continue

            pid = parameter_id(parameter)
            name = parameter.Definition.Name

            if descriptor["id"] is not None and pid == descriptor["id"]:
                return parameter
            if descriptor["id"] is None and name == descriptor["name"]:
                return parameter
        except Exception:
            pass

    # Fallback by name, useful when a custom parameter ID differs unexpectedly.
    try:
        parameter = element.LookupParameter(descriptor["name"])
        if parameter and parameter.StorageType == DB.StorageType.Double:
            return parameter
    except Exception:
        pass

    return None


def score_descriptor(descriptor, preferred_words, preferred_id=None):
    if preferred_id is not None and descriptor["id"] == preferred_id:
        return -1000

    name = descriptor["name"].lower()
    for index, word in enumerate(preferred_words):
        word_lower = word.lower()
        if name == word_lower:
            return index
        if word_lower in name:
            return 20 + index
    return 100


def order_descriptors(descriptors, preferred_words, preferred_id=None):
    return sorted(
        descriptors,
        key=lambda x: (
            score_descriptor(x, preferred_words, preferred_id),
            x["display"]
        )
    )


def choose_parameter_descriptor(descriptors, title, button_name,
                                preferred_words=None, preferred_id=None):
    preferred_words = preferred_words or []
    ordered = order_descriptors(
        descriptors,
        preferred_words,
        preferred_id
    )

    display_map = {}
    options = []
    for descriptor in ordered:
        display_map[descriptor["display"]] = descriptor
        options.append(descriptor["display"])

    selected = forms.SelectFromList.show(
        options,
        title=title,
        button_name=button_name,
        multiselect=False
    )
    if not selected:
        return None
    return display_map[selected]


def first_existing_instance(symbol):
    for instance in DB.FilteredElementCollector(doc) \
            .OfClass(DB.FamilyInstance) \
            .WhereElementIsNotElementType():
        try:
            if instance.Symbol.Id == symbol.Id:
                return instance
        except Exception:
            pass
    return None


def preview_family_instance_parameters(symbol, level, sample_point):
    existing = first_existing_instance(symbol)
    if existing:
        return collect_double_parameter_descriptors(existing, True)

    transaction = DB.Transaction(doc, "Preview Family parameters")
    transaction.Start()
    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()

        preview_point = DB.XYZ(
            sample_point.X,
            sample_point.Y,
            level.Elevation
        )
        instance = doc.Create.NewFamilyInstance(
            preview_point,
            symbol,
            level,
            DB.Structure.StructuralType.NonStructural
        )
        doc.Regenerate()
        descriptors = collect_double_parameter_descriptors(instance, True)
        transaction.RollBack()
        return descriptors
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return []


def collect_pipe_parameter_descriptors(sample_pipe):
    return collect_double_parameter_descriptors(sample_pipe, False)


def make_virtual_pipe_descriptor(name, display, source_kind):
    """Descriptor for Pipe values not exposed as writable Double parameters.

    Pipe Size is commonly a calculated String parameter.  Family Nominal
    Diameter, however, requires a Double length value.  These virtual sources
    keep Size visible in the UI while resolving it to the Pipe's numeric
    nominal diameter when values are assigned.
    """
    return {
        "name": name,
        "id": source_kind,
        "spec": "Length",
        "display": display,
        "source_kind": source_kind,
    }


def pipe_diameter_builtin_descriptor():
    return make_virtual_pipe_descriptor(
        "Pipe Diameter",
        PIPE_DIAMETER_BUILTIN_LABEL,
        "PIPE_DIAMETER_BUILTIN"
    )


def pipe_size_builtin_descriptor():
    return make_virtual_pipe_descriptor(
        "Pipe Size",
        PIPE_SIZE_BUILTIN_LABEL,
        "PIPE_SIZE_BUILTIN"
    )


# -----------------------------------------------------------------------------
# USER INTERFACE
# -----------------------------------------------------------------------------
def set_combo_items(combo, labels, preferred=None):
    combo.Items.Clear()
    for label in labels:
        combo.Items.Add(label)
    if not labels:
        combo.SelectedIndex = -1
        return
    if preferred and preferred in labels:
        combo.SelectedItem = preferred
    else:
        combo.SelectedIndex = 0


def descriptor_config_key(descriptor):
    if descriptor is None:
        return ""
    return "{0}|{1}".format(descriptor.get("id"), descriptor.get("name"))


def find_descriptor_by_config(descriptors, stored_key):
    if not stored_key:
        return None
    for descriptor in descriptors:
        if descriptor_config_key(descriptor) == stored_key:
            return descriptor
    return None


class CADPipeFamilyWindow(forms.WPFWindow):
    def __init__(self, xaml_file, family_labels, family_map, levels,
                 sample_point, sample_pipe, block_count, import_instance,
                 config):
        forms.WPFWindow.__init__(self, xaml_file)
        self.family_labels_all = family_labels
        self.family_map = family_map
        self.levels = levels
        self.sample_point = sample_point
        self.sample_pipe = sample_pipe
        self.config = config
        self.result = None
        self.family_parameter_map = {}
        self.pipe_parameter_map = {}
        self.level_map = {}

        self.cad_summary_text.Text = (
            "CAD Import ID {0} | Nhận diện {1} block cùng loại".format(
                eid_value(import_instance.Id), block_count
            )
        )

        self.family_filter_box.TextChanged += self.family_filter_changed
        self.family_combo.SelectionChanged += self.family_changed
        self.level_combo.SelectionChanged += self.level_changed
        self.run_button.Click += self.run_clicked
        self.cancel_button.Click += self.cancel_clicked

        self._populate_levels()
        self._populate_placement_modes()
        self._populate_pipe_parameters()
        self._filter_family_list()
        self._load_basic_settings()

    def _config_value(self, name, default=None):
        try:
            return getattr(self.config, name)
        except Exception:
            return default

    def _populate_levels(self):
        labels = []
        for level in self.levels:
            label = "{0} | Elev. {1:.1f} mm | ID {2}".format(
                level.Name,
                internal_to_mm(level.Elevation),
                eid_value(level.Id)
            )
            self.level_map[label] = level
            labels.append(label)

        active_level = None
        try:
            active_level = active_view.GenLevel
        except Exception:
            active_level = None
        preferred = None
        saved_level_id = self._config_value("level_id", None)
        for label, level in self.level_map.items():
            if saved_level_id is not None and eid_value(level.Id) == saved_level_id:
                preferred = label
                break
            if preferred is None and active_level and level.Id == active_level.Id:
                preferred = label
        set_combo_items(self.level_combo, labels, preferred)

    def _populate_placement_modes(self):
        modes = [
            "Điểm chèn của CAD Block",
            "Tâm hình học của CAD Block",
        ]
        saved = self._config_value(
            "placement_mode", "Điểm chèn của CAD Block"
        )
        set_combo_items(self.placement_combo, modes, saved)

    def _populate_pipe_parameters(self):
        descriptors = collect_pipe_parameter_descriptors(self.sample_pipe)
        ordered_elevation = order_descriptors(
            descriptors,
            ["Middle Elevation", "Center Elevation", "Offset", "Elevation"]
        )
        elevation_labels = [GEOMETRY_ELEVATION_LABEL]
        for descriptor in ordered_elevation:
            elevation_labels.append(descriptor["display"])
            self.pipe_parameter_map[descriptor["display"]] = descriptor

        saved_elevation = self._config_value(
            "pipe_elevation_source", GEOMETRY_ELEVATION_LABEL
        )
        set_combo_items(
            self.pipe_elevation_combo,
            elevation_labels,
            saved_elevation
        )

        # Pipe Size is normally a calculated String parameter and therefore
        # was excluded by collect_double_parameter_descriptors().  Add explicit
        # built-in Size/Diameter sources before the generic Double parameters.
        size_descriptor = pipe_size_builtin_descriptor()
        diameter_descriptor = pipe_diameter_builtin_descriptor()
        diameter_labels = [
            size_descriptor["display"],
            diameter_descriptor["display"],
        ]
        self.pipe_parameter_map[size_descriptor["display"]] = size_descriptor
        self.pipe_parameter_map[diameter_descriptor["display"]] = (
            diameter_descriptor
        )

        try:
            diameter_bip_id = int(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        except Exception:
            diameter_bip_id = None
        ordered_diameter = order_descriptors(
            descriptors,
            ["Diameter", "Nominal Diameter", "Size"],
            diameter_bip_id
        )
        for descriptor in ordered_diameter:
            # Avoid showing the same built-in Diameter twice.
            if (diameter_bip_id is not None and
                    descriptor.get("id") == diameter_bip_id):
                continue
            label = descriptor["display"]
            if label in self.pipe_parameter_map:
                continue
            diameter_labels.append(label)
            self.pipe_parameter_map[label] = descriptor

        saved_diameter = self._config_value(
            "pipe_diameter_source", PIPE_SIZE_BUILTIN_LABEL
        )
        set_combo_items(
            self.pipe_diameter_combo,
            diameter_labels,
            saved_diameter
        )

    def _load_basic_settings(self):
        self.max_ray_box.Text = to_text(self._config_value(
            "max_ray_height_mm", DEFAULT_MAX_RAY_HEIGHT_MM
        ))
        self.duplicate_tolerance_box.Text = to_text(self._config_value(
            "duplicate_tolerance_mm", DEFAULT_DUPLICATE_TOLERANCE_MM
        ))
        self.pipe_search_tolerance_box.Text = to_text(self._config_value(
            "pipe_search_tolerance_mm", DEFAULT_PIPE_SEARCH_TOLERANCE_MM
        ))
        self.align_connector_checkbox.IsChecked = bool(self._config_value(
            "align_connector", True
        ))
        self.skip_duplicates_checkbox.IsChecked = bool(self._config_value(
            "skip_duplicates", True
        ))

    def _filter_family_list(self):
        search_text = to_text(self.family_filter_box.Text).strip().lower()
        filtered = []
        for label in self.family_labels_all:
            if not search_text or search_text in label.lower():
                filtered.append(label)

        preferred = None
        saved_symbol_id = self._config_value("family_symbol_id", None)
        for label in filtered:
            symbol = self.family_map[label]
            if saved_symbol_id is not None and eid_value(symbol.Id) == saved_symbol_id:
                preferred = label
                break
        set_combo_items(self.family_combo, filtered, preferred)
        self.family_count_text.Text = "{0} Family Type".format(len(filtered))

    def family_filter_changed(self, sender, args):
        self._filter_family_list()

    def level_changed(self, sender, args):
        self._refresh_family_parameters()

    def family_changed(self, sender, args):
        self._refresh_family_parameters()

    def _selected_level(self):
        label = to_text(self.level_combo.SelectedItem)
        return self.level_map.get(label)

    def _selected_symbol(self):
        label = to_text(self.family_combo.SelectedItem)
        return self.family_map.get(label)

    def _refresh_family_parameters(self):
        symbol = self._selected_symbol()
        level = self._selected_level()
        if symbol is None or level is None:
            set_combo_items(self.family_elevation_combo, [])
            set_combo_items(self.family_diameter_combo, [])
            return

        self.status_text.Text = "Đang đọc Instance Parameter của Family..."
        try:
            descriptors = preview_family_instance_parameters(
                symbol, level, self.sample_point
            )
        except Exception as error:
            descriptors = []
            self.status_text.Text = "Không đọc được Family: {0}".format(
                to_text(error)
            )

        self.family_parameter_map = {}
        labels = []
        for descriptor in descriptors:
            labels.append(descriptor["display"])
            self.family_parameter_map[descriptor["display"]] = descriptor

        elevation_order = order_descriptors(
            descriptors,
            ["Elevation from Level", "Elevation", "Offset", "Middle Elevation"]
        )
        diameter_order = order_descriptors(
            descriptors,
            ["Nominal Diameter", "Diameter", "Size"]
        )
        elevation_labels = [x["display"] for x in elevation_order]
        diameter_labels = [x["display"] for x in diameter_order]

        saved_elev_key = self._config_value("family_elevation_target", "")
        saved_dia_key = self._config_value("family_diameter_target", "")
        saved_elev = find_descriptor_by_config(descriptors, saved_elev_key)
        saved_dia = find_descriptor_by_config(descriptors, saved_dia_key)

        set_combo_items(
            self.family_elevation_combo,
            elevation_labels,
            saved_elev["display"] if saved_elev else None
        )
        set_combo_items(
            self.family_diameter_combo,
            diameter_labels,
            saved_dia["display"] if saved_dia else None
        )

        if descriptors:
            self.status_text.Text = (
                "Đã lọc {0} writable Instance Parameter kiểu Length.".format(
                    len(descriptors)
                )
            )
        else:
            self.status_text.Text = (
                "Family không đặt thử được hoặc không có writable "
                "Instance Parameter kiểu Length."
            )

    def _parse_positive_number(self, textbox, field_name, allow_zero=False):
        raw = to_text(textbox.Text).strip().replace(",", ".")
        try:
            value = float(raw)
        except Exception:
            raise Exception("{0} không hợp lệ.".format(field_name))
        if allow_zero:
            if value < 0:
                raise Exception("{0} phải lớn hơn hoặc bằng 0.".format(field_name))
        elif value <= 0:
            raise Exception("{0} phải lớn hơn 0.".format(field_name))
        return value

    def run_clicked(self, sender, args):
        try:
            level = self._selected_level()
            symbol = self._selected_symbol()
            if level is None:
                raise Exception("Chưa chọn Level.")
            if symbol is None:
                raise Exception("Chưa chọn Family Type.")

            family_elev_label = to_text(
                self.family_elevation_combo.SelectedItem
            )
            family_dia_label = to_text(
                self.family_diameter_combo.SelectedItem
            )
            family_elev = self.family_parameter_map.get(family_elev_label)
            family_dia = self.family_parameter_map.get(family_dia_label)
            if family_elev is None or family_dia is None:
                raise Exception("Chưa chọn đủ Family parameter đích.")
            if descriptor_config_key(family_elev) == descriptor_config_key(family_dia):
                raise Exception(
                    "Parameter cao độ và đường kính không được trùng nhau."
                )

            pipe_elev_label = to_text(
                self.pipe_elevation_combo.SelectedItem
            )
            if pipe_elev_label == GEOMETRY_ELEVATION_LABEL:
                pipe_elev = "GEOMETRIC_CENTERLINE"
            else:
                pipe_elev = self.pipe_parameter_map.get(pipe_elev_label)
            pipe_dia_label = to_text(
                self.pipe_diameter_combo.SelectedItem
            )
            pipe_dia = self.pipe_parameter_map.get(pipe_dia_label)
            if pipe_elev is None or pipe_dia is None:
                raise Exception("Chưa chọn đủ nguồn parameter của Pipe.")

            placement_mode = to_text(self.placement_combo.SelectedItem)
            placement_key = (
                "center" if placement_mode == "Tâm hình học của CAD Block"
                else "origin"
            )
            max_ray_mm = self._parse_positive_number(
                self.max_ray_box, "Chiều cao ray"
            )
            duplicate_tolerance_mm = self._parse_positive_number(
                self.duplicate_tolerance_box,
                "Tolerance kiểm tra trùng",
                allow_zero=True
            )
            pipe_search_tolerance_mm = self._parse_positive_number(
                self.pipe_search_tolerance_box,
                "Tolerance tìm Pipe theo mặt bằng",
                allow_zero=True
            )

            self.result = {
                "level": level,
                "symbol": symbol,
                "placement_mode": placement_mode,
                "placement_key": placement_key,
                "family_elevation_target": family_elev,
                "family_diameter_target": family_dia,
                "elevation_source": pipe_elev,
                "diameter_source": pipe_dia,
                "pipe_elevation_label": pipe_elev_label,
                "pipe_diameter_label": pipe_dia_label,
                "max_ray_height_mm": max_ray_mm,
                "duplicate_tolerance_mm": duplicate_tolerance_mm,
                "pipe_search_tolerance_mm": pipe_search_tolerance_mm,
                "align_connector": bool(
                    self.align_connector_checkbox.IsChecked
                ),
                "skip_duplicates": bool(
                    self.skip_duplicates_checkbox.IsChecked
                ),
            }
            self.Close()
        except Exception as error:
            self.status_text.Text = to_text(error)

    def cancel_clicked(self, sender, args):
        self.result = None
        self.Close()


# -----------------------------------------------------------------------------
# TEMPORARY 3D VIEW AND RAY CASTING
# -----------------------------------------------------------------------------
def create_temporary_3d_view():
    view_family_type = None
    for item in DB.FilteredElementCollector(doc) \
            .OfClass(DB.ViewFamilyType) \
            .WhereElementIsElementType():
        try:
            if item.ViewFamily == DB.ViewFamily.ThreeDimensional:
                view_family_type = item
                break
        except Exception:
            pass

    if view_family_type is None:
        return None

    transaction = DB.Transaction(doc, "Create temporary raycast view")
    transaction.Start()
    try:
        view_3d = DB.View3D.CreateIsometric(doc, view_family_type.Id)
        try:
            view_3d.Name = "_pyRevit_CAD_Pipe_Raycast_{0}".format(
                eid_value(view_3d.Id)
            )
        except Exception:
            pass
        transaction.Commit()
        return view_3d
    except Exception:
        transaction.RollBack()
        return None


def delete_temporary_view(view_3d):
    if view_3d is None:
        return

    transaction = DB.Transaction(doc, "Delete temporary raycast view")
    transaction.Start()
    try:
        doc.Delete(view_3d.Id)
        transaction.Commit()
    except Exception:
        transaction.RollBack()


def first_pipe_in_model():
    for pipe in DB.FilteredElementCollector(doc) \
            .OfClass(DB.Plumbing.Pipe) \
            .WhereElementIsNotElementType():
        return pipe
    return None


def curve_point_xy_distance_squared(curve, normalized_parameter, xy_point):
    point = curve.Evaluate(normalized_parameter, True)
    dx = point.X - xy_point.X
    dy = point.Y - xy_point.Y
    return dx * dx + dy * dy


def pipe_center_and_direction_at_xy(pipe, xy_point, hit_point=None):
    """
    Find the point on the pipe centerline whose PLAN position is closest to
    xy_point. For a sloped straight pipe, Z is interpolated from the actual
    3D LocationCurve, so this is the local center elevation at the CAD XY.
    """
    try:
        location = pipe.Location
        if not isinstance(location, DB.LocationCurve):
            raise Exception("Pipe không có LocationCurve")
        curve = location.Curve

        if isinstance(curve, DB.Line):
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            dx = end.X - start.X
            dy = end.Y - start.Y
            denominator = dx * dx + dy * dy
            if denominator <= 1e-12:
                center = curve.Evaluate(0.5, True)
                direction = end.Subtract(start)
                return center, direction

            t = (
                (xy_point.X - start.X) * dx +
                (xy_point.Y - start.Y) * dy
            ) / denominator
            t = max(0.0, min(1.0, t))
            center = curve.Evaluate(t, True)
            direction = end.Subtract(start)
            return center, direction

        # Generic curve fallback: minimize plan distance over normalized 0..1.
        left = 0.0
        right = 1.0
        for unused in range(60):
            third = (right - left) / 3.0
            t1 = left + third
            t2 = right - third
            d1 = curve_point_xy_distance_squared(curve, t1, xy_point)
            d2 = curve_point_xy_distance_squared(curve, t2, xy_point)
            if d1 <= d2:
                right = t2
            else:
                left = t1
        t = (left + right) * 0.5
        center = curve.Evaluate(t, True)

        delta = 1e-5
        t0 = max(0.0, t - delta)
        t1 = min(1.0, t + delta)
        direction = curve.Evaluate(t1, True).Subtract(
            curve.Evaluate(t0, True)
        )
        return center, direction

    except Exception:
        try:
            location = pipe.Location
            if isinstance(location, DB.LocationCurve) and hit_point is not None:
                projection = location.Curve.Project(hit_point)
                if projection:
                    center = projection.XYZPoint
                    try:
                        derivatives = location.Curve.ComputeDerivatives(
                            projection.Parameter, False
                        )
                        direction = derivatives.BasisX
                    except Exception:
                        direction = location.Curve.GetEndPoint(1).Subtract(
                            location.Curve.GetEndPoint(0)
                        )
                    return center, direction
        except Exception:
            pass

    try:
        bbox = pipe.get_BoundingBox(None)
        if bbox:
            return bbox_center(bbox.Min, bbox.Max), None
    except Exception:
        pass
    return hit_point, None


def get_pipe_numeric_diameter(pipe):
    """Return Pipe nominal diameter as an internal Revit length value."""
    try:
        parameter = pipe.get_Parameter(
            DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM
        )
        if (parameter is not None and parameter.HasValue and
                parameter.StorageType == DB.StorageType.Double):
            return parameter.AsDouble()
    except Exception:
        pass

    # Defensive fallback. Standard Pipe elements normally expose the built-in
    # diameter parameter, but connector radius still gives a numeric value.
    try:
        connector_manager = pipe.ConnectorManager
        for connector in connector_manager.Connectors:
            try:
                radius = connector.Radius
                if radius is not None and radius > 1e-12:
                    return radius * 2.0
            except Exception:
                pass
    except Exception:
        pass

    return None


def get_pipe_parameter_value(pipe, descriptor):
    if descriptor is None:
        return None

    source_kind = descriptor.get("source_kind")
    if source_kind in (
            "PIPE_SIZE_BUILTIN",
            "PIPE_DIAMETER_BUILTIN"):
        # RBS_CALCULATED_SIZE is a formatted String such as "100 mm".
        # Do not parse display text because it changes with project units and
        # locale. Resolve both UI choices to the numeric nominal diameter.
        return get_pipe_numeric_diameter(pipe)

    parameter = find_parameter_by_descriptor(pipe, descriptor)
    if parameter is None or not parameter.HasValue:
        return None
    try:
        return parameter.AsDouble()
    except Exception:
        return None


def collect_pipe_search_records():
    """Cache Pipe and bounding box once for fast fallback searching."""
    records = []
    for pipe in DB.FilteredElementCollector(doc) \
            .OfClass(DB.Plumbing.Pipe) \
            .WhereElementIsNotElementType():
        try:
            bbox = pipe.get_BoundingBox(None)
        except Exception:
            bbox = None
        records.append({
            "pipe": pipe,
            "bbox": bbox,
        })
    return records


def find_pipe_by_plan_fallback(candidate, placement_point, ray_origin,
                               pipe_records, max_ray_height,
                               xy_tolerance):
    """
    Find a Pipe by its plan projection when ReferenceIntersector misses it.

    The CAD block bounding box is used as the primary plan footprint. A Pipe
    is accepted when its local centerline is inside/near that footprint, or
    when the Family placement point is near the centerline. Pipe radius is
    added automatically, so tolerance represents extra drafting offset only.
    """
    candidate_min = candidate.get("bbox_min")
    candidate_max = candidate.get("bbox_max")
    best = None

    for record in pipe_records:
        pipe = record["pipe"]
        bbox = record.get("bbox")

        # Fast vertical and plan bounding-box rejection.
        if bbox is not None:
            if bbox.Max.Z < ray_origin.Z:
                continue
            if bbox.Min.Z > ray_origin.Z + max_ray_height:
                continue
            if candidate_min is not None and candidate_max is not None:
                bbox_gap = bbox_to_bbox_xy_distance(
                    candidate_min, candidate_max, bbox.Min, bbox.Max
                )
                if bbox_gap > xy_tolerance:
                    continue

        try:
            center_point, pipe_direction = pipe_center_and_direction_at_xy(
                pipe, placement_point, None
            )
        except Exception:
            continue
        if center_point is None:
            continue

        diameter = get_pipe_numeric_diameter(pipe)
        radius = diameter * 0.5 if diameter is not None else 0.0

        # The local pipe solid must be above the ray origin and within range.
        if center_point.Z + radius < ray_origin.Z:
            continue
        if center_point.Z - radius > ray_origin.Z + max_ray_height:
            continue

        point_distance = xyz_xy_distance(placement_point, center_point)
        if candidate_min is not None and candidate_max is not None:
            block_distance = point_to_bbox_xy_distance(
                center_point, candidate_min, candidate_max
            )
        else:
            block_distance = point_distance

        allowed_distance = radius + xy_tolerance
        if (point_distance > allowed_distance and
                block_distance > allowed_distance):
            continue

        physical_plan_overlap = (
            point_distance <= radius + mm_to_internal(1.0) or
            block_distance <= radius + mm_to_internal(1.0)
        )
        vertical_distance = max(0.0, center_point.Z - ray_origin.Z)

        # Prefer real plan overlap, then a pipe crossing the CAD footprint,
        # then the centerline closest to the requested Family point.
        score = (
            0 if physical_plan_overlap else 1,
            block_distance,
            point_distance,
            vertical_distance,
            eid_value(pipe.Id) or 0,
        )

        if best is None or score < best["score"]:
            best = {
                "score": score,
                "pipe": pipe,
                "pipe_center": center_point,
                "pipe_direction": pipe_direction,
                "center_xy_offset": point_distance,
                "block_xy_offset": block_distance,
                "hit_distance": vertical_distance,
                "diameter": diameter,
            }

    return best


def raycast_pipe_data(candidates, placement_key, level, view_3d,
                      max_ray_height, elevation_source,
                      diameter_source, pipe_records,
                      pipe_search_tolerance):
    pipe_filter = DB.ElementClassFilter(DB.Plumbing.Pipe)
    intersector = DB.ReferenceIntersector(
        pipe_filter,
        DB.FindReferenceTarget.Element,
        view_3d
    )
    intersector.FindReferencesInRevitLinks = False

    results = []
    ray_start_offset = mm_to_internal(1.0)

    with forms.ProgressBar(
            title="Tìm Pipe phía trên {value}/{max_value}",
            cancellable=True,
            step=1) as progress:

        for index, candidate in enumerate(candidates):
            if progress.cancelled:
                break

            progress.update_progress(index + 1, len(candidates))

            source_point = candidate[placement_key]
            placement_point = DB.XYZ(
                source_point.X,
                source_point.Y,
                level.Elevation
            )
            ray_origin = DB.XYZ(
                source_point.X,
                source_point.Y,
                level.Elevation + ray_start_offset
            )

            item = {
                "index": index + 1,
                "candidate": candidate,
                "placement_point": placement_point,
                "pipe": None,
                "pipe_center": None,
                "pipe_direction": None,
                "center_xy_offset": None,
                "block_xy_offset": None,
                "elevation_value": None,
                "diameter_value": None,
                "hit_distance": None,
                "detection_method": "-",
                "ready": False,
                "status": "Không giao Pipe",
            }

            try:
                pipe = None
                center_point = None
                pipe_direction = None
                center_xy_offset = None
                block_xy_offset = None
                proximity = None
                detection_method = None

                # Step 1: exact solid intersection in the temporary 3D view.
                reference_context = intersector.FindNearest(
                    ray_origin,
                    DB.XYZ.BasisZ
                )
                if reference_context is not None:
                    ray_proximity = reference_context.Proximity
                    if ray_proximity <= max_ray_height:
                        reference = reference_context.GetReference()
                        ray_pipe = doc.GetElement(reference.ElementId)
                        if isinstance(ray_pipe, DB.Plumbing.Pipe):
                            hit_point = None
                            try:
                                hit_point = reference.GlobalPoint
                            except Exception:
                                hit_point = None
                            if hit_point is None:
                                hit_point = ray_origin.Add(
                                    DB.XYZ.BasisZ.Multiply(ray_proximity)
                                )

                            center_point, pipe_direction = (
                                pipe_center_and_direction_at_xy(
                                    ray_pipe, placement_point, hit_point
                                )
                            )
                            pipe = ray_pipe
                            center_xy_offset = xyz_xy_distance(
                                placement_point, center_point
                            )
                            block_xy_offset = point_to_bbox_xy_distance(
                                center_point,
                                candidate["bbox_min"],
                                candidate["bbox_max"]
                            )
                            proximity = ray_proximity
                            detection_method = "Raycast solid"

                # Step 2: robust plan fallback. This also handles Pipes hidden
                # from the temporary 3D view and CAD insertion points that are
                # slightly offset from the pipe centerline.
                if pipe is None:
                    fallback = find_pipe_by_plan_fallback(
                        candidate,
                        placement_point,
                        ray_origin,
                        pipe_records,
                        max_ray_height,
                        pipe_search_tolerance
                    )
                    if fallback is not None:
                        pipe = fallback["pipe"]
                        center_point = fallback["pipe_center"]
                        pipe_direction = fallback["pipe_direction"]
                        center_xy_offset = fallback["center_xy_offset"]
                        block_xy_offset = fallback["block_xy_offset"]
                        proximity = fallback["hit_distance"]
                        detection_method = "Đối chiếu XY"

                if pipe is None:
                    results.append(item)
                    continue

                if elevation_source == "GEOMETRIC_CENTERLINE":
                    elevation_value = center_point.Z - level.Elevation
                else:
                    elevation_value = get_pipe_parameter_value(
                        pipe,
                        elevation_source
                    )

                diameter_value = get_pipe_parameter_value(
                    pipe,
                    diameter_source
                )

                if elevation_value is None:
                    item["status"] = "Pipe thiếu parameter cao độ"
                elif diameter_value is None:
                    item["status"] = "Pipe thiếu parameter đường kính"
                else:
                    item["status"] = "Sẵn sàng"
                    item["ready"] = True

                item["pipe"] = pipe
                item["pipe_center"] = center_point
                item["pipe_direction"] = pipe_direction
                item["center_xy_offset"] = center_xy_offset
                item["block_xy_offset"] = block_xy_offset
                item["elevation_value"] = elevation_value
                item["diameter_value"] = diameter_value
                item["hit_distance"] = proximity
                item["detection_method"] = detection_method or "-"
                results.append(item)

            except Exception as error:
                item["status"] = "Lỗi tìm Pipe: {0}".format(to_text(error))
                results.append(item)

    return results


# -----------------------------------------------------------------------------
# DUPLICATE CHECK AND FAMILY PLACEMENT
# -----------------------------------------------------------------------------
def family_instance_location(instance):
    try:
        location = instance.Location
        if isinstance(location, DB.LocationPoint):
            return location.Point
    except Exception:
        pass
    return None


def same_level(instance, level):
    try:
        return instance.LevelId == level.Id
    except Exception:
        return True


def existing_symbol_positions(symbol, level):
    positions = []
    for instance in DB.FilteredElementCollector(doc) \
            .OfClass(DB.FamilyInstance) \
            .WhereElementIsNotElementType():
        try:
            if instance.Symbol.Id != symbol.Id:
                continue
            if not same_level(instance, level):
                continue
            point = family_instance_location(instance)
            if point:
                positions.append(point)
        except Exception:
            pass
    return positions


def has_duplicate_xy(point, existing_points, tolerance):
    for existing_point in existing_points:
        if xyz_xy_distance(point, existing_point) <= tolerance:
            return True
    return False


def set_family_double_parameter(instance, descriptor, value):
    parameter = find_parameter_by_descriptor(instance, descriptor)
    if parameter is None:
        raise Exception(
            "Không tìm thấy Family parameter: {0}".format(
                descriptor["name"]
            )
        )
    if parameter.IsReadOnly:
        raise Exception(
            "Family parameter chỉ đọc: {0}".format(
                descriptor["name"]
            )
        )
    parameter.Set(float(value))


def get_instance_connectors(instance):
    connectors = []
    try:
        mep_model = instance.MEPModel
        if mep_model is None:
            return connectors
        manager = mep_model.ConnectorManager
        if manager is None:
            return connectors
        for connector in manager.Connectors:
            connectors.append(connector)
    except Exception:
        pass
    return connectors


def get_connector_axis_xy(instance):
    """
    Select the physical connector whose CoordinateSystem.BasisZ has the
    strongest horizontal component. With two opposite connectors, either axis
    is valid because alignment is treated as parallel, not directional.
    """
    best_axis = None
    best_length = 0.0
    for connector in get_instance_connectors(instance):
        try:
            axis = connector.CoordinateSystem.BasisZ
            length = vector_xy_length(axis)
            if length > best_length:
                best_length = length
                best_axis = axis
        except Exception:
            pass
    return normalize_xy(best_axis) if best_axis is not None else None


def align_family_connector_to_pipe(instance, placement_point, pipe_direction):
    pipe_axis = normalize_xy(pipe_direction)
    if pipe_axis is None:
        raise Exception("Pipe không có phương ngang để xoay quanh Z")

    doc.Regenerate()
    connector_axis = get_connector_axis_xy(instance)
    if connector_axis is None:
        raise Exception(
            "Không đọc được connector có BasisZ nằm ngang trong Family"
        )

    angle = signed_xy_angle(connector_axis, pipe_axis)
    angle = normalize_parallel_angle(angle)
    if angle is None:
        raise Exception("Không tính được góc xoay connector")

    if abs(angle) > 1e-9:
        axis = DB.Line.CreateBound(
            placement_point,
            placement_point.Add(DB.XYZ.BasisZ)
        )
        DB.ElementTransformUtils.RotateElement(
            doc, instance.Id, axis, angle
        )
        doc.Regenerate()
    return angle


def place_families(ray_results, symbol, level,
                   family_elevation_target,
                   family_diameter_target,
                   align_connector,
                   skip_duplicates,
                   duplicate_tolerance_mm):
    tolerance = mm_to_internal(duplicate_tolerance_mm)
    existing_points = existing_symbol_positions(symbol, level)

    transaction = DB.Transaction(doc, "CAD Block to Pipe Family")
    transaction.Start()

    placed_count = 0
    skipped_duplicate_count = 0
    failed_count = 0

    try:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()

        for item in ray_results:
            can_place = bool(item.get("ready"))
            if not can_place and not PLACE_FAMILY_WHEN_NO_PIPE:
                continue

            placement_point = item["placement_point"]

            if skip_duplicates and has_duplicate_xy(
                    placement_point,
                    existing_points,
                    tolerance):
                item["status"] = "Bỏ qua - đã có Family"
                skipped_duplicate_count += 1
                continue

            sub_transaction = DB.SubTransaction(doc)
            sub_transaction.Start()

            try:
                instance = doc.Create.NewFamilyInstance(
                    placement_point,
                    symbol,
                    level,
                    DB.Structure.StructuralType.NonStructural
                )

                rotation_angle = None
                if can_place:
                    set_family_double_parameter(
                        instance,
                        family_elevation_target,
                        item["elevation_value"]
                    )
                    set_family_double_parameter(
                        instance,
                        family_diameter_target,
                        item["diameter_value"]
                    )

                    if align_connector:
                        rotation_angle = align_family_connector_to_pipe(
                            instance,
                            placement_point,
                            item.get("pipe_direction")
                        )

                sub_transaction.Commit()
                existing_points.append(placement_point)
                item["family_instance"] = instance
                item["rotation_angle"] = rotation_angle
                if rotation_angle is None:
                    item["status"] = "Đã đặt Family"
                else:
                    item["status"] = "Đã đặt + xoay {0:.2f}°".format(
                        math.degrees(rotation_angle)
                    )
                placed_count += 1

            except Exception as error:
                sub_transaction.RollBack()
                item["status"] = "Lỗi đặt/xoay Family: {0}".format(
                    to_text(error)
                )
                failed_count += 1

        transaction.Commit()

    except Exception:
        transaction.RollBack()
        raise

    return placed_count, skipped_duplicate_count, failed_count


# -----------------------------------------------------------------------------
# REPORT
# -----------------------------------------------------------------------------
def print_report(import_instance, sample_candidate, matched_candidates,
                 symbol, level, family_elevation_target,
                 family_diameter_target, elevation_source,
                 diameter_source, ray_results, placed_count,
                 duplicate_count, failed_count):
    output.print_md("# CAD Block → Family → Pipe")
    output.print_md(
        "**CAD Import ID:** {0}  \\n**CAD block cùng loại:** {1}  \\n**Family:** {2} : {3}  \\n**Level:** {4}".format(
            eid_value(import_instance.Id),
            len(matched_candidates),
            symbol.Family.Name,
            element_type_name(symbol),
            level.Name
        )
    )

    if elevation_source == "GEOMETRIC_CENTERLINE":
        elevation_source_name = "Tim hình học Pipe - Elevation Level"
    else:
        elevation_source_name = elevation_source["name"]

    output.print_md(
        "**Mapping cao độ:** `{0}` → `{1}`  \\n**Mapping đường kính:** `{2}` → `{3}`".format(
            elevation_source_name,
            family_elevation_target["name"],
            diameter_source["name"],
            family_diameter_target["name"]
        )
    )

    output.print_md(
        "**Kết quả:** đặt `{0}`, bỏ qua trùng `{1}`, lỗi đặt `{2}`.".format(
            placed_count,
            duplicate_count,
            failed_count
        )
    )

    table_rows = []
    for item in ray_results:
        pipe_id = "-"
        elevation_mm = "-"
        diameter_mm = "-"
        family_id = "-"
        center_z_mm = "-"
        direction_text = "-"
        rotation_deg = "-"
        detection_method = item.get("detection_method", "-")
        center_xy_offset_mm = "-"
        block_xy_offset_mm = "-"

        if item.get("pipe"):
            pipe_id = eid_value(item["pipe"].Id)
        if item.get("elevation_value") is not None:
            elevation_mm = "{0:.1f}".format(
                internal_to_mm(item["elevation_value"])
            )
        if item.get("diameter_value") is not None:
            diameter_mm = "{0:.1f}".format(
                internal_to_mm(item["diameter_value"])
            )
        if item.get("family_instance"):
            family_id = eid_value(item["family_instance"].Id)
        if item.get("pipe_center") is not None:
            center_z_mm = "{0:.1f}".format(
                internal_to_mm(item["pipe_center"].Z)
            )
        if item.get("pipe_direction") is not None:
            horizontal = normalize_xy(item["pipe_direction"])
            if horizontal is not None:
                direction_text = "({0:.3f}, {1:.3f})".format(
                    horizontal.X, horizontal.Y
                )
        if item.get("rotation_angle") is not None:
            rotation_deg = "{0:.2f}".format(
                math.degrees(item["rotation_angle"])
            )
        if item.get("center_xy_offset") is not None:
            center_xy_offset_mm = "{0:.1f}".format(
                internal_to_mm(item["center_xy_offset"])
            )
        if item.get("block_xy_offset") is not None:
            block_xy_offset_mm = "{0:.1f}".format(
                internal_to_mm(item["block_xy_offset"])
            )

        point = item["placement_point"]
        table_rows.append([
            item["index"],
            "{0:.1f}".format(internal_to_mm(point.X)),
            "{0:.1f}".format(internal_to_mm(point.Y)),
            pipe_id,
            detection_method,
            center_xy_offset_mm,
            block_xy_offset_mm,
            center_z_mm,
            elevation_mm,
            diameter_mm,
            direction_text,
            rotation_deg,
            family_id,
            item["status"],
        ])

    output.print_table(
        table_data=table_rows,
        columns=[
            "No.",
            "X (mm)",
            "Y (mm)",
            "Pipe ID",
            "Cách tìm",
            "Point→center XY (mm)",
            "Block→center XY (mm)",
            "Pipe center Z (mm)",
            "Elevation from Level (mm)",
            "Diameter (mm)",
            "Pipe XY direction",
            "Rotate Z (deg)",
            "Family ID",
            "Status",
        ]
    )


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def save_window_config(config, settings):
    try:
        config.level_id = eid_value(settings["level"].Id)
        config.family_symbol_id = eid_value(settings["symbol"].Id)
        config.placement_mode = settings["placement_mode"]
        config.family_elevation_target = descriptor_config_key(
            settings["family_elevation_target"]
        )
        config.family_diameter_target = descriptor_config_key(
            settings["family_diameter_target"]
        )
        config.pipe_elevation_source = settings["pipe_elevation_label"]
        config.pipe_diameter_source = settings["pipe_diameter_label"]
        config.max_ray_height_mm = settings["max_ray_height_mm"]
        config.duplicate_tolerance_mm = settings["duplicate_tolerance_mm"]
        config.pipe_search_tolerance_mm = settings["pipe_search_tolerance_mm"]
        config.align_connector = settings["align_connector"]
        config.skip_duplicates = settings["skip_duplicates"]
        script.save_config()
    except Exception:
        pass


def main():
    if active_view is None:
        forms.alert("Không tìm thấy Active View.", exitscript=True)

    import_instance, picked_point = pick_cad_symbol_point()
    if import_instance is None:
        script.exit()

    with forms.ProgressBar(
            title="Đang phân tích CAD block...",
            indeterminate=True,
            cancellable=False):
        candidates = collect_cad_block_candidates(
            import_instance,
            active_view
        )

    if not candidates:
        forms.alert(
            "Không tìm thấy GeometryInstance lồng trong CAD Import.\n\n"
            "CAD có thể đã explode, chỉ chứa line rời, hoặc cấu trúc DWG "
            "không còn block reference.",
            exitscript=True
        )

    sample_candidate = find_nearest_candidate(candidates, picked_point)
    if sample_candidate is None:
        forms.alert("Không nhận diện được CAD block mẫu.", exitscript=True)

    matched_candidates = [
        candidate for candidate in candidates
        if candidate["match_key"] == sample_candidate["match_key"]
    ]
    if not matched_candidates:
        forms.alert("Không tìm thấy CAD block cùng loại.", exitscript=True)

    family_options, family_map = collect_family_symbols()
    if not family_options:
        forms.alert("Model chưa có Family Type.", exitscript=True)

    sample_pipe = first_pipe_in_model()
    if sample_pipe is None:
        forms.alert("Model hiện tại không có Pipe.", exitscript=True)

    levels = list(DB.FilteredElementCollector(doc)
                  .OfClass(DB.Level)
                  .WhereElementIsNotElementType())
    levels.sort(key=lambda x: x.Elevation)
    if not levels:
        forms.alert("Model không có Level.", exitscript=True)

    config = script.get_config()
    xaml_file = script.get_bundle_file("ui.xaml")
    window = CADPipeFamilyWindow(
        xaml_file,
        family_options,
        family_map,
        levels,
        sample_candidate["origin"],
        sample_pipe,
        len(matched_candidates),
        import_instance,
        config
    )
    window.ShowDialog()
    settings = window.result
    if settings is None:
        script.exit()
    save_window_config(config, settings)

    level = settings["level"]
    symbol = settings["symbol"]
    placement_key = settings["placement_key"]
    family_elevation_target = settings["family_elevation_target"]
    family_diameter_target = settings["family_diameter_target"]
    elevation_source = settings["elevation_source"]
    diameter_source = settings["diameter_source"]
    max_ray_height = mm_to_internal(settings["max_ray_height_mm"])
    pipe_search_tolerance = mm_to_internal(
        settings["pipe_search_tolerance_mm"]
    )
    pipe_records = collect_pipe_search_records()

    temp_view = create_temporary_3d_view()
    if temp_view is None:
        forms.alert(
            "Không tạo được temporary 3D View để raycast.",
            exitscript=True
        )

    ray_results = []
    try:
        ray_results = raycast_pipe_data(
            matched_candidates,
            placement_key,
            level,
            temp_view,
            max_ray_height,
            elevation_source,
            diameter_source,
            pipe_records,
            pipe_search_tolerance
        )
    finally:
        delete_temporary_view(temp_view)

    if not ray_results:
        forms.alert("Không có kết quả raycast.", exitscript=True)

    placed_count, duplicate_count, failed_count = place_families(
        ray_results,
        symbol,
        level,
        family_elevation_target,
        family_diameter_target,
        settings["align_connector"],
        settings["skip_duplicates"],
        settings["duplicate_tolerance_mm"]
    )

    print_report(
        import_instance,
        sample_candidate,
        matched_candidates,
        symbol,
        level,
        family_elevation_target,
        family_diameter_target,
        elevation_source,
        diameter_source,
        ray_results,
        placed_count,
        duplicate_count,
        failed_count
    )

    no_pipe_count = len([x for x in ray_results if x["pipe"] is None])
    forms.alert(
        "Hoàn thành.\n\n"
        "CAD block cùng loại: {0}\n"
        "Family đã đặt: {1}\n"
        "Bỏ qua do trùng: {2}\n"
        "Không giao Pipe: {3}\n"
        "Lỗi đặt hoặc xoay Family: {4}".format(
            len(matched_candidates),
            placed_count,
            duplicate_count,
            no_pipe_count,
            failed_count
        ),
        title="CAD Block to Pipe Family"
    )


if __name__ == "__main__":
    try:
        main()
    except OperationCanceledException:
        pass
    except Exception:
        output.print_md("# CAD Block to Pipe Family - Chi tiết lỗi")
        output.print_code(traceback.format_exc())
        forms.alert(
            "Tool gặp lỗi. Xem pyRevit Output để kiểm tra chi tiết.",
            title="CAD Block to Pipe Family"
        )
