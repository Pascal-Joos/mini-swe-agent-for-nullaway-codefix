#!/usr/bin/env bash
set -e

# Default to UID/GID from environment or from the container's current uid/gid
USER_ID=${HOST_UID:-$(id -u)}
GROUP_ID=${HOST_GID:-$(id -g)}
USER_NAME=${HOST_USER:-minisweuser}
USER_HOME=/home/${USER_NAME}

# Create group if it doesn't exist
if ! getent group "${GROUP_ID}" > /dev/null 2>&1; then
  groupadd -g "${GROUP_ID}" "${USER_NAME}"
fi

# Create user if it doesn't exist
if ! id -u "${USER_ID}" > /dev/null 2>&1 2>/dev/null; then
  useradd --no-create-home -u "${USER_ID}" -g "${GROUP_ID}" -s /bin/bash "${USER_NAME}"
fi

# Ensure home directory exists and has correct ownership
mkdir -p "${USER_HOME}"
chown -R "${USER_ID}:${GROUP_ID}" "${USER_HOME}"

# Switch to the user and exec the requested command
exec su -s /bin/bash -c "$*" "${USER_NAME}"