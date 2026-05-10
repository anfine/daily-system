FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py cloud_api.py archive_daily.py build_daily_char_meta_map.py db.py meta_keys.py ./

EXPOSE 8787

CMD ["python", "agent.py"]
