#!/bin/bash
# Push kraken-live-bot to Docker Hub or custom registry

set -e

# Configuration
REGISTRY="${REGISTRY:-docker.io}"
USERNAME="${DOCKER_USERNAME:-}"
IMAGE_NAME="kraken-live-bot"
TAG="${1:-latest}"

# Validate inputs
if [[ -z "$USERNAME" ]]; then
    echo "Error: DOCKER_USERNAME not set"
    echo "Usage: ./scripts/push.sh [tag] (default: latest)"
    exit 1
fi

FULL_IMAGE="${REGISTRY}/${USERNAME}/${IMAGE_NAME}:${TAG}"

echo "📦 Pushing image: $FULL_IMAGE"

# Build locally if not already built
if ! docker image inspect "kraken-live-bot:${TAG}" &>/dev/null; then
    echo "Building image..."
    docker build -t "kraken-live-bot:${TAG}" .
fi

# Tag for registry
docker tag "kraken-live-bot:${TAG}" "$FULL_IMAGE"

# Log in (assumes DOCKER_PASSWORD set in environment)
if [[ -n "$DOCKER_PASSWORD" ]]; then
    echo "$DOCKER_PASSWORD" | docker login -u "$USERNAME" --password-stdin "$REGISTRY"
fi

# Push
docker push "$FULL_IMAGE"

echo "✅ Pushed: $FULL_IMAGE"
