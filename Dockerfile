# 1. Gunakan base image python resmi
FROM python:3.10-slim

# 2. Install FFmpeg dan library sistem yang dibutuhkan OpenCV
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# 3. Setup direktori kerja
WORKDIR /app

# 4. Copy requirements dan install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy seluruh kode
COPY . .

# 6. Jalankan FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]