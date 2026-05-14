# Install

## Docker (recommended)

```bash
git clone https://github.com/jesposito/translarr
cd translarr
cp .env.example .env
$EDITOR .env
docker compose up -d
docker compose logs -f translarr
```

Check it's alive:

```bash
curl http://localhost:9100/health
```

You should see:

```json
{"status": "ok", "version": "0.1.0", "llm_provider": "anthropic", "llm_model": "claude-sonnet-4-6"}
```

## Wire up Radarr

Radarr → Settings → Connect → Add → Webhook:

- Name: `Translarr`
- URL: `http://translarr:9000/webhooks/radarr` (or the Docker host's IP + `:9100` if Radarr lives outside the compose network)
- Method: `POST`
- Triggers: `On Import`, `On Upgrade`
- Headers (if you set `WEBHOOK_SECRET`): `X-Translarr-Secret: <secret>`

## Wire up Sonarr

Same flow as Radarr, URL ends in `/sonarr` instead.

## Wire up Emby

Emby → Notifications → Add → Webhook:

- URL: `http://translarr:9000/webhooks/emby`
- Events: `Library New`, `Library Updated`

## Wire up Jellyfin

Requires the [Webhook plugin](https://github.com/jellyfin/jellyfin-plugin-webhook). Once installed:

- URL: `http://translarr:9000/webhooks/jellyfin`
- Triggers: `ItemAdded`

## Local Ollama (no API costs)

In `.env`:

```
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:14b
OLLAMA_HOST=http://ollama:11434
```

Uncomment the `ollama` service in `docker-compose.yml`. Then:

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen3:14b
```

Quality drops vs Claude, but cost is zero and everything stays on-box.
