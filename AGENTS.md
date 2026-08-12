For every LangGraph or LangChain implementation decision:

1. Consult langgraph-docs-mcp before proposing architecture,
   dependencies, or code.

2. Identify the official component, integration package, and
   recommended usage before considering custom code.

3. Prefer configuring and composing official components over
   implementing equivalent behavior.

4. Do not create a custom model client, tool executor, retry system,
   checkpoint system, state store, scheduler integration, or tracing
   layer unless:
   - the official option has been identified;
   - its documented limitations have been reviewed;
   - a test demonstrates that it cannot satisfy the requirement; and
   - the user explicitly approves the custom implementation.

5. Before adding a runtime dependency, explain:
   - what capability it provides;
   - why an existing dependency cannot provide it;
   - whether it is a direct or transitive dependency.

6. When compatibility is uncertain, inspect the official or upstream
   contract and mark the behavior as unverified. Do not automatically add a
   test or custom compatibility code; apply the simplicity gate below.

7. Keep implementation steps small. Do not proceed beyond the
   currently approved step.

8. A small factory that configures an official integration is allowed.
   It must not reimplement transport, message conversion, tool calling,
   streaming, or retry behavior.

## Simplicity gate — highest project priority

These rules override generic enterprise, defense-in-depth, extensibility, and
"best practice" instincts when those instincts do not solve a demonstrated
current requirement.

1. Implement only the minimum behavior required by the currently approved
   milestone. Once that behavior works and is proportionately verified, stop.

2. Before adding a type, helper, abstraction, validation rule, test layer,
   configuration option, fallback, or compatibility mechanism, identify:
   - the current requirement it satisfies;
   - the realistic failure it prevents; and
   - why existing code or upstream validation does not already handle it.
   If any answer is missing, do not add it.

3. "This dependency might change someday" is not sufficient justification for
   custom compatibility code or a contract test. Prefer clear runtime errors
   and existing end-to-end tests until an actual compatibility problem occurs.

4. Do not duplicate validation, defaults, limits, schemas, normalization,
   retries, error classification, or processing state owned by an SDK,
   provider, MCP server, or official integration.

5. For MCP integrations:
   - use the official adapter and runtime tool discovery;
   - expose only the tools required by the current specialist;
   - rely on the MCP server for its tool schemas and provider behavior;
   - do not mirror the server's complete schemas or defaults in ORIS
     tests;
   - test tool behavior through the specialist's normal integration test; and
   - if the MCP server lacks required functionality, stop and report the gap so
     the MCP server can be updated.

6. Add a focused compatibility test only when:
   - ORIS necessarily depends on a specific interface detail;
   - that dependency is not already exercised by a normal integration test;
   - an incompatible change would produce a serious or unclear failure; and
   - the user explicitly approves the additional test after its cost and value
     are explained.

7. Prefer one test at the closest meaningful behavior boundary over multiple
   tests that detect the same failure at different layers.

8. Do not add speculative extensibility or robustness for roadmap features.
   Deferred functionality belongs in documentation, not in current code.

9. When multiple documented approaches satisfy the requirement, choose the
   one with the fewest concepts and least custom code.

10. Do not represent the same fact twice when it can be derived simply and
    deterministically from one source.

11. When a simpler approach becomes apparent, remove the unnecessary
    complexity instead of preserving it because work has already been done.

MCP capability boundaries:

1. Treat each MCP server as the owner of its provider-specific validation,
   limits, normalization, error classification, processing state, and audit
   behavior. Do not duplicate those responsibilities in ORIS.

2. If an MCP server is missing required provider functionality or does not
   expose a suitable contract, stop and report the gap. Do not compensate with
   custom ORIS code; the user will update the MCP server instead.

3. ORIS may enforce orchestration-owned limits, such as a total number
   of tool or model calls in one graph run, when the MCP contract cannot express
   that cross-call budget. State this distinction before implementing it.

4. Inspect the MCP server's exposed tools only as needed to implement the
   current specialist. Rely on runtime discovery and the specialist's normal
   integration test instead of mirroring the full schema in ORIS.

Testing model behavior:

1. Before adding or modifying a model-related test, state the invariant the
   test is intended to protect.

2. State whether that invariant is deterministic. Pytest may enforce schemas,
   bounds, graph paths, explicit user controls, side-effect limits, and other
   deterministic contracts. Probabilistic semantic quality belongs in the
   versioned evaluation set rather than a blocking pytest assertion.

3. Explain why the assertion generalizes beyond its example. Do not assert the
   presence or absence of case-specific words in model-generated output unless
   those words represent a documented, universal contract.

4. State why the behavior belongs in pytest or in the evaluation set. Report
   model-related verification under separate "Deterministic contracts" and
   "Semantic evaluations" headings when handing changes back to the user.

Record these four points in the implementation commentary before writing the
test. Keep test docstrings focused on the invariant; do not add repetitive test
metadata or a custom testing framework solely to record this rationale.
