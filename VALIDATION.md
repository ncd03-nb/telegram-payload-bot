# Kết quả kiểm thử — 13/10/2026 (GMT+7)

Bot: **@pdoplus_bot**. Controller Docker `telegram-payload-bot-bot-1`. Repo private: https://github.com/ncd03-nb/telegram-payload-bot. Các lượt thử ảnh riêng bên dưới chạy trước khi bật chế độ công khai; kết quả mới hiện chia sẻ anyone/reader.

## Hai nguồn ROM thật

| Nguồn | ZIP OTA | Phân vùng trong manifest | Byte tải để xuất 3 ảnh | Runner gồm build/upload |
|---|---:|---:|---:|---:|
| Xiaomi OS4.0.24.0.XEOCNXM madrid | 12.465.321.399 | 66 | 14.935.421 | 38 giây |
| OPPO downloadCheck do người dùng cung cấp | 10.088.874.007 | 67 | 15.134.289 | 46 giây |

Chỉ xuất **vbmeta, init_boot, boot** ở lần thử này, không tuyên bố đã trích xuất toàn bộ 66/67 phân vùng. Những con số thời gian là kết quả của hai tác vụ cụ thể, không đảm bảo tốc độ cho ROM/phân vùng khác. OPPO cần header updater để nhận redirect sang CDN, sau đó đọc Range/ZIP64 bình thường.

Các ảnh đều được xác minh SHA-256 theo manifest sau trích xuất; sau upload kiểm tra dung lượng và MD5 do Google Drive trả. Metadata Drive được đọc lại cho cả 6 ảnh, ảnh không được chia sẻ `anyone`. Đã tải lại toàn bộ `vbmeta.img` của mỗi hãng qua Drive API và kiểm tra SHA-256. Chưa tải lại toàn bộ boot/init_boot từ Drive; kiểm tra sau upload cho chúng dựa trên dung lượng và MD5 từ Drive.

| Nguồn | Ảnh | Byte | SHA-256 |
|---|---|---:|---|
| Xiaomi | vbmeta.img | 12.288 | `7ecd568785b5a7a79aea078dfe6d8c8e3666d56b8e8d77ed90cfe491d67d2999` |
| Xiaomi | init_boot.img | 8.388.608 | `f75ff54ab0d7cc39d8d6ceb1f957489d47dfe302dafd1c1a2a06ad66aa7b437d` |
| Xiaomi | boot.img | 100.663.296 | `2ad437afb0c5edbcbb9430066313d75883c39f4853ddc0541ff7e5eeb32bb92a` |
| OPPO | vbmeta.img | 8.192 | `d184b4fd02b78407d538e1a9b751ad559092bd52b5214b8044b6c6c81579a8bb` |
| OPPO | init_boot.img | 8.388.608 | `146fcd9f56f6a7367d3bdfda5d25e9437adf71c12e319b6d747d75c01ca9e0d4` |
| OPPO | boot.img | 100.663.296 | `89972fd025861ec52e596ab28ecd4a1e11dfa55bd8d32c928437b36b1f1c47f9` |

## Actions và Telegram

- Xiaomi job `adc9016ca0d4`: [workflow thành công](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38345711510), 11:17:20–11:17:58 GMT+7.
- OPPO job `6e998c982bdc`: [workflow thành công](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38345779399), 11:18:11–11:18:57 GMT+7.
- Cả hai runner xác nhận gửi thông báo kết quả vào Telegram. Controller đã nhận artifact và chuyển cả hai job sang `done`.
- `/files 6e998c982bdc` đã thử qua handler và gửi lại 3 nút tải thành công.
- 15 tests đạt trên Windows Python 3.14 và Docker Linux Python 3.12; CI commit `1bbbe77` thành công. Docker build đạt.
- Secrets Telegram, Google OAuth và GitHub token nằm trong file riêng/Actions secrets; kiểm tra không có giá trị secret trong tập file được commit.

Kết quả thực tế chứa Drive IDs/URL riêng và nằm ở `data/actions-xiaomi-report.json`, `data/actions-oppo-report.json`, không commit. Ảnh kiểm thử trên Drive giữ lại cho người dùng tải; không tự xóa.

## ZIP nhiều phân vùng và chế độ group công khai

- Xiaomi `f3a3eba45fcf`: [Actions thành công](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38346181287), ZIP 20.958.944 byte.
- OPPO `ec0d38d3c6cf`: [Actions thành công](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38346249067), ZIP 21.150.744 byte.
- Mỗi tác vụ xuất `boot.img` và `init_boot.img` thành **một ZIP**, upload một file, gửi một thông báo kết quả/một nút tải. Cả hai `notified=true`, controller `done`.
- Đã tải lại toàn bộ hai ZIP từ Drive, kiểm tra SHA-256 ZIP, kiểm tra danh sách file và SHA-256 của từng ảnh trong ZIP, tất cả đạt. Hai ZIP kiểm thử đã có quyền anyone/reader; API permissions xác nhận.
- Bot API `getMe` xác nhận `can_join_groups=true`, `can_read_all_group_messages=true` sau khi người dùng tắt Privacy.
- 25 tests đạt trên Windows Python 3.14 và Docker Linux Python 3.12. Tests bao gồm người dùng ngoài whitelist trong chế độ public, lệnh không có username trong supergroup, reply/topic, truyền owner/chat/thread vào Actions, từ chối đọc kết quả/hủy tác vụ của người khác, bỏ qua hội thoại/lệnh bot khác và chia sẻ file chỉ sau checksum đạt.
- Kiểm thử group ở mức handler/API payload, chưa kiểm thử đầu-cuối trong group thật vì chưa có group được thêm bot trong phiên này. Báo cáo ZIP nằm trong `data/zip-xiaomi-report.json`, `data/zip-oppo-report.json`, không commit.

