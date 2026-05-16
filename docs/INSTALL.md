# Install

## Docker (recommended)

```bash
docker run -d \
  --name translarr \
  -p 9100:9000 \
  -v /mnt/user/appdata/translarr:/data \
  -v /mnt/user/media:/media \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/jesposito/translarr:main
```

Or with `docker compose`:

```bash
git clone https://github.com/jesposito/translarr
cd translarr
cp .env.example .env
$EDITOR .env   # set your API key, adjust paths
docker compose up -d
```

Verify it's running:

```bash
curl http://localhost:9100/health
# {"status":"ok","version":"0.1.0","llm_provider":"anthropic","llm_model":"claude-sonnet-4-6"}
```

The container exposes port **9000** internally. Map it to whatever host port you want.

## Wire up Radarr

Radarr → Settings → Connect → Add → Webhook:

- Name: `Translarr`
- URL: `http://translarr:9000/webhooks/radarr` (or the host IP if Radarr is outside the Docker network)
- Method: `POST`
- Triggers: `On Import`, `On Upgrade`
- Headers (if `WEBHOOK_SECRET` is set): `X-Translarr-Secret: <secret>`

## Wire up Sonarr

Same as Radarr, but URL ends in `/webhooks/sonarr`.

## Wire up Emby

Emby → Notifications → Add → Webhook:

- URL: `http://translarr:9000/webhooks/emby`
- Events: `Library New`, `Library Updated`

For in-player translation (subtitle search modal), see the [Emby Plugin](../README.md#emby-plugin-optional) section.

## Wire up Jellyfin

Requires the [Webhook plugin](https://github.com/jellyfin/jellyfin-plugin-webhook). Once installed:

- URL: `http://translarr:9000/webhooks/jellyfin`
- Triggers: `ItemAdded`

## Wire up Plex

Add a webhook via Plex settings or Tautulli:

- URL: `http://translarr:9000/webhooks/plex`
- Handles `library.new` and `media.play` events

## Local Ollama (no API costs)

In `.env` or docker compose:

```yaml
environment:
  LLM_PROVIDER: ollama
  LLM_MODEL: qwen3:14b
  OLLAMA_HOST: http://ollama:11434
```

Uncomment the `ollama` service in `docker-compose.yml`. Then:

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen3:14b
```

Quality is lower than Claude, but cost is zero and everything stays on-box.
