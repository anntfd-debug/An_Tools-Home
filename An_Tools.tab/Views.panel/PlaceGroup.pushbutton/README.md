# Place Group From Linked Families

## Chức năng

- Pick một hoặc nhiều `RevitLinkInstance` trực tiếp trong Active View.
- Chỉ giữ các Link thực sự hiển thị trong Active View.
- Lọc Family bằng ô nhập nhiều dòng theo `Family Name` hoặc `Type Name`.
- Mỗi dòng, dấu `;` hoặc dấu `,` là một từ khóa; các từ khóa dùng điều kiện OR.
- Chỉ xử lý Family thuộc đúng Active View:
  - Nếu Link dùng **By Linked View** trên Revit 2024+, tool dùng view-specific collector của linked view để thu hẹp ứng viên.
  - Sau đó tool vẫn bắt buộc kiểm tra lại `Plan View Range` của Active View trong host.
  - Family phải vừa giao với View Range, vừa có điểm đặt đại diện nằm trong View Range của tầng hiện tại.
  - Nếu Link dùng **By Host View**, tool kiểm tra category visibility, Crop/Section Box và Plan View Range.
- Đặt Model Group tại một trong bốn góc:
  - `LEFT_FRONT`
  - `RIGHT_FRONT`
  - `LEFT_BACK`
  - `RIGHT_BACK`
- Xoay Group theo `HandOrientation` hoặc `FacingOrientation`.
- Có Offset X/Y và góc hiệu chỉnh.
- Có chế độ **chỉ tạo 1 Group để kiểm tra góc và vị trí**.
- Nút **Kiểm tra** lưu cache danh sách Family phù hợp trong phiên giao diện.
- Khi bấm **Tạo Group**, nếu Link, Active View và nội dung lọc không đổi, tool dùng thẳng cache và không quét lại.
- Progress kiểm tra visibility chỉ đếm các Family đã khớp từ khóa, không đếm toàn bộ Family trong Link.
- Có progress bar và Cancel.
- Cancel trong lúc đặt Group sẽ rollback toàn bộ Group của lần chạy.

## Quy trình khuyến nghị

1. Chạy tool.
2. Pick nhiều Revit Link rồi bấm **Finish**.
3. Chọn Group Type và nhập từ khóa Family.
4. Bấm **Kiểm tra**. Tool chỉ hiển thị progress trên số Family đã khớp từ khóa và lưu kết quả vào cache.
5. Giữ tick `Chỉ tạo 1 Group để kiểm tra góc và vị trí`, rồi bấm **Tạo Group**. Tool dùng cache nên không quét lại.
6. Chạy và kiểm tra Group thử trong model.
7. Điều chỉnh góc/offset nếu cần. Việc đổi offset, góc, Group Type hoặc option đặt không làm mất cache.
8. Nếu sửa nội dung ô lọc, cache tự hết hiệu lực; bấm **Kiểm tra** để tạo cache mới.
9. Khi kết quả đúng, chạy lại và bỏ tick chế độ kiểm tra để tạo hàng loạt.
10. Nên giữ bật `Bỏ qua Group cùng Type đã có gần vị trí đặt` để Group thử không bị tạo trùng.

## Cơ chế cache và progress

Cache chỉ hợp lệ khi ba dữ liệu sau không thay đổi:

- Active View.
- Danh sách Revit Link đã pick.
- Nội dung ô lọc Family sau khi chuẩn hóa từ khóa.

Các thay đổi về Group Type, góc đặt, offset, hướng xoay, dung sai hoặc chế độ tạo thử không ảnh hưởng đến danh sách Family nguồn, vì vậy vẫn có thể dùng cache cũ.

Quá trình quét được chia thành hai giai đoạn:

1. Tool lọc nhanh Family/Type Name trong bộ nhớ và không mở progress bar cho toàn bộ Family không liên quan.
2. Tool mở progress bar để kiểm tra visibility chỉ cho các Family đã khớp text. Ví dụ có 8 Family khớp thì progress hiển thị từ `1 / 8` đến `8 / 8`.

Sau khi bấm **Kiểm tra**, nếu cache còn hợp lệ thì bấm **Tạo Group** sẽ đi thẳng sang progress đặt Group. Báo cáo pyRevit cũng ghi rõ tool đã dùng cache hay phải quét lại.

## Lưu ý về visibility

Revit API không cung cấp một collector host-view duy nhất trả về chính xác toàn bộ element bên trong Link cho mọi kiểu hiển thị. Tool dùng phương pháp chính xác nhất có thể:

- View-specific collector khi Link hiển thị By Linked View, nhưng collector này chỉ dùng để thu hẹp ứng viên.
- Với mọi `ViewPlan`, tool luôn kiểm tra lại View Range của Active View trong host, không phụ thuộc kiểu hiển thị của Link.
- Tool kiểm tra cả BoundingBox giao View Range và cao độ của `Location.Point`/tâm `LocationCurve`; nhờ đó Family tầng 3–4 không bị đặt Group trong mặt bằng tầng 2.
- Link visibility + category + Crop/Section Box + View Range khi hiển thị By Host View.

Các trường hợp đồ họa rất đặc biệt như element bị che khuất bởi element khác, filter tùy chỉnh sâu trong Link, non-rectangular crop phức tạp hoặc design option đặc biệt có thể cần kiểm tra thêm trong model.

## Sửa lỗi lọc sai tầng

Bản này sửa trường hợp Active View đang ở tầng 2 nhưng Group vẫn được tạo tại Family tầng 3 hoặc tầng 4. Nguyên nhân là collector của **By Linked View** có thể trả về Family nằm trong linked view nhưng không thuộc View Range của host view hiện tại.

Cơ chế mới:

1. Chuyển BoundingBox của Family từ tọa độ Link sang host.
2. Kiểm tra hình học Family có giao với View Range của Active View hay không.
3. Lấy điểm đặt đại diện từ `LocationPoint`, `LocationCurve` hoặc tâm BoundingBox.
4. Chỉ giữ Family khi điểm đại diện cũng nằm trong View Range, với dung sai 5 mm.


## Cấu trúc

```text
PlaceGroupFromLinkedFamily.pushbutton
├── script.py
├── ui.xaml
├── bundle.yaml
└── README.md
```

Tool được viết theo cú pháp tương thích IronPython 2.7 của pyRevit.

## Báo cáo kết quả pyRevit

Sau khi chạy, tool không mở các hộp thoại `forms.alert()` riêng lẻ. Toàn bộ kết quả được gom vào cửa sổ output của pyRevit, gồm:

- Active View và toàn bộ thông số đã chạy.
- Số Family phù hợp đang hiển thị.
- Số Group đã tạo.
- Số Group bị bỏ qua do trùng.
- Link không hiển thị đã bị bỏ qua.
- Family dùng BoundingBox dự phòng.
- Family nguồn bị Mirror.
- Danh sách lỗi chi tiết và traceback khi có lỗi tổng.

Mỗi Group được tạo có một dòng riêng trong bảng, gồm:

- Liên kết chọn/zoom Group.
- Group Type.
- Family và Type nguồn trong Link.
- Tên Revit Link.
- Tọa độ X, Y, Z theo đơn vị mm.
- Góc xoay.
- Góc đặt đã chọn.
- Nguồn hình học Solid hoặc BoundingBox.
- Trạng thái Mirror của Family nguồn.

Trong liên kết pyRevit, bấm tên Group để chọn phần tử; bấm biểu tượng **Show** bên cạnh để Revit đưa phần tử vào khung nhìn và zoom tới vị trí đó.
