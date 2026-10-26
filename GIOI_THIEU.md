# Cutin Image: tạo, chỉnh và dựng ảnh sản phẩm bằng AI

Cutin Image là bộ công cụ ảnh AI cho người bán hàng, đội marketing và nhà thiết kế. Từ một câu mô tả hoặc vài tấm ảnh sản phẩm, hệ thống tạo ra ảnh dùng được ngay cho sàn thương mại điện tử, quảng cáo và mạng xã hội.

Phía sau giao diện là một worker GPU chạy trên Runpod, vận hành **ba mô hình** và cung cấp **năm tính năng**.

---

## Ba mô hình

| Mô hình | Vai trò | Điểm đáng chú ý |
|---|---|---|
| **Qwen-Image-2.1** | Mô hình chất lượng cao, dùng cho mức "Tinh" | Hiểu chỉ dẫn dài, nhận nhiều ảnh tham chiếu để sửa và ghép ảnh. Có bản turbo 6 bước (LoRA) cho mức "Nhanh" của Chỉnh ảnh. Dùng theo giấy phép nghiên cứu phi thương mại, nên chỉ mở cho các tài khoản được cấp quyền. |
| **Z-Image-Turbo** | Tạo ảnh từ văn bản ở mức "Nhanh" | Chỉ 9 bước, ra ảnh trong vài giây. Hợp để thử ý tưởng và dựng nhiều phương án. |
| **FLUX.2 klein 4B** | Mô hình thử nghiệm ("FLUX (thử)") | Chỉ 4 bước, không cần CFG, giấy phép Apache-2.0. Một mô hình làm được cả tạo ảnh, chỉnh ảnh và ghép nhiều ảnh. Vừa GPU 24 GB. Đang trong giai đoạn so sánh chất lượng với hai mô hình còn lại. |

Ngoài ba mô hình tạo ảnh, tính năng Xoá nền mức "Nhanh" dùng công cụ **rembg**, không cần nạp mô hình khuếch tán nên cho kết quả trong vài giây.

---

## Năm tính năng và ứng dụng thực tế

### 1. Tạo ảnh (text → ảnh)
Mô tả bằng lời, nhận ảnh. Chọn tỉ lệ (1:1, 4:3, 3:4, 16:9, 9:16) và độ phân giải 1024, 1536 hoặc 2048.

- **Nhanh** (Z-Image): vài giây mỗi ảnh.
- **Tinh** (Qwen): chi tiết hơn, chậm hơn.
- **FLUX (thử)**: 4 bước, để so sánh.

*Ứng dụng:* ảnh minh hoạ cho bài đăng, nền cho banner, ý tưởng bối cảnh chụp sản phẩm trước khi đầu tư buổi chụp thật.

### 2. Chỉnh ảnh
Chọn một ảnh rồi nói điều cần đổi: "đổi màu áo thành đỏ", "làm nền tối hơn". Phần còn lại của ảnh được giữ nguyên.

- **Nhanh**: Qwen với LoRA turbo 6 bước.
- **Tinh**: Qwen đầy đủ, bám chỉ dẫn tốt hơn.
- **FLUX (thử)**: klein 4 bước.

*Ứng dụng:* tạo biến thể màu sắc cho cùng một sản phẩm, đổi nền cho chiến dịch theo mùa, sửa lỗi nhỏ mà không phải chụp lại.

### 3. Ghép ảnh
Ghép từ 2 đến 10 ảnh thành một cảnh. Thứ tự ảnh là thứ tự tham chiếu, ví dụ "đặt sản phẩm ở ảnh 1 lên mặt bàn của ảnh 2".

*Ứng dụng:* đặt sản phẩm vào bối cảnh có sẵn, tạo bộ sưu tập từ nhiều món, dựng ảnh quảng cáo từ tư liệu rời rạc.

### 4. Xoá nền
Tách nền và trả về ảnh PNG nền trong suốt.

- **Nhanh**: rembg, vài giây, viền có thể chưa mượt.
- **Tinh**: Qwen xuất ảnh RGBA, viền mượt hơn, phù hợp tóc và lông.

*Ứng dụng:* ảnh sản phẩm nền trắng cho sàn thương mại điện tử, chuẩn bị ảnh cho thiết kế và in ấn.

### 5. Production: bộ 8 ảnh sản phẩm
Từ 1 đến 10 ảnh sản phẩm, hệ thống tạo **8 kiểu ảnh** dùng cho sàn, quảng cáo và mạng xã hội:

1. Nền trắng, chính diện
2. Ảnh hero thương hiệu
3. Cận chi tiết
4. Flat lay từ trên xuống
5. Trong bối cảnh sử dụng
6. Có người dùng (tay)
7. Concept lợi ích chính
8. Ra mắt, chừa chỗ cho chữ

Quy trình gồm hai bước: tạo **bản nháp** cả bộ để xem nhanh, sau đó chọn ảnh ưng ý và **làm lại bản cuối** với chất lượng cao. Hệ thống đo màu sản phẩm từ ảnh gốc và đưa vào chỉ dẫn để màu sắc không bị lệch. Mỗi bộ tính 8 lượt, mỗi ảnh làm lại bản cuối tính 1 lượt.

*Ứng dụng:* một người bán chỉ cần vài tấm ảnh sản phẩm để có đủ bộ ảnh đăng sàn, chạy quảng cáo và đăng mạng xã hội, thay cho nhiều buổi chụp riêng lẻ.

---

## Cách các tính năng kết hợp với nhau

Kết quả của tính năng này có thể chuyển thẳng sang tính năng khác ngay trên canvas: từ ảnh vừa tạo bấm **Sửa ảnh này**, **Xoá nền** hoặc **Ghép ảnh** mà không phải tải xuống rồi tải lên lại. Một quy trình điển hình:

1. Xoá nền ảnh sản phẩm.
2. Ghép sản phẩm vào bối cảnh có sẵn.
3. Chỉnh màu hoặc ánh sáng bằng lời.
4. Dùng Production để mở rộng thành bộ 8 ảnh.

## Lưu ý khi sử dụng

- Mức "FLUX (thử)" là bản thử nghiệm, chỉ mở cho một số tài khoản để so sánh chất lượng và tốc độ. Chưa có số liệu đánh giá chính thức, nên chưa nên coi là thay thế cho mức Tinh, nhất là với ảnh sản phẩm cần giữ nguyên logo và chữ.
- Bộ ảnh Production tạo bằng FLUX chưa hỗ trợ "làm lại bản cuối".
- Ảnh gốc tải lên chỉ lưu 24 giờ.
- Các mức chạy trên Qwen-Image-2.1 chịu giới hạn giấy phép nghiên cứu phi thương mại của mô hình này.
