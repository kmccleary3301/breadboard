# Gap Recovery Workflow

## Setup

- baseUrl: `http://127.0.0.1:4173`
- mock bridge API endpoints active
- create session task: `run gap workflow`

## Steps

1. Create and attach the gap-profile session.
2. Verify connection state enters `gap`.
3. Verify `Recover Stream` action is enabled.
4. Trigger recover action.
5. Verify connection state returns to active (`connecting` or `streaming`).
6. Verify transcript still contains expected bootstrap content.
