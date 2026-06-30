from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
import os
import cv2
import wave
import shutil
import numpy as np
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("temp", exist_ok=True)
DELIMITER = "###END###"


# ==========================================
# HELPER
# ==========================================
def convert_to_wav(in_path: str, out_path: str):
    """Konversi audio apapun ke WAV menggunakan ffmpeg"""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-ar", "44100", "-ac", "1", out_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise Exception(f"ffmpeg error: {result.stderr}")

def encode_message_into_image(image, secret_message):
    # Ubah pesan ke dalam bentuk bit (0 dan 1)
    # Kita tambahkan penanda akhir pesan '00000000' (null terminator)
    binary_message = ''.join(format(ord(c), '08b') for c in secret_message) + '00000000'
    
    # Ratakan (flatten) matriks gambar menjadi array 1 dimensi agar mudah diolah
    flat_image = image.flatten()
    
    # Sisipkan setiap bit pesan ke LSB piksel
    for i in range(len(binary_message)):
        # (flat_image[i] & 254) membuat bit terakhir jadi 0
        # | int(binary_message[i]) memasukkan bit pesan
        flat_image[i] = (flat_image[i] & 254) | int(binary_message[i])
    
    # Kembalikan ke bentuk matriks gambar semula
    return flat_image.reshape(image.shape)

def decode_message_from_image(image):
    # Terima input sebagai PIL Image atau numpy array
    if not isinstance(image, np.ndarray):
        try:
            # Pastikan image RGB agar konsisten
            image = image.convert('RGB')
        except Exception:
            pass
        image = np.array(image)

    flat_image = image.flatten()
    binary_message = ""
    decoded_message = ""

    # Ambil bit dari LSB setiap piksel
    for i in range(len(flat_image)):
        binary_message += str(int(flat_image[i]) & 1)

        # Jika sudah terkumpul 8 bit, ubah jadi karakter
        if len(binary_message) % 8 == 0:
            byte = binary_message[-8:]
            try:
                char = chr(int(byte, 2))
            except Exception:
                # Kalau konversi gagal, lanjutkan
                continue

            # Jika menemukan null terminator, hentikan proses
            if char == '\0':
                break
            decoded_message += char

    return decoded_message

# ==========================================
# 1. MODUL IMAGE
# ==========================================
@app.post("/api/image/compress")
async def compress_image(file: UploadFile = File(...)):
    image = Image.open(file.file)
    out_path = f"temp/comp_{file.filename}.jpg"
    image.convert('RGB').save(out_path, format='JPEG', quality=20)
    return FileResponse(out_path, media_type="image/jpeg", filename=f"comp_{file.filename}.jpg")


@app.post("/api/image/encode")
async def encode_image(file: UploadFile = File(...), message: str = Form(...)):
    image = Image.open(file.file)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    img_array = np.array(image)
    stego_array = encode_message_into_image(img_array, message)
    secret_img = Image.fromarray(stego_array)
    out_path = f"temp/enc_{file.filename}.png"
    secret_img.save(out_path, format='PNG')
    return FileResponse(out_path, media_type="image/png", filename=f"enc_{file.filename}.png")


@app.post("/api/image/decode")
async def decode_image(file: UploadFile = File(...)):
    in_path = f"temp/{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    try:
        message = decode_message_from_image(Image.open(in_path))
        if not message:
            raise Exception("No message")
        return JSONResponse(content={"message": message})
    except:
        return JSONResponse(content={"message": "Tidak ada pesan tersembunyi ditemukan."}, status_code=400)


# ==========================================
# 2. MODUL AUDIO — Fix: Konversi ke WAV dulu
# ==========================================
@app.post("/api/audio/compress")
async def compress_audio(file: UploadFile = File(...)):
    in_path = f"temp/{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    out_path = f"temp/comp_{file.filename}.mp3"
    audio_clip = AudioFileClip(in_path)
    audio_clip.write_audiofile(out_path, bitrate="32k", logger=None)
    audio_clip.close()
    return FileResponse(out_path, media_type="audio/mpeg", filename=f"comp_{file.filename}.mp3")


@app.post("/api/audio/encode")
async def encode_audio(file: UploadFile = File(...), message: str = Form(...)):
    in_path = f"temp/raw_{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Selalu konversi ke WAV PCM dulu agar wave module bisa baca
    wav_path = f"temp/input_{file.filename}.wav"
    try:
        convert_to_wav(in_path, wav_path)
    except Exception as e:
        return JSONResponse(content={"message": f"Gagal konversi audio: {str(e)}"}, status_code=400)

    out_path = f"temp/enc_{file.filename}.wav"

    song = wave.open(wav_path, mode='rb')
    frame_bytes = bytearray(list(song.readframes(song.getnframes())))

    # Konversi pesan ke biner dengan delimiter
    msg = message + DELIMITER
    bits = ''.join(format(ord(c), '08b') for c in msg)

    if len(bits) > len(frame_bytes):
        song.close()
        return JSONResponse(content={"message": f"Audio terlalu pendek. Butuh {len(bits)} bits, tersedia {len(frame_bytes)} bits."}, status_code=400)

    for i, bit in enumerate(bits):
        frame_bytes[i] = (frame_bytes[i] & 0b11111110) | int(bit)

    with wave.open(out_path, 'wb') as fd:
        fd.setparams(song.getparams())
        fd.writeframes(bytes(frame_bytes))
    song.close()

    return FileResponse(out_path, media_type="audio/wav", filename=f"enc_{file.filename}.wav")


