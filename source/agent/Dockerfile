# Base image digest-pinned for a reproducible build (a floating tag can silently change the
# shipped image). Digest resolved from public.ecr.aws docker/library/python:3.12-slim via the
# registry manifest API (docker-content-digest header) on 2026-10-17. To re-pin on a base bump:
#   TOKEN=$(curl -s https://public.ecr.aws/token/ | sed -E 's/.*"token":"([^"]+)".*/\1/')
#   curl -sI -H "Authorization: Bearer $TOKEN" \
#     -H "Accept: application/vnd.oci.image.index.v1+json" \
#     https://public.ecr.aws/v2/docker/library/python/manifests/3.12-slim | grep -i docker-content-digest
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app



COPY requirements_agentcore.txt requirements_agentcore.txt
# Install from requirements file (aws-opentelemetry-distro is pinned there)
RUN pip install -r requirements_agentcore.txt


# Set AWS region environment variable

ENV AWS_REGION=us-west-2
ENV AWS_DEFAULT_REGION=us-west-2


# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

# Copy entire project (respecting .dockerignore)
COPY . .

# Use the full module path

CMD ["opentelemetry-instrument", "python", "-m", "agentcore_wrapper"]
