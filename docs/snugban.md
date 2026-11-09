# Snugban receiver

nanobot can take coding work from Snugban, Keith's coding coordinator (`~/Projects/snugban`). Snugban posts signed webhooks to nanobot's gateway at `POST /snugban`; nanobot verifies each one, works the dispatch with its agent, and reports back over Snugban's peer API and MCP tools.

Snugban's own documentation of the protocol is `docs/agent-protocol.md` in the Snugban repository. The receiver's code is in `nanobot/snugban/`.

## Setup

nanobot runs on another host and reaches Snugban over Tailscale. Below, `snugban-host` is the Mac running Snugban and `nanobot-host` is the machine running `nanobot gateway`; replace `<tailnet>` with your tailnet's name.

### 1. Make Snugban reachable over the tailnet

Snugban listens on `127.0.0.1:7420` by default. In `~/.snugban/config.toml` on snugban-host, set `[server] host` to an address nanobot-host can reach (its Tailscale address) and `[server] public_url`, then restart Snugban. From nanobot-host, check:

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://snugban-host.<tailnet>.ts.net:7420/api/health   # 200
```

### 2. Register nanobot in Snugban

On Snugban's **Agents** page, register an agent:

| Field | Value |
| --- | --- |
| Name | `nanobot` |
| Kind | `nanobot` |
| Max concurrency | `1` |
| Webhook URL | `http://nanobot-host.<tailnet>.ts.net:18790/snugban` |

The webhook URL cannot be changed after registration. Click **Issue key** and keep both values it shows once: the key (`sb_agent_…`) and the webhook secret.

### 3. Give the agent Snugban's MCP tools

The agent reports through Snugban's MCP tools, which it sees as `mcp_snugban_<tool>`, so the MCP server **must be named `snugban`**. In `~/.nanobot/config.json`, merge into `tools`:

```json
{
  "tools": {
    "mcpServers": {
      "snugban": {
        "url": "http://snugban-host.<tailnet>.ts.net:7420/mcp",
        "headers": { "Authorization": "Bearer sb_agent_…" },
        "toolTimeout": 60
      }
    }
  }
}
```

nanobot does not expand `${VAR}` in `config.json`, so this key is in the file in plain text: keep it `chmod 600`.

### 4. Turn on the receiver

Either in `~/.nanobot/config.json`:

```json
{
  "gateway": {
    "host": "100.x.y.z",
    "port": 18790,
    "snugban": {
      "url": "http://snugban-host.<tailnet>.ts.net:7420",
      "agentKey": "sb_agent_…",
      "webhookSecret": "…"
    }
  }
}
```

or, to keep the key and secret out of the file, leave `gateway.snugban` out and set these in the gateway's environment (for the LaunchAgent, its plist's `EnvironmentVariables`; for systemd, `Environment=` or an `EnvironmentFile=` readable only by you):

| Variable | Value |
| --- | --- |
| `SNUGBAN_URL` | `http://snugban-host.<tailnet>.ts.net:7420` |
| `SNUGBAN_AGENT_KEY` | the `sb_agent_…` key |
| `SNUGBAN_WEBHOOK_SECRET` | the webhook secret |

A value in `config.json` wins over its environment variable. If only some are set, the gateway prints `✗ Snugban receiver not started: …` naming what is missing and runs without the receiver. With none set, there is no `/snugban` route (it answers 404).

`gateway.host` and `gateway.port` set where the webhook server listens; `nanobot gateway --port` overrides the port. Set `host` to nanobot-host's Tailscale IP (`tailscale ip -4`), not `0.0.0.0`: the same server answers the unauthenticated `POST /message` (see [Security](#security)), and binding to the Tailscale address keeps it off every other network. If n8n on the LAN still calls `/message`, it must then use the tailnet address too. Restart the gateway, which should print:

```
✓ Snugban: webhooks at http://100.x.y.z:18790/snugban, reporting to http://snugban-host.<tailnet>.ts.net:7420
```

### 5. Let the agent push

For each dispatch the agent clones `https://github.com/<githubRepo>.git` into a checkout of the dispatch's own, `<workspace>/snugban/<project slug>/<dispatchId>`, which nanobot removes once the dispatch is completed, aborted, replaced or failed by a crash (it is kept while the dispatch waits for an answer). It pushes the dispatch's branch, so nanobot-host needs git credentials (HTTPS, e.g. `gh auth setup-git`) that can push to Keith's repositories.

With the `claude-cli` provider, each model call is one `claude --print` run, limited by `providers.claudeCli.timeout` (300 seconds unless set). A coding task may need more; raise it if runs are cut short.

## What each webhook does

Every webhook is checked first: the `X-Snugban-Signature` HMAC over `<X-Snugban-Timestamp>.<raw body>` with the webhook secret (constant-time), a timestamp within 5 minutes, and a signature not seen in the last 10 minutes; these fail with **401** before the body is parsed. Then the body's signed `deliveryId` must equal the `X-Snugban-Delivery` header and must not have been seen in the last 10 minutes, or the answer is **401**. A signed body that is not a webhook nanobot knows (bad JSON, unknown `type`, missing or malformed fields) gets **400**. Bodies over 1 MiB get **413**. Work is done in the background, so answers are immediate.

