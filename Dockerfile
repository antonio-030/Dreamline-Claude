FROM python:3.12-slim

WORKDIR /app

# Systemabhängigkeiten + Node.js installieren
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI installieren (für claude-abo Provider)
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root User erstellen (Claude CLI verweigert Root)
RUN useradd -m -s /bin/bash dreamline
USER dreamline

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
