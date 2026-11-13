# Báo cáo cá nhân — Day 6

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** Sổ tay sinh viên khóa 64 - 2023 (cập nhật 14/8/2023), tải từ website Trường ĐH GTVT: https://khcb.utc.edu.vn/so-tay-sinh-vien-k64-2907404. Tài liệu công khai, dùng cho mục đích học tập. Bản PDF kèm trong repo (`starter/data/raw/`) chỉ để tái lập bài lab; bản gốc tải lại được từ link trên. PDF có **88 trang vật lý → 74 trang nội dung**. Quy tắc: một trang được tính nếu text sau làm sạch có ≥ 200 ký tự **và** tỷ lệ dấu chấm ≤ 25%. 14 trang bị loại và ghi log ([ingest_errors.json](../../starter/results/ingest_errors.json)): bìa và trang trống (1, 2, 6, 73, 80), mục lục (3, 4), trang ảnh chụp màn hình app eUTC gần như không có chữ (75, 76, 77, 82, 83), trang "Ghi chép" toàn dấu chấm (87, 88). Tổng 117.523 ký tự nội dung. Chi tiết từng trang trong [manifest.json](../../starter/data/manifest.json).
- **Chuẩn hóa, lỗi và giới hạn:** đọc bằng `pypdf` 7.0.0 (chế độ `plain`), chuẩn hóa Unicode NFC, gộp khoảng trắng, bỏ header số trang `STSV * n` / `n * STSV` ở cả 88 trang (đồng thời kiểm tra số trang in trùng số trang PDF), bỏ 31 ký tự icon thuộc vùng Unicode riêng (mũi tên trong phần hướng dẫn app). Giới hạn: PDF không giữ cấu trúc bảng nên bảng giá KTX (trang 64) và bảng quy đổi điểm (trang 22) bị trích thành chữ rời rạc; chunk theo từng trang nên đoạn văn vắt qua 2 trang bị cắt đôi; không dùng OCR nên nội dung trong ảnh chụp màn hình app bị bỏ qua.
- **Embedding, vector store, metric, lệnh chạy và chi phí:** model `bge-m3` qua Ollama 0.40.2 (chạy local, digest `79076464…`), **1024 chiều**, câu hỏi và tài liệu embed giống nhau (không thêm tiền tố). Vector store: PostgreSQL 16.16 + **pgvector 0.8.7** (Docker), metric **cosine**, tìm kiếm chính xác (quét toàn bảng, không dùng index xấp xỉ). Lệnh chạy (từ `starter/`): `python -m src.run all` hoặc từng bước `ingest | verify | index | evaluate`. Hướng dẫn cài đặt đầy đủ ở [starter/README.md](../../starter/README.md). Nạp lại không nhân đôi: mỗi chunk có ID ổn định, chunk không đổi được bỏ qua, chunk cũ không còn bị xóa; hai lần `index` sau lần đầu đều bỏ qua 290/290 (A) và 197/197 (B) chunk, số dòng trong DB giữ nguyên ([index_runs.json](../../starter/results/index_runs.json)). Thời gian embed: A 97,2 giây, B 101,8 giây (tổng thời gian index 97,6 / 102,0 giây); truy vấn trung vị khoảng 3–4 ms. **Chi phí: 0** (không dùng API trả phí).
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:** mỗi chunk có `document_id`, `source` (đường dẫn PDF), `page` (trang PDF), `printed_page`. Trường bổ sung: `phan` (text: `1`, `2.1`…`2.8`, `3`), `phan_title`, `muc` (`Điều N` trong Quy chế đào tạo hoặc `Mục I…XVI` trong Phần 3), `muc_title`. Giá trị lấy trực tiếp từ các dòng tiêu đề trong sổ tay (`PHẦN 2.3.`, `Điều 9.`, `XII. …`), không suy đoán. Chọn `phan` làm trường lọc vì sổ tay nhắc cùng một chủ đề ở nhiều phần: quy định (Phần 2.x) và hướng dẫn thủ tục (Phần 3), ví dụ rèn luyện ở 2.3 và 3-II, kỷ luật ở 2.4 và 3-IV, học bổng/trợ cấp ở 2.5, 2.6 và 3-XIII.

## Cấu hình và benchmark

- **A / B / C:**
  - **A:** chunk cố định 500 ký tự, chồng lấn 50, cắt trong từng trang, không lọc → 290 chunk (trung vị 500 ký tự).
  - **B:** chunk theo cấu trúc: ngắt cứng ở PHẦN/Chương/Điều/mục La Mã; gom các khoản (`1.`), điểm (`a)`), gạch đầu dòng tới tối đa 900 ký tự; gắn "đường dẫn" `[Phần … > Điều/Mục …]` vào đầu chunk; không lọc → 197 chunk (trung vị 755 ký tự).
  - **C:** dùng **đúng các chunk và vector của B**, chỉ thêm lọc `phan` trong SQL (`WHERE phan = …`) với 5 câu có filter; câu không có filter thì C chạy không lọc. **Khác biệt duy nhất giữa B và C là bộ lọc.** A và B dùng cùng model, cùng store, cùng metric, cùng bộ câu hỏi.
