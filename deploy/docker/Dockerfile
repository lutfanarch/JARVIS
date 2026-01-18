FROM python:3.11-slim

# Prevent interactive prompts during package installation.  Setting
# DEBIAN_FRONTEND ensures tzdata installs without hanging.  The TZ
# environment variable defines a default system timezone; cron jobs use
# CRON_TZ separately for scheduling.  The subsequent ln command
# links /etc/localtime to the chosen timezone and writes it to
# /etc/timezone.
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

# Set work directory
WORKDIR /app

# Install system dependencies required for TimescaleDB client and cron
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc cron git tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Create default directories for artifacts and scripts
RUN mkdir -p /app/artifacts /app/scripts

# Default command runs a shell.  The scheduler container will override this.
CMD ["bash"]