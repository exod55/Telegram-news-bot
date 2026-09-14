FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Copy dependency list and install packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project code
COPY . .

# Run your bot script
CMD ["python", "bot.py"]
