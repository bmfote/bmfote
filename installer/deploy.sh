#!/bin/bash
# bmfote deploy — stand up your own cloud memory backend.
#
# Usage:
#   npx bmfote deploy
#
# What it does:
#   1. Checks for Turso CLI and Railway CLI
#   2. Creates a Turso database
#   3. Runs the schema (tables, FTS5, triggers)
#   4. Creates a Railway project and deploys the server
#   5. Sets env vars on Railway
#   6. Generates an API token and public domain
#   7. Prints the setup command for connecting machines
#
# Prerequisites:
#   - turso CLI installed and authenticated (turso auth login)
#   - railway CLI installed and authenticated (railway login)

set -euo pipefail

DB_NAME="bmfote-memory"

echo "bmfote deploy"
echo "============="
echo ""
echo "This will create your own cloud memory backend."
echo ""

# --- Step 1: Check and install prerequisites ---
echo "[1/7] Checking prerequisites..."

ask_install() {
  local name="$1"
  echo ""
  read -p "  Install $name now? [Y/n] " -n 1 -r
  echo ""
  [[ -z "$REPLY" || "$REPLY" =~ ^[Yy]$ ]]
}

# Turso CLI
if ! command -v turso &> /dev/null; then
  echo "  Turso CLI not found."
  if ask_install "Turso CLI"; then
    curl -sSfL https://get.tur.so/install.sh | bash
    echo ""
    # Reload PATH
    export PATH="$HOME/.turso:$PATH"
    if ! command -v turso &> /dev/null; then
      echo "  ERROR: Turso CLI install failed. Install manually:"
      echo "  curl -sSfL https://get.tur.so/install.sh | bash"
      exit 1
    fi
  else
    echo "  Install manually: curl -sSfL https://get.tur.so/install.sh | bash"
    exit 1
  fi
fi
echo "  Turso CLI: $(turso --version 2>/dev/null | head -1 || echo 'found')"

# Turso auth — test with an actual API call, not just auth status
if ! turso db list &>/dev/null; then
  echo "  Not logged in to Turso (or session expired)."
  if ask_install "and log in to Turso (opens browser)"; then
    turso auth login
    if ! turso db list &>/dev/null; then
      echo "  ERROR: Turso login failed."
      exit 1
    fi
  else
    echo "  Run manually: turso auth login"
    exit 1
  fi
fi
echo "  Turso: authenticated"

# Railway CLI
if ! command -v railway &> /dev/null; then
  echo "  Railway CLI not found."
  if ask_install "Railway CLI"; then
    npm install -g @railway/cli
    if ! command -v railway &> /dev/null; then
      echo "  ERROR: Railway CLI install failed. Install manually:"
      echo "  npm install -g @railway/cli"
      exit 1
    fi
  else
    echo "  Install manually: npm install -g @railway/cli"
    exit 1
  fi
fi
echo "  Railway CLI: $(railway --version 2>/dev/null || echo 'found')"

# Railway auth
if ! railway whoami &>/dev/null; then
  echo "  Not logged in to Railway."
  if ask_install "and log in to Railway (opens browser)"; then
    railway login
    if ! railway whoami &>/dev/null; then
      echo "  ERROR: Railway login failed."
      exit 1
    fi
  else
    echo "  Run manually: railway login"
    exit 1
  fi
fi
echo "  Railway: authenticated"

if ! command -v git &> /dev/null; then
  echo "  ERROR: git not found. Please install git first."
  exit 1
fi

# --- Step 2: Create Turso database ---
echo ""
echo "[2/7] Creating Turso database..."

# Check if database already exists
if turso db show "$DB_NAME" &>/dev/null; then
  echo "  Database '$DB_NAME' already exists — reusing it"
else
  if ! turso db create "$DB_NAME" 2>&1 | while read -r line; do echo "  $line"; done; then
    echo "  Database creation failed. You may need to log in again."
    if ask_install "and log in to Turso (opens browser)"; then
      turso auth login
      turso db create "$DB_NAME" 2>&1 | while read -r line; do echo "  $line"; done
    else
      echo "  Run manually: turso auth login && turso db create $DB_NAME"
      exit 1
    fi
  fi
  echo "  Created database: $DB_NAME"
