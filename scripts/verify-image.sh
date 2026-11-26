#!/usr/bin/env bash
set -euo pipefail

build_args=(
  --platform linux/amd64
  --label "org.opencontainers.image.source=https://github.com/${GITHUB_REPOSITORY:-local/ocr-agent}"
  --label "org.opencontainers.image.revision=${GITHUB_SHA:-local}"
)
if [[ "${GITHUB_REF_TYPE:-}" == tag ]]; then
  build_args+=(--label "org.opencontainers.image.version=${GITHUB_REF_NAME#v}")
fi

docker build "${build_args[@]}" --target test -t ocr-agent:test .
docker build "${build_args[@]}" --target runtime -t ocr-agent:verified .
export OCR_API_TOKEN
OCR_API_TOKEN="$(openssl rand -hex 32)"

# No host port and no real document: run the probe inside the final image.
# Match the NAS deployment's writable model cache while keeping the root read-only.
container="$(docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges \
  --volume /var/lib/ocr-models \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 -e OCR_API_TOKEN ocr-agent:verified)"
trap 'docker rm -fv "$container" >/dev/null' EXIT
if ! docker exec -i "$container" python - < tests/smoke_container.py; then
  docker logs --tail 70 "$container"
  exit 1
fi
