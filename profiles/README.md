# Profiles

Each `.json` file in this directory is automatically loaded as a built-in profile at startup.

## Adding a new profile

Create a new `.json` file here and restart the container. The profile will appear as a tab in the UI.

---

### OpenAI-style profile

```json
{
  "name": "My OpenAI Profile",
  "api_type": "openai",
  "endpoint_template": "http://{host}/v1/cust-openai/v1/responses",
  "host": "your-host-here",
  "model": "openai.gpt-oss-20b",
  "bearer_token": "",
  "api_key": "",
  "extra_headers": {},
  "extra_body": {}
}
```

### Claude (Bedrock invoke) profile

```json
{
  "name": "My Claude Profile",
  "api_type": "claude",
  "host": "your-host-here",
  "cust_model": "cust-model",
  "model": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "anthropic_version": "bedrock-2023-05-31",
  "max_tokens": 1024,
  "bearer_token": "",
  "api_key": "",
  "extra_headers": {},
  "extra_body": {}
}
```

## Fields

| Field | Description |
|-------|-------------|
| `api_type` | `"openai"` or `"claude"` — controls request format |
| `host` | Hostname/IP of the AI Gateway |
| `cust_model` | Path segment for Claude URLs: `/v1/{cust_model}/model/...` |
| `model` | Model name (OpenAI) or full model ID (Claude, goes in URL) |
| `bearer_token` | Authorization: Bearer value |
| `api_key` | x-ns-aig-apikey value |
| `anthropic_version` | Claude only — `bedrock-2023-05-31` |
| `max_tokens` | Claude only — required |
| `extra_headers` | Additional HTTP headers as key/value object |
| `extra_body` | Additional body fields as key/value object |

Credentials left blank here can be filled in the UI and saved per-profile.
