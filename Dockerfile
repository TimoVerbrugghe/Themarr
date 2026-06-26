FROM python:3.11-slim

WORKDIR /app

# Install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the scripts
COPY plex_theme_downloader.py .
COPY theme_coverage_report.py .
COPY analyze_mismatch.py .

# Run the script
CMD ["python", "plex_theme_downloader.py"]
