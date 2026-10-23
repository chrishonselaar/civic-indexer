#!/bin/bash
# Portable bring-up for the OCR-burst image. Injects PUBLIC_KEY if the provider set it
# (RunPod does; Vast provides its own ssh layer instead), starts sshd if host keys exist,
# then runs the command (default: idle so the container stays up). sshd failure is
# NON-FATAL on purpose — on Vast the platform owns ssh and the container must survive
# regardless, and PUBLIC_KEY may be unset. No `set -e`: nothing here should be able to
# kill the container before `exec`.
mkdir -p /root/.ssh && chmod 700 /root/.ssh
if [ -n "$PUBLIC_KEY" ]; then
  echo "$PUBLIC_KEY" >> /root/.ssh/authorized_keys
fi
/usr/sbin/sshd 2>/dev/null || true
exec "${@:-sleep infinity}"
