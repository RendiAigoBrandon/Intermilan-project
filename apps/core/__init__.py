"""Bootstrap lingkungan OCR sebelum PaddlePaddle diimpor."""

import os


# PaddlePaddle 3.3.x pada Windows CPU dapat gagal pada kombinasi PIR dan oneDNN.
# Nilai harus tersedia sebelum import paddle atau paddleocr pertama kali terjadi.
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
