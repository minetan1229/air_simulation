# Discord Remote Bridge Specification

This document's canonical source is the actual behavior of `tools/discord_bridge.py` and `tools/bridge_*.py`. If code and documentation diverge, code takes precedence.

## 1. Overview

A bridge that drives two agent backends and remotely operates the local `simulation/v3` workspace from Discord.

| Backend | Execution | Stream Format | Conversation ID | Default Model |
|---|---|---|---|---|
| `antigravity` (primary) | `agy -p <prompt> --model <m> --output-format stream-json --dangerously-skip-permissions` | NDJSON (`event`) | Conversation UUID | `gemini-3.8-flash-low` |
| `kilo` (fallback) | `kilo run --model <provider/model> --format json --auto -- <prompt>` | NDJSON (`type`) | `ses_*` session | `kilo/kilo-auto/free` (**free**) |

## 2. Routing (`tools/bridge_router.py`)

1. **Primary Execution**: Execute the requested backend once, normalize NDJSON to `StreamEvent`, and stream to Discord live display.
2. **429 Auto-Continuation**: On detection of `TooManyRequests` / `RESOURCE_EXHAUSTED` / `quota exceeded` / `out of quota` etc., **do not finalize as failure**. After exponential backoff (`base` 2s, `cap` 60s, max `BRIDGE_RATE_LIMIT_RETRIES` 3 times), resume same processing with `continue` (Antigravity: `--conversation` / `--continue`, Kilo: `--session` / `--continue`).
   - Strong markers (`too many requests` / `resource_exhausted` / `quota exceeded` etc.) detected from all output.
   - Weak markers (standalone `rate limit` / `429`) limited to stderr/error output to prevent false positives in normal response bodies.
3. **Auto Fallback**: If primary backend cannot complete due to API error, timeout, zero remaining rate, or persistent 429 failure, **do not interrupt** but switch to Kilo Code free model and complete. Switch is notified to Discord response channel and Webhook (to main agent).

## 3. Rate Limits / Quota (`tools/bridge_quota.py`)

- Retrieval: `agy -p "/quota"` → `group⇥metric⇥N%⇥resetISO` format, `agy -p "/credits"` → remaining credits.
- Caching: `QuotaTracker` caches for default 120s (`BRIDGE_QUOTA_TTL`).
- Exhaustion Determination: `is_exhausted` when weekly remaining (`Weekly Limit Remaining`) ≤ 1%.
- **Estimated Available Count**: No assumed total; calculated from measured slope of "send count vs consumption %". Returns `None` if insufficient measurements.
- Display: `!quota` / `/quota` (dedicated embed), summary also in `/status`.

## 4. Monitor Agent (`tools/bridge_monitor.py`)

- Monitored: Host health (CPU/RAM/GPU), backend executable existence, API response latency, consecutive errors, rate limit occurrences, fallback count, quota remaining.
- Monitor Interval: `BRIDGE_MONITOR_INTERVAL` (default 60s). Alert types: `backend_unavailable` / `consecutive_errors` / `quota_exhausted` / `fallback`.
- Notification Destinations: Discord response channel (embed) + Discord Webhook (to main agent, via `tools/notify_discord.py`).
- Display: `!monitor` / `/monitor`.

## 5. Authorization & Guardrails

- **Authorization (is_authorized)**: If `ALLOWED_USER_IDS` is empty, **reject all users** (secure default). Empty list does NOT mean "allow all".
- **Channel Restriction**: If `ALLOWED_CHANNEL_IDS` is set, only those channels permitted.
- **Rate Limiting**: Per-user token bucket (default: 10 commands / 60s). Configurable via `DISCORD_RATE_LIMIT_MAX` / `DISCORD_RATE_LIMIT_WINDOW`.
- **Prompt Length Validation**: Prompts exceeding default 4000 chars rejected (`DISCORD_MAX_PROMPT_LEN`).
- **!chrome / /chrome URL Validation**: Only http/https allowed. Private IPs / loopback / link-local / cloud metadata blocked. Bare `localhost` hostname permitted.
- **Single Instance Control**: Lock on loopback port (default 49372, `DISCORD_BRIDGE_LOCK_PORT`) to prevent duplicate launches.
- **`--dangerously-skip-permissions` / `--auto`**: Bridge-side authorization (`is_authorized()`) always applied.
- **Internal Exceptions**: Only Error ID sent to Discord. Stack traces logged to server log.
- **Task ID Uniqueness**, **Model Name Whitelist**, **Persistent View Re-registration on Startup**.

