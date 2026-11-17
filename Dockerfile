FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The SQLite file lives on a volume so data survives container restarts.
RUN mkdir -p /app/data && chmod +x start.sh
ENV DATABASE_URL=sqlite:////app/data/attendance.db
VOLUME /app/data

EXPOSE 8000

# SECRET_KEY must be supplied at run time (docker run -e SECRET_KEY=... or --env-file).
CMD ["./start.sh"]