- **Bộ câu hỏi (≥20 câu):** 25 câu tiếng Việt, **18 dev / 7 test**, chốt trước khi chạy. Có các loại câu hỏi trực tiếp, diễn đạt lại, điều kiện và ngoại lệ. **5 câu có filter** (3 dev: d16, d17, d18; 2 test: t06, t07), giá trị `phan` được coi là ngữ cảnh sinh viên đang đọc, cung cấp trước khi truy xuất. Mỗi câu có `expected_answer`, `gold_page`, `evidence_quote`. Lệnh `verify` kiểm tra mọi evidence đều nằm trong text đã chuẩn hóa của trang gold: kết quả 25/25 ([benchmark_verification.json](../../starter/results/benchmark_verification.json)). Nguy cơ rò rỉ: t07 cùng Phần 2.3 với d13, d16.
- **Tiêu chí chọn chiến lược (chốt trước khi xem test):** dev Evidence Hit@3 cao nhất → nếu hòa thì dev Hit@1 → nếu hòa thì tỷ lệ tuân thủ filter trên dev → nếu hòa thì ít chunk hơn.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian embed | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 13/18 | 17/18 | 290 | 97,2 s | 0 |
| A | test | 6/7 | 7/7 | 290 | (dùng chung) | 0 |
| B | dev | 15/18 | 18/18 | 197 | 101,8 s | 0 |
| B | test | 5/7 | 7/7 | 197 | (dùng chung) | 0 |
| C | dev | 15/18 | 18/18 | 197 (chung với B) | 0 (dùng lại B) | 0 |
| C | test | 6/7 | 7/7 | 197 (chung với B) | 0 (dùng lại B) | 0 |

| Strategy | Coherence trung bình /2 (n=10) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | **1,0** (10 vị trí đều điểm 1) | **20/21** | dev 1/3, test 1/2 (không áp dụng lọc) | 0: 0 · 1: 0 · 2: 7 → **2,0** |
| B | **1,9** (9 vị trí điểm 2, 1 vị trí điểm 1) | **21/21** | dev 0/3, test 0/2 (không áp dụng lọc) | 0: 0 · 1: 0 · 2: 7 → **2,0** |
| C | **1,9** (= B, cùng chunk) | **21/21** | **dev 3/3, test 2/2** | 0: 0 · 1: 0 · 2: 7 → **2,0** |

**Ghi chú nhãn thủ công:**
- **Coherence A:** cả 10 chunk đều bị cắt giữa từ hoặc giữa câu ở đầu hoặc cuối. Ví dụ ở p41, mức học bổng chính sách bị cắt còn "80% lương cơ b". Ở p55, chunk bắt đầu từ ý b) rồi lấn sang mục VIII không liên quan. Vì vậy không chunk nào đạt 2, nhưng phần giữa vẫn đọc được nên không chunk nào bị 0.
- **Coherence B:** chỉ p20 được 1 điểm. Câu "vi phạm các điều kiện trên" nhắc tới các ý a)–c) nằm ở chunk trước.
- **Metadata:** đúng trang 63/63 bản ghi. Lỗi duy nhất ở A, chunk `fixed:p54:02` (t01, hạng 3): nhãn `muc` lấy theo vị trí đầu chunk nên ghi Mục VI, trong khi gần như toàn bộ nội dung thuộc Mục VII (cấp thẻ sinh viên). Các bản ghi audit có `muc` trống (thuộc Phần 2.3, 2.4, 2.6, 2.8, là các phần xuất hiện trong top-3 của tập test) được tính là đúng vì các phần này không chia Điều hay Mục.
- **Grounding:** cả ba chiến lược đều có đủ bằng chứng trong top-3 ở 7/7 câu test. Khác biệt nằm ở **thứ hạng**: ở t06, B chỉ có bằng chứng ở hạng 3 (hạng 1–2 là Phần 2.3), C lọc Phần 3 nên đưa bằng chứng lên hạng 1. Ở t04, cả ba có câu trả lời ở hạng 2; chunk hạng 1 lấy từ quy chế ngoại trú (Phần 2.8) ghi "30 ngày", cũng khớp.

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:** trace đầy đủ top-3 (câu hỏi, chunk_id, text, score, metadata, kết quả hit) ở `starter/results/traces_{A,B,C}.jsonl`, tổng hợp ở `summary.json`. Nhãn thủ công ở `starter/results/manual/`:
- `coherence.csv`: 10 vị trí chọn trước khi xem kết quả, là điểm giữa của 10 trang nội dung cách đều nhau (trang 5, 13, 20, 27, 34, 41, 48, 55, 62, 69), mỗi vị trí chấm chunk A và chunk B chứa nó. C dùng chung chunk với B nên coherence bằng B.
- `grounding_metadata_audit.csv`: 7 câu test × top-3 × 3 chiến lược = 21 bản ghi mỗi chiến lược; đối chiếu `page`, `phan`, `muc` với PDF; grounding chấm theo cả top-3 của từng câu.

