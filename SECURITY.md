# Security policy

Img3D is a personal project maintained by **Shiwam Shorya Sharma**. It is designed to run on your own machine or
a trusted local network — not as a public internet service.

## Supported versions

Only the latest commit on `main` receives fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

1. Use GitHub's private reporting: **Security → Report a vulnerability** on this repository
   ([how it works](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)).
2. If that form is not available, open an issue titled "Security contact request" **without any details**, and
   you will be contacted privately.

Include the affected component (backend API, WebSocket, frontend, Blender launcher …), the steps to reproduce and
the impact you expect. You can expect a first response within 7 days.

Vulnerabilities in upstream projects (TRELLIS.2, PyTorch, FastAPI, Blender, nvdiffrast, …) should be reported to
those projects directly; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for links.

## Deployment hardening

- Keep the backend on `127.0.0.1` (the default). For LAN access use the Vite proxy and set `UI_USERNAME` +
  `UI_PASSWORD` in `backend/.env` to turn on login. Traffic is plain HTTP — never expose Img3D directly to the
  internet.
- Secrets (`HF_TOKEN`, UI credentials, `IMG3D_AUTH_SECRET`) live only in `backend/.env`, which is gitignored.
  Never commit them; rotate a token immediately if it leaks.
- Set `IMG3D_ENVIRONMENT=production` to disable the interactive API docs.
- Uploaded images are validated by decoding them (type, size, pixel count), and the optional Blender download is
  checked against the official SHA-256 before it is extracted.
- Model checkpoints and remote code are loaded from local folders you downloaded yourself; only use the official
  Hugging Face repositories listed in [`backend/.model/README.md`](backend/.model/README.md).
