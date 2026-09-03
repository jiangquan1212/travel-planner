# Travel Planner · FastAPI 后端 Docker 镜像（生产）
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PORT=3000 \
    HOST=0.0.0.0

# 先拷贝依赖清单以利用构建缓存
COPY python_backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 拷贝代码与前端
COPY python_backend /app/python_backend
COPY public /app/public
RUN mkdir -p /app/data

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3000/api/health')"

CMD ["python", "python_backend/main.py"]
