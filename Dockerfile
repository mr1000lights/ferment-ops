FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql
COPY config ./config
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1 FDP_HOME=/app
ENTRYPOINT ["fdp"]
CMD ["run", "--mode", "live"]
