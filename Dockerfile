FROM python:3.11-slim
 
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 && rm -rf /var/lib/apt/lists/*
 
RUN useradd -m -u 1000 user
 
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
 
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONPATH=/home/user/code
 
WORKDIR $HOME/code

RUN mkdir -p logs && chown user:user logs

COPY --chown=user:user pyproject.toml ./
 
RUN uv pip install -r pyproject.toml --system
 
COPY --chown=user:user app/ ./app/
COPY --chown=user:user .env ./.env
 
USER user
EXPOSE 80
 
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "-b", "0.0.0.0:80", "--timeout", "120"]