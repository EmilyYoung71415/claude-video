# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repo root, if it exists
- `CONTEXT-MAP.md` at the repo root, if it exists
- `docs/adr/`, if it exists

If these files do not exist, proceed silently. The domain-modeling skills create them lazily when terms or decisions actually get resolved.

## File structure

This repo currently uses a single-context layout.

## Use the glossary's vocabulary

When output names a domain concept, use the term as defined in `CONTEXT.md` when one exists.

## Flag ADR conflicts

If output contradicts an existing ADR, surface it explicitly rather than silently overriding.