## 6. Command List

| Slash | Prefix | Description |
|---|---|---|
| `/chat <prompt>` | `!chat <prompt>` | Execute agent (parallel, 429 auto-continue, auto-fallback) |
| `/status` | `!status` | CPU, memory, GPU, tasks, backend, quota, monitor status |
| `/quota` | `!quota` | Remaining rate limits (utilization, estimated available count, credits) |
| `/monitor` | `!monitor` | Monitor agent status and alerts |
| `/backend [antigravity\|kilo]` | `!backend` | Switch execution backend (Kilo is free model) |
| `/models` / `/model [id]` | `!models` / `!m` | List/switch models (Antigravity / Kilo Code) |
| `/chats` / `/resume <id>` | `!chats` / `!resume` | Conversation list / resume (both backends) |
| `/new` | `!new` | Reset conversation context |
| `/chrome [url]` | `!chrome` | Launch local Chrome |
| `/abort` | `!abort` | Stop running task (🛑 reaction also works) |

## 7. Environment Variables

| Variable | Default | Meaning |
|---|---|---|
| `DISCORD_BOT_TOKEN` | (required) | Bot token |
| `ALLOWED_USER_IDS` | (empty = reject all) | Comma-separated allowed user IDs |
| `ALLOWED_CHANNEL_IDS` | (empty = all channels) | Comma-separated allowed channel IDs |
| `DISCORD_RATE_LIMIT_MAX` | `10` | Max commands for rate limit |
| `DISCORD_RATE_LIMIT_WINDOW` | `60.0` | Rate limit window seconds |
| `DISCORD_MAX_PROMPT_LEN` | `4000` | Max prompt characters |
| `DISCORD_BRIDGE_LOCK_PORT` | `49372` | Single instance control port |
| `DISCORD_DANGEROUS_SKIP_PERMISSIONS` | `0` | Unused (always disabled) |
| `DEFAULT_MODEL` | `gemini-3.8-flash-low` | Default model (Antigravity) |
| `DEFAULT_BACKEND` | `antigravity` | Default backend |
| `FALLBACK_MODEL` | `kilo/kilo-auto/free` | Fallback model (**free**) |
| `BRIDGE_FALLBACK_ENABLED` | `1` | Fallback enabled/disabled |
| `BRIDGE_RATE_LIMIT_RETRIES` | `3` | Max retries for 429 auto-continue |
| `BRIDGE_BACKOFF_BASE` / `BRIDGE_BACKOFF_CAP` | `2.0` / `60.0` | Exponential backoff base/cap seconds |
| `BRIDGE_TASK_TIMEOUT` | `1800` | Per-task timeout seconds |
| `BRIDGE_MONITOR_INTERVAL` | `60` | Monitor agent interval seconds |
| `BRIDGE_MONITOR_ERROR_THRESHOLD` | `3` | Consecutive errors for alert |
| `BRIDGE_QUOTA_TTL` | `120` | Quota cache seconds |
| `AGY_CLI_PATH` / `KILO_CLI_PATH` | (auto-detect) | Explicit CLI executable paths |

## 8. Tests

- `tests/test_discord_bridge.py`: Unit tests for authorization logic, rate limiting, URL validation, etc.
- `tests/test_bridge_agents.py`: Unit tests for quota parsing, rate detection, backoff, backend command generation/parsing, **429 auto-continue**, **fallback**, monitor agent.
- No actual external network (including Discord) transmission in tests (mocks only).