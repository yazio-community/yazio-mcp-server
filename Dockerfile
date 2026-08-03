# Alpine rather than slim: the Debian base and its CPython together account for
# 130 MB before a single dependency is installed, and nothing here needs glibc.
# Every dependency publishes a musllinux wheel, so the build stage still
# compiles nothing.
FROM python:3.13-alpine AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /src

# README.md is not documentation here: pyproject.toml names it as the package
# readme, and the build fails without it.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# `--without-pip` plus the base image's own pip pointed at the venv, rather than
# a venv with pip inside it: pip is 12 MB that the runtime image would otherwise
# carry for nothing.
RUN python -m venv --without-pip /opt/venv \
 && pip --python /opt/venv/bin/python install .

# cryptography is 15 MB and this server never reaches it: it arrives through
# `mcp -> pyjwt[crypto]`, for the OAuth and JWT verification paths that a
# Basic-auth server built without AuthSettings does not take. pyjwt imports it
# lazily and degrades to its no-crypto algorithm set, so removing it costs the
# HMAC-only signing this image already has no caller for.
#
# This is a deliberate divergence from the declared dependency set: should the
# server ever grow an OAuth path, drop this line before that ships, or the
# failure surfaces at runtime rather than at install.
RUN pip --python /opt/venv/bin/python uninstall -y cryptography

# Bytecode caches are 11 MB of the 30 MB left, and are rebuilt on demand —
# except that PYTHONDONTWRITEBYTECODE below means they are not, which costs a
# few milliseconds of import time per process start and saves the space for
# good. (Stripping the remaining native extensions was measured at 272 kB, and
# is not worth a binutils install.)
RUN find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} +


FROM python:3.13-alpine

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The interpreter is here to run one venv, so the tooling that would build
# another one is dead weight.
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip \
           /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.13/ensurepip \
           /usr/local/lib/python3.13/idlelib \
           /usr/local/lib/python3.13/test \
           /usr/local/bin/pip*

COPY --from=build /opt/venv /opt/venv

# The server keeps nothing on disk — tokens are cached in memory and the
# caller's credentials never leave the request — so the process has no reason to
# own any files. Fixed uid so a Kubernetes runAsUser can name it.
RUN adduser --disabled-password --uid 10001 yazio
USER yazio

EXPOSE 8931

# 0.0.0.0 rather than the loopback default: a container's own loopback is
# reachable from nothing else. Basic credentials are encoded, not encrypted, so
# publish this port only behind TLS or on a network you control.
#
# Clients that reach the server under any name other than localhost — a compose
# service name, a Kubernetes Service, an Ingress hostname — need that name added
# as `--allowed-host <name>:*`, or the transport's DNS-rebinding guard answers
# 421. Append it by overriding `command:` in compose or `args:` in a pod spec.
CMD ["yazio-mcp-server", "--host", "0.0.0.0", "--port", "8931"]
