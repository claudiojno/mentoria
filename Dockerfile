FROM 839930326968.dkr.ecr.us-east-1.amazonaws.com/python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates

EXPOSE 80
ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
