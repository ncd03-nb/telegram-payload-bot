# Telegram Payload Bot

Bot riêng **@pdoplus_bot**: đọc `payload.bin` hoặc `payload.bin` bên trong ZIP OTA, trích xuất các phân vùng được chọn thành `.img`, kiểm tra SHA-256 theo manifest rồi upload vào Google Drive. Controller chạy Docker; tác vụ nặng chạy GitHub Actions.

## Sử dụng

1. Mở bot và gửi `/start`.
2. Gửi link ROM, hoặc `/list <link>`. Bot trả mã ROM và danh sách phân vùng/dung lượng.
3. Gửi `/extract <mã ROM> boot,init_boot,vbmeta`, hoặc `/extract <link> boot`.
4. Tin nhắn tiến độ được chỉnh sửa khoảng 15 giây/lần khi runner bắt đầu. Nếu chọn nhiều phân vùng, bot nén các ảnh vào **một ZIP**, upload ZIP lên Google Drive rồi gửi **một tin nhắn có một nút tải**. Nếu chọn một phân vùng, kết quả là `.img` trực tiếp. Tin nhắn có tên file và dung lượng.

`/extract <mã ROM> all` xuất mọi phân vùng được hỗ trợ, tổng tối đa 40 GiB. `/status [mã tác vụ]` xem trạng thái; `/cancel <mã tác vụ>` hủy; `/files <mã tác vụ>` lấy lại nút tải. Mã ROM hết hạn sau 24 giờ. Chỉ Telegram user trong `ALLOWED_USER_IDS` được dùng, chỉ chat riêng.

Google Drive tạo thư mục `PayloadBot/<mã tác vụ>/`. File giữ **riêng tư**, không tự bật chia sẻ công khai; khi mở nút tải phải đăng nhập tài khoản Drive đã kết nối. Với ảnh lớn, Google có thể hiện trang xác nhận tải. Bot này không tự xóa ảnh theo lịch 12h của bot OneDrive.

## Docker và GitHub Actions

```powershell
Copy-Item .env.example .env
# Điền token bot riêng, user ID và Actions PAT trong .env.
python scripts/google_login.py --client config/google-client.json
# OAuth client cần là Desktop app; scope drive.file cho phép quản lý file do ứng dụng tạo.
docker compose up -d --build
```

Host controller: `http://127.0.0.1:8082/health`. File OAuth `config/google-token.json`, `.env` và SQLite `data/jobs.sqlite3` là dữ liệu riêng, không commit. Script `scripts/setup_github.py` tạo repo riêng tư `ncd03-nb/telegram-payload-bot`, mã hóa và lưu ba Actions secrets `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, `GOOGLE_TOKEN_B64`, và lưu dispatch token trong `.env`. Script cần PyNaCl (`pip install pynacl`) và quyền GitHub tạo repo/secrets; controller chỉ cần Actions read/write trên repo bot.

Workflow `extract.yml` nhận URL và tên phân vùng qua JSON/env; không ghép input vào lệnh shell. Quyền workflow chỉ `contents: read`. File ảnh đi thẳng vào Drive, không lưu trong Actions artifacts. Artifact chỉ gồm metadata kết quả, tồn tại 2 ngày để controller phục hồi. Một tác vụ chạy ở một thời điểm trên controller; restart controller giữ runner đang chạy và đối chiếu theo mã duy nhất, không tự dispatch lại khi HTTP không rõ kết quả.

Google OAuth của ứng dụng External ở trạng thái Testing có thể hết hạn refresh token sau 7 ngày; khi gặp lỗi OAuth, đăng nhập lại rồi cập nhật secret `GOOGLE_TOKEN_B64`. Private GitHub Actions có quota phút chạy; không phải tài nguyên miễn phí vô hạn. Controller phải online để nhận lệnh mới; runner đang chạy tiếp tục nếu máy controller tắt. Không có host dự phòng được triển khai cho bot này.

## Phạm vi hỗ trợ

- HTTP(S) công khai, server hỗ trợ `Range`/HTTP 206. Từ chối IP nội bộ, cổng tùy chỉnh, URL chứa credentials; kiểm tra DNS cho cả redirect.
- ZIP/ZIP64, `payload.bin` đặt ở thư mục gốc với phương thức `ZIP_STORED`. ZIP nén payload ở lớp ZIP hoặc mã hóa sẽ báo lỗi, không tải cả ROM để thử.
- Payload v2, full OTA: `REPLACE`, `REPLACE_BZ`, `REPLACE_XZ`, `ZERO`, `DISCARD`. Mỗi operation giới hạn 32 MiB nén/64 MiB giải nén. Kiểm tra SHA-256 operation nếu có và bắt buộc SHA-256 ảnh cuối. Có thể liệt kê delta OTA nhưng không xuất operation cần ảnh cũ.
- Đọc metadata khoảng vài trăm KiB; chỉ tải các operation của phân vùng được chọn. Không cần tải toàn bộ ROM 10–12 GB. Ảnh được ghi một phân vùng/lần trên runner, kiểm tra SHA-256 rồi thêm vào ZIP64 (DEFLATE level 1) và xóa ảnh trung gian. Chỉ ZIP cuối được upload resumable 8 MiB/chunk; kiểm tra dung lượng và MD5 trên Drive rồi xóa bản local. Với một phân vùng, upload `.img` trực tiếp. ZIP lớn vẫn cần chỗ trống trên runner; nếu không đủ cho ZIP và ảnh đang nén, bot báo chọn ít phân vùng hơn. Giới hạn 40 GiB là tổng ảnh, không đảm bảo mọi ROM đủ chỗ để xuất `all`.
- OPlus `downloadCheck` được xử lý bằng header của updater (`User-Agent: okhttp/3.12.12`, `userId: oplus-ota|16002018`), theo cách trong repo ROM hiện có. Chỉ gửi các header này đến host OTA OPlus được hỗ trợ, không gửi credential Google/GitHub tới CDN.
- Kiểm tra hash không xác nhận chữ ký nhà sản xuất; bot xuất ảnh để tải về, không tự flash thiết bị.

## Kiểm thử

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/probe.py --sources config/test-sources.json --partitions vbmeta,init_boot,boot
```

`config/test-sources.json` là mapping tên hãng → URL riêng, không commit URL ký. Kết quả báo cáo/ảnh local nằm trong `data/`. Kiểm thử gồm checksum lỗi, giới hạn giải nén, extent vượt file, đường dẫn không hợp lệ, ZIP64, delta bị từ chối, Range/ETag sai và phục hồi workflow sau restart.

Nguồn định dạng: [AOSP update_metadata.proto](https://android.googlesource.com/platform/system/update_engine/+/refs/heads/main/update_metadata.proto). Upload theo [Google Drive resumable upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads), quyền theo [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).
