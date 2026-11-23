# Docs rules

Applies to everything under docs/ and to README.md and dev/README.md.

## Files
- DESIGN.md is the source of truth for architecture and behavior. Code follows it.
- TODO.md tracks remaining work per milestone. Keep the status overview current.
- README.md is for users running the app. dev/README.md is for developers.

## Rules
- Update the docs in the same change as the code that alters documented behavior.
- If code deviates from DESIGN.md, update DESIGN.md and say why; never leave them out of sync.
- When a milestone task is finished, tick it in TODO.md and update the status overview.
- Do not document features of later milestones as if they already exist.
- Keep the section numbering in DESIGN.md stable; add new sections at the end of a level
  rather than renumbering, and fix any cross-references you break.
- Never put real secrets, tokens, or connection strings in examples. Use placeholders.
- Commands in docs must be copy-pasteable and use yarn (never npm or pnpm) for the frontend.
- Match the existing tone: short, direct, imperative. No marketing language.
- Do not create new markdown files unless asked.
