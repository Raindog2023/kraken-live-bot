#!/bin/bash
# Initial setup for kraken-live-bot deployment

set -e

echo "🐳 Kraken Live Bot – Setup Script"
echo "=================================="

# Step 1: Check dependencies
echo "✓ Checking dependencies..."
for cmd in docker git; do
    if ! command -v $cmd &>/dev/null; then
        echo "❌ $cmd not found. Please install it first."
        exit 1
    fi
done

# Step 2: Create .env from template
if [[ ! -f .env ]]; then
    echo "✓ Creating .env from template..."
    cp .env.example .env
    echo "⚠️  Edit .env with your API keys before running compose up"
else
    echo "ℹ️  .env already exists"
fi

# Step 3: Build image
echo "✓ Building Docker image..."
docker build -t kraken-live-bot:local .

# Step 4: Create volumes
echo "✓ Ensuring volumes exist..."
docker volume inspect kraken-live-bot_bot_logs &>/dev/null || \
    docker volume create kraken-live-bot_bot_logs
docker volume inspect kraken-live-bot_bot_models &>/dev/null || \
    docker volume create kraken-live-bot_bot_models
docker volume inspect kraken-live-bot_bot_data &>/dev/null || \
    docker volume create kraken-live-bot_bot_data

# Step 5: Start container
echo "✓ Starting container..."
docker compose up -d

# Step 6: Wait for healthcheck
echo "⏳ Waiting for service to be healthy..."
for i in {1..30}; do
    if docker compose ps | grep -q "healthy"; then
        echo "✅ Service is healthy!"
        break
    fi
    sleep 1
done

# Step 7: Summary
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Kraken API keys and secrets"
echo "  2. View logs: docker compose logs -f bot"
echo "  3. Access dashboard: http://localhost:8000/dashboard"
echo "  4. Train ML model: docker compose run --rm bot train <csv> <model.joblib>"
echo ""
echo "For production deployment, see DEPLOYMENT.md"
