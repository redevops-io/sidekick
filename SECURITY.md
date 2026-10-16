# Security policy

Report vulnerabilities by email to **redevops@redevops.io** (listed on the ReDevOps GitHub organization); please do not file public issues for undisclosed vulnerabilities.

## Things to know before running sidekick
- Agents run auto-approved: edits are accepted and `Bash` is limited to an allowlist (`--approval bypass` removes that limit). Run it only on repositories and machines where that is acceptable.
- `sidekick gateway` triggers auto-approved runs from chat messages; it is closed unless `SIDEKICK_GATEWAY_ALLOW` is set, and `SIDEKICK_GATEWAY_OPEN=1` accepts anyone. It binds `0.0.0.0:8787` by default for webhooks.