## Phiên bản ROM trong kết quả

- Kết quả mới lưu `rom_version` trong metadata artifact/outputs để `/files` hiển thị lại cùng phiên bản.
- Đọc lại directory và metadata của hai URL thật: Xiaomi hiển thị `OS4.0.24.0.XEOCNXM` từ tên ZIP; mã build nội bộ trong metadata không thay thế phiên bản này. Chỉ đọc khoảng 67 KiB, không tải lại phân vùng.
- Với URL OPPO mẫu, tên ZIP là hash, nội dung metadata không phải văn bản đọc được; kết quả hiển thị “Không xác định”. Không suy đoán phiên bản từ hash/token URL. Với metadata target build dạng văn bản hoặc tên gói có phiên bản OPlus, bot tự nhận diện.
- 32 tests đạt trên Windows; thêm tests metadata stored/deflate, CRC lỗi, metadata quá lớn, bỏ qua query URL, phân biệt target/pre-build, ưu tiên phiên bản dễ đọc hơn mã build nội bộ và định dạng kết quả giữ reply/topic/tên phân vùng.

## Chạy song song

- Nguyên nhân job `66a2cf055bf5` chờ: controller cũ chỉ có một worker, đang observe `b1d47d5fa9de`; job mới chưa được dispatch (không có run ID/dispatch timestamp).
- Controller hiện có scheduler với observer riêng từng tác vụ, `MAX_CONCURRENT_JOBS=0` bỏ trần chạy song song tại bot. Giới hạn tiếp nhận vẫn giữ như README; giới hạn runner của GitHub áp dụng cho cả tài khoản.
- 35 tests đạt trên Windows: 6 tác vụ cùng được observe trong khi job đầu chưa hoàn tất; job đã dispatch được resume, không gửi trùng; cấu hình giới hạn tùy chọn trả slot cho job tiếp theo; shutdown hủy observers nhưng giữ trạng thái để phục hồi; job đã hủy không được dispatch.

## Pipeline nhanh cho phân vùng lớn

- Benchmark local bằng cùng URL OPPO, xuất toàn bộ `vendor` 362,15 MiB và kiểm tra SHA-256 theo manifest ở cả hai phiên bản: bản tuần tự `d68e62d` mất 38,643 giây/182 request; bản gom Range 8 MiB + prefetch 8 + decode 2 mất 11,781 giây/33 request. Cả hai tải 232,97 MiB dữ liệu và SHA-256 giống nhau: `f0d06418ceebbdfa074c2514d7eeee062d37c574460f4c4fedfbac9f4fbfc9cf`. Thời gian gồm trích và kiểm tra ảnh, chưa gồm upload Drive/ZIP hay chờ runner; không đảm bảo hệ số tăng tốc trên CDN/ROM khác.
- Pipeline xử lý nhóm hoàn tất trước, giữ extents độc lập và kiểm tra hash toàn ảnh. Thêm tests nhóm đến sai thứ tự, một request chậm không chặn cửa sổ tải tiếp, hash hỏng, cancellation dọn file/tasks, extents chồng lấn, upload chunk lớn và tái sử dụng MD5 đã kiểm tra.
- Báo cáo benchmark nằm trong `data/speed-benchmark/report.json`, không commit URL hoặc file ảnh.
- Xiaomi `system_ext` 780,17 MiB: bản tuần tự mất 311,693 giây/391 request; bản prefetch/gom nhóm mất 20,291 giây/74 request. SHA-256 đúng manifest ở cả hai: `37530880b9d7df04157d8da48c63bd4d4d66c73e52df6c9ad94985c904306abc`.
- Đo lại bản xử lý nhóm hoàn tất trước và so cấu hình 8/8, 8/16, 16/16 (luồng tải/MiB mỗi Range), vẫn decode 2: cấu hình 8/8 đạt OPPO 9,712 giây, Xiaomi 18,355 giây; 8/16 đạt 9,879 và 18,502 giây. Chọn 8/8 mặc định vì tăng nhóm/luồng không cải thiện thời gian trong phép thử này. Kết quả đo gồm checksum, không gồm ZIP/upload; tốc độ CDN, tải CPU/mạng local có thể dao động. Báo cáo cấu hình ở `data/speed-benchmark/tuning.json`.
- 41 tests đạt trên Windows Python 3.14 và Docker Linux Python 3.12, bao gồm pipeline hủy/lỗi/các nhóm sai thứ tự và phục hồi Actions đang chạy.
- Kiểm thử bản mới trực tiếp trên [GitHub Actions run 38351090632](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38351090632), job `1febaf69476c`: `vendor` OPPO trích 9,724 giây + checksum 0,804 giây; `boot` trích 2,775 giây + checksum 0,220 giây. SHA-256 vendor khớp manifest và benchmark local. Vendor dùng 33 nhóm/35 request (bao gồm retry), boot chỉ tải 3 request và giữ zero extents sparse.
- Hai ảnh được đóng thành một ZIP 317.079.656 byte, upload bằng chunk 32 MiB trong 15,774 giây (gồm chuẩn bị thư mục/session). Drive xác nhận MD5/kích thước, quyền anyone/reader; runner `done`, `notified=true`. Báo cáo ở `data/performance-actions-report.json`.
- [CI cho mã 34c8c72](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/38351021358) thành công. Controller health HTTP 200; job đang chạy trước triển khai tiếp tục được observe, không tự gửi lại workflow.
