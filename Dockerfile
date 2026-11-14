FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# gettext builds the translation files (.po -> .mo) for Russian and English
RUN apt-get update && apt-get install -y --no-install-recommends gettext && rm -rf /var/lib/apt/lists/*

COPY . .

RUN django-admin compilemessages --ignore=.venv

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]