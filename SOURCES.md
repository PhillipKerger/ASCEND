# Current Official Implementation References

These links were current on July 19, 2026. Re-check them during implementation and avoid
spreading API assumptions throughout the codebase.

- OpenAI model overview and model IDs:
  https://developers.openai.com/api/docs/models
- Reasoning models, effort (`xhigh`, `max`) and GPT-5.6 `pro` mode:
  https://developers.openai.com/api/docs/guides/reasoning
- Responses API reference:
  https://developers.openai.com/api/reference/resources/responses/methods/create/
- Structured Outputs:
  https://developers.openai.com/api/docs/guides/structured-outputs
- Web search tool:
  https://developers.openai.com/api/docs/guides/tools-web-search
- Codex CLI:
  https://developers.openai.com/codex/cli
- Codex authentication (`codex login`, ChatGPT/API key distinction, and login status):
  https://developers.openai.com/codex/auth
- Codex non-interactive mode (`codex exec`):
  https://developers.openai.com/codex/non-interactive-mode
- Codex CLI command reference:
  https://developers.openai.com/codex/cli/reference
- Codex configuration and security:
  https://developers.openai.com/codex/config-reference
  https://developers.openai.com/codex/agent-approvals-security
- Codex pricing and plan availability:
  https://chatgpt.com/codex/pricing/
- OpenAI Agents SDK, optional rather than required:
  https://openai.github.io/openai-agents-python/

Implementation rule: feature-detect and validate installed versions where possible. Keep
model names and Codex flags configurable. Codex with saved ChatGPT authentication is the
recommended/default backend; the direct Responses API is explicit and advanced, with no silent
provider fallback.
