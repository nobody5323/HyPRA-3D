# =============================================================
# HyPRA 后端镜像
#   构建：docker compose build backend 或 docker build -t hypra-backend .
#   运行：uvicorn app.main:app（容器内端口 8000）
# =============================================================

FROM python:3.13-slim

# 时区/基础库（sqlite3 为标准库，无需额外 apt）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先复制依赖清单（利用层缓存，减少重复构建）
COPY pyproject.toml README.md ./
COPY app ./app

# pip 源可经构建参数覆盖。
# ⚠️ 清华镜像（pypi.tuna.tsinghua.edu.cn）缺少 jieba 的源码分发（只有 sdist），
#    用它构建会以 “No matching distribution found for jieba” 失败；
#    国内网络请改用阿里云镜像，或保持默认官方源（始终可用）。
ARG PIP_INDEX_URL=https://pypi.org/simple

# 安装运行时依赖（非 editable，装进 site-packages）
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" .

# 数据目录（compose 中以卷挂载到 /data 时覆盖此默认路径）
RUN mkdir -p /data

EXPOSE 8000

# 默认命令（评审自填 key 由 backend/.env 经 compose env_file 注入）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