| Webhook | What nanobot does |
| --- | --- |
| `dispatch` | Acknowledges it over REST (`…/ack`), then runs the agent in the session `snugban:<dispatchId>` with a prompt holding the task, the definition of done, the repository, branch and base branch, Snugban's instructions, and how to report with the `mcp_snugban_*` tools. Task text is fenced off as untrusted data. While a turn runs, nanobot sends `…/heartbeat` itself every `heartbeatEverySeconds` (at most 60). Each `dispatchId` is taken once; one Snugban could not acknowledge is taken again when Snugban resends it. A project with no GitHub repository is failed at once. A new dispatch on a branch nanobot is still working (a revision) stops the old run first; dispatches on one branch never run together. |
| `dispatch` with `revision` | The same, with the changes asked for, and told to continue the existing branch. |
| `decision` | Keith's answer (or that he dismissed the question) becomes the next message in the dispatch of the same `taskId` that nanobot is working on: after the turn in progress, or after a resume. With no such dispatch it goes to the session `snugban:questions`. Each `questionId` is given once. |
| `pause` | Cancels the turn in progress, killing the processes it started (shell commands, the `claude` CLI and what they started), and stops heartbeats. A pause arriving while the dispatch is still being acknowledged holds it until resumed. **404** if nanobot is not working that dispatch, so Snugban does not record a pause that did not happen. |
| `resume` | Starts the agent again on the interrupted message, told it was paused and to check the checkout and branch for work already done. **404** if nanobot is not working that dispatch. |
| `abort` | Cancels the turn in progress (killing its processes), its subagents and its heartbeats, and drops any queued answers. The `dispatchId` is remembered, so the dispatch is never run if it is sent again, including when the abort overtakes the acknowledgement. |
| `status_request` | Sends `…/heartbeat` if nanobot is working the dispatch (or has it paused); otherwise nothing. |

Beyond the webhooks:

- A heartbeat answered with 404 or 409 means Snugban has ended the dispatch, so nanobot stops the run as for an abort. That covers an abort webhook that never arrived.
- When the agent's turns end, nanobot sends one more heartbeat to see where the dispatch stands. If it was neither completed nor failed, nanobot reports progress saying so (the agent may be waiting for Keith's answer) and stops sending heartbeats, so Snugban's stall handling takes over.
- If a turn raises an error, nanobot fails the dispatch, giving only the error's type.

## Limits

- **Pause is not "stop at a good point".** nanobot cannot suspend an agent turn, so a pause cancels it wherever it is, and resume starts a new turn. Work the agent had not committed may be lost, and so, most likely, is the interrupted turn's session history; the resumed turn is given the interrupted message again.
- **A turn that ends without completing or failing leaves the dispatch with Snugban.** nanobot reports progress saying so and stops its heartbeats, but it has already taken that `dispatchId`, so when Snugban's stall handling resends the dispatch nanobot ignores the resend. Snugban then tells Keith after 30 minutes. A decision for the task still continues the run.
- **A hung turn looks alive.** nanobot sends heartbeats for as long as a turn is running, whether or not the agent is making progress, so Snugban's stall handling does not see a turn stuck on a tool or a model call. Only the provider's own timeouts (`providers.claudeCli.timeout`, `tools.exec.timeout`) end it.
- **State is in memory.** After a gateway restart nanobot knows no dispatch: pause and resume get 404, and `status_request` sends nothing. Snugban's stall handling then resends the dispatch (same `dispatchId`) after 20 minutes, and nanobot takes it again on the same branch.
- **Answers to questions asked outside a dispatch** (from a chat channel) go to `snugban:questions`, not to the chat that asked.
- **Messages the agent sends to its own channel** (`snugban`) have nowhere to go and are logged as `Unknown channel: snugban`.
- **Abort stops what nanobot started.** A push already sent to GitHub when the abort arrives may still land.

## Security

- The webhook secret and agent key are never logged. Keep them out of repositories and backups that leave the machine. If one leaks, **Replace key** in Snugban issues new ones.
- **`POST /message` is unauthenticated** and listens wherever the webhook server does (`0.0.0.0` by default): anything that can reach port 18790 can make the agent act. n8n on the LAN relies on it, so it is unchanged. On a tailnet host, restrict who can reach port 18790 with Tailscale ACLs (allow snugban-host and the n8n host only), or bind `gateway.host` to one address (the Tailscale address, or `127.0.0.1` when Snugban and its callers are local).
- The agent pushes with your git credentials and, with the `claude-cli` provider, runs `claude --dangerously-skip-permissions`. A task's text is fenced off as untrusted, but a model can still be misled by it: give nanobot-host credentials that can push only where it should.