fi

TURSO_URL=$(turso db show "$DB_NAME" --url 2>/dev/null)
if [ -z "$TURSO_URL" ]; then
  echo "  ERROR: Could not get database URL. Check turso auth."
  exit 1
fi
echo "  URL: $TURSO_URL"

# --- Step 3: Create auth token ---
echo ""
echo "[3/7] Creating database auth token..."
TURSO_TOKEN=$(turso db tokens create "$DB_NAME" --expiration none 2>/dev/null)
echo "  Token created (non-expiring)"

# --- Step 4: Run schema ---
echo ""
echo "[4/7] Running database schema..."

# Get schema from local repo or GitHub
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd 2>/dev/null || echo "")"
SCHEMA_FILE="${SCRIPT_DIR:+$SCRIPT_DIR/../engine/schema.sql}"

if [ -z "$SCHEMA_FILE" ] || [ ! -f "$SCHEMA_FILE" ]; then
  SCHEMA_FILE="/tmp/bmfote-schema.sql"
  curl -fsSL "https://raw.githubusercontent.com/bmfote/bmfote/main/engine/schema.sql" \
    -o "$SCHEMA_FILE" || {
    echo "  ERROR: Could not download schema.sql"
    exit 1
  }
fi

turso db shell "$DB_NAME" < "$SCHEMA_FILE" 2>&1 | while read -r line; do echo "  $line"; done
echo "  Schema applied (tables, FTS5, triggers)"

# --- Step 5: Generate API token ---
echo ""
echo "[5/7] Generating API token..."
API_TOKEN=$(openssl rand -base64 32)
echo "  API token generated"

# --- Step 6: Deploy to Railway ---
echo ""
echo "[6/7] Deploying to Railway..."

# Clone repo to temp dir for deployment
DEPLOY_DIR=$(mktemp -d)
git clone --depth 1 https://github.com/bmfote/bmfote.git "$DEPLOY_DIR" 2>&1 | while read -r line; do echo "  $line"; done

cd "$DEPLOY_DIR"

# Create Railway project and service
railway init --name bmfote 2>&1 | while read -r line; do echo "  $line"; done

# Add service with env vars
railway add -s "bmfote-api" \
  -v "TURSO_DATABASE_URL=$TURSO_URL" \
  -v "TURSO_AUTH_TOKEN=$TURSO_TOKEN" \
  -v "API_TOKEN=$API_TOKEN" 2>&1 | while read -r line; do echo "  $line"; done

# Deploy
railway up 2>&1 | while read -r line; do echo "  $line"; done

# Generate domain
DOMAIN=$(railway domain 2>&1 | grep "https://" | sed 's/.*https/https/' | tr -d ' ')
echo "  Domain: $DOMAIN"

# Clean up temp dir
cd /
rm -rf "$DEPLOY_DIR"

# --- Step 7: Wait for deploy and verify ---
echo ""
echo "[7/7] Waiting for deployment..."

# Poll until the API responds (max 90 seconds)
for i in $(seq 1 18); do
  if curl -sf --connect-timeout 3 --max-time 5 \
    -H "Authorization: Bearer $API_TOKEN" \
    "$DOMAIN/api/stats" > /dev/null 2>&1; then
    echo "  Server is live!"
    break
  fi
  if [ "$i" -eq 18 ]; then
    echo "  WARNING: Server not responding yet. It may still be building."
    echo "  Check: railway logs"
  fi
  sleep 5
done

# --- Done ---
echo ""
echo "================================================"
echo "  bmfote backend deployed!"
echo "================================================"
echo ""
echo "  Database:  $TURSO_URL"
echo "  Server:    $DOMAIN"
echo "  API Token: $API_TOKEN"
echo ""
echo "  To connect any machine, run:"
echo ""
echo "    npx bmfote setup --url $DOMAIN --token \"$API_TOKEN\""
echo ""
echo "  Save that command — you'll need it for each machine."
echo "================================================"
