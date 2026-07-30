# Agent Instructions

## Source of truth

The complete and approved product requirements are located at:

- `docs/PRODUCT_SPEC.md`

Read that document before proposing or implementing any product-related change.

Do not introduce features, roles, workflows, states, business rules, or assumptions that are not explicitly defined in the product specification.

When a requirement is ambiguous or missing, report the ambiguity and request clarification instead of inventing a solution.

## Current implementation

Before changing the code:

1. Inspect the complete repository.
2. Understand the existing market-data implementation.
3. Review the tests and runtime commands.
4. Identify the exact files affected by the requested task.
5. Present a plan before implementation when requested.

## Development rules

- Preserve existing working behavior unless the task explicitly changes it.
- Keep Telegram transport separate from application and business logic.
- Development uses Telegram `getUpdates`; production will later use Webhook.
- Do not hard-code secrets, tokens, chat IDs, or environment-specific values.
- Do not log secrets or sensitive Telegram payloads.
- Add or update tests for every behavioral change.
- Run the relevant tests before reporting completion.
- Report commands executed, test results, changed files, and remaining risks.
- Do not perform unrelated refactoring.
- Do not expand the product scope without explicit approval.