@app.post("/api/audio/decode")
async def decode_audio(file: UploadFile = File(...)):
    in_path = f"temp/raw_{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Konversi ke WAV jika perlu
    wav_path = f"temp/dec_{file.filename}.wav"
    try:
        convert_to_wav(in_path, wav_path)
    except Exception as e:
        return JSONResponse(content={"message": f"Gagal konversi audio: {str(e)}"}, status_code=400)

    song = wave.open(wav_path, mode='rb')
    frame_bytes = bytearray(list(song.readframes(song.getnframes())))
    song.close()

    # Ekstrak LSB
    extracted_bits = [str(frame_bytes[i] & 1) for i in range(len(frame_bytes))]
    string_bits = "".join(extracted_bits)

    chars = []
    for i in range(0, len(string_bits), 8):
        byte = string_bits[i:i + 8]
        if len(byte) == 8:
            chars.append(chr(int(byte, 2)))

    full_msg = "".join(chars)
    if DELIMITER in full_msg:
        secret = full_msg.split(DELIMITER)[0]
        return JSONResponse(content={"message": secret})
    else:
        return JSONResponse(content={"message": "Tidak ada pesan tersembunyi ditemukan."}, status_code=400)


# ==========================================
# 3. MODUL VIDEO — Fix: Compress playable + Encode dengan H264
# ==========================================
@app.post("/api/video/compress")
async def compress_video(file: UploadFile = File(...)):
    in_path = f"temp/{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    out_path = f"temp/comp_{file.filename}.mp4"

    # Gunakan ffmpeg langsung agar lebih reliable dan playable
    result = subprocess.run([
        "ffmpeg", "-y", "-i", in_path,
        "-vf", "scale=-2:480",
        "-c:v", "libx264",
        "-crf", "28",           # kualitas (18=lossless, 28=medium, 51=worst)
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "32k",
        out_path
    ], capture_output=True, text=True)

    if result.returncode != 0:
        return JSONResponse(content={"message": f"Kompresi gagal: {result.stderr}"}, status_code=500)

    return FileResponse(out_path, media_type="video/mp4", filename=f"comp_{file.filename}.mp4")


@app.post("/api/video/encode")
async def encode_video(file: UploadFile = File(...), message: str = Form(...)):
    in_path = f"temp/{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Simpan frame pertama sebagai PNG untuk steganografi
    cap = cv2.VideoCapture(in_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    ret, first_frame = cap.read()
    if not ret:
        cap.release()
        return JSONResponse(content={"message": "Gagal membaca video."}, status_code=400)

    # Steganografi di frame pertama
    rgb_frame = cv2.cvtColor(first_frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb_frame)
    img_array = np.array(pil_img)
    stego_array = encode_message_into_image(img_array, message)
    secret_img = Image.fromarray(stego_array)
    stego_bgr = cv2.cvtColor(np.array(secret_img), cv2.COLOR_RGB2BGR)

    # Simpan semua frame ke folder temp
    frames_dir = f"temp/frames_{file.filename}"
    os.makedirs(frames_dir, exist_ok=True)
    cv2.imwrite(f"{frames_dir}/frame_0000.png", stego_bgr)

    idx = 1
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imwrite(f"{frames_dir}/frame_{idx:04d}.png", frame)
        idx += 1
    cap.release()

    # Gabungkan frames + audio asli dengan ffmpeg (H264, bisa diplay)
    temp_video = f"temp/novid_{file.filename}.mp4"
    out_path = f"temp/enc_{file.filename}.mp4"

    # Step 1: frames -> video tanpa audio
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", f"{frames_dir}/frame_%04d.png",
        "-c:v", "libx264rgb",  
        "-crf", "0",           
        "-preset", "ultrafast",
        temp_video
    ], capture_output=True)

    # Step 2: tambahkan audio dari video asli
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", temp_video,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        out_path
    ], capture_output=True, text=True)

    # Cleanup frames
    shutil.rmtree(frames_dir, ignore_errors=True)

    if result.returncode != 0:
        # Kalau audio gagal, return video tanpa audio
        return FileResponse(temp_video, media_type="video/mp4", filename=f"enc_{file.filename}.mp4")

    return FileResponse(out_path, media_type="video/mp4", filename=f"enc_{file.filename}.mp4")


@app.post("/api/video/decode")
async def decode_video(file: UploadFile = File(...)):
    in_path = f"temp/{file.filename}"
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    cap = cv2.VideoCapture(in_path)
    ret, frame = cap.read()
    cap.release()

    if ret:
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            message = decode_message_from_image(pil_img)
            if not message:
                raise Exception("No message")
            return JSONResponse(content={"message": message})
        except:
            return JSONResponse(content={"message": "Tidak ada pesan tersembunyi ditemukan."}, status_code=400)

    return JSONResponse(content={"message": "Gagal membaca frame video."}, status_code=400)