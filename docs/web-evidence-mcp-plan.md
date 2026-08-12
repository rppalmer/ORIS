# Web Evidence MCP server plan

- Status: Roadmap; not approved for implementation
- Updated: 2026-08-10
- Intended repository: a separate Python project from ORIS

## Purpose

Build a reusable, local-first MCP server that collects public-web evidence for
ORIS and other projects. The server will provide deterministic,
read-only access to Tavily, a self-hosted SearXNG instance, Firecrawl, and an
explicit browser-scraping fallback built with Playwright.

The server owns external data collection. Client applications own question
decomposition, workflow routing, synthesis, citations, memory, and scheduling.
It must not contain an LLM, choose a provider silently, or decide what a client
should research.

This is one cohesive MCP server rather than one server per vendor because the
providers share a public-web, read-only trust boundary. Net-Razor remains a
separate MCP server because authenticated cookies, X, Hacker News, and YouTube
have different credentials, failure modes, and audit requirements.

## Intended architecture

```text
ORIS Web Research
        |
ORIS WebSearch adapter
        |
 Web Evidence MCP
   /      |       \
search  extraction  browser scrape
```

The initial transport will be `stdio`. It is the smallest local deployment and
keeps the server private. Streamable HTTP is considered only if multiple
simultaneous clients need one continuously running server.

ORIS will eventually connect with the official
`langchain-mcp-adapters` package through an ORIS-owned `WebSearch` adapter. The
adapter will translate the approved MCP tool call and structured result into
the existing Web Research contract. The fixed graph will not depend on MCP tool
names or artifact layout, and this integration will not introduce an
unrestricted model-controlled tool loop.

This server may also be useful to a future dynamic MCP exploration specialist,
but that is a separate, deferred ORIS capability. It is not part of this
server's acceptance criteria and will not replace fixed Web Research or serve
scheduled jobs.

## Proposed MCP tools

### `search_web`

Search for public pages using an explicitly selected provider.

Initial inputs:

- `query`;
- `provider`: `tavily` or `searxng`;
- a bounded result count;
- optional provider-independent language, date, and domain filters where both
  the selected provider and the stable contract support them.

The first version will not accept `auto` as a provider and will not silently
fall back to another provider. A failure must identify the provider that failed.

The MCP request will define provider-independent meanings for common search
controls. Each provider adapter must translate those controls or return an
explicit unsupported-option error; it may not silently ignore them. Basic query
search is the portable baseline. Domain and recency controls are
capability-dependent: Tavily and Firecrawl Search expose native equivalents,
while SearXNG behavior depends on its configured engines and must be proven by
contract tests. Firecrawl Search may later implement `search_web`, but
Firecrawl page extraction remains a separate `extract_pages` capability.

### `extract_pages`

Extract normalized content from an explicit, bounded list of public URLs using
Firecrawl. Search and extraction remain distinct operations so a client can
review or filter URLs before spending extraction credits.

The initial output should contain cleaned content and page metadata. Raw HTML,
screenshots, links, or structured LLM extraction are added only for a proven
use case. Firecrawl's own agentic or LLM-generated output will not replace the
client application's local synthesis model.

### `scrape_page`

Render and extract one explicitly supplied public URL with Playwright. This is
the manual browser fallback for JavaScript-heavy pages or pages that require a
known sequence of waits or selector-based interactions.

Initial behavior:

- one URL and one isolated browser context per call;
- headless operation by default, with headed mode reserved for diagnosis;
- fixed navigation and overall timeouts;
- explicit CSS selectors and bounded wait actions;
- no arbitrary JavaScript supplied through MCP arguments;
- no CAPTCHA solving, proxy rotation, automated login, or access-control
  bypass;
- return final URL, title, normalized visible content, method, duration, and
  diagnostic status;
- close the page, browser context, and browser after every call.

Standard Playwright runs first. `playwright-stealth` is an optional mode and is
disabled by default. It may be enabled only for a reviewed target where a
contract test demonstrates that standard Playwright cannot retrieve otherwise
public content. Every use must be explicit and recorded in the audit event.

`playwright-stealth` is a third-party package, not an official Playwright
feature. Its maintainer describes it as a proof-of-concept that should only be
expected to handle simple bot detection. It is not a guarantee of access and
does not justify bypassing a site's terms, authentication, CAPTCHA, or explicit
denial.

## Stable normalized results

Provider-specific SDK objects and raw payloads must not cross the MCP boundary.
Search results should normalize to fields equivalent to:

```text
query
provider
results[]
  title
  url
  snippet
  relevance_score (optional)
provider_request_id (optional)
elapsed_seconds
```

Extraction and browser results should use a separate page-content contract:

```text
requested_url
final_url
provider_or_method
title
content
fetched_at
elapsed_seconds
```

The server will use MCP structured output with explicit Python types. Clients
must still validate the structured result at their own trust boundary.

## Determinism and safety

- Provider selection, result limits, extraction limits, timeouts, browser mode,
  and stealth use are explicit.
- No hidden retry, automatic provider fallback, or unbounded crawling is
  permitted initially.
