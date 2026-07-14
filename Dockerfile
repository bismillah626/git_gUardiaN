# Use a lightweight Python image
FROM python:3.11-slim

# Install system-level dependencies required for our tools
RUN apt-get update && apt-get install -y \
    git \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install ESLint globally
RUN npm install -g eslint

# Install Gitleaks
RUN curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first (caches the pip layer)
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project code into the container
COPY . .

# Expose ports for FastAPI (8000) and Streamlit (8501)
EXPOSE 8000 8501

# The command to start the FastAPI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
