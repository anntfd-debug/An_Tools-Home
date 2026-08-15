# -*- coding: utf-8 -*-
"""
pyRevit - Ungroup Groups

Chức năng:
1. Ungroup các Group đã được chọn trước khi chạy tool.
2. Hoặc tìm Group trong view hiện hành theo tên Group Type:
   - Contains: tên chứa một phần từ khóa.
   - Exact: tên trùng chính xác từ khóa.
3. Hiển thị danh sách Group Type và số lượng instance để người dùng chọn.
4. Hỗ trợ nhiều từ khóa, ngăn cách bằng dấu chấm phẩy (;).
5. Có thể tự động bỏ Pin trước khi Ungroup.

Tương thích IronPython 2.7 và CPython của pyRevit.
"""

import os
import traceback

from pyrevit import revit, DB, forms, script
from System.Windows import Thickness
from System.Windows.Controls import CheckBox


doc = revit.doc
uidoc = revit.uidoc
active_view = revit.active_view
output = script.get_output()

XAML_FILE = os.path.join(os.path.dirname(__file__), "ui.xaml")


# ============================================================
# TEXT / ELEMENT ID HELPERS
# ============================================================

def safe_text(value):
    try:
        return u"{}".format(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return u""


def element_id_key(element_id):
    """Khóa tương thích Revit cũ và Revit dùng ElementId 64-bit."""
    if element_id is None:
        return None

    try:
        return safe_text(element_id.Value)
    except Exception:
        try:
            return safe_text(element_id.IntegerValue)
        except Exception:
            return safe_text(element_id)


def get_element_name(element):
    """Lấy Name ổn định hơn giữa IronPython/CPython và các bản Revit."""
    if element is None:
        return u"<Không xác định>"

    try:
        name = element.Name
        if name:
            return safe_text(name)
    except Exception:
        pass

    try:
        name = DB.Element.Name.GetValue(element)
        if name:
            return safe_text(name)
    except Exception:
        pass

    # Fallback cho ElementType/GroupType.
    parameter_names = ["SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME"]
    for bip_name in parameter_names:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            param = element.get_Parameter(bip)
            if param and param.HasValue:
                name = param.AsString()
                if name:
                    return safe_text(name)
        except Exception:
            pass

    return u"<Không xác định>"


def get_group_type_name(group):
    try:
        group_type = doc.GetElement(group.GetTypeId())
        return get_element_name(group_type)
    except Exception:
        try:
            return get_element_name(group)
        except Exception:
            return u"<Không xác định>"


def get_group_kind(group):
    """Detail Group là view-specific; Model Group không view-specific."""
    try:
        return u"Detail" if group.ViewSpecific else u"Model"
    except Exception:
        return u"Group"


# ============================================================
# GROUP COLLECTION
# ============================================================

def unique_element_ids(element_ids):
    result = []
    seen = set()

    for element_id in element_ids:
        key = element_id_key(element_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(element_id)

    return result


def get_preselected_group_ids():
    result = []

    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if isinstance(element, DB.Group):
            result.append(element_id)

    return unique_element_ids(result)


def collect_group_ids_in_active_view():
    """
    Thu thập Model Group và Detail Group được Revit trả về trong active view.
    """
    result = []

    try:
        collector = (
            DB.FilteredElementCollector(doc, active_view.Id)
            .OfClass(DB.Group)
            .WhereElementIsNotElementType()
        )

        for element in collector:
            if isinstance(element, DB.Group):
                result.append(element.Id)

        return unique_element_ids(result), None

    except Exception as ex:
        return [], safe_text(ex)


def build_group_records(group_ids):
    """
    Gom các Group instance theo Group Type.

    record = {
        "type_key": str,
        "type_id": ElementId,
        "name": str,
        "kind": str,
        "ids": [ElementId, ...]
    }
    """
    record_map = {}

    for group_id in group_ids:
        group = doc.GetElement(group_id)
        if not isinstance(group, DB.Group):
            continue

        type_id = group.GetTypeId()
        type_key = element_id_key(type_id)

        if type_key not in record_map:
            record_map[type_key] = {
                "type_key": type_key,
                "type_id": type_id,
                "name": get_group_type_name(group),
                "kind": get_group_kind(group),
                "ids": []
            }

        record_map[type_key]["ids"].append(group_id)

    records = list(record_map.values())
    records.sort(key=lambda item: (
        safe_text(item["kind"]).lower(),
        safe_text(item["name"]).lower()
    ))

    return records


# ============================================================
# WPF WINDOW
# ============================================================

class UngroupWindow(forms.WPFWindow):
    def __init__(self, xaml_file, preselected_ids):
        self._initializing = True
        self.result_group_ids = []
        self.auto_unpin = True
        self.cancelled = True
        self.preselected_ids = unique_element_ids(preselected_ids)
        self.view_group_ids = []
        self.view_collect_error = None
        self.visible_rows = []

        forms.WPFWindow.__init__(self, xaml_file)

        self.view_group_ids, self.view_collect_error = collect_group_ids_in_active_view()

        if self.preselected_ids:
            self.rbSelected.IsChecked = True
        else:
            self.rbCurrentView.IsChecked = True

        self.rbContains.IsChecked = True
        self.cbCaseSensitive.IsChecked = False
        self.cbAutoUnpin.IsChecked = True

        self._initializing = False
        self.refresh_interface()

    # --------------------------------------------------------
    # UI STATE
    # --------------------------------------------------------

    def refresh_interface(self):
        selected_count = len(self.preselected_ids)
        view_count = len(self.view_group_ids)

        self.txtSelectedInfo.Text = (
            u"Đang có {} Group được chọn trước khi chạy tool."
            .format(selected_count)
        )

        view_name = get_element_name(active_view)
        if self.view_collect_error:
            self.txtViewInfo.Text = (
                u"View: {} | Không thể quét Group: {}"
                .format(view_name, self.view_collect_error)
            )
        else:
            self.txtViewInfo.Text = (
                u"View: {} | Tìm thấy {} Group instance."
                .format(view_name, view_count)
            )

        is_view_mode = bool(self.rbCurrentView.IsChecked)
        self.pnlFilterControls.IsEnabled = is_view_mode
        self.refresh_group_rows()

    def get_keywords(self):
        raw_text = safe_text(self.tbKeyword.Text)
        raw_text = raw_text.replace(u"\r", u"").replace(u"\n", u";")

        keywords = []
        for item in raw_text.split(u";"):
            item = item.strip()
            if item:
                keywords.append(item)

        return keywords

    def record_matches_keywords(self, record):
        keywords = self.get_keywords()
        if not keywords:
            return True

        group_name = safe_text(record["name"])
        case_sensitive = bool(self.cbCaseSensitive.IsChecked)
        exact_mode = bool(self.rbExact.IsChecked)

        if not case_sensitive:
            group_name = group_name.lower()
            keywords = [keyword.lower() for keyword in keywords]

        for keyword in keywords:
            if exact_mode:
                if group_name == keyword:
                    return True
            else:
                if keyword in group_name:
                    return True

        return False

    def get_source_records(self):
        if bool(self.rbSelected.IsChecked):
            return build_group_records(self.preselected_ids)

        records = build_group_records(self.view_group_ids)
        return [record for record in records if self.record_matches_keywords(record)]

    def refresh_group_rows(self):
        self.spGroupList.Children.Clear()
        self.visible_rows = []

        records = self.get_source_records()

        for record in records:
            checkbox = CheckBox()
            checkbox.IsChecked = True
            checkbox.Margin = Thickness(6, 4, 6, 4)
            checkbox.Content = u"[{}] {}    —    {} instance".format(
                record["kind"],
                record["name"],
                len(record["ids"])
            )
            checkbox.ToolTip = u"Group Type ID: {}".format(record["type_key"])
            checkbox.Checked += self.row_checked_changed
            checkbox.Unchecked += self.row_checked_changed

            self.spGroupList.Children.Add(checkbox)
            self.visible_rows.append((checkbox, record))

        self.update_summary()

    def update_summary(self):
        checked_type_count = 0
        checked_instance_count = 0

        for checkbox, record in self.visible_rows:
            if bool(checkbox.IsChecked):
                checked_type_count += 1
                checked_instance_count += len(record["ids"])

        self.txtListSummary.Text = (
            u"Đã chọn {} Group Type / {} Group instance để Ungroup."
            .format(checked_type_count, checked_instance_count)
        )

        self.btnUngroup.IsEnabled = checked_instance_count > 0
        self.btnUngroup.Content = (
            u"Ungroup {} Group".format(checked_instance_count)
            if checked_instance_count > 0
            else u"Ungroup"
        )

    # --------------------------------------------------------
    # EVENTS
    # --------------------------------------------------------

    def source_changed(self, sender, args):
        if self._initializing:
            return
        self.refresh_interface()

    def filter_changed(self, sender, args):
        if self._initializing:
            return
        if bool(self.rbCurrentView.IsChecked):
            self.refresh_group_rows()

    def keyword_changed(self, sender, args):
        if self._initializing:
            return
        if bool(self.rbCurrentView.IsChecked):
            self.refresh_group_rows()

    def row_checked_changed(self, sender, args):
        if self._initializing:
            return
        self.update_summary()

    def select_all_clicked(self, sender, args):
        for checkbox, record in self.visible_rows:
            checkbox.IsChecked = True
        self.update_summary()

    def select_none_clicked(self, sender, args):
        for checkbox, record in self.visible_rows:
            checkbox.IsChecked = False
        self.update_summary()

    def refresh_view_clicked(self, sender, args):
        self.view_group_ids, self.view_collect_error = collect_group_ids_in_active_view()
        self.refresh_interface()

    def ungroup_clicked(self, sender, args):
        result_ids = []

        for checkbox, record in self.visible_rows:
            if bool(checkbox.IsChecked):
                result_ids.extend(record["ids"])

        result_ids = unique_element_ids(result_ids)

        if not result_ids:
            forms.alert(
                u"Chưa có Group nào được chọn để Ungroup.",
                title=u"Ungroup Groups"
            )
            return

        self.result_group_ids = result_ids
        self.auto_unpin = bool(self.cbAutoUnpin.IsChecked)
        self.cancelled = False
        self.Close()

    def cancel_clicked(self, sender, args):
        self.cancelled = True
        self.Close()


# ============================================================
# UNGROUP ENGINE
# ============================================================

def ungroup_group_ids(group_ids, auto_unpin=True):
    success_items = []
    failed_items = []
    skipped_count = 0
    cancelled = False

    group_ids = unique_element_ids(group_ids)
    total = len(group_ids)

    transaction = DB.Transaction(doc, "Ungroup Groups")
    transaction.Start()

    try:
        with forms.ProgressBar(
            title=u"Ungroup Group {value} / {max_value}",
            cancellable=True,
            step=1
        ) as progress:

            for index, group_id in enumerate(group_ids):
                progress.update_progress(index + 1, total)

                if progress.cancelled:
                    cancelled = True
                    break

                group = doc.GetElement(group_id)
                if not isinstance(group, DB.Group):
                    skipped_count += 1
                    continue

                group_name = get_group_type_name(group)
                group_kind = get_group_kind(group)

                sub_transaction = DB.SubTransaction(doc)
                sub_transaction.Start()

                try:
                    if auto_unpin:
                        try:
                            if group.Pinned:
                                group.Pinned = False
                        except Exception:
                            # Nếu không thể bỏ pin, UngroupMembers sẽ báo lỗi cụ thể.
                            pass

                    group.UngroupMembers()
                    sub_transaction.Commit()

                    success_items.append({
                        "name": group_name,
                        "kind": group_kind,
                        "id": group_id
                    })

                except Exception as ex:
                    try:
                        sub_transaction.RollBack()
                    except Exception:
                        pass

                    failed_items.append({
                        "name": group_name,
                        "kind": group_kind,
                        "id": group_id,
                        "error": safe_text(ex)
                    })

        transaction.Commit()

    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise

    return {
        "success": success_items,
        "failed": failed_items,
        "skipped": skipped_count,
        "cancelled": cancelled,
        "requested": total
    }


# ============================================================
# RESULT
# ============================================================

def show_result(result):
    success_count = len(result["success"])
    failed_count = len(result["failed"])
    skipped_count = result["skipped"]

    if failed_count:
        output.print_md(u"## Ungroup Groups - Chi tiết Group không thể Ungroup")

        for item in result["failed"]:
            try:
                element_link = output.linkify(item["id"])
            except Exception:
                element_link = u"ID {}".format(element_id_key(item["id"]))

            output.print_md(
                u"- **[{}] {}** | {} | {}".format(
                    item["kind"],
                    item["name"],
                    element_link,
                    item["error"]
                )
            )

    if failed_count or skipped_count or result["cancelled"]:
        message_lines = [
            u"Ungroup thành công: {} Group".format(success_count),
            u"Không thể Ungroup: {} Group".format(failed_count),
            u"Đã bỏ qua: {} Group".format(skipped_count)
        ]

        if result["cancelled"]:
            message_lines.append(u"Người dùng đã dừng giữa quá trình; các Group đã xử lý vẫn được giữ lại.")

        forms.alert(
            u"\n".join(message_lines),
            title=u"Ungroup Groups",
            warn_icon=failed_count > 0
        )

    elif success_count:
        forms.toast(
            u"Đã Ungroup {} Group.".format(success_count),
            title=u"Ungroup Groups"
        )

    else:
        forms.alert(
            u"Không có Group nào được Ungroup.",
            title=u"Ungroup Groups"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    preselected_ids = get_preselected_group_ids()

    window = UngroupWindow(XAML_FILE, preselected_ids)
    window.ShowDialog()

    if window.cancelled or not window.result_group_ids:
        return

    try:
        result = ungroup_group_ids(
            window.result_group_ids,
            auto_unpin=window.auto_unpin
        )
        show_result(result)

    except Exception as ex:
        output.print_md(u"## Ungroup Groups - Lỗi nghiêm trọng")
        output.print_code(traceback.format_exc())

        forms.alert(
            u"Tool không thể hoàn tất.\n\n{}".format(safe_text(ex)),
            title=u"Ungroup Groups",
            warn_icon=True
        )


if __name__ == "__main__":
    main()
