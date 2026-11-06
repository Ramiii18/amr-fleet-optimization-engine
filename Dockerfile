# The HTTP service (D29) as a container image.
#
# Build and run (needs Docker; on Windows, Docker Desktop needs WSL2):
#     docker build -t amr-engine .
#     docker run --rm -p 127.0.0.1:8000:8000 amr-engine
# Then open http://127.0.0.1:8000/docs. CI builds this image and checks that it answers.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Only what the package needs to install: no tests, docs or local files.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install ".[api]"

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 amr
USER amr

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# Inside the container the server must listen on every interface to be reachable through the
# published port; publish it on 127.0.0.1 (as above) to keep it local to the host.
CMD ["amr-api", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
