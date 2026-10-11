FROM python:3.12-slim

WORKDIR /app

COPY gautam-trading/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

WORKDIR /app/gautam-trading
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "10000"]
