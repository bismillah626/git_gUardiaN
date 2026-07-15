# Use a lightweight Python image
FROM python:3.11-slim

# Install system-level dependencies required for our tools (Node.js for ESLint, Git for cloning repos)
RUN apt-get update && apt-get install -y \
    git \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install ESLint globally
RUN npm install -g eslint

# Install Gitleaks
RUN curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
    | tar -xz -C /usr/local/bin gitleaks \
    && chmod +x /usr/local/bin/gitleaks

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file first (this caches the installation step to save time on future builds)
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project code into the container
COPY . .

# Expose port 8000 for the FastAPI webhook
EXPOSE 8000

# The command to start the FastAPI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]















