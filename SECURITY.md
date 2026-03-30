# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it by [opening a private security advisory](https://github.com/marc-woernle/bu-ai-bibliography/security/advisories/new) on this repository.

Do **not** open a public issue for security vulnerabilities.

## Scope

This project is a static website and data pipeline. The main security considerations are:

- **API keys**: All API keys (Anthropic, Semantic Scholar) are loaded from environment variables, never hardcoded in source files.
- **Data privacy**: The bibliography contains only publicly available academic metadata (titles, authors, abstracts, DOIs). No private or personally identifiable information is collected or stored.
- **Static site**: The web app runs entirely client-side with no server, no cookies, no user data collection, and no external API calls from the browser.

## Supported Versions

Only the latest version on the `main` branch is supported.
