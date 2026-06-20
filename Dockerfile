# ── Stage 1: Employee 插件 ──────────────────────────────
FROM python:3.13-slim AS employee-base

WORKDIR /opt/hermes-employee
COPY . .
RUN pip install -e . --quiet

# ── Stage 2: Hermes Agent + Employee ────────────────────
# 官方镜像 nousresearch/hermes-agent:latest，但需要登录才能拉
# 所以我们手动构建 Hermes 基础层
FROM debian:13-slim AS hermes-base

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-pip \
    python3-venv \
    xz-utils \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# 手动安装 Hermes Agent（跳过 install.sh，直接 pip）
RUN python3 -m venv /root/.hermes/hermes-agent/venv \
    && /root/.hermes/hermes-agent/venv/bin/pip install hermes-agent --quiet \
    && mkdir -p /root/.local/bin \
    && ln -sf /root/.hermes/hermes-agent/venv/bin/hermes /root/.local/bin/hermes

# ── Stage 3: 最终镜像 ──────────────────────────────────
FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-pip \
    python3-venv \
    xz-utils \
    nodejs \
    npm \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 复制 Hermes Agent 安装
COPY --from=hermes-base /root/.hermes /root/.hermes
COPY --from=hermes-base /root/.local /root/.local

# 复制 Employee 插件
COPY --from=employee-base /opt/hermes-employee /opt/hermes-employee
RUN /root/.hermes/hermes-agent/venv/bin/pip install -e /opt/hermes-employee --quiet

# 注册并启用插件
RUN mkdir -p /root/.hermes/plugins \
    && ln -sfn /opt/hermes-employee/hermes_employee_plugin /root/.hermes/plugins/hermes-employee \
    && mkdir -p /root/.hermes/employee

RUN printf 'plugins:\n  enabled:\n    - hermes-employee\n' > /root/.hermes/config.yaml

# PATH
ENV PATH="/root/.local/bin:/root/.hermes/hermes-agent/venv/bin:${PATH}"

# 容器内 root
USER root
WORKDIR /workspace
VOLUME /workspace /root/.hermes
CMD ["bash"]
