FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy framework + agents + customer configs
COPY agency/ agency/
COPY agents/ agents/
COPY customers/ customers/
COPY serve.py .

# Default: read CUSTOMER_ID from env
ENV CUSTOMER_ID=""

CMD ["python", "serve.py", "--customer", "${CUSTOMER_ID}"]

# Override CMD in docker-compose per customer:
#   command: python serve.py --customer pizzeria-mario
