# Starter · PostgreSQL + pgvector

> **Bài nộp của Nguyễn Tuấn Phong (2A202603712):**  Phần dưới đây là cách tái lập kết quả.

## Chạy lại bài làm

Corpus: Sổ tay sinh viên K64 (2023), Trường ĐH GTVT. PDF nằm ở `data/raw/SoTaySinhVien-UTC-K64-2023.pdf` (bản gốc: https://khcb.utc.edu.vn/so-tay-sinh-vien-k64-2907404). Benchmark nằm ở `benchmark/questions.json`, manifest ở `data/manifest.json`.

Yêu cầu: Python 3.10+, Docker, [Ollama](https://ollama.com) chạy local ở `http://127.0.0.1:11434`. Không cần API key. Chạy các lệnh sau từ thư mục `starter/`:

```bash
python -m pip install -r requirements.txt
ollama pull bge-m3                                   # embedding 1024 chiều
# copy .env.example thành .env, đặt EMBEDDING_MODEL=bge-m3 (bỏ trống thì mặc định là bge-m3)
docker compose -p day6_student_starter up -d --wait  # PostgreSQL 16 + pgvector
python scripts/check_setup.py

python -m src.run all        # = ingest -> verify -> index -> evaluate
```

Có thể chạy từng bước riêng:

| Lệnh | Việc làm | Kết quả ghi ra |
|---|---|---|
| `python -m src.run ingest` | đọc PDF, chuẩn hóa, đếm trang nội dung | `data/processed/pages.jsonl`, `data/manifest.json`, `results/ingest_errors.json` |
| `python -m src.run verify` | kiểm tra evidence của từng câu nằm trong trang gold | `results/benchmark_verification.json` |
| `python -m src.run index` | chunk A và B, embed, upsert vào pgvector (chunk không đổi thì bỏ qua, chunk cũ thì xóa) | `results/chunk_stats.json`, `results/index_runs.json` |
| `python -m src.run evaluate` | truy xuất top-3 cho A, B, C trên dev và test | `results/traces_{A,B,C}.jsonl`, `results/summary.json` |

Nhãn thủ công nằm ở `results/manual/`. Khi chạy lại, `evaluate` không ghi đè các file nhãn đã có mà ghi sang `*_new.csv`. Báo cáo: [../report/individual/2A202603712_NguyenTuanPhong.md](../report/individual/2A202603712_NguyenTuanPhong.md).

---

Bộ khởi động **tùy chọn** cho lab cá nhân. Docker Compose tạo PostgreSQL + pgvector local; các file trong `src/` là **khung pseudocode có TODO**, chưa đọc tài liệu, tạo embedding, lưu vector hay chạy benchmark. Bạn cần tự hiện thực. Vẫn có thể chọn Qdrant, Chroma hoặc vector store tương đương theo [GUIDE.md](../GUIDE.md).

## Cấu trúc

```text
starter/
├── docker-compose.yml     # PostgreSQL 16 + pgvector, chỉ mở cổng local
├── init.sql               # bật extension khi tạo database mới
├── requirements.txt       # psycopg, pypdf và python-dotenv
├── .env.example           # cấu hình mẫu; copy thành .env
├── scripts/check_setup.py # kiểm tra kết nối và extension (đã chạy được)
├── src/
│   ├── models.py          # cấu trúc dữ liệu mẫu
│   ├── ingestion.py       # TODO: đọc / chuẩn hóa tài liệu
│   ├── chunking.py        # TODO: chia chunk
│   ├── embeddings.py      # TODO: chọn model API hoặc local
│   ├── store.py           # TODO: lưu vector, metadata và filter
│   ├── evaluation.py      # TODO: chấm truy xuất, lưu trace
│   └── run.py             # TODO: kết nối các bước thành pipeline
├── data/                  # bạn tự chuẩn bị corpus được phép sử dụng
└── results/               # lưu kết quả của bạn
```

## 1. Cài và kiểm tra database

Cần Docker Desktop hoặc Docker Engine + Compose, Python **3.11–3.14**, và mạng để tải image/dependencies lần đầu. Các lệnh sau chạy từ thư mục `starter/`:

```bash
cd starter
python3 -m venv .venv
```

Kích hoạt `.venv`: macOS/Linux dùng `source .venv/bin/activate`; Windows PowerShell dùng `.venv\Scripts\Activate.ps1`. Sau đó:

```bash
python -m pip install -r requirements.txt
# macOS/Linux: cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py
```

Kỳ vọng: hiện tên database, phiên bản PostgreSQL và pgvector, sau đó `Setup OK`. Đây **chỉ là kiểm tra hạ tầng**, không phải kết quả benchmark. Docker dùng cổng `127.0.0.1:55433` và mật khẩu cố định **chỉ dùng local cho lab**; nếu cổng bận, sửa đồng thời port trong `docker-compose.yml` và `DATABASE_URL` trong `.env`. `init.sql` chỉ chạy khi volume mới được tạo; nếu database sẵn có chưa bật extension, xem log Docker và kiểm tra cấu hình thay vì xóa volume chứa dữ liệu.

Dừng container nhưng giữ dữ liệu:

```bash
docker compose -p day6_student_starter stop
```

## 2. Chọn embedding model

Điền key tương ứng trong `.env` nếu dùng OpenRouter, OpenAI hoặc Gemini; **không commit `.env`**. Model local không cần API key. `requirements.txt` chỉ có dependencies cơ bản: tự cài SDK hoặc thư viện local phù hợp với model bạn chọn và ghi lại version/lệnh cài trong báo cáo. Ghi tên model và số chiều; dùng cùng model cho toàn bộ chiến lược và vector query tương thích vector document.

## 3. Hoàn thiện pipeline

1. Đặt corpus vào `data/` hoặc cung cấp manifest/hướng dẫn truy cập. Đọc [GUIDE.md](../GUIDE.md) về **30 trang nội dung**, quyền dữ liệu và metadata.
2. Hoàn thiện từng hàm TODO trong `src/`. Lưu ID tài liệu, nguồn, trang/vị trí và ít nhất một trường filter có ích.
3. Xây ít nhất ba cấu hình; ví dụ A fixed-size, B theo đoạn, C như B nhưng có filter. Cần cùng corpus, model và benchmark; nạp lại không nhân đôi và xử lý chunk cũ khi dữ liệu đổi.
4. Tự viết ít nhất 20 câu hỏi kèm evidence, tách dev/test trước khi thử theo [BENCHMARK.md](../BENCHMARK.md). Lưu trace top-3 và báo cáo đủ năm tiêu chí.
5. Sau khi hoàn thiện, chạy `python -m src.run` từ thư mục `starter/`. Trước khi làm TODO, lệnh này **chủ động báo `NotImplementedError`**.

Lưu code, cấu hình, kết quả và lệnh chạy để người chấm tái lập; không commit key, dữ liệu mật hoặc dữ liệu không có quyền chia sẻ.
