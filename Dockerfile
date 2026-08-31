# Travel Planner · FastAPI 后端 Docker 镜像
FROM python:3.12-slim

WORKDIR /app

# 安装依赖（先复制 requirements 利用缓存）
COPY python_backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 复制项目（前端 + 后端 + 数据目录）
COPY public /app/public
COPY python_backend /app/python_backend
COPY data /app/data

ENV PORT=3000
ENV HOST=0.0.0.0

EXPOSE 3000

CMD ["python", "python_backend/main.py"]