Người chấm là tác giả, nên có nguy cơ thiên lệch.

## Phân tích và kết luận

- **So sánh A–B (đổi cách chunk):** B tốt hơn A trên dev: Hit@1 15/18 so với 13/18, Hit@3 18/18 so với 17/18, với ít chunk hơn (197 so với 290). Ví dụ:
  - **d10** ("Chuyển trường được công nhận tối đa bao nhiêu tín chỉ?", gold trang 26): A trượt cả top-3 vì câu "không vượt quá 50% khối lượng học tập tối thiểu…" nằm ở chunk khác với phần đầu Điều 12. B giữ trọn khoản này.
  - **d04**: A cắt chunk giữa từ (`'ểm thi kết thúc học phần…'`), còn chunk B bắt đầu bằng `[Phần 2.1 > Điều 9. Đánh giá và tính điểm học phần]` nên đọc riêng vẫn hiểu.

  Trên test, A và B đều đạt Hit@3 7/7; B kém A 1 câu ở Hit@1 (5/7 so với 6/7). Khác biệt rõ nhất nằm ở chấm thủ công: coherence của B là 1,9/2, của A chỉ 1,0/2, vì A luôn cắt giữa từ hoặc giữa câu. Metadata của B cũng đúng hoàn toàn (21/21), còn A sai 1 nhãn `muc` do chunk cố định vắt qua ranh giới hai mục.
- **So sánh B–C (thêm filter):** Hit@3 và grounding bằng nhau (2,0/2); điểm khác là thứ hạng của chunk chứa bằng chứng. C tuân thủ filter 3/3 dev và 2/2 test, B là 0/3 và 0/2. Ví dụ:
  - **t06** ("Họp lớp đánh giá rèn luyện cần bao nhiêu sinh viên?", gold trang 51, Phần 3-II): B xếp 2 chunk của Phần 2.3 (trang 36, 37) lên trên, chunk đúng chỉ đứng thứ 3. C lọc `phan = 3` nên đưa trang 51 lên hạng 1.
  - **d18** ("Học bổng được cấp bao nhiêu tháng?"): khi không lọc, A trả về cả học bổng chính sách "12 tháng/năm" (trang 41) và trợ cấp ở Phần 2.6 "10 tháng" (trang 45). Lọc Phần 2.5 giúp đáp đúng học bổng khuyến khích học tập (10 tháng).
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:** theo tiêu chí đã chốt, B và C hòa nhau ở Hit@3 (18/18) và Hit@1 (15/18) trên dev, C hơn ở tuân thủ filter → **chọn C**. Đánh đổi:
  - B/C tốn thêm công viết luật nhận diện cấu trúc riêng cho sổ tay; chunk dài hơn (trung vị 755 so với 500 ký tự) nên đưa vào LLM tốn token hơn.
  - Filter chỉ có ích khi biết trước sinh viên đang hỏi phần nào. Nếu lọc sai phần, câu trả lời đúng sẽ bị loại hoàn toàn.

  Kết quả chỉ áp dụng cho sổ tay này, không suy rộng sang tài liệu khác.
- **Hạn chế:**
  - Test chỉ 7 câu, chênh lệch 1 câu là khoảng 14%, nên khác biệt giữa các chiến lược chưa có ý nghĩa thống kê.
  - Câu hỏi do AI soạn nháp (tôi đã đọc lại) có thể thiên về cách diễn đạt gần với văn bản gốc.
  - Hit được chấm bằng cách khớp chuỗi evidence, nên câu trả lời đúng nhưng diễn đạt khác sẽ không được tính.
  - Nhãn thủ công do một người chấm. Grounding đạt 2,0/2 ở cả 3 chiến lược vì 7 câu test đều dễ tìm, nên thang đo này không phân biệt được các chiến lược trên tập test nhỏ.
  - Lỗi metadata nhỏ: dòng "Chương III" cuối trang 21 tạo thành một chunk ngắn mang nhãn `Điều 8`; trường `muc` để trống ở Phần 2.2–2.8 vì các phần này không có Điều.
  - Thời gian đo trên một máy cá nhân, một lần chạy.
- **Nếu làm lại hoặc mở rộng:**
  - Chunk theo khối Điều/Mục vắt qua nhiều trang (vẫn ghi lại các trang) để không cắt đôi đoạn văn ở biên trang.
  - Trích bảng bằng công cụ chuyên cho bảng (ví dụ pdfplumber) cho trang KTX và trang quy đổi điểm.
  - Thử hybrid retrieval (BM25 + vector) cho các câu hỏi chứa con số cụ thể.
  - Mở rộng tập test và nhờ người thứ hai chấm độc lập.
