FROM python:3.9-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
COPY pip.conf /root/.pip/pip.conf
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app.py game_logic.py data_storage.py player_manager.py ./
COPY static/ ./static/

EXPOSE 5000

CMD ["python", "app.py"]
