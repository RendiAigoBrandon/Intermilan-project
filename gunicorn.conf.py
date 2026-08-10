# Gunicorn configuration for INTERMILAN production deployment.
# Loaded automatically when gunicorn starts from the project root.
# https://docs.gunicorn.org/en/stable/configure.html

# Increase worker timeout to accommodate long OCR operations on large SPM PDFs.
# Default is 30s; a 13-page PDF with 11 OCR pages takes ~36s locally.
# 180s gives headroom for production server variability.
timeout = 180

# Keep graceful for worker shutdown
graceful_timeout = 30
