FROM python:3.11-alpine

RUN apk add --no-cache tzdata
ENV TZ=America/Sao_Paulo

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# O Cron agora apenas dá um "ping" na API do Flask a cada 2 horas para forçar a coleta
RUN echo "0 */2 * * * wget -qO- -X POST http://localhost:80/api/atualizar > /proc/1/fd/1 2>&1" > /etc/crontabs/root

EXPOSE 80

# Inicia o agendador e sobe a aplicação web no Flask
CMD crond -b && python app.py