- Only `http` and `https` targets are accepted.
- Requests to loopback, link-local, private-network, local-file, and other
  non-public destinations are rejected by default, including after redirects.
  This prevents an LLM-supplied URL from turning the server into an SSRF path.
- Scraping is limited to public content and must respect applicable site terms,
  robots guidance, rate limits, and access restrictions.
- Authenticated browser sessions and imported cookies are outside the first
  version and require a separate credential-handling decision.
- Retrieved text is untrusted evidence. Clients must not execute instructions
  embedded in search results or page content.
- API keys stay in server-side environment configuration and never appear in
  MCP arguments or results.

## Audit and resource behavior

Each call should record a correlation ID, tool name, provider or browser mode,
sanitized inputs, target domains, start time, duration, result count, status,
and concise error. Logs must exclude credentials, cookies, authorization
headers, and full page content by default.

Because `stdout` belongs to the MCP protocol under `stdio`, operational logs
must use MCP logging, `stderr`, or a configured local audit file. A simple local
JSON Lines audit file is sufficient initially.

Browser work is on demand and one-at-a-time. The server must not keep a browser
resident between calls until measurement proves that the memory tradeoff is
worthwhile. Development browser work should run on the MacBook when practical;
Mac mini deployment must be measured alongside the resident oMLX model.

## Dependency policy

Dependencies are added only in the phase that uses them:

- official MCP Python SDK for the server and structured tool output;
- Tavily's supported Python integration or API client for Tavily;
- a small HTTP client for SearXNG's JSON API if the MCP SDK's transitive stack
  cannot satisfy the requirement;
- Firecrawl's supported client or API for extraction;
- official Playwright Python plus its managed browser binary for browser
  scraping;
- third-party `playwright-stealth` only after its limitations and compatibility
  are verified by a focused contract test.

Versions must be rechecked and pinned when each phase begins. As of this note,
Playwright is the official Microsoft browser-automation library and
`playwright-stealth` 2.0.3 is the current third-party release. Neither becomes
an ORIS dependency merely because it appears in this roadmap.

## Build phases

1. Create a separate `web-evidence-mcp` Python 3.12 repository with `uv`, a
   `src/` layout, Ruff, pytest, its own `AGENTS.md`, and no provider integration.
2. Define normalized request/result models and three bounded MCP tool contracts.
   Use fakes to test structured outputs, unsupported provider options, and
   failures over `stdio`.
3. Add Tavily search and prove parity with ORIS's current direct
   adapter using focused unit and opt-in live contracts.
4. Add the SearXNG search adapter and compare it with Tavily using the same
   accepted Web Research evaluation questions. Evaluate Firecrawl Search as an
   additional provider only if it offers a measured benefit beyond its planned
   extraction role.
5. Add Firecrawl page extraction only after search snippets prove insufficient
   for a documented case.
6. Add standard Playwright `scrape_page` using a local fixture site for routine
   tests and one opt-in public-page contract.
7. Evaluate `playwright-stealth` separately. Keep it disabled unless a reviewed
   site-specific test proves it is both necessary and effective.
8. Integrate ORIS through official `langchain-mcp-adapters` and an ORIS-owned
   `WebSearch` adapter. Retain the direct Tavily path until MCP parity, tracing,
   error propagation, and resource behavior are verified. MCP tool names and
   artifact layout must not enter the fixed Web Research graph.

Only one phase is implemented at a time. Provider auto-selection, parallel
browser sessions, crawling, caching, persistent cookies, and distributed
deployment remain deferred until measured needs justify them.

## Acceptance criteria before ORIS migration

- All MCP tools return validated structured content.
- Unit tests use injected provider fakes and make no network calls.
- Each provider has a separate opt-in contract test.
- Search and extraction limits are enforced before external access.
- Errors name the provider and preserve useful context without leaking secrets.
- Audit records correlate MCP calls with client traces.
- Browser contexts close after both success and failure.
- Public URL and redirect validation blocks local/private targets.
- The MCP route matches or improves the direct Tavily baseline without changing
  ORIS's Web Research state contract.

## Session handoff

Start implementation in a new session and a new repository. A fresh session
keeps ORIS and the capability server from sharing dependencies or
accidentally mixing orchestration with data collection. This document preserves
the useful architectural context from the ORIS discussion.

Suggested opening request for that session:

> Create a separate Python project for the Web Evidence MCP server. First read
> `docs/web-evidence-mcp-plan.md` from the ORIS project. Follow only
> phase 1, keep the implementation simple and explicit, consult current official
> MCP documentation before code, explain every direct dependency, and do not add
> a provider integration until I approve the next phase.

Copy this plan into the new repository during phase 1 so it becomes the new
project's durable source of truth.

## References

- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [MCP structured tool output](https://py.sdk.modelcontextprotocol.io/server/)
- [LangChain MCP adapters](https://docs.langchain.com/oss/python/langchain/mcp)
- [Tavily API](https://docs.tavily.com/documentation/api-reference/introduction)
- [SearXNG search API](https://docs.searxng.org/dev/search_api.html)
- [Firecrawl API](https://docs.firecrawl.dev/api-reference/introduction)
- [Playwright Python](https://playwright.dev/python/docs/intro)
- [`playwright-stealth` package and limitations](https://pypi.org/project/playwright-stealth/)
