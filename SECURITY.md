# Security Policy

## Supported Versions

This project is currently maintained as a rolling source project.

In practice, the latest version on the default branch is the only version that should be assumed to receive security fixes.

## Reporting a Vulnerability

Please do not disclose security vulnerabilities publicly before a fix is available.

If you discover a vulnerability, report it privately to the maintainer first. Include:

- A clear description of the issue
- Reproduction steps
- Impact assessment
- Screenshots, logs, or proof of concept when helpful
- Any suggested mitigation, if known

If no private reporting channel is published yet, create one before public release, such as:

- a dedicated email address
- a private issue intake form
- a security contact listed on the repository hosting platform

## What Counts as Security-Relevant

Examples include:

- API key leakage
- Secrets written to unexpected locations
- Unsafe handling of local files
- Arbitrary file overwrite
- Unexpected network transmission of local content
- Vulnerabilities in packaging or update workflows

## Secrets Handling

- Real API keys must never be committed to the repository.
- API keys should remain local to the user's machine.
- If you suspect a key was exposed, rotate it immediately.

## Scope Notes

This is a local desktop tool that:

- reads selected PDF files locally
- stores local settings on the user's machine
- sends extracted text to a user-configured OpenAI-compatible API endpoint

Because endpoint choice is user-configured, downstream data handling also depends on the selected API provider.

## Disclosure Process

A typical process for this project should be:

1. Private report received
2. Maintainer validates and scopes impact
3. Fix prepared and tested
4. Release published
5. Public disclosure posted after users have a reasonable chance to update

## Operational Advice for Users

- Use your own trusted API endpoint
- Keep your system protected from local malware
- Review configuration before sharing screenshots or debug logs
- Do not publish local config files containing endpoint details
