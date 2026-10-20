# Telegram Payload Bot

Bot riêng **@pdoplus_bot**: đọc `payload.bin` hoặc `payload.bin` bên trong ZIP OTA, trích xuất các phân vùng được chọn thành `.img`, kiểm tra SHA-256 theo manifest rồi upload vào Google Drive. Controller chạy hoàn toàn trên Cloudflare; đọc metadata, trích xuất và upload chạy trên GitHub Actions. Repo hiện là public; inputs/artifacts tác vụ được mã hóa AES-256-GCM. Xem [hướng dẫn controller Cloudflare](https://github.com/ncd03-nb/telegram-onedrive-bot/blob/main/docs/cloudflare-controller.md). Docker/Render cũ đã dừng.

Bot cũng **tự nhận diện ZIP chứa các ảnh phân vùng có sẵn** (`system/*.img`, `images/*.img`, `firmware-update/*.img`...) và **`super/super.img`** chứa phân vùng logic. Có thể gửi link `super.img` raw/Android sparse trực tiếp. Không cần thêm lệnh hoặc chọn định dạng: `/list <link>` rồi `/extract <link hoặc mã ROM> system,vendor`.

## Sử dụng

1. Mở bot và gửi `/start`.
2. Gửi `/list <link ROM>`. Bot cập nhật một tin “Đang đọc…” riêng; khi đọc xong mới gửi một tin kết quả reply lệnh gốc, gộp mã ROM và danh sách phân vùng/dung lượng. Danh sách dài có nút Trước/Sau trên cùng tin nhắn. Link không kèm lệnh được bỏ qua.
3. Gửi `/extract <mã ROM> boot,init_boot,vbmeta`, hoặc `/extract <link> boot`.
4. Tin nhắn tiến độ được chỉnh sửa khoảng 2 giây/lần khi tiến độ thay đổi sau khi runner bắt đầu. Nếu chọn nhiều phân vùng, bot nén các ảnh vào **một ZIP**, upload ZIP lên Google Drive rồi gửi **một tin kết quả reply lệnh gốc, có một nút tải**. Nếu chọn một phân vùng, kết quả là `.img` trực tiếp. Tin nhắn có phiên bản ROM, tên file, dung lượng và tên các ảnh trong ZIP. Phiên bản lấy từ metadata target build trong ZIP OTA (tối đa 64 KiB), hoặc tên/đường dẫn URL gốc và URL CDN sau redirect; không đọc query token. Nếu thiếu thông tin, hiển thị “Không xác định”; nếu chỉ có mã build, hiển thị “Build <mã>”. `/files` giữ lại phiên bản của kết quả mới. Cả tiến độ và kết quả reply trực tiếp tin nhắn yêu cầu của người dùng, giữ đúng topic trong group.

`/extract <mã ROM> all` xuất mọi phân vùng được hỗ trợ, tổng tối đa 40 GiB. `/status [mã tác vụ]` xem trạng thái; `/cancel <mã tác vụ>` hủy; `/files <mã tác vụ>` lấy lại nút tải. Mã ROM hết hạn sau 24 giờ.

Link Google Drive dùng được với cả `/list` và `/extract`, ví dụ `/extract https://drive.google.com/file/d/FILE_ID/view my_stock`. Hỗ trợ link `/file/d/...`, `/open?id=...`, `/uc?id=...`, link tải `drive.usercontent.google.com`, resource key và trang xác nhận tải file lớn. Drive phải chứa **file ZIP OTA hoặc payload.bin**, được chia sẻ **Bất kỳ ai có đường liên kết / Người xem** và cho phép tải. Bot lấy byte bằng HTTP Range để chỉ đọc các phân vùng cần thiết, không tải cả ROM. Phiên bản ROM có thể lấy từ tên file Drive hoặc metadata bên trong ZIP.

Đọc nguồn Drive không sử dụng OAuth của chủ bot: người dùng bot không thể dùng link để mở file riêng tư của tài khoản đã kết nối. OAuth hiện có chỉ dùng cho upload kết quả. Link thư mục/Docs, quota tải xuống, yêu cầu đăng nhập hoặc nguồn không hỗ trợ Range sẽ báo lỗi rõ ràng; không bỏ qua các giới hạn của Google Drive.

Bot đã bật `PUBLIC_BOT=true`: mọi người được dùng trong chat riêng; `ALLOW_GROUPS=true` nhận lệnh bắt đầu bằng `/` trong group và supergroup, kể cả topic. Không cần thêm `@pdoplus_bot`; bot bỏ qua link không kèm lệnh, lệnh gửi đích danh bot khác và nội dung trò chuyện thông thường. Tin nhắn phản hồi reply đúng lệnh của người gửi. Mỗi người tối đa 3 tác vụ đang chờ/chạy, các tác vụ được kiểm tra theo chủ tác vụ. `/status`, `/cancel`, mã ROM và `/files` được kiểm tra theo chủ tác vụ.

Trong @BotFather, `/setprivacy` → chọn @pdoplus_bot → Disable. Nếu bot đã ở trong group trước khi đổi, xóa rồi thêm lại. Bot cần quyền gửi tin nhắn; không cần quyền quản trị. [Telegram group privacy](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).

Google Drive tạo thư mục `PayloadBot/<mã tác vụ>/`. `PUBLIC_FILES=true` chia sẻ **riêng file kết quả** với quyền “anyone with link / reader” sau khi upload và kiểm tra checksum; người khác không cần tài khoản Drive của chủ bot để tải. Với ảnh lớn, Google có thể hiện trang xác nhận tải. Bot này không tự xóa ảnh theo lịch 12h của bot OneDrive. Đặt `PUBLIC_BOT=false` để giới hạn theo `ALLOWED_USER_IDS`, `ALLOW_GROUPS=false` để chỉ nhận chat riêng, `PUBLIC_FILES=false` để giữ kết quả mới riêng tư. Thay đổi cờ không thu hồi quyền chia sẻ của file cũ.

## Cấu hình Docker cũ và GitHub Actions

Phần Docker dưới đây dành cho môi trường thử/cấu hình OAuth. Host production đã chuyển sang Cloudflare; không cần giữ máy bật. Quyền truy cập controller được đặt trong secret `BOTS_CONFIG`; các biến quyền của Actions cần được đồng bộ khi đổi cấu hình.

```powershell
Copy-Item .env.example .env
# Điền token bot riêng, user ID và Actions PAT trong .env.
python scripts/google_login.py --client config/google-client.json
# OAuth client cần là Desktop app; scope drive.file cho phép quản lý file do ứng dụng tạo.
docker compose up -d --build
```

Host controller: `http://127.0.0.1:8082/health`. File OAuth `config/google-token.json`, `.env` và SQLite `data/jobs.sqlite3` là dữ liệu riêng, không commit. Script `scripts/setup_github.py` tạo repo riêng tư `ncd03-nb/telegram-payload-bot`, mã hóa và lưu ba Actions secrets `TELEGRAM_BOT_TOKEN`, `ALLOWED_USER_IDS`, `GOOGLE_TOKEN_B64`, và lưu dispatch token trong `.env`. Script đồng bộ ba Actions variables `PUBLIC_BOT`, `ALLOW_GROUPS`, `PUBLIC_FILES` từ `.env`; chạy lại script khi đổi cờ để controller và runner có cùng cấu hình. Script cần PyNaCl (`pip install pynacl`) và quyền GitHub tạo repo/secrets; controller chỉ cần Actions read/write trên repo bot.

Workflow `extract.yml` nhận URL và tên phân vùng qua JSON/env; không ghép input vào lệnh shell. Quyền workflow chỉ `contents: read`. File ảnh đi thẳng vào Drive, không lưu trong Actions artifacts. Artifact chỉ gồm metadata kết quả, tồn tại 2 ngày để controller phục hồi. `MAX_CONCURRENT_JOBS=0` bỏ trần chạy song song tại controller: mọi tác vụ đã nhận được gửi lên GitHub ngay, mỗi tác vụ có observer riêng. GitHub vẫn xếp hàng theo số runner/giới hạn concurrency của toàn tài khoản và quota phút chạy. Có thể đặt từ 1 đến 20 để tự giới hạn concurrency tại bot. Giới hạn nhận yêu cầu (3 tác vụ/người, tổng 20) vẫn áp dụng độc lập. Restart controller phục hồi từng runner đang chạy, đối chiếu theo mã duy nhất, không tự dispatch lại khi HTTP không rõ kết quả. Thông báo phân biệt chờ lượt xử lý trong bot với đã gửi Actions/chờ runner.

Google OAuth của ứng dụng External ở trạng thái Testing có thể hết hạn refresh token sau 7 ngày; khi gặp lỗi OAuth, đăng nhập lại rồi cập nhật secret `GOOGLE_TOKEN_B64`. Private GitHub Actions có quota phút chạy; không phải tài nguyên miễn phí vô hạn. Controller phải online để nhận lệnh mới; runner đang chạy tiếp tục nếu máy controller tắt. Không có host dự phòng được triển khai cho bot này.

## Phạm vi hỗ trợ

- HTTP(S) công khai, server hỗ trợ `Range`/HTTP 206. Từ chối IP nội bộ, cổng tùy chỉnh, URL chứa credentials; kiểm tra DNS cho cả redirect.
- Link chia sẻ Google Drive công khai được đổi sang endpoint tải, xử lý xác nhận và giữ resource key. Không gửi OAuth Google/GitHub tới link nguồn hoặc CDN.
- ZIP/ZIP64, `payload.bin` đặt ở thư mục gốc với phương thức `ZIP_STORED`. ZIP nén payload ở lớp ZIP hoặc mã hóa sẽ báo lỗi, không tải cả ROM để thử.
- ZIP/ZIP64 chứa ảnh `.img` riêng: đọc đúng entry bằng Range, hỗ trợ `ZIP_STORED` và DEFLATE, kiểm tra CRC32/kích thước trước khi upload. Chỉ lấy tên file làm tên phân vùng; từ chối đường dẫn không an toàn và tên phân vùng trùng nhau.
- `super.img` raw hoặc Android sparse, liblp v10.0–10.2, một block device: kiểm tra SHA-256 geometry/header/bảng metadata, giới hạn và chồng lấn extents; hỗ trợ LINEAR/ZERO và sparse RAW/FILL/DONT_CARE. Tự dùng metadata backup nếu bản chính lỗi. Slot duy nhất có dữ liệu hiện thành tên `system`/`vendor`; nếu cả hai slot có dữ liệu thì giữ `_a`/`_b` để người dùng chọn đúng ảnh. Không hỗ trợ super chia nhiều file/device hoặc ảnh mã hóa.
- Ảnh ZIP/super không có SHA-256 chuẩn của từng phân vùng như payload OTA. Bot tính SHA-256 kết quả và xác minh MD5/kích thước khi upload Drive; metadata hash/CRC không thay thế xác minh chữ ký ROM. CRC của toàn super nén chỉ kiểm tra được khi đã đọc hết entry.
- Payload v2, full OTA: `REPLACE`, `REPLACE_BZ`, `REPLACE_XZ`, `ZERO`, `DISCARD`. Mỗi operation giới hạn 32 MiB nén/64 MiB giải nén. Kiểm tra SHA-256 operation nếu có và bắt buộc SHA-256 ảnh cuối. Có thể liệt kê delta OTA nhưng không xuất operation cần ảnh cũ.
- Đọc metadata khoảng vài trăm KiB; chỉ tải các operation của phân vùng được chọn. Không cần tải toàn bộ ROM 10–12 GB. Ảnh được ghi một phân vùng/lần trên runner, kiểm tra SHA-256 rồi thêm vào ZIP64 (DEFLATE level 1) và xóa ảnh trung gian. Chỉ ZIP cuối được upload resumable 8 MiB/chunk; kiểm tra dung lượng và MD5 trên Drive rồi xóa bản local. Với một phân vùng, upload `.img` trực tiếp. ZIP lớn vẫn cần chỗ trống trên runner; nếu không đủ cho ZIP và ảnh đang nén, bot báo chọn ít phân vùng hơn. Giới hạn 40 GiB là tổng ảnh, không đảm bảo mọi ROM đủ chỗ để xuất `all`.
- Riêng `super.img` nằm trong ZIP DEFLATE: phải giải nén tuần tự phần trước vùng cần đọc. Bot lưu cache trên ổ runner, bộ đệm giải nén có giới hạn, hiện tiến độ cache và dọn khi tác vụ kết thúc/hủy. ZIP có ảnh riêng chỉ đọc entry được chọn. Phân vùng nằm cuối super hoặc `/extract all` có thể chậm/tốn ổ; nếu runner thiếu dung lượng sẽ báo lỗi và không upload ảnh dở dang. Quét metadata có timeout 8 phút, workflow quét tối đa 10 phút.
- OPlus `downloadCheck` được xử lý bằng header của updater (`User-Agent: okhttp/3.12.12`, `userId: oplus-ota|16002018`), theo cách trong repo ROM hiện có. Chỉ gửi các header này đến host OTA OPlus được hỗ trợ, không gửi credential Google/GitHub tới CDN.
- Kiểm tra hash không xác nhận chữ ký nhà sản xuất; bot xuất ảnh để tải về, không tự flash thiết bị.

## Tốc độ trích xuất

Runner mặc định tải trước 8 nhóm Range song song, gom các operation có dữ liệu liền nhau vào một request tối đa 8 MiB và giải nén bằng 2 luồng. Nhóm hoàn tất được ghi ngay vào đúng extents, không chờ một request chậm ở đầu hàng. Mỗi nhóm tối đa 32 MiB giải nén (operation đơn vẫn theo giới hạn 64 MiB); cửa sổ tải trước có giới hạn bộ nhớ. Full OTA có extents chồng lấn bị từ chối. ZERO/DISCARD giữ vùng sparse bằng zero trên file mới, không cần ghi lại hàng GB số 0.

Kiểm tra SHA-256 từng operation và ảnh cuối vẫn bắt buộc. SHA-256 và MD5 cuối được tính trong một lượt đọc; upload tái sử dụng MD5 đã kiểm tra. Drive resumable dùng chunk 32 MiB, vẫn xác nhận offset khi mất phản hồi và đối chiếu checksum/kích thước trên Drive. ZIP64 vẫn dùng DEFLATE level 1 để cân bằng CPU và dung lượng upload. Tin tiến độ hiện dung lượng và tốc độ trung bình MiB/s, cập nhật 2 giây/lần khi có thay đổi.

Có thể chỉnh Actions variables `EXTRACT_WORKERS` (1–16, mặc định 8), `RANGE_BATCH_MIB` (1–32, mặc định 8), `DECODE_WORKERS` (1–4, mặc định 2), `DRIVE_CHUNK_MIB` (1–256, mặc định 32). Số luồng hoặc khối upload lớn hơn không đảm bảo nhanh hơn nếu CDN, CPU hoặc đường mạng giới hạn; đo lại trên cùng nguồn khi chỉnh. Script setup đồng bộ các biến này từ `.env`. Tác vụ đang chạy giữ bản mã đã checkout; tối ưu áp dụng cho tác vụ mới.

Workflow thủ công **Measure Drive upload throughput** đo cùng dữ liệu trên runner trực tiếp và Docker (bridge/host), mỗi chế độ hai lần. File thử 1,5 GiB được upload riêng tư, kiểm tra MD5/kích thước rồi xóa cùng thư mục tác vụ thử. Workflow lưu artifact số đo hai ngày; không gửi tin Telegram hoặc thay đổi file ROM của người dùng. Script `scripts/benchmark_drive.py` hỗ trợ `BENCHMARK_CHUNKS` và `BENCHMARK_SIZE_MIB` để đo cấu hình khác; mặc định khi chạy script riêng là file 512 MiB với các khối 32/128/256/32 MiB.

## Kiểm thử

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/probe.py --sources config/test-sources.json --partitions vbmeta,init_boot,boot
```

`config/test-sources.json` là mapping tên hãng → URL riêng, không commit URL ký. Kết quả báo cáo/ảnh local nằm trong `data/`. Kiểm thử gồm checksum lỗi, giới hạn giải nén, extent vượt file, đường dẫn không hợp lệ, ZIP64, delta bị từ chối, Range/ETag sai và phục hồi workflow sau restart.

Hỗ trợ nguồn Drive đã được kiểm tra bằng 74 bài kiểm tra, gồm trang xác nhận file lớn, resource key, link giả mạo, redirect đăng nhập và file riêng tư/quota. [Lượt thử Actions](https://github.com/ncd03-nb/telegram-payload-bot/actions/runs/39222383586) đọc ZIP OTA tổng hợp từ Drive công khai, trích `boot`, kiểm tra SHA-256 và upload kết quả thành công. File nguồn/kết quả thử đã chuyển vào thùng rác. Một link ROM Drive thực tế trước đó bị HTTP 200 đã đọc được 65 phân vùng qua khoảng 400 KiB dữ liệu; chưa tải cả ROM hoặc trích phân vùng lớn trong bài kiểm tra này.

Nguồn định dạng: [AOSP update_metadata.proto](https://android.googlesource.com/platform/system/update_engine/+/refs/heads/main/update_metadata.proto). Upload theo [Google Drive resumable upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads), quyền theo [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

Định dạng super/sparse theo AOSP [liblp metadata_format.h](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/include/liblp/metadata_format.h) và [libsparse sparse_format.h](https://android.googlesource.com/platform/system/core/+/refs/heads/main/libsparse/sparse_format.h). Bộ kiểm tra hiện có 86 bài, gồm checksum metadata/backup, extents vượt ảnh, sparse RAW/FILL/ZERO, CRC ZIP, ZIP64, đường dẫn không an toàn và dọn cache trên đúng ổ dữ liệu của runner.
